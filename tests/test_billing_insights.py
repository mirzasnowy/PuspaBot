"""Tests: cost/token insights over the usage ledger (G28).

Mostly pure-function tests over ``UsageRow`` lists (no database);
one Postgres round-trip test uses its own rows for a fresh user and
deletes them afterwards, like the ledger tests do -- conftest does
not truncate ``billing_usage_rows``.
"""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from puspa.billing.insights import (
    MAX_DAYS,
    format_idr,
    format_rate_per_1k_tokens,
    insights_for_user,
    insights_summary,
    validate_days,
)
from puspa.billing.ledger import MemoryUsageStore, PostgresUsageStore, UsageRow, create_schema

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


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


def test_validate_days_bounds():
    assert validate_days(1) == 1
    assert validate_days(MAX_DAYS) == MAX_DAYS
    with pytest.raises(ValueError):
        validate_days(0)
    with pytest.raises(ValueError):
        validate_days(MAX_DAYS + 1)
    with pytest.raises(ValueError):
        validate_days("7")


def test_format_idr_is_exact_never_approximate():
    assert format_idr(0) == "Rp0"
    assert format_idr(500) == "Rp500"
    assert format_idr(1500) == "Rp1.500"
    assert format_idr(1234567) == "Rp1.234.567"
    assert "~" not in format_idr(1)


def test_rate_label_four_decimals_and_none_without_tokens():
    assert format_rate_per_1k_tokens(500, 1000) == "Rp500.0000 / 1k tokens"
    assert format_rate_per_1k_tokens(1500, 3000) == "Rp500.0000 / 1k tokens"
    assert format_rate_per_1k_tokens(1, 1000) == "Rp1.0000 / 1k tokens"
    assert format_rate_per_1k_tokens(500, 0) is None
    assert "~" not in (format_rate_per_1k_tokens(1, 100000) or "")


def test_summary_totals_tokens_cost_and_window():
    rows = [
        _row(cost_idr=1000),
        _row(bot_id="bot-b", model="pricey-model", cost_idr=500, task_id="task-2"),
        # 8 days old: outside the 7-day window.
        _row(cost_idr=9999, recorded_at=NOW - timedelta(days=8)),
    ]
    report = insights_summary(rows, days=7, now=NOW)
    assert report["window_days"] == 7
    assert report["turns"] == 2
    assert report["tokens"] == {"input": 2000, "output": 400, "cached": 200, "total": 2600}
    assert report["cost"]["total_idr"] == 1500
    assert report["cost"]["label"] == "Rp1.500"
    assert report["cost"]["avg_per_turn_idr"] == 750
    assert report["cost"]["per_1k_tokens_label"] == "Rp576.9231 / 1k tokens"
    assert report["task_cost_ceiling_idr"] == 2000


def test_summary_empty_rows_is_honest_zeros():
    report = insights_summary([], days=7, now=NOW)
    assert report["turns"] == 0
    assert report["tokens"]["total"] == 0
    assert report["cost"]["total_idr"] == 0
    assert report["cost"]["label"] == "Rp0"
    assert report["cost"]["per_1k_tokens_idr"] is None
    assert report["cost"]["per_1k_tokens_label"] is None
    assert report["per_model"] == []
    assert report["tool_usage"] == []
    assert report["top_costly_tasks"] == []
    assert len(report["activity_per_day"]) == 7
    assert all(day["turns"] == 0 for day in report["activity_per_day"])


def test_summary_breakdowns_tools_modules_and_top_tasks():
    rows = [
        _row(model="cheap-model", driver="openrouter", cost_idr=1000, tools=["read"]),
        _row(model="cheap-model", driver="openrouter", cost_idr=500, tools=["read", "write"]),
        _row(
            model="pricey-model",
            driver="local",
            cost_idr=2000,
            tools=["write"],
            task_id="task-2",
            module_id="billing",
            tool_errors=2,
        ),
        _row(
            task_id="", cost_idr=0, tools=[], module_id="other"
        ),  # no task: excluded from top tasks
    ]
    report = insights_summary(rows, days=7, now=NOW)
    assert [(entry["model"], entry["cost_idr"]) for entry in report["per_model"]] == [
        ("pricey-model", 2000),
        ("cheap-model", 1500),
    ]
    assert report["per_model"][0]["share"] == pytest.approx(2000 / 3500)
    assert [(entry["driver"], entry["cost_idr"]) for entry in report["per_driver"]] == [
        ("local", 2000),
        ("openrouter", 1500),
    ]
    assert report["tool_usage"] == [
        {"tool": "read", "uses": 2},
        {"tool": "write", "uses": 2},
    ]
    assert report["module_usage"] == [
        {"module_id": "billing", "turns": 1, "cost_idr": 2000},
        {"module_id": "briefing", "turns": 2, "cost_idr": 1500},
        {"module_id": "other", "turns": 1, "cost_idr": 0},
    ]
    assert [task["task_id"] for task in report["top_costly_tasks"]] == ["task-2", "task-1"]
    assert report["top_costly_tasks"][0]["cost_idr"] == 2000
    assert report["tool_errors"] == 2


def test_summary_activity_per_day_covers_every_day():
    rows = [
        _row(cost_idr=100, recorded_at=NOW - timedelta(days=6)),
        _row(cost_idr=200, recorded_at=NOW),
        _row(cost_idr=300, recorded_at=NOW),
    ]
    report = insights_summary(rows, days=7, now=NOW)
    days = report["activity_per_day"]
    assert len(days) == 7
    assert days[0] == {"date": "2026-11-12", "turns": 1, "tokens": 1300, "cost_idr": 100}
    assert days[-1] == {"date": "2026-11-18", "turns": 2, "tokens": 2600, "cost_idr": 500}
    assert days[3]["turns"] == 0  # a quiet day is a zero, not a gap


def test_insights_for_user_memory_store():
    store = MemoryUsageStore()
    store.append(_row(user_id="user-9", cost_idr=700))
    store.append(_row(user_id="user-9", cost_idr=300, recorded_at=NOW - timedelta(days=30)))
    store.append(_row(user_id="other", cost_idr=9999))
    report = insights_for_user("user-9", days=7, store=store, now=NOW)
    assert report["turns"] == 1
    assert report["cost"]["total_idr"] == 700


def test_insights_for_user_postgres_round_trip(make_user, test_dsn):
    user_id = make_user()["user_id"]
    create_schema(test_dsn)
    store = PostgresUsageStore(test_dsn)
    try:
        store.append(_row(user_id=user_id, cost_idr=800))
        store.append(_row(user_id=user_id, cost_idr=400, task_id="task-2"))
        store.append(_row(user_id=user_id, cost_idr=9999, recorded_at=NOW - timedelta(days=8)))
        report = insights_for_user(user_id, days=7, dsn=test_dsn, now=NOW)
        assert report["turns"] == 2
        assert report["cost"]["total_idr"] == 1200
        assert report["tokens"]["total"] == 2600
    finally:
        with psycopg.connect(test_dsn) as conn:
            conn.execute("DELETE FROM billing_usage_rows WHERE user_id = %s", (user_id,))
            conn.commit()


def test_insights_for_user_rejects_bad_days():
    with pytest.raises(ValueError):
        insights_for_user("user-1", days=0, store=MemoryUsageStore())
    with pytest.raises(ValueError):
        insights_for_user("user-1", days=91, store=MemoryUsageStore())
