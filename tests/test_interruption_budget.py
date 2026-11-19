"""Tests for the interruption budget (Kosmos K10).

Three layers, matching the wiring:

- Pure budget rules (in-memory, deterministic, no database):
  costs, value bars, the scarcity rule, the empty-budget
  silence, the daily reset, and the audit log.
- Scheduler wiring (Postgres, ``puspa_test``): proactive
  triggers pay from the budget in ``tick`` / ``run_trigger`` /
  ``run_missed``; a denied trigger stays due and is retried
  later; silent system triggers never pay.
- Gateway wiring (no network, ``httpx.MockTransport``):
  ``GatewayRunner.notify`` sends nothing once the budget
  refuses, and still sends when the budget allows.

The repo-wide conftest still runs its Postgres reset fixture
for this file; only the scheduler section touches the
database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest

from puspa.channels.agent import TaskRunnerAgent
from puspa.channels.authz import Allowlist
from puspa.channels.config import ChannelConfig
from puspa.channels.run import GatewayRunner
from puspa.proactivity.budget import (
    DEFAULT_DAILY_BUDGET,
    HIGH_VALUE_THRESHOLD,
    INITIATIVE_KINDS,
    LOW_REMAINING_THRESHOLD,
    Decision,
    Initiative,
    InterruptionBudget,
    initiative_from,
)
from puspa.scheduler.scheduler import (
    PROACTIVE_TRIGGER_TYPES,
    create_trigger,
    run_missed,
    tick,
)

DAY = "2026-11-19"
OTHER_DAY = "2026-11-20"
NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


# --- pure budget: defaults and validation -----------------------------------


def test_default_budget_is_five_units():
    budget = InterruptionBudget()
    assert budget.daily_budget == DEFAULT_DAILY_BUDGET == 5
    assert budget.remaining(DAY) == 5
    assert budget.spent(DAY) == 0
    assert budget.spend_log(DAY) == []
    assert budget.summary(DAY) == {
        "day": DAY,
        "daily_budget": 5,
        "spent": 0,
        "remaining": 5,
        "allowed": 0,
        "denied": 0,
        "denied_by_reason": {},
    }


def test_unknown_kind_and_bad_value_are_rejected():
    with pytest.raises(ValueError):
        Initiative(kind="shout", value=0.9)
    with pytest.raises(ValueError):
        Initiative(kind="bill_due", value=1.5)
    with pytest.raises(ValueError):
        Initiative(kind="bill_due", value=-0.1)
    with pytest.raises(ValueError):
        InterruptionBudget(daily_budget=-1)
    with pytest.raises(ValueError):
        InterruptionBudget(high_value_threshold=1.5)
    with pytest.raises(ValueError):
        _ = InterruptionBudget().remaining("not-a-date")


def test_initiative_from_dict_and_instance():
    made = initiative_from({"kind": "reminder", "value": 0.7, "label": "t-1"})
    assert made == Initiative(kind="reminder", value=0.7, label="t-1")
    assert initiative_from(made) is made
    with pytest.raises(ValueError):
        initiative_from({"kind": "reminder"})
    with pytest.raises(TypeError):
        initiative_from("reminder")


def test_kind_costs_are_the_documented_table():
    assert INITIATIVE_KINDS["urgent_alert"].cost == 1
    assert INITIATIVE_KINDS["bill_due"].cost == 1
    assert INITIATIVE_KINDS["price_drop"].cost == 2
    assert INITIATIVE_KINDS["reminder"].cost == 1
    assert INITIATIVE_KINDS["digest"].cost == 1
    assert INITIATIVE_KINDS["suggestion"].cost == 2
    assert INITIATIVE_KINDS["silent_action"].cost == 0


# --- pure budget: allow / deny rules ----------------------------------------


def test_value_below_kind_minimum_is_denied_on_a_full_budget():
    budget = InterruptionBudget()
    decision = budget.try_spend(Initiative(kind="suggestion", value=0.5), day=DAY)
    assert decision.allowed is False
    assert decision.reason == "value_below_threshold"
    assert decision.cost == 0
    assert budget.remaining(DAY) == 5  # a denial spends nothing
    assert budget.spent(DAY) == 0


def test_allowed_spend_deducts_the_kind_cost():
    budget = InterruptionBudget()
    decision = budget.try_spend(Initiative(kind="price_drop", value=0.9), day=DAY)
    assert decision.allowed is True
    assert decision.reason == "allowed"
    assert decision.cost == 2
    assert decision.remaining_after == 3
    assert budget.remaining(DAY) == 3
    assert budget.spent(DAY) == 2


def test_empty_budget_means_silence_for_every_kind():
    budget = InterruptionBudget()
    assert budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY).allowed is True
    assert budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY).allowed is True
    assert budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY).allowed is True
    assert budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY).allowed is True
    assert budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY).allowed is True
    assert budget.remaining(DAY) == 0
    for kind, value in (
        ("urgent_alert", 1.0),
        ("bill_due", 1.0),
        ("price_drop", 1.0),
        ("suggestion", 1.0),
    ):
        decision = budget.try_spend(Initiative(kind=kind, value=value), day=DAY)
        assert decision.allowed is False
        assert decision.reason == "budget_empty"
    assert budget.remaining(DAY) == 0


def test_initiative_that_does_not_fit_in_what_is_left_is_denied():
    budget = InterruptionBudget()
    budget.try_spend(Initiative(kind="price_drop", value=0.9), day=DAY)  # -2, left 3
    budget.try_spend(Initiative(kind="price_drop", value=0.9), day=DAY)  # -2, left 1
    assert budget.remaining(DAY) == 1
    decision = budget.try_spend(Initiative(kind="price_drop", value=0.9), day=DAY)
    assert decision.allowed is False
    assert decision.reason == "insufficient_budget"
    # A cheaper initiative still fits in the last unit.
    assert budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY).allowed is True
    assert budget.remaining(DAY) == 0


def test_scarcity_suppresses_low_value_first_but_keeps_high_value():
    budget = InterruptionBudget()
    # Spend down to the scarcity line: 5 - 2 (price drop) - 1 (bill) = 2 left.
    assert budget.try_spend(Initiative(kind="price_drop", value=0.9), day=DAY).allowed is True
    assert budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY).allowed is True
    assert budget.remaining(DAY) == LOW_REMAINING_THRESHOLD == 2
    # A mid-value price drop is affordable (cost 2) but suppressed:
    # the last units are reserved for what matters most.
    denied = budget.try_spend(Initiative(kind="price_drop", value=0.7), day=DAY)
    assert denied.allowed is False
    assert denied.reason == "low_value_when_scarce"
    assert budget.remaining(DAY) == 2
    # A high-value bill still passes while the budget is scarce.
    allowed = budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY)
    assert allowed.allowed is True
    assert budget.remaining(DAY) == 1
    # Below the high-value bar is still suppressed at 1 unit left.
    denied = budget.try_spend(Initiative(kind="reminder", value=0.8), day=DAY)
    assert denied.allowed is False
    assert denied.reason == "low_value_when_scarce"
    # Exactly at the bar passes.
    assert (
        budget.try_spend(
            Initiative(kind="urgent_alert", value=HIGH_VALUE_THRESHOLD), day=DAY
        ).allowed
        is True
    )
    assert budget.remaining(DAY) == 0


def test_budget_suppresses_spam_but_allows_high_value():
    """The acceptance test in miniature: a flood of low-value
    suggestions is capped, and one high-value alert still gets
    through afterwards."""
    budget = InterruptionBudget()
    allowed = denied = 0
    for n in range(10):
        decision = budget.try_spend(
            Initiative(kind="suggestion", value=0.85, label=f"sug-{n}"), day=DAY
        )
        if decision.allowed:
            allowed += 1
        else:
            denied += 1
    # Each suggestion costs 2: two pass (5 -> 3 -> 1), the rest
    # are denied (the third does not fit, the rest hit the
    # scarcity bar or the empty budget).
    assert allowed == 2
    assert denied == 8
    assert budget.remaining(DAY) == 1
    # One high-value alert still fits in the last unit.
    assert budget.try_spend(Initiative(kind="urgent_alert", value=0.95), day=DAY).allowed is True
    assert budget.remaining(DAY) == 0
    # And now even the highest value is silenced.
    assert (
        budget.try_spend(Initiative(kind="urgent_alert", value=1.0), day=DAY).reason
        == "budget_empty"
    )


def test_silent_action_never_pays_and_never_gated():
    budget = InterruptionBudget()
    for _ in range(5):
        budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY)
    assert budget.remaining(DAY) == 0
    decision = budget.try_spend(Initiative(kind="silent_action", value=0.0), day=DAY)
    assert decision.allowed is True
    assert decision.cost == 0
    assert budget.remaining(DAY) == 0


# --- pure budget: days, log, summary ----------------------------------------


def test_budget_resets_every_day_and_days_are_independent():
    budget = InterruptionBudget()
    budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY)
    budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY)
    assert budget.remaining(DAY) == 3
    assert budget.remaining(OTHER_DAY) == 5
    budget.try_spend(Initiative(kind="bill_due", value=0.9), day=OTHER_DAY)
    assert budget.remaining(OTHER_DAY) == 4
    assert budget.remaining(DAY) == 3  # yesterday is untouched


def test_spend_log_audits_allowed_and_denied():
    budget = InterruptionBudget()
    budget.try_spend(Initiative(kind="price_drop", value=0.9, label="p-1"), day=DAY)
    budget.try_spend(Initiative(kind="suggestion", value=0.5, label="s-1"), day=DAY)
    log = budget.spend_log(DAY)
    assert [r.kind for r in log] == ["price_drop", "suggestion"]
    assert [r.allowed for r in log] == [True, False]
    assert [r.reason for r in log] == ["allowed", "value_below_threshold"]
    assert log[0].label == "p-1" and log[0].cost == 2
    assert log[1].label == "s-1" and log[1].cost == 2  # the would-be cost is kept
    assert log[0].to_dict() == {
        "day": DAY,
        "kind": "price_drop",
        "label": "p-1",
        "value": 0.9,
        "cost": 2,
        "allowed": True,
        "reason": "allowed",
    }
    summary = budget.summary(DAY)
    assert summary["allowed"] == 1
    assert summary["denied"] == 1
    assert summary["denied_by_reason"] == {"value_below_threshold": 1}
    assert summary["spent"] == 2 and summary["remaining"] == 3


def test_decision_to_dict_round_trip():
    decision = InterruptionBudget().try_spend(Initiative(kind="digest", value=0.6), day=DAY)
    assert isinstance(decision, Decision)
    assert decision.to_dict() == {
        "allowed": True,
        "reason": "allowed",
        "kind": "digest",
        "value": 0.6,
        "cost": 1,
        "remaining_after": 4,
        "day": DAY,
    }


def test_reset_day_restores_the_full_budget():
    budget = InterruptionBudget()
    budget.try_spend(Initiative(kind="bill_due", value=0.9), day=DAY)
    assert budget.reset_day(DAY) == 1
    assert budget.remaining(DAY) == 5
    assert budget.spend_log(DAY) == []


# --- scheduler wiring ---------------------------------------------------------


def _trigger_rows(test_dsn, trigger_id):
    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT next_trigger_at FROM triggers WHERE id = %s", (trigger_id,))
        trigger_row = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM trigger_runs WHERE trigger_id = %s", (trigger_id,))
        run_count = cur.fetchone()[0]
    return trigger_row, run_count


def test_scheduler_proactive_trigger_pays_and_stays_due_when_denied(test_dsn, make_user):
    user = make_user()
    budget = InterruptionBudget()
    due = NOW - timedelta(minutes=5)
    trigger = create_trigger(user["user_id"], "price_drop", due, dsn=test_dsn)
    # Two price drops cost 2 each: the first two ticks run, the
    # third is denied (1 unit left does not fit cost 2).
    assert tick(sink=None, dsn=test_dsn, now=NOW, budget=budget) != []
    assert budget.remaining(NOW.date()) == 3
    # The trigger advanced one day, so create a second one due now.
    trigger2 = create_trigger(user["user_id"], "price_drop", due, dsn=test_dsn)
    assert tick(sink=None, dsn=test_dsn, now=NOW, budget=budget) != []
    assert budget.remaining(NOW.date()) == 1
    trigger3 = create_trigger(user["user_id"], "price_drop", due, dsn=test_dsn)
    assert tick(sink=None, dsn=test_dsn, now=NOW, budget=budget) == []
    assert budget.remaining(NOW.date()) == 1
    # The denied trigger was NOT run and NOT advanced: it stays due.
    row, run_count = _trigger_rows(test_dsn, trigger3.id)
    assert run_count == 0
    assert row is not None and row[0] == trigger3.next_trigger_at
    # The denial is in the budget's audit log, not in the ledger.
    reasons = [r.reason for r in budget.spend_log(NOW.date())]
    assert reasons == ["allowed", "allowed", "insufficient_budget"]
    assert trigger.id and trigger2.id  # created triggers exist


def test_scheduler_scarcity_lets_bill_through_but_not_price_drop(test_dsn, make_user):
    user = make_user()
    budget = InterruptionBudget()
    due = NOW - timedelta(minutes=5)
    # Spend down to the scarcity line with one price drop (2)
    # and one bill (1): 2 units left.
    budget.try_spend(Initiative(kind="price_drop", value=0.9), day=NOW.date())
    budget.try_spend(Initiative(kind="bill_due", value=0.9), day=NOW.date())
    assert budget.remaining(NOW.date()) == 2
    price = create_trigger(user["user_id"], "price_drop", due, dsn=test_dsn)
    bill = create_trigger(user["user_id"], "due_date_bill", due, dsn=test_dsn)
    events: list[dict] = []

    def sink(event_type, content):
        events.append({"type": event_type, "content": content})

    runs = tick(sink=sink, dsn=test_dsn, now=NOW, budget=budget)
    # Only the bill ran: its assessed value (0.85) clears the
    # scarcity bar; the price drop (0.70) does not.
    assert [r["trigger_id"] for r in runs] == [bill.id]
    assert budget.remaining(NOW.date()) == 1
    _, price_runs = _trigger_rows(test_dsn, price.id)
    assert price_runs == 0
    assert [e["type"] for e in events] == ["trigger_fired"]
    assert PROACTIVE_TRIGGER_TYPES["price_drop"] == ("price_drop", 0.70)
    assert PROACTIVE_TRIGGER_TYPES["due_date_bill"] == ("bill_due", 0.85)


def test_scheduler_silent_and_user_triggers_never_pay(test_dsn, make_user):
    user = make_user()
    budget = InterruptionBudget()
    # Empty the budget completely first.
    for _ in range(5):
        budget.try_spend(Initiative(kind="bill_due", value=0.9), day=NOW.date())
    assert budget.remaining(NOW.date()) == 0
    due = NOW - timedelta(minutes=5)
    schedule = create_trigger(user["user_id"], "schedule", due, dsn=test_dsn)
    backup = create_trigger(user["user_id"], "backup_daily", due, dsn=test_dsn)
    runs = tick(sink=None, dsn=test_dsn, now=NOW, budget=budget)
    # Both still run: a user schedule and a silent system job
    # never pay from the interruption budget.
    assert {r["trigger_id"] for r in runs} == {schedule.id, backup.id}
    assert budget.remaining(NOW.date()) == 0
    assert budget.spend_log(NOW.date()) != []  # only the setup spends


def test_scheduler_no_budget_means_no_gating(test_dsn, make_user):
    user = make_user()
    due = NOW - timedelta(minutes=5)
    trigger = create_trigger(user["user_id"], "price_drop", due, dsn=test_dsn)
    # Without a budget the old behaviour is unchanged.
    assert tick(sink=None, dsn=test_dsn, now=NOW) != []
    _, run_count = _trigger_rows(test_dsn, trigger.id)
    assert run_count == 1


def test_scheduler_missed_proactive_trigger_is_gated_too(test_dsn, make_user):
    user = make_user()
    budget = InterruptionBudget()
    for _ in range(5):
        budget.try_spend(Initiative(kind="bill_due", value=0.9), day=NOW.date())
    assert budget.remaining(NOW.date()) == 0
    old = NOW - timedelta(hours=3)
    trigger = create_trigger(user["user_id"], "price_drop", old, dsn=test_dsn)
    assert run_missed(sink=None, dsn=test_dsn, now=NOW, budget=budget) == []
    _, run_count = _trigger_rows(test_dsn, trigger.id)
    assert run_count == 0


# --- gateway wiring -----------------------------------------------------------


def _telegram_client():
    def handler(request):
        if "sendMessage" in str(request.url):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"message_id": 7, "chat": {"id": 111}}},
            )
        return httpx.Response(404, json={"ok": False})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _runner(tmp_path, **overrides):
    profile = f"test-{uuid.uuid4().hex[:8]}"
    config = ChannelConfig(
        telegram_bot_token="tok",
        whatsapp_access_token="",
        whatsapp_phone_number_id="555",
        allowlist=Allowlist.parse("telegram:111"),
        state_dir=str(tmp_path),
    )
    kwargs = {
        "profile": profile,
        "config": config,
        "agent": TaskRunnerAgent(),
        "http_client": _telegram_client(),
    }
    kwargs.update(overrides)
    return GatewayRunner.open(**kwargs)


def test_gateway_notify_sends_when_budget_allows(tmp_path):
    runner = _runner(tmp_path)
    try:
        record = runner.notify("telegram", "111", initiative={"kind": "bill_due", "value": 0.9})
        assert record is not None
        assert record.status == "sent"
        assert runner.budget.remaining() == 4
    finally:
        runner.close()


def test_gateway_notify_sends_nothing_once_budget_is_empty(tmp_path):
    budget = InterruptionBudget()
    for _ in range(5):
        budget.try_spend(Initiative(kind="bill_due", value=0.9))
    runner = _runner(tmp_path, budget=budget)
    try:
        assert (
            runner.notify("telegram", "111", initiative={"kind": "bill_due", "value": 1.0}) is None
        )
        assert (
            runner.notify("telegram", "111", initiative={"kind": "urgent_alert", "value": 1.0})
            is None
        )
        # No delivery was recorded for the refused pings.
        assert runner.ledger.list_for_chat("telegram", "111") == []
        reasons = [r.reason for r in budget.spend_log()]
        assert reasons[-2:] == ["budget_empty", "budget_empty"]
    finally:
        runner.close()


def test_gateway_notify_without_initiative_is_ungated(tmp_path):
    runner = _runner(tmp_path)
    try:
        record = runner.notify("telegram", "111")
        assert record is not None
        assert record.status == "sent"
        assert runner.budget.remaining() == 5
    finally:
        runner.close()
