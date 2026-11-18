"""Audit trail with initiator (G6, maturity phase).

Pattern: OpenBot ``app/src/lib/audit/`` + ``api/openapi/audit-events.yaml``
+ ``shared/audit-words.ts`` (rewritten as Puspa code, never copied
verbatim; see ``NOTICE.md`` and ``REUSE.md``).

Three additions to the ledger (Buku Kerja), none of which adds a
32nd event type — the closed set of 31 types is untouched:

1. **Initiator attribute.** Every event content may carry
   ``initiator: {"kind", "id"}``: who or what started the run —
   ``person``, ``deployment``, ``routine``, ``responsibility`` (a
   goal), ``memory``, or ``handoff``. ``Ledger.append_event`` takes
   an optional ``initiator`` argument and stores it in the content.
   The "Nobody watching" filter is routine + handoff +
   responsibility + memory: runs no person is watching.
2. **Handover as control events.** ``help_requested`` /
   ``control_taken`` / ``control_released`` are audited through
   existing handoff types (``handoff_proposed`` /
   ``handoff_handed_over`` / ``handoff_accepted``) with a
   ``control`` attribute naming the handover step. While a human
   controls the browser, bot actions are REJECTED (not queued):
   :class:`ControlState` holds the control state and refuses them.
3. **Export guards + self-audit.** A CSV export holds at most
   5,000 rows (a truncation marker header when cut), cells starting
   with ``= + - @`` get a ``'`` prefix (formula-injection guard),
   secrets are redacted, and saved files contribute path + size,
   never content. Every export is itself recorded — who, how many
   rows, which filters.

Choice, documented (the task allowed two): the export record does
NOT go into the core ledger as a new event type, and it is not
shoehorned into an unrelated existing type either (an export is
not evidence). It goes into a separate append-only export log
(:class:`AuditExportLog`, one JSONL file, hash-chained like the
ledger) that lives outside the core ledger. The core ledger stays
the single source of truth for what Puspa did; the export log is
the record of who looked at it.

Retention comes from the ``AUDIT_RETENTION_DAYS`` environment
variable; unset means forever. Retention prunes the export log
(and exported files), never the core ledger.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "AUDIT_RETENTION_ENV",
    "INITIATOR_KINDS",
    "MAX_EXPORT_ROWS",
    "NOBODY_WATCHING_KINDS",
    "AuditExportLog",
    "ControlRejected",
    "ControlState",
    "ExportResult",
    "Initiator",
    "attach_initiator",
    "audit_retention_days",
    "export_events_csv",
    "file_audit_entry",
    "filter_nobody_watching",
    "initiator_of",
    "is_nobody_watching",
    "sanitize_cell",
]

#: Environment variable for export retention (days). Unset = forever.
AUDIT_RETENTION_ENV = "AUDIT_RETENTION_DAYS"

#: Maximum rows in one CSV export.
MAX_EXPORT_ROWS = 5000

#: Header line marking a truncated export (first line of the CSV).
TRUNCATED_MARKER = "# Puspa-Export-Truncated: true"

#: Initiator kinds (OpenBot's list, same names).
INITIATOR_KINDS = frozenset(
    {"person", "deployment", "routine", "responsibility", "memory", "handoff"}
)

#: "Nobody watching": runs no person is supervising.
NOBODY_WATCHING_KINDS = frozenset({"routine", "handoff", "responsibility", "memory"})

#: Handover step -> existing ledger event type (no new type).
CONTROL_EVENT_TYPES = {
    "help_requested": "handoff_proposed",
    "control_taken": "handoff_handed_over",
    "control_released": "handoff_accepted",
}

#: Cells starting with one of these get a "'" prefix (formula injection).
_FORMULA_LEADERS = ("=", "+", "-", "@")

#: Secret assignments redacted in exported content (same rule as
#: ``puspa.memory.trust.redact_secrets``; kept local so this module
#: never imports the memory package).
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(password|kata[_\s-]?sandi|passwd)\s*[:=]\s*\S+"),
)

REDACTED = "«redacted»"


def _redact_text(text: str) -> str:
    """Replace secret assignments in ``text`` with ``«redacted»``,
    keeping the key so a reviewer can see a secret was there."""
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(
            lambda m: m.group(0).split("=", 1)[0].split(":", 1)[0] + f"={REDACTED}",
            out,
        )
    return out


def _redact_value(value):
    """Recursively redact secret assignments in a JSON-like value,
    so the exported content stays valid JSON after redaction."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Initiator attribute
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Initiator:
    """Who or what started a run."""

    kind: str  # one of INITIATOR_KINDS
    id: str  # person / deployment / routine / goal / memory / handoff id

    def __post_init__(self) -> None:
        if self.kind not in INITIATOR_KINDS:
            raise ValueError(
                f"unknown initiator kind: {self.kind!r} (must be one of {sorted(INITIATOR_KINDS)})"
            )
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("initiator id must be a non-empty string")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id}

    @classmethod
    def from_dict(cls, data: dict) -> Initiator:
        return cls(kind=data["kind"], id=data["id"])


