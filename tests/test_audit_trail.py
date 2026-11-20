"""Tests for the audit trail with initiator (G6).

In-memory only, except the export log which writes JSONL files
under a tmp dir. No ledger database is used: events here are plain
dicts in ledger shape.
"""

import csv
import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from puspa.ledger import EVENT_TYPES
from puspa.ledger.audit import (
    MAX_EXPORT_ROWS,
    AuditExportLog,
    ControlRejected,
    ControlState,
    Initiator,
    attach_initiator,
    audit_retention_days,
    export_events_csv,
    file_audit_entry,
    filter_nobody_watching,
    initiator_of,
    is_nobody_watching,
    sanitize_cell,
)


def _event(event_type="tool_called", *, initiator=None, content=None, task_id="task-1"):
    body = dict(content or {})
    if initiator is not None:
        body = attach_initiator(body, initiator)
    return {
        "id": "evt-1",
        "created_at": "2026-11-18T10:00:00+00:00",
        "task_id": task_id,
        "agent_id": "agent-1",
        "type": event_type,
        "content": body,
    }


# --- initiator attribute: still 33 event types ---


def test_event_types_still_33():
    assert len(EVENT_TYPES) == 33


def test_initiator_kinds():
    for kind in ("person", "deployment", "routine", "responsibility", "memory", "handoff"):
        assert Initiator(kind=kind, id="x").kind == kind
    with pytest.raises(ValueError, match="unknown initiator kind"):
        Initiator(kind="ghost", id="x")
    with pytest.raises(ValueError, match="non-empty string"):
        Initiator(kind="person", id="")


def test_attach_initiator_does_not_mutate():
    content = {"tool": "click"}
    out = attach_initiator(content, Initiator(kind="person", id="user-1"))
    assert out["initiator"] == {"kind": "person", "id": "user-1"}
    assert "initiator" not in content
    assert initiator_of(_event(initiator=Initiator(kind="person", id="user-1"))) is not None


def test_initiator_of_returns_none_without_attribute():
    assert initiator_of(_event()) is None
    assert is_nobody_watching(_event()) is False


# --- "Nobody watching" filter ---


@pytest.mark.parametrize("kind", ["routine", "handoff", "responsibility", "memory"])
def test_nobody_watching_kinds(kind):
    event = _event(initiator=Initiator(kind=kind, id="run-1"))
    assert is_nobody_watching(event) is True


@pytest.mark.parametrize("kind", ["person", "deployment"])
def test_watched_kinds_are_not_nobody_watching(kind):
    event = _event(initiator=Initiator(kind=kind, id="user-1"))
    assert is_nobody_watching(event) is False


def test_filter_nobody_watching():
    events = [
        _event(initiator=Initiator(kind="person", id="user-1")),
        _event(initiator=Initiator(kind="routine", id="cron-1")),
        _event(initiator=Initiator(kind="memory", id="dream-1")),
        _event(),
    ]
    watched_out = filter_nobody_watching(events)
    assert len(watched_out) == 2
    assert {initiator_of(e).kind for e in watched_out} == {"routine", "memory"}


# --- handover / control state ---


def test_handover_events_use_existing_types():
    seen = []

    def log(event_type, content):
        seen.append((event_type, content))

    control = ControlState(log=log)
    assert control.bot_action_allowed() is True
    control.request_help(
        "bot", "page needs a login", initiator=Initiator(kind="person", id="user-1")
    )
    control.take_control("user-1", initiator=Initiator(kind="person", id="user-1"))
    assert control.in_human_control is True
    assert control.bot_action_allowed() is False
    control.release_control("user-1", initiator=Initiator(kind="person", id="user-1"))
    assert control.bot_action_allowed() is True

    types = [t for t, _ in seen]
    assert types == ["handoff_proposed", "handoff_handed_over", "handoff_accepted"]
    assert all(t in EVENT_TYPES for t in types)
    assert seen[0][1]["control"] == "help_requested"
    assert seen[1][1]["control"] == "control_taken"
    assert seen[2][1]["control"] == "control_released"
    assert seen[1][1]["initiator"] == {"kind": "person", "id": "user-1"}


def test_bot_action_rejected_not_queued_while_human_controls():
    control = ControlState()
    control.take_control("user-1")
    with pytest.raises(ControlRejected, match="controls the browser"):
        control.reject_bot_action({"tool": "click", "kind": "click"})
    # ...and allowed again once control is released.
    control.release_control("user-1")
    assert control.reject_bot_action({"tool": "click"}) is None


def test_control_state_without_log_still_works():
    control = ControlState()
    control.take_control("user-1")
    assert control.controller == "user-1"


# --- CSV export guards ---


@pytest.mark.parametrize("cell", ["=cmd", "+cmd", "-cmd", "@cmd"])
def test_formula_leader_cells_get_prefix(cell):
    assert sanitize_cell(cell) == "'" + cell
    assert sanitize_cell("safe") == "safe"
    assert sanitize_cell(42) == "42"
    assert sanitize_cell(None) == ""


