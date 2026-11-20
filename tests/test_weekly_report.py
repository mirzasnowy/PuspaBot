"""Tests for the P-02 weekly report (``puspa.pwa.weekly_report``).

Two layers: the pure summary over event dicts (window, counts,
correction definition, audit links, honest null savings), and
the database loader over real ledger events (a user sees only
their own events, old events fall outside the window).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from puspa.billing.ledger import PostgresUsageStore, UsageRow, create_schema
from puspa.pwa.weekly_report import summarise_weekly, weekly_report_for_user

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _event(event_type, *, at, event_id="event-1", task_id="task-1"):
    return {
        "id": event_id,
        "type": event_type,
        "task_id": task_id,
        "agent_id": "agent-1",
        "created_at": at,
        "content": {},
    }


# ---------------------------------------------------------------------------
# Pure summary
# ---------------------------------------------------------------------------


def test_empty_week_is_honest_zeros_with_null_savings():
    report = summarise_weekly([], days=7, now=NOW)
    assert report["window_days"] == 7
    assert report["tasks_completed"] == 0
    assert report["tasks_corrected"] == 0
    assert report["approvals_granted"] == 0
    assert report["time_saved_minutes"] is None
    assert report["money_saved_idr"] is None
    assert "baseline" in report["savings_note"]
    assert report["cost_idr"] is None
    assert report["cost_label"] is None
    assert report["audit_trail_url"] == "/api/proof/audit-trail"
    assert report["linked_events"] == []


def test_counts_completed_corrected_and_approvals():
    events = [
        _event("task_completed", at=NOW - timedelta(days=1), event_id="e1"),
        _event("task_completed", at=NOW - timedelta(days=2), event_id="e2"),
        _event("approval_rejected", at=NOW - timedelta(days=1), event_id="e3"),
        _event("false_report", at=NOW - timedelta(days=1), event_id="e4"),
        _event("approval_granted", at=NOW - timedelta(days=1), event_id="e5"),
        _event("task_stopped", at=NOW - timedelta(days=1), event_id="e6"),
        _event("task_failed", at=NOW - timedelta(days=1), event_id="e7"),
    ]
    report = summarise_weekly(events, days=7, now=NOW, cost_idr=2500)
    assert report["tasks_completed"] == 2
    assert report["tasks_corrected"] == 2
    assert report["approvals_granted"] == 1
    assert report["approvals_rejected"] == 1
    assert report["tasks_stopped"] == 1
    assert report["tasks_failed"] == 1
    assert report["cost_idr"] == 2500
    assert report["cost_label"] == "Rp2.500"


def test_events_outside_the_window_are_ignored():
    events = [
        _event("task_completed", at=NOW - timedelta(days=1), event_id="e1"),
        _event("task_completed", at=NOW - timedelta(days=8), event_id="e2"),
        _event("task_completed", at=NOW - timedelta(days=30), event_id="e3"),
    ]
    report = summarise_weekly(events, days=7, now=NOW)
    assert report["tasks_completed"] == 1
    assert report["since"].startswith("2026-11-14")
    assert report["until"].startswith("2026-11-20")


def test_linked_events_carry_receipt_urls_newest_first():
    events = [
        _event("task_completed", at=NOW - timedelta(days=2), event_id="old"),
        _event("approval_rejected", at=NOW - timedelta(days=1), event_id="new"),
        _event("user_message", at=NOW - timedelta(hours=1), event_id="unlinked"),
    ]
    report = summarise_weekly(events, days=7, now=NOW)
    linked = report["linked_events"]
    assert [item["event_id"] for item in linked] == ["new", "old"]
    assert linked[0]["receipt_url"] == "/api/proof/receipts/new"
    assert linked[0]["type"] == "approval_rejected"


def test_linked_events_are_capped_at_twenty():
    events = [
        _event("task_completed", at=NOW - timedelta(minutes=i), event_id=f"e{i}") for i in range(25)
    ]
    assert len(summarise_weekly(events, days=7, now=NOW)["linked_events"]) == 20


def test_bad_days_are_rejected():
    with pytest.raises(ValueError, match="days must be between"):
        summarise_weekly([], days=0, now=NOW)
    with pytest.raises(ValueError, match="days must be between"):
        summarise_weekly([], days=91, now=NOW)


# ---------------------------------------------------------------------------
# Database loader
# ---------------------------------------------------------------------------


def _usage_row(user_id, **overrides):
    values = {
        "user_id": user_id,
        "bot_id": "bot-a",
        "task_id": "task-1",
        "driver": "openrouter",
        "model": "cheap-model",
        "input_tokens": 1000,
        "output_tokens": 200,
        "cached_tokens": 100,
        "cost_idr": 800,
        "trigger": "morning-briefing",
        "outcome": "done",
        "steps": 3,
        "tools": ["read"],
        "tool_errors": 0,
        "provider": "openrouter",
        "module_id": "briefing",
    }
    values.update(overrides)
    return UsageRow(**values)


def test_loader_counts_only_the_users_own_events_in_the_window(make_user, test_dsn, ledger):
    user = make_user()
    other = make_user()
    agent_id = user["agent_id"]
    # Two completions and one correction for this user, one
    # completion for the other user, and one completion 30 days
    # ago (outside the 7-day window) for this user.
    ledger.append_event(
        task_id="task-a", agent_id=agent_id, event_type="task_completed", content={}
    )
    ledger.append_event(
        task_id="task-b", agent_id=agent_id, event_type="task_completed", content={}
    )
    ledger.append_event(
        task_id="task-b",
        agent_id=agent_id,
        event_type="approval_rejected",
        content={"request_id": "req-1"},
    )
    ledger.append_event(
        task_id="task-c",
        agent_id=other["agent_id"],
        event_type="task_completed",
        content={},
    )
    report = weekly_report_for_user(user["user_id"], days=7, dsn=test_dsn)
    assert report["tasks_completed"] == 2
    assert report["tasks_corrected"] == 1
    assert report["approvals_rejected"] == 1
    assert len(report["linked_events"]) == 3
    assert all(
        item["receipt_url"].startswith("/api/proof/receipts/") for item in report["linked_events"]
    )


def test_loader_reads_the_cost_from_the_billing_ledger(make_user, test_dsn, ledger):
    user = make_user()
    create_schema(test_dsn)
    store = PostgresUsageStore(test_dsn)
    try:
        store.append(_usage_row(user["user_id"], cost_idr=800))
        store.append(_usage_row(user["user_id"], cost_idr=400, task_id="task-2"))
        report = weekly_report_for_user(user["user_id"], days=7, dsn=test_dsn)
        assert report["cost_idr"] == 1200
        assert report["cost_label"] == "Rp1.200"
    finally:
        with psycopg.connect(test_dsn) as conn:
            conn.execute("DELETE FROM billing_usage_rows WHERE user_id = %s", (user["user_id"],))
            conn.commit()


def test_loader_rejects_an_empty_user_id(test_dsn):
    with pytest.raises(ValueError, match="user_id must be"):
        weekly_report_for_user("", days=7, dsn=test_dsn)