def attach_initiator(content: dict, initiator: Initiator | dict) -> dict:
    """Return a copy of ``content`` with the initiator attribute
    attached. The original dict is not mutated."""
    if isinstance(initiator, dict):
        initiator = Initiator.from_dict(initiator)
    out = dict(content)
    out["initiator"] = initiator.to_dict()
    return out


def initiator_of(event: dict) -> Initiator | None:
    """The initiator recorded on a ledger event, or None."""
    raw = (event.get("content") or {}).get("initiator")
    if not isinstance(raw, dict):
        return None
    try:
        return Initiator.from_dict(raw)
    except (KeyError, ValueError):
        return None


def is_nobody_watching(event: dict) -> bool:
    """True when the run that produced this event had no person
    watching it (routine / handoff / responsibility / memory)."""
    initiator = initiator_of(event)
    return initiator is not None and initiator.kind in NOBODY_WATCHING_KINDS


def filter_nobody_watching(events: list[dict]) -> list[dict]:
    """Only the events whose runs nobody was watching."""
    return [e for e in events if is_nobody_watching(e)]


# ---------------------------------------------------------------------------
# Handover / control state
# ---------------------------------------------------------------------------


class ControlRejected(Exception):
    """A bot action was refused because a human controls the browser."""


@dataclass
class ControlState:
    """Who controls the browser right now: the bot, or a human.

    Handover steps are audited through existing ledger types
    (``CONTROL_EVENT_TYPES``) with a ``control`` attribute, via the
    injected ``log`` callable (``log(event_type, content)``, the
    shape ``Gate`` and ``PuspaTool`` use). While a human is in
    control, bot actions are REJECTED, not queued.
    """

    log: Callable[..., Any] | None = None
    _controller: str | None = field(default=None, init=False)  # None = bot controls

    @property
    def controller(self) -> str | None:
        """The human in control, or None when the bot controls."""
        return self._controller

    @property
    def in_human_control(self) -> bool:
        return self._controller is not None

    def _log(self, event_type: str, content: dict) -> None:
        if self.log is None:
            return
        try:
            self.log(event_type, content)
        except TypeError:
            self.log({"type": event_type, "content": content})

    def _record(self, step: str, actor: str, initiator: Initiator | dict | None) -> dict:
        content: dict = {"control": step, "actor": actor}
        if initiator is not None:
            content = attach_initiator(content, initiator)
        self._log(CONTROL_EVENT_TYPES[step], content)
        return content

    def request_help(
        self, actor: str, reason: str, *, initiator: Initiator | dict | None = None
    ) -> dict:
        """The bot asks a human for help (audited as a control
        event). The bot stays in control until a human takes it."""
        content = self._record("help_requested", actor, initiator)
        content["reason"] = reason
        return content

    def take_control(self, actor: str, *, initiator: Initiator | dict | None = None) -> dict:
        """A human takes control of the browser."""
        self._controller = actor
        return self._record("control_taken", actor, initiator)

    def release_control(self, actor: str, *, initiator: Initiator | dict | None = None) -> dict:
        """The human hands control back to the bot."""
        self._controller = None
        return self._record("control_released", actor, initiator)

    def bot_action_allowed(self) -> bool:
        """False while a human controls the browser."""
        return self._controller is None

    def reject_bot_action(self, action: dict) -> None:
        """Refuse a bot action while a human is in control. Raises
        :class:`ControlRejected`; the action is dropped, never
        queued. A no-op (returns None) when the bot is in control.
        """
        if self.bot_action_allowed():
            return
        raise ControlRejected(
            f"bot action refused: {self._controller} controls the browser "
            f"(action: {action.get('tool', action.get('kind', '?'))})"
        )


