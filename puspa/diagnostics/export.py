"""Redacted diagnostics export for bug reports.

One bundle a user can paste into a bug report without
leaking secrets: recent log lines, the message trace (the
finished spans for the user's recent tasks), and the recent
ledger events for the user's agents — every string redacted
*before* it is written, with the same redaction point the
rest of Puspa uses (:mod:`puspa.diagnostics.redact`).

Pattern: AstrBot added message tracing plus a redacted
diagnostics export in v4.29.3. The idea is taken from there;
every line below is Puspa's own code, never copied, and
AstrBot is not a dependency.

Shape of the bundle (``puspa-diagnostics``, version 1)::

    {
      "format": "puspa-diagnostics",
      "version": 1,
      "puspa_version": "0.1.0",
      "user": {"id": "<user id>"},        # id only, never email
      "exported_at": "<ISO-8601>",
      "task_id": "<task id or null>",      # when the export is scoped to one task
      "sections": {
        "logs": ["[trace_id=...] ...", ...],          # newest last, capped
        "message_trace": [{span...}, ...],            # oldest first, capped
        "ledger_events": [{event...}, ...],           # oldest first, capped
      },
      "counts": {"logs": n, "message_trace": n, "ledger_events": n},
      "truncated": {"logs": false, "message_trace": false, "ledger_events": false},
      "redactions_applied": 3,          # how many «redacted» markers are in the file
      "excluded": [...],                 # what is deliberately never in a bundle
    }

Deliberately NOT exported (also written into the file itself
under ``excluded``, so the gap is visible, not silent):

- secret values (API keys, tokens, passwords, Authorization
  headers, DB URLs, private-key blocks, vault values) — every
  string is redacted before it is written, and the structure
  (keys, event types, span names) stays intact;
- email addresses (personal data in a bundle meant to be
  shared in public);
- the user's email address (the bundle carries the user id
  only);
- vault ciphertext and password hashes (never exported);
- any other user's data (every query filters on this user's
  agents; spans are filtered to this user's task ids);
- message bodies beyond the trace: spans carry names,
  kinds, durations, and attributes — never raw message text
  from the chat store.

Two layers, so the redaction is unit-testable without a
database:

- :func:`build_diagnostics_bundle` is pure: it takes log
  lines, spans, and ledger events and returns the redacted
  document. Every test that proves a secret does not leak
  goes through this function.
- :func:`collect_diagnostics` gathers the three sections
  from the live system (log file, span store, Postgres) and
  calls the builder; :func:`export_diagnostics` writes the
  result atomically and optionally records the export in an
  :class:`AuditExportLog <puspa.ledger.audit.AuditExportLog>`
  (the K12 rule: every export is recorded).
"""

from __future__ import annotations

import json
import os
import platform
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.diagnostics.redact import REDACTED, redact_diagnostics_value

__all__ = [
    "DIAGNOSTICS_FORMAT",
    "DIAGNOSTICS_VERSION",
    "EXCLUDED",
    "MAX_LEDGER_EVENTS",
    "MAX_LOG_LINES",
    "MAX_SPANS",
    "DiagnosticsExport",
    "build_diagnostics_bundle",
    "collect_diagnostics",
    "export_diagnostics",
]

#: Format name of the bundle file.
DIAGNOSTICS_FORMAT = "puspa-diagnostics"

#: Format version of the bundle. Bump when the sections change.
DIAGNOSTICS_VERSION = 1

#: Default DSN (peer auth as the OS user, no password).
DEFAULT_DSN = None

#: Caps: a bundle stays small enough to paste into a bug
#: report. Each section keeps the most recent rows and says
#: so under ``truncated`` when it cut anything off.
MAX_LOG_LINES = 500
MAX_SPANS = 200
MAX_LEDGER_EVENTS = 200

