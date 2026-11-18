"""Tests: usage ledger — UsageRow, Battery view, routine forecast,
task_budget_remaining (Rp2.000/task policy), upsell thresholds.
Mostly in-memory; one Postgres round-trip test uses its own schema
and cleans up its own rows."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from puspa.billing.ledger import (
    MemoryUsageStore,
    PostgresUsageStore,
    UsageRow,
    battery_view,
    create_schema,
    default_memory_store,
    forecast_routines,
    task_budget_remaining,
    upsell_due,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
DSN = os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")


def _row(**overrides):
    values = {
        "user_id": "user-1",
        "bot_id": "bot-a",
        "task_id": "task-1",
        "driver": "openrouter",
        "model": "cheap-model",
        "input_tokens": 1000,
        "output_tokens": 200,
        "cached_tokens": 100,
        "cost_idr": 500,
        "trigger": "morning-briefing",
        "outcome": "done",
        "steps": 3,
        "tools": ["read", "write"],
        "tool_errors": 0,
        "provider": "openrouter",
        "module_id": "briefing",
        "recorded_at": NOW,
    }
    values.update(overrides)
    return UsageRow(**values)


def test_usage_row_defaults_and_currency():
    row = UsageRow(user_id="u", bot_id="b")
    assert row.currency == "IDR"
    assert row.cost_idr == 0
    assert row.tools == []
    assert row.recorded_at is not None
    with pytest.raises(ValueError, match="always IDR"):
        UsageRow(user_id="u", bot_id="b", currency="USD")
    with pytest.raises(ValueError):
        UsageRow(user_id="u", bot_id="b", cost_idr=-1)
    with pytest.raises(ValueError):
        UsageRow(user_id="u", bot_id="b", input_tokens=-5)


def test_memory_store_round_trip():
    store = MemoryUsageStore()
    store.append(_row())
    store.append(_row(bot_id="bot-b", cost_idr=700, task_id="task-2"))
    assert len(store.rows_for_user("user-1")) == 2
    assert len(store.rows_for_task("user-1", "task-1")) == 1
    assert store.rows_for_task("user-1", "task-1")[0].cost_idr == 500
    assert store.rows_for_user("other-user") == []
    recent = store.rows_for_user("user-1", since=NOW - timedelta(days=1))
    assert len(recent) == 2
    old = store.rows_for_user("user-1", since=NOW + timedelta(days=1))
    assert old == []


def test_task_budget_remaining_uses_recorded_usage():
    store = MemoryUsageStore()
    assert task_budget_remaining("user-1", "task-1", store=store) == 2000
    store.append(_row(cost_idr=500))
    store.append(_row(cost_idr=1200))
    assert task_budget_remaining("user-1", "task-1", store=store) == 300
    store.append(_row(cost_idr=900))
    # Never negative: overspend clamps at zero.
    assert task_budget_remaining("user-1", "task-1", store=store) == 0
    # Another task is unaffected; a custom cap is honoured.
    assert task_budget_remaining("user-1", "task-2", store=store) == 2000
    assert task_budget_remaining("user-1", "task-1", cap_rp=5000, store=store) == 2400


def test_task_budget_remaining_shared_default_store():
    store = default_memory_store()
    task_id = f"shared-{uuid.uuid4().hex[:8]}"
    store.append(_row(task_id=task_id, cost_idr=800))
    try:
        assert task_budget_remaining("user-1", task_id) == 1200
    finally:
        store.rows[:] = [r for r in store.rows if r.task_id != task_id]


def test_battery_view_7d_totals_shares_and_cap():
    rows = [
        _row(bot_id="bot-a", trigger="morning-briefing", cost_idr=1000),
        _row(bot_id="bot-a", trigger="morning-briefing", cost_idr=500),
        _row(bot_id="bot-b", trigger="evening-summary", cost_idr=500),
        # 8 days old: outside the 7-day window.
        _row(
            bot_id="bot-b",
            trigger="evening-summary",
            cost_idr=9999,
            recorded_at=NOW - timedelta(days=8),
        ),
    ]
    view = battery_view(rows, now=NOW, cap_idr=1500)
    assert view["window_days"] == 7
    assert view["total_idr"] == 2000
    assert view["turns"] == 3
    assert view["over_cap"] is True
    assert view["per_bot"][0] == {"key": "bot-a", "total_idr": 1500, "share": 0.75}
    assert view["per_bot"][1]["key"] == "bot-b"
    assert view["per_routine"][0]["key"] == "morning-briefing"


def test_battery_view_empty_is_zero_not_crash():
    view = battery_view([], now=NOW)
    assert view["total_idr"] == 0
    assert view["per_bot"] == []
    assert view["over_cap"] is False


def test_forecast_routines_frequent_and_costly():
    rows = [_row(trigger="hourly-check", cost_idr=100) for _ in range(6)]
    rows.append(_row(trigger="video-render", cost_idr=5000))
    rows.append(_row(trigger="video-render", cost_idr=5000))
    rows.append(_row(trigger="rare-cheap", cost_idr=10))
    forecast = forecast_routines(rows)
    by_routine = {item["routine"]: item for item in forecast}
    assert by_routine["hourly-check"]["flags"] == ["frequent"]
    assert by_routine["hourly-check"]["runs"] == 6
    assert by_routine["video-render"]["flags"] == ["costly"]
    assert by_routine["video-render"]["avg_idr"] == 5000
    assert "rare-cheap" not in by_routine
    # Most expensive first.
    assert forecast[0]["routine"] == "video-render"


def test_upsell_due_thresholds_and_snooze():
    assert upsell_due(bots=2, messages=10, routines=1)["due"] is False
    assert upsell_due(bots=3, messages=0, routines=0)["due"] is True
    assert upsell_due(bots=0, messages=50, routines=0)["due"] is True
    assert upsell_due(bots=0, messages=0, routines=3)["due"] is True
    snoozed = upsell_due(
        bots=3, messages=0, routines=0, snoozed_until=NOW + timedelta(days=5), now=NOW
    )
    assert snoozed["due"] is False
    assert snoozed["snoozed"] is True
    expired_snooze = upsell_due(
        bots=3, messages=0, routines=0, snoozed_until=NOW - timedelta(days=1), now=NOW
    )
    assert expired_snooze["due"] is True
    due = upsell_due(bots=3, messages=0, routines=0, now=NOW)
    assert due["snooze_until"] == (NOW + timedelta(days=10)).isoformat()


def test_postgres_store_round_trip():
    import psycopg

    create_schema(DSN)
    user_id = f"pg-user-{uuid.uuid4().hex[:8]}"
    task_id = f"pg-task-{uuid.uuid4().hex[:8]}"
    store = PostgresUsageStore(DSN)
    try:
        store.append(_row(user_id=user_id, task_id=task_id, cost_idr=400))
        store.append(_row(user_id=user_id, task_id=task_id, cost_idr=600, bot_id="bot-b"))
        rows = store.rows_for_task(user_id, task_id)
        assert len(rows) == 2
        assert sum(r.cost_idr for r in rows) == 1000
        assert rows[0].currency == "IDR"
        assert rows[0].tools == ["read", "write"]
        assert task_budget_remaining(user_id, task_id, dsn=DSN) == 1000
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM billing_usage_rows WHERE user_id = %s", (user_id,))