def test_export_writes_rows_and_records_the_export(tmp_path):
    log = AuditExportLog(tmp_path)
    events = [_event(), _event(event_type="tool_completed")]
    result = export_events_csv(events, exporter="user-1", filters={"task_id": "task-1"}, log=log)
    assert result.rows == 2
    assert result.total_rows == 2
    assert result.truncated is False
    rows = list(csv.reader(io.StringIO(result.csv_text)))
    assert rows[0][:5] == ["id", "created_at", "task_id", "agent_id", "type"]
    assert len(rows) == 3  # header + 2 rows
    records = log.read_all()
    assert len(records) == 1
    assert records[0]["exporter"] == "user-1"
    assert records[0]["rows"] == 2
    assert records[0]["filters"] == {"task_id": "task-1"}
    assert records[0]["truncated"] is False


def test_export_caps_at_5000_rows_with_truncation_marker(tmp_path):
    log = AuditExportLog(tmp_path)
    events = [_event() for _ in range(MAX_EXPORT_ROWS + 7)]
    result = export_events_csv(events, exporter="user-1", log=log)
    assert result.rows == MAX_EXPORT_ROWS
    assert result.total_rows == MAX_EXPORT_ROWS + 7
    assert result.truncated is True
    assert result.csv_text.startswith("# Puspa-Export-Truncated: true\n")
    records = log.read_all()
    assert records[0]["rows"] == MAX_EXPORT_ROWS
    assert records[0]["truncated"] is True


def test_export_sanitizes_formula_cells_and_redacts_secrets(tmp_path):
    log = AuditExportLog(tmp_path)
    # A cell that starts with "=" (here the task id) must be
    # prefixed; a secret inside the content JSON must be redacted
    # while the JSON stays valid.
    event = _event(
        task_id="=2+2",
        content={
            "api_key": "api_key=sk-live-123456",
            "file": file_audit_entry(__file__),
        },
    )
    result = export_events_csv([event], exporter="user-1", log=log)
    assert "'=2+2" in result.csv_text
    assert "sk-live-123456" not in result.csv_text
    assert "«redacted»" in result.csv_text
    # The content cell is still valid JSON after redaction.
    rows = list(csv.reader(io.StringIO(result.csv_text)))
    content = json.loads(rows[1][7])
    assert content["api_key"] == "api_key=«redacted»"


def test_export_requires_a_log():
    with pytest.raises(ValueError, match="AuditExportLog"):
        export_events_csv([_event()], exporter="user-1", log=None)


def test_export_log_hash_chain_verifies(tmp_path):
    log = AuditExportLog(tmp_path)
    log.record(exporter="a", rows=1, truncated=False, filters={})
    log.record(exporter="b", rows=2, truncated=True, filters={"type": "tool_called"})
    ok, broken = log.verify_chain()
    assert ok is True
    assert broken == []
    # Tamper with one record on disk: the chain breaks.
    lines = (tmp_path / "audit-exports.jsonl").read_text().splitlines()
    record = json.loads(lines[0])
    record["rows"] = 99
    lines[0] = json.dumps(record)
    (tmp_path / "audit-exports.jsonl").write_text("\n".join(lines) + "\n")
    ok, broken = log.verify_chain()
    assert ok is False
    assert len(broken) == 1


def test_file_audit_entry_has_path_and_size_not_content(tmp_path):
    target = tmp_path / "secret.txt"
    target.write_text("top secret content")
    entry = file_audit_entry(target)
    assert entry["path"] == str(target)
    assert entry["size_bytes"] == len("top secret content")
    assert "top secret content" not in json.dumps(entry)


# --- retention ---


def test_retention_unset_means_forever(monkeypatch):
    monkeypatch.delenv("AUDIT_RETENTION_DAYS", raising=False)
    assert audit_retention_days() is None


def test_retention_reads_env(monkeypatch):
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "30")
    assert audit_retention_days() == 30
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "not-a-number")
    with pytest.raises(ValueError, match="must be an integer"):
        audit_retention_days()


def test_prune_drops_old_export_records_only(tmp_path):
    log = AuditExportLog(tmp_path, retention_days=30)
    now = datetime.now(UTC)
    old = now - timedelta(days=40)
    recent = now - timedelta(days=5)
    first = log.record(exporter="a", rows=1, truncated=False, exported_at=old)
    second = log.record(exporter="b", rows=2, truncated=False, exported_at=recent)
    pruned = log.prune(now=now)
    assert pruned == [first["id"]]
    assert [r["id"] for r in log.read_all()] == [second["id"]]
    ok, _ = log.verify_chain()
    assert ok is True


def test_prune_with_no_retention_keeps_everything(tmp_path, monkeypatch):
    monkeypatch.delenv("AUDIT_RETENTION_DAYS", raising=False)
    log = AuditExportLog(tmp_path)
    log.record(
        exporter="a", rows=1, truncated=False, exported_at=datetime.now(UTC) - timedelta(days=400)
    )
    assert log.prune() == []
    assert len(log.read_all()) == 1
