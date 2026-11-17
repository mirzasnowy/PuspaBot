"""Tes pemicu (§2.12 + §2.14): 4 pemicu MVP, tolak email/webhook."""

from datetime import date, datetime, timezone

import pytest

from puspa.workflow.triggers import (
    TriggerError, match_price_drop, match_due_date, next_trigger_at,
    save_to_scheduler, validate_trigger,
)

SEKARANG = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


def test_schedule_date_monthly():
    p = {"type": "schedule", "mode": "date", "date": 25, "start_time": "07:00"}
    assert validate_trigger(p) == []
    next_time = next_trigger_at(p, SEKARANG)
    assert (next_time.year, next_time.month, next_time.day) == (2026, 11, 25)
    assert (next_time.hour, next_time.minute) == (7, 0)


def test_schedule_date_past_this_month_to_next_month():
    p = {"type": "schedule", "mode": "date", "date": 10, "start_time": "07:00"}
    next_time = next_trigger_at(p, SEKARANG)
    assert (next_time.year, next_time.month, next_time.day) == (2026, 12, 10)


def test_schedule_day_weekly():
    p = {"type": "schedule", "mode": "day", "day": "senin", "start_time": "07:00"}
    next_time = next_trigger_at(p, SEKARANG)  # 17 Nov 2026 = Selasa
    assert next_time.weekday() == 0
    assert (next_time.year, next_time.month, next_time.day) == (2026, 11, 23)


def test_schedule_interval_minimal_1_hour():
    p = {"type": "schedule", "mode": "interval_hour", "hour": 0.5}
    assert any("minimal 1 jam" in m for m in validate_trigger(p))
    p2 = {"type": "schedule", "mode": "interval_hour", "hour": 1}
    assert validate_trigger(p2) == []
    assert next_trigger_at(p2, SEKARANG) == datetime(
        2026, 11, 17, 13, 0, tzinfo=timezone.utc)


def test_trigger_email_and_webhook_rejected():
    for event_type in ("email_login", "webhook"):
        problems = validate_trigger({"type": event_type})
        assert problems and "sesudah MVP" in problems[0]


def test_due_date_n_day_before():
    p = {"type": "due_date", "days_before": 3, "provider": "PLN"}
    assert validate_trigger(p) == []
    bill = [
        {"provider": "PLN", "due_date": "2026-11-20", "status": "not_yet_pay"},
        {"provider": "PLN", "due_date": "2026-11-30", "status": "not_yet_pay"},
        {"provider": "PLN", "due_date": "2026-11-19", "status": "already_pay"},
        {"provider": "PDAM", "due_date": "2026-11-19", "status": "not_yet_pay"},
    ]
    match = match_due_date(p, bill, today=date(2026, 11, 17))
    assert len(match) == 1 and match[0]["due_date"] == "2026-11-20"


def test_price_drops_below_threshold():
    p = {"type": "price_drop", "product_id": "hp-x", "price_threshold": 5000000}
    assert validate_trigger(p) == []
    assert match_price_drop(p, 4999999) is True
    assert match_price_drop(p, 5000000) is True
    assert match_price_drop(p, 5000001) is False
    assert match_price_drop(p, None) is False  # belum ada data: tidak memicu


def test_save_trigger_schedule_to_scheduler_phase2(make_user, test_dsn):
    user_id = make_user()["user_id"]
    p = {"type": "schedule", "mode": "date", "date": 25, "start_time": "07:00"}
    trigger = save_to_scheduler(user_id=user_id, workflow_id="alur-uji",
                                 trigger=p, now=SEKARANG, dsn=test_dsn)
    assert trigger is not None
    assert trigger.event_type == "schedule"
    assert trigger.workflow == "alur-uji"
    assert (trigger.next_trigger_at.year, trigger.next_trigger_at.month,
            trigger.next_trigger_at.day) == (2026, 11, 25)


def test_non_schedule_trigger_not_stored_in_scheduler():
    assert save_to_scheduler(user_id="u", workflow_id="a",
                               trigger={"type": "manual"}) is None
