"""Tests for the sentinel decision log, usage traces, and analytics.

All tests are in-memory (synthetic events + tmp_path). No test
here touches the database or the network.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from puspa.sentinel.analytics import Analytics
from puspa.sentinel.decision_log import DailyDecisionLog
from puspa.sentinel.traces import trace_from_events

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)


def _event(event_id, event_type, task_id, minutes_ago, content=None, parent_id=None):
    return {
        "id": event_id,
        "created_at": NOW - timedelta(minutes=minutes_ago),
        "task_id": task_id,
        "agent_id": "agent-1",
        "subagent_id": None,
        "type": event_type,
        "content": content or {},
        "parent_id": parent_id,
        "previous_hash": "GENESIS",
        "hash": "x",
    }


# ---------------------------------------------------------------- decision log


def test_decision_log_records_actor_source_via(tmp_path):
    log = DailyDecisionLog(tmp_path)
    entry = log.record(
        actor="sentinel",
        source="scan",
        via="auto",
        decision="auto_resolve",
        details={"issue_key": "cost:task-1"},
        now=NOW,
    )
    assert entry["actor"] == "sentinel"
    assert entry["source"] == "scan"
    assert entry["via"] == "auto"
    assert entry["decision"] == "auto_resolve"
    assert entry["date"] == "2026-11-18"
    assert len(entry["entry_hash"]) == 64
    assert log.read_day(date(2026, 11, 18)) == [entry]


def test_decision_log_hash_is_stable_without_timestamp(tmp_path):
    log = DailyDecisionLog(tmp_path)
    first = log.record(
        actor="sentinel",
        source="scan",
        via="auto",
        decision="raise",
        details={"issue_key": "cost:task-1"},
        now=NOW,
    )
    second = log.record(
        actor="sentinel",
        source="scan",
        via="auto",
        decision="raise",
        details={"issue_key": "cost:task-1"},
        now=NOW + timedelta(hours=2),
    )
    assert first["entry_hash"] == second["entry_hash"]
    assert first["recorded_at"] != second["recorded_at"]


def test_decision_log_read_range_and_prune(tmp_path):
    log = DailyDecisionLog(tmp_path, retention_days=180)
    log.record(
        actor="sentinel",
        source="scan",
        via="auto",
        decision="a",
        now=datetime(2026, 5, 1, 12, tzinfo=UTC),
    )
    log.record(
        actor="sentinel",
        source="scan",
        via="auto",
        decision="b",
        now=datetime(2026, 11, 17, 12, tzinfo=UTC),
    )
    log.record(
        actor="sentinel",
        source="scan",
        via="auto",
        decision="c",
        now=datetime(2026, 11, 18, 12, tzinfo=UTC),
    )
    assert len(log.read_range(date(2026, 11, 17), date(2026, 11, 18))) == 2
    assert log.read_day(date(2026, 11, 19)) == []
    deleted = log.prune(now=NOW)
    assert deleted == ["decisions-2026-05-01.jsonl"]
    assert log.read_day(date(2026, 5, 1)) == []
    assert len(log.read_day(date(2026, 11, 18))) == 1


def test_decision_log_prune_ignores_other_files(tmp_path):
    log = DailyDecisionLog(tmp_path, retention_days=180)
    (tmp_path / "notes.txt").write_text("keep me", encoding="utf-8")
    (tmp_path / "decisions-not-a-date.jsonl").write_text("{}", encoding="utf-8")
    assert log.prune(now=NOW) == []
    assert (tmp_path / "notes.txt").exists()


# ---------------------------------------------------------------- traces


def test_trace_builds_task_tool_and_message_spans():
    events = [
        _event("e1", "task_created", "task-1", minutes_ago=60),
        _event(
            "e2", "user_message", "task-1", minutes_ago=55, content={"content": "compare prices"}
        ),
        _event("e3", "tool_called", "task-1", minutes_ago=50, content={"tool": "read_price"}),
        _event(
            "e4",
            "tool_completed",
            "task-1",
            minutes_ago=49,
            content={"tool": "read_price", "status": "ok"},
        ),
        _event(
            "e5",
            "agent_message",
            "task-1",
            minutes_ago=48,
            content={"content": "cheapest is Rp100"},
        ),
        _event("e6", "task_completed", "task-1", minutes_ago=47),
    ]
    trace = trace_from_events(events, "task-1")
    assert trace.root is not None
    assert trace.root.kind == "task"
    assert trace.root.name == "task:task-1"
    flat = trace.flatten()
    assert len(flat) == 4  # task + user + tool + agent (terminal ends the root)
    tool_span = next(span for span in flat if span["kind"] == "tool")
    assert tool_span["name"] == "tool:read_price"
    assert tool_span["attributes"]["status"] == "ok"
    assert tool_span["duration_seconds"] == pytest.approx(60.0)
    assert trace.root.duration_seconds == pytest.approx(13 * 60.0)
    assert trace.root.to_dict()["children"][0]["parent_id"] == trace.root.span_id


def test_trace_ignores_other_tasks_and_keeps_unpaired_calls():
    events = [
        _event("e1", "task_created", "task-1", minutes_ago=60),
        _event("e2", "tool_called", "task-1", minutes_ago=50, content={"tool": "read_price"}),
        _event("e3", "tool_called", "task-2", minutes_ago=40, content={"tool": "read_bill"}),
    ]
    trace = trace_from_events(events, "task-1")
    flat = trace.flatten()
    assert len(flat) == 2
    tool_span = flat[1]
    assert tool_span["ended_at"] is None
    assert tool_span["duration_seconds"] is None
    assert trace_from_events(events, "task-unknown").root is None


def test_trace_json_round_trip():
    import json

    events = [
        _event("e1", "task_created", "task-1", minutes_ago=60),
        _event("e2", "task_completed", "task-1", minutes_ago=50),
    ]
    trace = trace_from_events(events, "task-1")
    parsed = json.loads(trace.to_json())
    assert parsed["task_id"] == "task-1"
    assert parsed["root"]["kind"] == "task"


# ---------------------------------------------------------------- analytics


def test_analytics_counts_and_flushes_to_sink():
    seen = []
    analytics = Analytics(enabled=True, sink=seen.append)
    analytics.record_event("scan_run")
    analytics.record_event("scan_run")
    analytics.record_event("issue_found")
    assert analytics.counters == {"scan_run": 2, "issue_found": 1}
    payload = analytics.flush()
    assert payload["counters"] == {"scan_run": 2, "issue_found": 1}
    assert seen == [payload]
    assert analytics.counters == {}
    assert analytics.flush() is None


def test_analytics_off_is_a_noop():
    analytics = Analytics(enabled=False)
    analytics.record_event("scan_run")
    assert analytics.crash_report(RuntimeError("secret message")) is None
    assert analytics.counters == {}
    assert analytics.flush() is None


def test_analytics_rejects_non_snake_case_names():
    analytics = Analytics(enabled=True)
    with pytest.raises(ValueError):
        analytics.record_event("user-123")
    with pytest.raises(ValueError):
        analytics.record_event("task_id=abc")


def test_analytics_crash_report_carries_no_message():
    analytics = Analytics(enabled=True)
    report = analytics.crash_report(
        RuntimeError("user secret text hunter2"), context={"tool": "read_price"}
    )
    assert report is not None
    assert report["type"] == "RuntimeError"
    assert "hunter2" not in str(report.values())
    assert "secret" not in str(report.values())
    assert report["context"] == {"tool": "read_price"}
    assert len(report["fingerprint"]) == 64
    same = analytics.crash_report(RuntimeError("different text"))
    assert same["fingerprint"] == report["fingerprint"]


def test_analytics_disable_drops_buffered_data():
    analytics = Analytics(enabled=True)
    analytics.record_event("scan_run")
    analytics.disable()
    assert analytics.counters == {}
    analytics.enable()
    analytics.record_event("scan_run")
    assert analytics.counters == {"scan_run": 1}
