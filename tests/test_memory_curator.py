"""Tests for the skill inactivity curator (G24).

Run with ``--noconftest``: everything here is in-memory / tmp
directories. No database, no live LLM.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from puspa.memory.curator import (
    ARCHIVE_AFTER_DAYS,
    STALE_AFTER_DAYS,
    STATE_ACTIVE,
    STATE_ARCHIVED,
    STATE_STALE,
    SkillUsage,
    backup_skills,
    discover_skills,
    load_usage,
    next_state,
    record_patch,
    record_use,
    record_view,
    run_curator,
    save_usage,
)

NOW = datetime(2026, 11, 18, 15, 30, tzinfo=timezone.utc)


def _skill(root, name, *, body="# Skill\n", manifest=None):
    skill_dir = Path(root) / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    if manifest is not None:
        (skill_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    return skill_dir


def _aged_usage(*, days, state=STATE_ACTIVE, pinned=False):
    return SkillUsage(
        use_count=3,
        view_count=5,
        patch_count=1,
        last_activity_at=NOW - timedelta(days=days),
        state=state,
        pinned=pinned,
    )


# --- telemetry sidecar -------------------------------------------------


def test_usage_round_trip_and_defaults(tmp_path):
    skill_dir = _skill(tmp_path, "alpha")
    fresh = load_usage(skill_dir)
    assert fresh == SkillUsage()
    usage = _aged_usage(days=5)
    save_usage(skill_dir, usage)
    assert load_usage(skill_dir) == usage
    sidecar = json.loads((skill_dir / ".usage.json").read_text())
    assert sidecar["use_count"] == 3
    assert sidecar["view_count"] == 5
    assert sidecar["patch_count"] == 1
    assert sidecar["state"] == STATE_ACTIVE
    assert sidecar["pinned"] is False


def test_record_use_view_patch_count_and_clock(tmp_path):
    skill_dir = _skill(tmp_path, "alpha")
    record_use(skill_dir, now=NOW)
    record_view(skill_dir, now=NOW)
    record_view(skill_dir, now=NOW)
    record_patch(skill_dir, now=NOW)
    usage = load_usage(skill_dir)
    assert usage.use_count == 1
    assert usage.view_count == 2
    assert usage.patch_count == 1
    assert usage.last_activity_at == NOW


def test_record_use_revives_archived_skill(tmp_path):
    skill_dir = _skill(tmp_path, "alpha")
    save_usage(skill_dir, _aged_usage(days=300, state=STATE_ARCHIVED))
    usage = record_use(skill_dir, now=NOW)
    assert usage.state == STATE_ACTIVE


# --- deterministic transitions -----------------------------------------


def test_next_state_active_to_stale_at_threshold():
    assert next_state(_aged_usage(days=STALE_AFTER_DAYS - 1), now=NOW) == STATE_ACTIVE
    assert next_state(_aged_usage(days=STALE_AFTER_DAYS), now=NOW) == STATE_STALE


def test_next_state_stale_to_archived_one_step_per_run():
    # 200 days inactive but still active: one step only (stale, not archived).
    assert next_state(_aged_usage(days=200), now=NOW) == STATE_STALE
    assert (
        next_state(_aged_usage(days=200, state=STATE_STALE), now=NOW)
        == STATE_ARCHIVED
    )
    assert (
        next_state(
            _aged_usage(days=ARCHIVE_AFTER_DAYS - 1, state=STATE_STALE),
            now=NOW,
        )
        == STATE_STALE
    )


def test_next_state_pinned_is_immune_and_archived_is_terminal():
    assert (
        next_state(_aged_usage(days=500, pinned=True), now=NOW) == STATE_ACTIVE
    )
    assert (
        next_state(_aged_usage(days=1, state=STATE_ARCHIVED), now=NOW)
        == STATE_ARCHIVED
    )


def test_next_state_never_used_stays_active():
    assert next_state(SkillUsage(), now=NOW) == STATE_ACTIVE


# --- discovery + backup ------------------------------------------------


def test_discover_skills_only_counts_skill_md_dirs(tmp_path):
    _skill(tmp_path, "beta")
    _skill(tmp_path, "alpha")
    plain = tmp_path / "not-a-skill"
    plain.mkdir()
    (plain / "notes.txt").write_text("hi", encoding="utf-8")
    assert [p.name for p in discover_skills(tmp_path)] == ["alpha", "beta"]


def test_backup_skills_copies_everything_before_run(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    _skill(root, "alpha")
    backup = backup_skills(root, tmp_path / "backups", now=NOW)
    assert (backup / "alpha" / "SKILL.md").exists()
    assert backup.name == "20261118T153000"


# --- full run ----------------------------------------------------------


def test_run_curator_transitions_pins_and_never_deletes(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    stale_dir = _skill(root, "stale-one")
    save_usage(stale_dir, _aged_usage(days=100))
    archived_dir = _skill(root, "old-one")
    save_usage(archived_dir, _aged_usage(days=200, state=STATE_STALE))
    pinned_dir = _skill(root, "pinned-one")
    save_usage(pinned_dir, _aged_usage(days=500, pinned=True))
    fresh_dir = _skill(root, "fresh-one")
    save_usage(fresh_dir, _aged_usage(days=2))

    report = run_curator(root, now=NOW, backup_dir=tmp_path / "backups")

    assert report.scanned == 4
    assert report.backup_path is not None
    assert Path(report.backup_path).is_dir()
    assert {"skill": "stale-one", "from": STATE_ACTIVE, "to": STATE_STALE} in report.transitions
    assert {"skill": "old-one", "from": STATE_STALE, "to": STATE_ARCHIVED} in report.transitions
    assert report.archived == ["old-one"]
    assert report.pinned == ["pinned-one"]
    assert report.errors == []
    # Archive is the maximum: nothing deleted, files intact.
    for skill_dir in (stale_dir, archived_dir, pinned_dir, fresh_dir):
        assert (skill_dir / "SKILL.md").exists()
        assert (skill_dir / ".usage.json").exists()
    assert load_usage(stale_dir).state == STATE_STALE
    assert load_usage(archived_dir).state == STATE_ARCHIVED
    assert load_usage(pinned_dir).state == STATE_ACTIVE
    assert load_usage(fresh_dir).state == STATE_ACTIVE
    # The backup was taken before the run: it holds the old states.
    backup_old = json.loads(
        (Path(report.backup_path) / "old-one" / ".usage.json").read_text()
    )
    assert backup_old["state"] == STATE_STALE


def test_run_curator_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    skill_dir = _skill(root, "stale-one")
    save_usage(skill_dir, _aged_usage(days=100))
    before = (skill_dir / ".usage.json").read_text()

    report = run_curator(root, now=NOW, dry_run=True, backup_dir=tmp_path / "backups")

    assert report.dry_run is True
    assert report.backup_path is None
    assert not (tmp_path / "backups").exists()
    assert len(report.transitions) == 1
    assert (skill_dir / ".usage.json").read_text() == before


def test_run_curator_consolidate_is_opt_in_and_recorded(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    skill_dir = _skill(root, "stale-one")
    save_usage(skill_dir, _aged_usage(days=100))
    calls = []

    def consolidate(path, usage):
        calls.append((Path(path).name, usage.state))
        return "merged 2 overlapping sections"

    report = run_curator(root, now=NOW, consolidate=consolidate)
    assert calls == [("stale-one", STATE_ACTIVE)]
    assert report.consolidated == [
        {"skill": "stale-one", "outcome": "merged 2 overlapping sections"}
    ]
    # Without a consolidate callable, no consolidation is attempted.
    fresh_root = tmp_path / "skills2"
    fresh_root.mkdir()
    _skill(fresh_root, "stale-two")
    save_usage(fresh_root / "stale-two", _aged_usage(days=100))
    report2 = run_curator(fresh_root, now=NOW)
    assert report2.consolidated == []


def test_run_curator_survives_a_bad_skill(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    bad = _skill(root, "bad-one")
    (bad / ".usage.json").write_text("{not json", encoding="utf-8")
    good = _skill(root, "good-one")
    save_usage(good, _aged_usage(days=100))

    report = run_curator(root, now=NOW)
    assert report.scanned == 2
    assert load_usage(good).state == STATE_STALE
    # A corrupt sidecar falls back to fresh defaults, not a crash.
    assert report.errors == []
