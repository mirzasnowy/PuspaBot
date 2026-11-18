"""Inactivity curator for skills (G24).

Skills rot when nobody uses them. The curator is triggered by
inactivity — it is not a daemon — and walks the skill
directories once per run:

- Deterministic state machine: ``active -> stale -> archived``,
  one step per run. Archiving is the maximum: nothing is ever
  deleted; an archived skill stays on disk and can be revived
  by using it again.
- Pins are immune: a pinned skill never goes stale.
- A snapshot backup of the whole skills root is taken BEFORE
  any state changes, so a bad run can be rolled back.
- LLM consolidation is opt-in: a ``consolidate`` callable is
  only invoked for stale skills when the caller passes one
  (the follow-up wires it to ``puspa.router.complete``); without
  it the curator only moves states, never rewrites skill text.

Telemetry follows the hermes-agent ``tools/skill_usage.py``
shape (``.usage.json``: ``use_count``, ``view_count``,
``patch_count``, ``last_activity_at``, ``state``), stored as a
sidecar JSON file next to each skill's ``SKILL.md``. Sidecar
JSON was chosen over a Postgres table on purpose: skill usage
is per-workspace file state that must survive without a
database, it travels with the skill directory when skills are
copied, and the curator must be runnable from a plain checkout.
A Postgres mirror is a follow-up, not a requirement for the
state machine to be real.

Pattern rewritten from hermes-agent ``agent/curator.py`` +
``agent/curator_backup.py`` (MIT, adapted, not copied).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "ARCHIVE_AFTER_DAYS",
    "STALE_AFTER_DAYS",
    "STATE_ACTIVE",
    "STATE_ARCHIVED",
    "STATE_STALE",
    "USAGE_FILENAME",
    "CuratorReport",
    "SkillUsage",
    "backup_skills",
    "discover_skills",
    "load_usage",
    "next_state",
    "record_patch",
    "record_use",
    "record_view",
    "run_curator",
    "save_usage",
]

#: Skill states (deterministic machine: active -> stale -> archived).
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"

#: Days of inactivity before active -> stale (matches the
#: procedural decay window in ``puspa.memory.procedural``).
STALE_AFTER_DAYS = 90

#: Days of inactivity before stale -> archived.
ARCHIVE_AFTER_DAYS = 180

#: Telemetry sidecar file inside each skill directory.
USAGE_FILENAME = ".usage.json"


def _parse_time(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _format_time(moment):
    if moment is None:
        return None
    return moment.astimezone(UTC).isoformat()


@dataclass
class SkillUsage:
    """Telemetry + curation state for one skill.

    ``last_activity_at`` is the last recorded use, view, or
    patch. ``pinned`` skills never go stale. ``state`` is one of
    ``active`` / ``stale`` / ``archived``.
    """

    use_count: int = 0
    view_count: int = 0
    patch_count: int = 0
    last_activity_at: datetime | None = None
    state: str = STATE_ACTIVE
    pinned: bool = False

    def to_dict(self):
        return {
            "use_count": self.use_count,
            "view_count": self.view_count,
            "patch_count": self.patch_count,
            "last_activity_at": _format_time(self.last_activity_at),
            "state": self.state,
            "pinned": self.pinned,
        }

    @classmethod
    def from_dict(cls, data):
        """Tolerant parse: missing keys fall back to fresh defaults."""
        data = data or {}
        state = data.get("state") or STATE_ACTIVE
        if state not in (STATE_ACTIVE, STATE_STALE, STATE_ARCHIVED):
            state = STATE_ACTIVE
        return cls(
            use_count=int(data.get("use_count") or 0),
            view_count=int(data.get("view_count") or 0),
            patch_count=int(data.get("patch_count") or 0),
            last_activity_at=_parse_time(data.get("last_activity_at")),
            state=state,
            pinned=bool(data.get("pinned") or False),
        )

    def touch(self, now=None):
        """Mark one activity (use, view, or patch) at ``now``."""
        self.last_activity_at = now or datetime.now(UTC)


def _usage_path(skill_dir):
    return Path(skill_dir) / USAGE_FILENAME


def load_usage(skill_dir):
    """Read a skill's ``.usage.json``; fresh defaults when absent.

    A corrupt sidecar is treated as fresh (and the error is
    surfaced by the caller through ``run_curator``'s errors,
    not by crashing the run); the file itself is only rewritten
    by an explicit ``save_usage`` / ``record_*`` call.
    """
    path = _usage_path(skill_dir)
    if not path.exists():
        return SkillUsage()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return SkillUsage()
    if not isinstance(data, dict):
        return SkillUsage()
    return SkillUsage.from_dict(data)


def save_usage(skill_dir, usage):
    """Write a skill's ``.usage.json`` sidecar (atomic-ish)."""
    path = _usage_path(skill_dir)
    tmp_path = path.with_suffix(".usage.json.tmp")
    tmp_path.write_text(
        json.dumps(usage.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return path


def record_use(skill_dir, *, now=None):
    """Record one skill use: count +1, activity clock updated."""
    usage = load_usage(skill_dir)
    usage.use_count += 1
    usage.touch(now)
    if usage.state == STATE_ARCHIVED:
        # Using an archived skill revives it: archive is a state,
        # never a deletion, and real use is the honest un-archive.
        usage.state = STATE_ACTIVE
    save_usage(skill_dir, usage)
    return usage


def record_view(skill_dir, *, now=None):
    """Record one skill view: count +1, activity clock updated."""
    usage = load_usage(skill_dir)
    usage.view_count += 1
    usage.touch(now)
    save_usage(skill_dir, usage)
    return usage


def record_patch(skill_dir, *, now=None):
    """Record one skill patch: count +1, activity clock updated."""
    usage = load_usage(skill_dir)
    usage.patch_count += 1
    usage.touch(now)
    save_usage(skill_dir, usage)
    return usage


def next_state(
    usage, *, now=None, stale_after_days=STALE_AFTER_DAYS, archive_after_days=ARCHIVE_AFTER_DAYS
):
    """Pure next state for one skill's usage (deterministic).

    - Pinned skills stay active, always.
    - Archived is terminal for the curator (only real use, via
      ``record_use``, revives a skill).
    - A skill with no recorded activity yet stays active: with
      no baseline, inactivity cannot be judged.
    - Otherwise one step per run: active -> stale at
      ``stale_after_days`` of inactivity, stale -> archived at
      ``archive_after_days``. A skill inactive for 200 days
      therefore needs two runs to reach archived, which keeps
      every transition visible in a run report.
    """
    if usage.pinned:
        return STATE_ACTIVE
    if usage.state == STATE_ARCHIVED:
        return STATE_ARCHIVED
    last = usage.last_activity_at
    if last is None:
        return STATE_ACTIVE
    moment = now or datetime.now(UTC)
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    inactive_days = (moment - last).days
    if usage.state == STATE_STALE:
        if inactive_days >= archive_after_days:
            return STATE_ARCHIVED
        return STATE_STALE
    if inactive_days >= stale_after_days:
        return STATE_STALE
    return STATE_ACTIVE


def discover_skills(skills_root):
    """Skill directories under ``skills_root`` (sorted by name).

    A skill is a directory containing a ``SKILL.md``. Files and
    directories without one are ignored, as are hidden
    directories.
    """
    root = Path(skills_root)
    if not root.is_dir():
        raise ValueError(f"skills root is not a directory: {skills_root}")
    found = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if child.is_dir() and not child.name.startswith(".") and (child / "SKILL.md").exists():
            found.append(child)
    return found


def backup_skills(skills_root, backup_dir, *, now=None):
    """Snapshot the whole skills root before a curator run.

    Copies to ``<backup_dir>/<YYYYMMDDTHHMMSS>/`` and returns
    the snapshot path. The backup runs before any state change,
    so a bad run can be rolled back by copying it back.
    """
    root = Path(skills_root)
    moment = now or datetime.now(UTC)
    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%S")
    target = Path(backup_dir) / stamp
    if target.exists():
        raise ValueError(f"backup target already exists: {target}")
    shutil.copytree(root, target)
    return target


@dataclass
class CuratorReport:
    """Outcome of one curator run."""

    backup_path: str | None = None
    scanned: int = 0
    transitions: list = field(default_factory=list)
    archived: list = field(default_factory=list)
    pinned: list = field(default_factory=list)
    consolidated: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    dry_run: bool = False


def run_curator(
    skills_root,
    *,
    now=None,
    dry_run=False,
    backup_dir=None,
    consolidate=None,
    stale_after_days=STALE_AFTER_DAYS,
    archive_after_days=ARCHIVE_AFTER_DAYS,
):
    """Run one inactivity-curation pass over ``skills_root``.

    Order: (1) snapshot backup (skipped on a dry run, which
    writes nothing); (2) for each skill, compute the next state
    and, when the skill is stale and a ``consolidate`` callable
    was passed, call it as ``consolidate(skill_dir, usage)`` —
    its return value is recorded in the report, never applied
    automatically; (3) persist state changes (skipped on a dry
    run). Nothing is ever deleted. Per-skill failures are
    collected in ``report.errors`` and do not stop the run.
    """
    moment = now or datetime.now(UTC)
    report = CuratorReport(dry_run=dry_run)
    if not dry_run and backup_dir is not None:
        try:
            report.backup_path = str(backup_skills(skills_root, backup_dir, now=moment))
        except (OSError, ValueError) as exc:
            report.errors.append({"skill": None, "error": str(exc)})
            return report
    try:
        skills = discover_skills(skills_root)
    except ValueError as exc:
        report.errors.append({"skill": None, "error": str(exc)})
        return report
    report.scanned = len(skills)
    for skill_dir in skills:
        name = skill_dir.name
        try:
            usage = load_usage(skill_dir)
            if usage.pinned:
                report.pinned.append(name)
            target = next_state(
                usage,
                now=moment,
                stale_after_days=stale_after_days,
                archive_after_days=archive_after_days,
            )
            if target == STATE_STALE and consolidate is not None:
                try:
                    outcome = consolidate(skill_dir, usage)
                    report.consolidated.append({"skill": name, "outcome": str(outcome)})
                except Exception as exc:
                    report.errors.append({"skill": name, "error": f"consolidate: {exc}"})
            if target != usage.state:
                report.transitions.append({"skill": name, "from": usage.state, "to": target})
                if target == STATE_ARCHIVED:
                    report.archived.append(name)
                if not dry_run:
                    usage.state = target
                    save_usage(skill_dir, usage)
        except Exception as exc:
            report.errors.append({"skill": name, "error": str(exc)})
    return report
