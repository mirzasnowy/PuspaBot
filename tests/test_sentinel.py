"""Tests for the sentinel: detectors, state file, verifier, scanner.

All tests are in-memory (synthetic ledger events + tmp_path).
No test here touches the database or the network.
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.sentinel.detectors import (
    detect_cost_overruns,
    detect_queue_waits,
    detect_retry_loops,
    detect_tool_errors,
    detect_turn_stops,
    run_detectors,
)
from puspa.sentinel.scanner import Sentinel
from puspa.sentinel.state import (
    acknowledge_issue,
    mark_counted,
    new_state,
    prune_seen,
    read_issues,
    read_state,
    resolve_issue,
    upsert_issues,
    write_sentinel_files,
)
from puspa.sentinel.thresholds import SentinelThresholds
from puspa.sentinel.verifier import verify_issue

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
THRESHOLDS = SentinelThresholds()


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


# ---------------------------------------------------------------- detectors


def test_tool_error_fires_at_threshold():
    events = [
        _event(
            f"e{i}",
            "tool_completed",
            "task-1",
            minutes_ago=10 + i,
            content={"tool": "read_price", "status": "error"},
        )
        for i in range(3)
    ]
    issues = detect_tool_errors(events, thresholds=THRESHOLDS, now=NOW)
    assert len(issues) == 1
    assert issues[0]["key"] == "tool_error:read_price"
    assert issues[0]["evidence"]["error_count"] == 3
    assert issues[0]["severity"] == "warning"


def test_tool_error_below_threshold_is_silent():
    events = [
        _event(
            f"e{i}",
            "tool_completed",
            "task-1",
            minutes_ago=10 + i,
            content={"tool": "read_price", "status": "error"},
        )
        for i in range(2)
    ]
    assert detect_tool_errors(events, thresholds=THRESHOLDS, now=NOW) == []


def test_tool_error_ignores_ok_and_old_events():
    events = [
        _event(
            "e1",
            "tool_completed",
            "task-1",
            minutes_ago=10,
            content={"tool": "read_price", "status": "ok"},
        ),
        _event(
            "e2",
            "tool_completed",
            "task-1",
            minutes_ago=8 * 24 * 60,
            content={"tool": "read_price", "status": "error"},
        ),
        _event(
            "e3",
            "tool_completed",
            "task-1",
            minutes_ago=9 * 24 * 60,
            content={"tool": "read_price", "status": "error"},
        ),
        _event(
            "e4",
            "tool_completed",
            "task-1",
            minutes_ago=10 * 24 * 60,
            content={"tool": "read_price", "status": "error"},
        ),
    ]
    assert detect_tool_errors(events, thresholds=THRESHOLDS, now=NOW) == []


def test_turn_stop_flags_stopped_task():
    events = [
        _event("e1", "task_created", "task-1", minutes_ago=60),
        _event("e2", "task_stopped", "task-1", minutes_ago=5),
    ]
    issues = detect_turn_stops(events, thresholds=THRESHOLDS, now=NOW)
    assert [issue["key"] for issue in issues] == ["turn_stop:task-1"]


def test_turn_stop_ignores_delete_tombstone():
    events = [
        _event("e1", "task_created", "task-1", minutes_ago=60),
        _event("e2", "task_stopped", "task-1", minutes_ago=5, content={"deleted": True}),
    ]
    assert detect_turn_stops(events, thresholds=THRESHOLDS, now=NOW) == []


def test_turn_stop_flags_stalled_task():
    events = [_event("e1", "task_created", "task-1", minutes_ago=30 * 60)]
    issues = detect_turn_stops(events, thresholds=THRESHOLDS, now=NOW)
    assert [issue["key"] for issue in issues] == ["turn_stall:task-1"]
    assert issues[0]["evidence"]["idle_hours"] == pytest.approx(30.0)


def test_turn_stop_ignores_recent_and_completed_tasks():
    events = [
        _event("e1", "task_created", "task-1", minutes_ago=60),
        _event("e2", "task_created", "task-2", minutes_ago=30 * 60),
        _event("e3", "task_completed", "task-2", minutes_ago=29 * 60),
    ]
    assert detect_turn_stops(events, thresholds=THRESHOLDS, now=NOW) == []


def test_queue_wait_flags_missed_trigger():
    events = [
        _event(
            "e1",
            "trigger_missed",
            "task-1",
            minutes_ago=30,
            content={"trigger_id": "trigger-1", "task_id": "task-1"},
        )
    ]
    issues = detect_queue_waits(events, thresholds=THRESHOLDS, now=NOW)
    assert [issue["key"] for issue in issues] == ["queue_wait:missed:trigger-1"]


def test_queue_wait_flags_late_start():
    events = [
        _event(
            "e1",
            "trigger_fired",
            "task-1",
            minutes_ago=20,
            content={"trigger_id": "trigger-1", "task_id": "task-1"},
        ),
        _event("e2", "task_created", "task-1", minutes_ago=10),
    ]
    issues = detect_queue_waits(events, thresholds=THRESHOLDS, now=NOW)
    assert len(issues) == 1
    assert issues[0]["evidence"]["wait_seconds"] == pytest.approx(600.0)


def test_queue_wait_ignores_fast_start_and_grace_period():
    events = [
        _event(
            "e1",
            "trigger_fired",
            "task-1",
            minutes_ago=10,
            content={"trigger_id": "trigger-1", "task_id": "task-1"},
        ),
        _event("e2", "task_created", "task-1", minutes_ago=8),
        _event(
            "e3",
            "trigger_fired",
            "task-2",
            minutes_ago=2,
            content={"trigger_id": "trigger-2", "task_id": "task-2"},
        ),
    ]
    assert detect_queue_waits(events, thresholds=THRESHOLDS, now=NOW) == []


def test_queue_wait_flags_never_started_trigger():
    events = [
        _event(
            "e1",
            "trigger_fired",
            "task-1",
            minutes_ago=20,
            content={"trigger_id": "trigger-1", "task_id": "task-1"},
        ),
    ]
    issues = detect_queue_waits(events, thresholds=THRESHOLDS, now=NOW)
    assert [issue["key"] for issue in issues] == ["queue_wait:trigger-1"]


def test_cost_flags_overrun_task():
    events = [
        _event(
            "e1",
            "tool_completed",
            "task-1",
            minutes_ago=30,
            content={"tool": "read_price", "status": "ok", "cost_rp": 50000},
        ),
        _event(
            "e2",
            "tool_completed",
            "task-1",
            minutes_ago=20,
            content={"tool": "read_price", "status": "ok", "cost_rp": 40000},
        ),
        _event(
            "e3",
            "tool_completed",
            "task-2",
            minutes_ago=20,
            content={"tool": "read_price", "status": "ok", "cost_rp": 70000},
        ),
    ]
    issues = detect_cost_overruns(events, thresholds=THRESHOLDS, now=NOW)
    assert [issue["key"] for issue in issues] == ["cost:task-1"]
    assert issues[0]["evidence"]["cost_usd"] == pytest.approx(5.625)
    assert issues[0]["severity"] == "critical"


def test_retry_loop_flags_three_failed_calls():
    events = []
    for i in range(3):
        events.append(
            _event(
                f"c{i}",
                "tool_called",
                "task-1",
                minutes_ago=30 - i * 2,
                content={"tool": "read_price"},
            )
        )
        events.append(
            _event(
                f"d{i}",
                "tool_completed",
                "task-1",
                minutes_ago=29 - i * 2,
                content={"tool": "read_price", "status": "error"},
            )
        )
    issues = detect_retry_loops(events, thresholds=THRESHOLDS, now=NOW)
    assert len(issues) == 1
    assert issues[0]["key"] == "retry_loop:task-1:read_price"
    assert issues[0]["evidence"]["attempts"] == 3


def test_retry_loop_resets_on_success():
    events = [
        _event("c1", "tool_called", "task-1", minutes_ago=30, content={"tool": "read_price"}),
        _event(
            "d1",
            "tool_completed",
            "task-1",
            minutes_ago=29,
            content={"tool": "read_price", "status": "error"},
        ),
        _event("c2", "tool_called", "task-1", minutes_ago=28, content={"tool": "read_price"}),
        _event(
            "d2",
            "tool_completed",
            "task-1",
            minutes_ago=27,
            content={"tool": "read_price", "status": "ok"},
        ),
        _event("c3", "tool_called", "task-1", minutes_ago=26, content={"tool": "read_price"}),
        _event(
            "d3",
            "tool_completed",
            "task-1",
            minutes_ago=25,
            content={"tool": "read_price", "status": "error"},
        ),
    ]
    assert detect_retry_loops(events, thresholds=THRESHOLDS, now=NOW) == []


def test_run_detectors_combines_all_five():
    events = [
        _event(
            "e1",
            "tool_completed",
            "task-1",
            minutes_ago=10,
            content={"tool": "read_price", "status": "error", "cost_rp": 90000},
        ),
        _event(
            "e2",
            "tool_completed",
            "task-1",
            minutes_ago=9,
            content={"tool": "read_price", "status": "error"},
        ),
        _event(
            "e3",
            "tool_completed",
            "task-1",
            minutes_ago=8,
            content={"tool": "read_price", "status": "error"},
        ),
        _event("e4", "task_stopped", "task-1", minutes_ago=5),
    ]
    detectors = {issue["detector"] for issue in run_detectors(events, now=NOW)}
    assert detectors == {"tool_error", "turn_stop", "cost"}


# ---------------------------------------------------------------- state file


def test_state_missing_file_reads_fresh(tmp_path):
    state = read_state(tmp_path / "sentinel-state.json")
    assert state == {"version": 1, "last_scan": None, "issues": {}}


def test_state_corrupt_file_reads_fresh(tmp_path):
    path = tmp_path / "sentinel-state.json"
    path.write_text("{not json", encoding="utf-8")
    assert read_state(path)["issues"] == {}


def test_upsert_add_update_and_skip_acknowledged(tmp_path):
    state = new_state()
    issue = {
        "detector": "cost",
        "key": "cost:task-1",
        "severity": "critical",
        "summary": "expensive",
        "task_id": "task-1",
        "evidence": {"cost_rp": 90000},
        "first_seen": NOW.isoformat(),
        "last_seen": NOW.isoformat(),
    }
    outcome = upsert_issues(state, [issue], now=NOW)
    assert outcome["added"] == ["cost:task-1"]
    assert state["issues"]["cost:task-1"]["status"] == "open"
    assert state["issues"]["cost:task-1"]["count"] == 1
    outcome = upsert_issues(state, [issue], now=NOW)
    assert outcome["updated"] == ["cost:task-1"]
    assert state["issues"]["cost:task-1"]["count"] == 2
    assert state["issues"]["cost:task-1"]["first_seen"] == NOW.isoformat()
    assert acknowledge_issue(state, "cost:task-1", now=NOW)
    outcome = upsert_issues(state, [issue], now=NOW)
    assert outcome["skipped"] == ["cost:task-1"]
    assert state["issues"]["cost:task-1"]["status"] == "acknowledged"
    assert state["issues"]["cost:task-1"]["count"] == 2


def test_mark_counted_and_resolve(tmp_path):
    state = new_state()
    assert mark_counted(state, "missing") is None
    upsert_issues(
        state,
        [
            {
                "detector": "cost",
                "key": "cost:task-1",
                "severity": "critical",
                "summary": "x",
                "task_id": "task-1",
                "evidence": {},
                "first_seen": NOW.isoformat(),
                "last_seen": NOW.isoformat(),
            }
        ],
        now=NOW,
    )
    counted = mark_counted(state, "cost:task-1", now=NOW)
    assert counted["count"] == 2
    assert resolve_issue(state, "cost:task-1", resolution="fixed", now=NOW)
    assert state["issues"]["cost:task-1"]["status"] == "resolved"
    assert state["issues"]["cost:task-1"]["resolution"] == "fixed"
    assert not resolve_issue(state, "missing")


def test_prune_seen_keeps_acknowledged(tmp_path):
    state = new_state()
    old = (NOW - timedelta(days=10)).isoformat()
    recent = NOW.isoformat()
    state["issues"] = {
        "open-old": {"key": "open-old", "status": "open", "last_seen": old, "count": 1},
        "ack-old": {"key": "ack-old", "status": "acknowledged", "last_seen": old, "count": 1},
        "open-new": {"key": "open-new", "status": "open", "last_seen": recent, "count": 1},
        "resolved-old": {"key": "resolved-old", "status": "resolved", "last_seen": old, "count": 1},
    }
    pruned = prune_seen(state, now=NOW, lookback_days=7)
    assert pruned == ["open-old"]
    assert sorted(state["issues"]) == ["ack-old", "open-new", "resolved-old"]
    pruned = prune_seen(state, now=NOW + timedelta(days=8), lookback_days=7)
    # Eight days later the "new" open issue is 8 days unseen, past
    # the 7-day window too, and the resolved one is 18 days unseen,
    # past twice the window. Only the acknowledged issue survives.
    assert pruned == ["open-new", "resolved-old"]
    assert sorted(state["issues"]) == ["ack-old"]


def test_write_sentinel_files_round_trip(tmp_path):
    state = new_state()
    upsert_issues(
        state,
        [
            {
                "detector": "cost",
                "key": "cost:task-1",
                "severity": "critical",
                "summary": "x",
                "task_id": "task-1",
                "evidence": {},
                "first_seen": NOW.isoformat(),
                "last_seen": NOW.isoformat(),
            }
        ],
        now=NOW,
    )
    files = write_sentinel_files(state, tmp_path / "sentinel-state.json")
    assert read_state(files["state"])["issues"]["cost:task-1"]["status"] == "open"
    assert len(read_issues(read_state(files["state"]), status="open")) == 1
    import json

    snapshot = json.loads((tmp_path / "sentinel-state-issues.json").read_text())
    assert [item["key"] for item in snapshot] == ["cost:task-1"]


# ---------------------------------------------------------------- verifier


def test_verifier_confirms_live_condition():
    events = [
        _event(
            f"e{i}",
            "tool_completed",
            "task-1",
            minutes_ago=10 + i,
            content={"tool": "read_price", "status": "error"},
        )
        for i in range(3)
    ]
    issue = detect_tool_errors(events, thresholds=THRESHOLDS, now=NOW)[0]
    verdict = verify_issue(issue, event_source=lambda: events, now=NOW)
    assert verdict.verdict == "confirmed"
    assert verdict.issue_key == issue["key"]


def test_verifier_calls_stale_when_condition_gone():
    events = [
        _event(
            f"e{i}",
            "tool_completed",
            "task-1",
            minutes_ago=10 + i,
            content={"tool": "read_price", "status": "error"},
        )
        for i in range(3)
    ]
    issue = detect_tool_errors(events, thresholds=THRESHOLDS, now=NOW)[0]
    verdict = verify_issue(issue, event_source=list, now=NOW)
    assert verdict.verdict == "stale"


def test_verifier_calls_duplicate_when_acknowledged():
    events = [
        _event(
            f"e{i}",
            "tool_completed",
            "task-1",
            minutes_ago=10 + i,
            content={"tool": "read_price", "status": "error"},
        )
        for i in range(3)
    ]
    issue = detect_tool_errors(events, thresholds=THRESHOLDS, now=NOW)[0]
    state = new_state()
    upsert_issues(state, [issue], now=NOW)
    acknowledge_issue(state, issue["key"], now=NOW)
    verdict = verify_issue(issue, event_source=lambda: events, state=state, now=NOW)
    assert verdict.verdict == "duplicate"


def test_verifier_fails_closed_on_broken_source():
    issue = {"detector": "cost", "key": "cost:task-1", "evidence": {}}
    verdict = verify_issue(issue, event_source=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert verdict.verdict == "unverifiable"
    unknown = {"detector": "nope", "key": "nope:x", "evidence": {}}
    assert verify_issue(unknown, event_source=list).verdict == "unverifiable"


# ---------------------------------------------------------------- scanner


def _scan_events():
    return [
        _event(
            f"e{i}",
            "tool_completed",
            "task-1",
            minutes_ago=10 + i,
            content={"tool": "read_price", "status": "error"},
        )
        for i in range(3)
    ]


def test_scanner_scans_then_skips_inside_rescan_interval(tmp_path):
    sentinel = Sentinel(
        event_source=_scan_events,
        state_path=tmp_path / "sentinel-state.json",
        clock=lambda: NOW,
    )
    first = sentinel.scan()
    assert first["status"] == "scanned"
    assert first["added"] == ["tool_error:read_price"]
    second = sentinel.scan()
    assert second["status"] == "skipped"
    assert second["reason"] == "rescan interval not reached"


def test_scanner_forced_rescan_bumps_count(tmp_path):
    sentinel = Sentinel(
        event_source=_scan_events,
        state_path=tmp_path / "sentinel-state.json",
        clock=lambda: NOW,
    )
    sentinel.scan()
    forced = sentinel.scan(force=True)
    assert forced["status"] == "scanned"
    assert forced["updated"] == ["tool_error:read_price"]
    assert (
        read_state(tmp_path / "sentinel-state.json")["issues"]["tool_error:read_price"]["count"]
        == 2
    )


def test_scanner_auto_resolves_cleared_condition(tmp_path):
    events = {"events": _scan_events()}
    sentinel = Sentinel(
        event_source=lambda: events["events"],
        state_path=tmp_path / "sentinel-state.json",
        clock=lambda: NOW,
    )
    assert sentinel.scan()["added"] == ["tool_error:read_price"]
    events["events"] = []
    report = sentinel.scan(force=True)
    assert report["auto_resolved"] == ["tool_error:read_price"]
    stored = read_state(tmp_path / "sentinel-state.json")["issues"]["tool_error:read_price"]
    assert stored["status"] == "resolved"
    assert stored["resolution"] == "auto: condition cleared"


def test_scanner_reports_broken_source(tmp_path):
    sentinel = Sentinel(
        event_source=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        state_path=tmp_path / "sentinel-state.json",
        clock=lambda: NOW,
    )
    report = sentinel.scan()
    assert report["status"] == "error"


def test_run_sentinel_scan_never_raises_on_ledger_failure(monkeypatch, tmp_path):
    from puspa.ledger import Ledger

    def _broken(self, since, limit=10000):
        raise RuntimeError("no database")

    monkeypatch.setattr(Ledger, "recent_events", _broken)
    from puspa.sentinel.scanner import run_sentinel_scan

    report = run_sentinel_scan(user_id="user-1", state_dir=tmp_path)
    assert report["job"] == "sentinel_scan"
    assert report["status"] == "error"