#: What is deliberately left out of every bundle (also written
#: into the file itself, so the exclusion is visible).
EXCLUDED = (
    (
        "secret values: API keys, tokens, passwords, Authorization headers, "
        "database URLs, private-key blocks, vault values — redacted before writing"
    ),
    "email addresses: personal data, never part of a shareable bundle",
    "user email: the bundle carries the user id only",
    "vault ciphertext and password hashes: never exported",
    "other users' data: every query filters on this user's agents",
    (
        "raw message bodies: spans carry names, kinds, durations, and "
        "attributes — never chat text from the message store"
    ),
)


def _puspa_version() -> str:
    """Installed package version, falling back to the pyproject version."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("puspa")
    except (ImportError, PackageNotFoundError):
        return "0.1.0"


def _jsonable(value):
    """Recursively convert a value to plain JSON data.

    Datetimes become ISO-8601 strings and UUIDs become strings.
    Strings are left alone here: the whole document is redacted
    in one pass at the end of :func:`build_diagnostics_bundle`,
    so no string can slip through unredacted.
    """
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _normalise_span(span: dict) -> dict:
    """One finished span as bundle data (epoch -> ISO time)."""
    record = _jsonable(dict(span))
    started = record.get("started_at")
    if isinstance(started, (int, float)):
        record["started_at"] = datetime.fromtimestamp(started, tz=UTC).isoformat()
    return record


def _cap(rows: list, limit: int) -> tuple[list, bool]:
    """Keep the most recent ``limit`` rows; say when anything was cut."""
    if len(rows) <= limit:
        return rows, False
    return rows[-limit:], True


def build_diagnostics_bundle(
    *,
    user_id: str,
    log_lines: list[str] | None = None,
    spans: list[dict] | None = None,
    ledger_events: list[dict] | None = None,
    task_id: str | None = None,
) -> dict:
    """Build the redacted diagnostics document (pure, no I/O).

    Every string in the document — log lines, span attributes,
    event content — passes through redaction before it is
    returned, so a caller can never forget to redact one
    section. Structure is kept intact: keys, event types, span
    names, and counts are all still there, only secret values
    become ``«redacted»``.
    """
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    logs, logs_truncated = _cap([str(line) for line in (log_lines or [])], MAX_LOG_LINES)
    trace, trace_truncated = _cap([_normalise_span(span) for span in (spans or [])], MAX_SPANS)
    events, events_truncated = _cap(
        [_jsonable(dict(event)) for event in (ledger_events or [])], MAX_LEDGER_EVENTS
    )
    sections = {
        "logs": logs,
        "message_trace": trace,
        "ledger_events": events,
    }
    document = {
        "format": DIAGNOSTICS_FORMAT,
        "version": DIAGNOSTICS_VERSION,
        "puspa_version": _puspa_version(),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "user": {"id": user_id},
        "exported_at": datetime.now(UTC).isoformat(),
        "task_id": task_id,
        "sections": sections,
        "counts": {name: len(rows) for name, rows in sections.items()},
        "truncated": {
            "logs": logs_truncated,
            "message_trace": trace_truncated,
            "ledger_events": events_truncated,
        },
        "excluded": list(EXCLUDED),
    }
    # One choke point: redact the whole document, then count
    # the markers so the bundle says how much was scrubbed.
    redacted = redact_diagnostics_value(document)
    redacted["redactions_applied"] = json.dumps(redacted, ensure_ascii=False, default=str).count(
        REDACTED
    )
    return redacted


# ---------------------------------------------------------------------------
# Collection from the live system
# ---------------------------------------------------------------------------


def _connect(dsn: str | None):
    return _db_connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def _agent_ids(user_id: str, conn) -> list[str]:
    """Agent ids owned by this user (the ledger has no user_id)."""
    with conn.cursor() as cur:
        cur.execute("SELECT agent_id FROM agents WHERE user_id = %s", (user_id,))
        return [row["agent_id"] for row in cur.fetchall()]


def _ledger_events(
    agent_ids: list[str],
    conn,
    *,
    task_id: str | None = None,
    since: datetime | None = None,
) -> list[dict]:
    """Recent ledger events for these agents, oldest first."""
    if not agent_ids:
        return []
    conditions = ["agent_id = ANY(%s)"]
    params: list = [agent_ids]
    if task_id is not None:
        conditions.append("task_id = %s")
        params.append(task_id)
    if since is not None:
        conditions.append("created_at >= %s")
        params.append(since)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM events WHERE "
            + " AND ".join(conditions)
            + " ORDER BY created_at ASC, id ASC",
            params,
        )
        return [dict(row) for row in cur.fetchall()]


def _read_log_lines(log_file: str | Path | None, *, log_lines: list[str] | None) -> list[str]:
    """Log lines from an explicit list, a file, or neither."""
    if log_lines is not None:
        return [str(line) for line in log_lines]
    if log_file is None:
        return []
    path = Path(log_file)
    if not path.exists():
        raise ValueError(f"log file not found: {log_file}")
    return path.read_text(encoding="utf-8").splitlines()


def _parse_since(since: str | None) -> datetime | None:
    """An ISO date or datetime for ``--since``; None means no bound."""
    if since is None or str(since).strip() == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(since).strip())
    except ValueError as exc:
        raise ValueError(f"since must be an ISO date or datetime, got {since!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def collect_diagnostics(
    user_id: str,
    *,
    dsn: str | None = None,
    task_id: str | None = None,
    log_file: str | Path | None = None,
    log_lines: list[str] | None = None,
    since: str | None = None,
) -> dict:
    """Gather the three sections and build the redacted bundle.

    Raises ``ValueError`` when the user does not exist: exporting
    a stranger's id by typo must fail loudly instead of producing
    an empty bundle that looks like "no data".
    """
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    from puspa.observability.tracing import finished_spans, spans_for_trace

    lines = _read_log_lines(log_file, log_lines=log_lines)
    bound = _parse_since(since)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
            if cur.fetchone() is None:
                raise ValueError(f"unknown user: {user_id!r}")
        agent_ids = _agent_ids(user_id, conn)
        events = _ledger_events(agent_ids, conn, task_id=task_id, since=bound)
    if task_id is not None:
        spans = spans_for_trace(task_id)
    else:
        task_ids = {str(event.get("task_id")) for event in events if event.get("task_id")}
        spans = [span for span in finished_spans() if span.get("trace_id") in task_ids]
    return build_diagnostics_bundle(
        user_id=user_id,
        log_lines=lines,
        spans=spans,
        ledger_events=events,
        task_id=task_id,
    )


@dataclass
class DiagnosticsExport:
    """One finished export: where it went and what is in it."""

    path: Path
    user_id: str
    exported_at: datetime
    counts: dict[str, int] = field(default_factory=dict)
    redactions_applied: int = 0
    truncated: dict[str, bool] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return sum(self.counts.values())


def export_diagnostics(
    user_id: str,
    path: str | Path,
    *,
    dsn: str | None = None,
    task_id: str | None = None,
    log_file: str | Path | None = None,
    log_lines: list[str] | None = None,
    since: str | None = None,
    audit_dir: str | Path | None = None,
) -> DiagnosticsExport:
    """Write one user's redacted diagnostics bundle to ``path``.

    The file is written atomically (temp file + rename) so a
    failed export never leaves a half-written file behind. When
    ``audit_dir`` is given, the export is recorded in the
    audit export log there (the K12 rule: every export is
    recorded — who, how many rows, which filters).
    """
    from puspa.ledger.audit import AuditExportLog

    path = Path(path)
    document = collect_diagnostics(
        user_id,
        dsn=dsn,
        task_id=task_id,
        log_file=log_file,
        log_lines=log_lines,
        since=since,
    )
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)
    if audit_dir is not None:
        log = AuditExportLog(audit_dir)
        log.record(
            exporter=user_id,
            rows=sum(document["counts"].values()),
            truncated=any(document["truncated"].values()),
            filters={
                "kind": "diagnostics",
                "task_id": task_id,
                "since": since,
            },
        )
    return DiagnosticsExport(
        path=path,
        user_id=user_id,
        exported_at=datetime.fromisoformat(document["exported_at"]),
        counts=dict(document["counts"]),
        redactions_applied=int(document["redactions_applied"]),
        truncated=dict(document["truncated"]),
    )