# ---------------------------------------------------------------------------
# CSV export guards
# ---------------------------------------------------------------------------


def sanitize_cell(value: object) -> str:
    """One CSV cell, safe to open in a spreadsheet: a leading
    ``= + - @`` gets a ``'`` prefix (formula-injection guard)."""
    text = "" if value is None else str(value)
    if text[:1] in _FORMULA_LEADERS:
        return "'" + text
    return text


def file_audit_entry(path: str | Path) -> dict:
    """What a saved file contributes to an audit record: its path
    and size, never its content."""
    path = Path(path)
    return {"path": str(path), "size_bytes": path.stat().st_size}


@dataclass(frozen=True)
class ExportResult:
    """One finished export: the CSV text plus its audit facts."""

    csv_text: str
    rows: int  # rows actually written
    total_rows: int  # rows the filter matched, before the cap
    truncated: bool
    exporter: str
    filters: dict


def export_events_csv(
    events: list[dict],
    *,
    exporter: str,
    filters: dict | None = None,
    log: AuditExportLog | None = None,
) -> ExportResult:
    """Export ledger events as CSV, with the guards, and record the
    export itself.

    Columns: id, created_at, task_id, agent_id, type,
    initiator_kind, initiator_id, content (JSON, secrets redacted).
    At most ``MAX_EXPORT_ROWS`` rows; a cut export starts with the
    truncation marker header. ``log`` (an :class:`AuditExportLog`)
    is required: every export is recorded (who, how many rows,
    which filters). Secrets in content are redacted; a saved file
    in an event contributes path + size only (see
    :func:`file_audit_entry`).
    """
    if log is None:
        raise ValueError("exporting audit events needs an AuditExportLog to record the export")
    if not exporter:
        raise ValueError("exporter must be a non-empty string")
    filters = dict(filters or {})
    total = len(events)
    truncated = total > MAX_EXPORT_ROWS
    rows = events[:MAX_EXPORT_ROWS]
    buffer = io.StringIO()
    if truncated:
        buffer.write(TRUNCATED_MARKER + "\n")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "created_at",
            "task_id",
            "agent_id",
            "type",
            "initiator_kind",
            "initiator_id",
            "content",
        ]
    )
    for event in rows:
        content = event.get("content") or {}
        initiator = initiator_of(event)
        content_json = json.dumps(
            _redact_value(content), sort_keys=True, ensure_ascii=False, default=str
        )
        writer.writerow(
            [
                sanitize_cell(event.get("id", "")),
                sanitize_cell(event.get("created_at", "")),
                sanitize_cell(event.get("task_id", "")),
                sanitize_cell(event.get("agent_id", "")),
                sanitize_cell(event.get("type", "")),
                sanitize_cell(initiator.kind if initiator else ""),
                sanitize_cell(initiator.id if initiator else ""),
                sanitize_cell(content_json),
            ]
        )
    result = ExportResult(
        csv_text=buffer.getvalue(),
        rows=len(rows),
        total_rows=total,
        truncated=truncated,
        exporter=exporter,
        filters=filters,
    )
    log.record(exporter=exporter, rows=len(rows), truncated=truncated, filters=filters)
    return result


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def audit_retention_days(env: dict | None = None) -> int | None:
    """Retention for audit exports, in days, from
    ``AUDIT_RETENTION_DAYS``. Unset means forever (None). An invalid
    value raises ValueError instead of silently picking a number."""
    raw = (env if env is not None else os.environ).get(AUDIT_RETENTION_ENV)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        days = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(
            f"{AUDIT_RETENTION_ENV} must be an integer number of days, got {raw!r}"
        ) from exc
    if days < 0:
        raise ValueError(f"{AUDIT_RETENTION_ENV} must not be negative, got {days}")
    return days


# ---------------------------------------------------------------------------
# Export log (separate from the core ledger, by design)
# ---------------------------------------------------------------------------


def _entry_hash(
    *,
    exporter: str,
    rows: int,
    truncated: bool,
    filters: dict,
    exported_at: str,
    previous_hash: str,
) -> str:
    payload = {
        "exporter": exporter,
        "rows": rows,
        "truncated": truncated,
        "filters": filters,
        "exported_at": exported_at,
        "previous_hash": previous_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditExportLog:
    """Append-only log of audit exports, outside the core ledger.

    One JSONL file (``audit-exports.jsonl``) in ``directory``.
    Each record chains to the previous one by hash (``GENESIS`` for
    the first), so a tampered record is detectable with
    :meth:`verify_chain`. Records hold who exported, how many rows,
    whether the export was truncated, and which filters applied —
    never the exported rows themselves.
    """

    FILENAME = "audit-exports.jsonl"
    GENESIS = "GENESIS"

    def __init__(self, directory: str | Path, *, retention_days: int | None = None) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._retention_days = retention_days
        self._path = self.directory / self.FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def _read_raw(self) -> list[dict]:
        if not self._path.exists():
            return []
        records = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records

    def record(
        self,
        *,
        exporter: str,
        rows: int,
        truncated: bool,
        filters: dict | None = None,
        exported_at: datetime | None = None,
    ) -> dict:
        """Append one export record. Returns the stored record."""
        if not exporter:
            raise ValueError("exporter must be a non-empty string")
        if rows < 0:
            raise ValueError("rows must not be negative")
        records = self._read_raw()
        previous_hash = records[-1]["hash"] if records else self.GENESIS
        when = exported_at or datetime.now(UTC)
        exported_iso = when.astimezone(UTC).isoformat()
        entry_hash = _entry_hash(
            exporter=exporter,
            rows=rows,
            truncated=truncated,
            filters=dict(filters or {}),
            exported_at=exported_iso,
            previous_hash=previous_hash,
        )
        record = {
            "id": f"export-{len(records) + 1}-{entry_hash[:12]}",
            "exported_at": exported_iso,
            "exporter": exporter,
            "rows": rows,
            "truncated": truncated,
            "filters": dict(filters or {}),
            "previous_hash": previous_hash,
            "hash": entry_hash,
        }
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        return record

    def read_all(self) -> list[dict]:
        return self._read_raw()

    def verify_chain(self) -> tuple[bool, list[str]]:
        """True when every record's hash and previous-hash link
        check out; otherwise (False, [record ids that break]).

        Pruning truncates the head of the chain, so verification
        anchors at the oldest KEPT record's previous hash: the
        chain from there on is fully checked, but what came before
        the oldest kept record can no longer be verified. That is
        inherent to retention pruning, not a silent gap.
        """
        records = self._read_raw()
        if not records:
            return (True, [])
        broken: list[str] = []
        expected_prev = records[0]["previous_hash"]
        for record in records:
            recomputed = _entry_hash(
                exporter=record["exporter"],
                rows=record["rows"],
                truncated=record["truncated"],
                filters=record["filters"],
                exported_at=record["exported_at"],
                previous_hash=record["previous_hash"],
            )
            if record["previous_hash"] != expected_prev or recomputed != record["hash"]:
                broken.append(record["id"])
            expected_prev = record["hash"]
        return (not broken, broken)

    def prune(self, *, now: datetime | None = None) -> list[str]:
        """Drop export records older than the retention window.
        Returns the pruned record ids. With no retention configured
        (env unset and no argument), nothing is pruned — forever
        means forever. Only the export log is pruned, never the
        core ledger."""
        days = self._retention_days
        if days is None:
            days = audit_retention_days()
        if days is None:
            return []
        now = now or datetime.now(UTC)
        records = self._read_raw()
        kept = []
        pruned = []
        for record in records:
            age = now - datetime.fromisoformat(record["exported_at"])
            if age.days >= days:
                pruned.append(record["id"])
            else:
                kept.append(record)
        if pruned:
            self._path.write_text(
                "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in kept),
                encoding="utf-8",
            )
        return pruned
