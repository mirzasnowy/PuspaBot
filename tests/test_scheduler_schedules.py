"""Tests for the explicit schedule union (schedules.py).

In-memory: no database, no network.
"""

from datetime import datetime, timezone

import pytest

from puspa.scheduler.schedules import (
    RoutineScheduleInput,
    ScheduleError,
    is_valid_timezone,
    merge_schedule,
    next_occurrence,
    parse_cron_expression,
    schedule_from_record,
    validate_schedule,
)

NOW = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)  # Wednesday


# --- validation -----------------------------------------------------------

def test_valid_schedules_pass():
    assert validate_schedule({"type": "once", "at": "2026-11-20T07:00:00+00:00",
                              "timezone": "Asia/Jakarta"}) == []
    assert validate_schedule({"type": "daily", "time": "07:00",
                              "weekdays": [0, 2, 4],
                              "timezone": "UTC"}) == []
    assert validate_schedule({"type": "daily", "time": "07:00"}) == []
    assert validate_schedule({"type": "interval", "seconds": 3600,
                              "timezone": "UTC"}) == []
    assert validate_schedule({"type": "cron", "expression": "0 9 * * 1-5",
                              "timezone": "UTC"}) == []


def test_unknown_type_and_timezone_rejected():
    assert validate_schedule({"type": "weekly"}) != []
    assert any("timezone" in p for p in validate_schedule(
        {"type": "daily", "time": "07:00", "timezone": "Mars/Olympus"}))
    # Empty timezone falls back to UTC (reader default), not an error.
    assert validate_schedule({"type": "daily", "time": "07:00",
                              "timezone": ""}) == []


def test_field_problems_reported():
    assert validate_schedule({"type": "once", "at": "tomorrow"}) != []
    assert validate_schedule({"type": "once", "at": "2026-11-20T07:00:00"}) != []
    assert validate_schedule({"type": "daily", "time": "7:00"}) != []
    assert validate_schedule({"type": "daily", "time": "07:00",
                              "weekdays": [7]}) != []
    assert validate_schedule({"type": "daily", "time": "07:00",
                              "weekdays": [1, 1]}) != []
    assert validate_schedule({"type": "interval", "seconds": 30}) != []
    assert validate_schedule({"type": "interval", "seconds": "3600"}) != []
    assert validate_schedule({"type": "cron", "expression": "0 9 * *"}) != []
    assert validate_schedule("not a dict") != []


def test_is_valid_timezone():
    assert is_valid_timezone("Asia/Jakarta") is True
    assert is_valid_timezone("UTC") is True
    assert is_valid_timezone("Not/AZone") is False
    assert is_valid_timezone("") is False
    assert is_valid_timezone(None) is False


# --- legacy records ---------------------------------------------------------

def test_schedule_from_record_defaults_timezone_and_weekdays():
    record = {"type": "daily", "time": "07:00"}
    schedule = schedule_from_record(record)
    assert schedule["timezone"] == "UTC"
    assert schedule["weekdays"] == []
    assert validate_schedule(schedule) == []


def test_schedule_from_record_keeps_existing_timezone():
    schedule = schedule_from_record(
        {"type": "once", "at": "2026-11-20T07:00:00+07:00",
         "timezone": "Asia/Jakarta"})
    assert schedule["timezone"] == "Asia/Jakarta"


def test_schedule_from_record_rejects_non_schedules():
    assert schedule_from_record({"type": "weekly"}) is None
    assert schedule_from_record("daily") is None
    assert schedule_from_record(None) is None


# --- next occurrence --------------------------------------------------------

def test_once_next_occurrence():
    schedule = {"type": "once", "at": "2026-11-20T07:00:00+00:00",
                "timezone": "UTC"}
    assert next_occurrence(schedule, NOW) == datetime(
        2026, 11, 20, 7, 0, tzinfo=timezone.utc)
    assert next_occurrence(schedule, datetime(
        2026, 11, 21, tzinfo=timezone.utc)) is None


def test_daily_next_occurrence_same_day_and_next_day():
    schedule = {"type": "daily", "time": "07:00", "timezone": "UTC"}
    assert next_occurrence(schedule, datetime(
        2026, 11, 18, 6, 0, tzinfo=timezone.utc)) == datetime(
        2026, 11, 18, 7, 0, tzinfo=timezone.utc)
    assert next_occurrence(schedule, NOW) == datetime(
        2026, 11, 19, 7, 0, tzinfo=timezone.utc)


def test_daily_weekdays_skip_to_next_matching_day():
    # NOW is Wednesday; weekdays [0] = Mondays only.
    schedule = {"type": "daily", "time": "07:00", "weekdays": [0],
                "timezone": "UTC"}
    assert next_occurrence(schedule, NOW) == datetime(
        2026, 11, 23, 7, 0, tzinfo=timezone.utc)


def test_daily_timezone_is_applied():
    # 07:00 Asia/Jakarta = 00:00 UTC.
    schedule = {"type": "daily", "time": "07:00",
                "timezone": "Asia/Jakarta"}
    assert next_occurrence(schedule, datetime(
        2026, 11, 18, 1, 0, tzinfo=timezone.utc)) == datetime(
        2026, 11, 19, 0, 0, tzinfo=timezone.utc)


def test_interval_next_occurrence():
    schedule = {"type": "interval", "seconds": 3600, "timezone": "UTC"}
    assert next_occurrence(schedule, NOW) == datetime(
        2026, 11, 18, 11, 0, tzinfo=timezone.utc)


def test_cron_next_occurrence():
    schedule = {"type": "cron", "expression": "0 9 * * 1-5",
                "timezone": "UTC"}
    assert next_occurrence(schedule, NOW) == datetime(
        2026, 11, 19, 9, 0, tzinfo=timezone.utc)


def test_cron_expression_parsing():
    minute, hour, dom, month, dow = parse_cron_expression("*/15 9 * * 0,6")
    assert minute == frozenset({0, 15, 30, 45})
    assert hour == frozenset({9})
    assert 0 in dow and 6 in dow
    with pytest.raises(ScheduleError):
        parse_cron_expression("0 9 * *")
    with pytest.raises(ScheduleError):
        parse_cron_expression("61 9 * * *")


def test_next_occurrence_rejects_invalid_schedule():
    with pytest.raises(ScheduleError):
        next_occurrence({"type": "weekly"}, NOW)


# --- merge ------------------------------------------------------------------

def test_merge_keeps_old_fields_and_overrides_new_ones():
    old = {"type": "daily", "time": "07:00", "weekdays": [0, 1],
           "timezone": "UTC"}
    merged = merge_schedule(old, {"time": "08:30"})
    assert merged == {"type": "daily", "time": "08:30",
                      "weekdays": [0, 1], "timezone": "UTC"}


def test_merge_type_change_replaces_whole_schedule():
    old = {"type": "daily", "time": "07:00", "weekdays": [0],
           "timezone": "UTC"}
    merged = merge_schedule(old, {"type": "interval", "seconds": 7200})
    assert merged == {"type": "interval", "seconds": 7200,
                      "timezone": "UTC"}


def test_merge_keeps_old_timezone_when_new_has_none():
    old = {"type": "daily", "time": "07:00", "timezone": "Asia/Jakarta"}
    merged = merge_schedule(old, {"time": "08:00"})
    assert merged["timezone"] == "Asia/Jakarta"


def test_merge_rejects_invalid_update():
    with pytest.raises(ScheduleError):
        merge_schedule({"type": "daily", "time": "07:00"},
                       {"time": "25:00"})
    with pytest.raises(ScheduleError):
        merge_schedule({"type": "daily", "time": "07:00"},
                       {"timezone": "Nowhere/Special"})


# --- user input -------------------------------------------------------------

def test_user_input_once_and_daily():
    schedule, problems = RoutineScheduleInput.from_user_input(
        {"type": "once", "at": "2026-11-20T07:00:00+00:00",
         "timezone": "UTC"})
    assert problems == [] and schedule["type"] == "once"
    schedule, problems = RoutineScheduleInput.from_user_input(
        {"type": "daily", "time": "07:00", "weekdays": [0, 2]})
    assert problems == [] and schedule["weekdays"] == [0, 2]
    assert schedule["timezone"] == "UTC"  # default when omitted


def test_user_input_interval_and_cron():
    schedule, problems = RoutineScheduleInput.from_user_input(
        {"type": "interval", "seconds": 7200})
    assert problems == [] and schedule["seconds"] == 7200
    schedule, problems = RoutineScheduleInput.from_user_input(
        {"type": "cron", "expression": "30 8 * * *",
         "timezone": "Asia/Jakarta"})
    assert problems == [] and schedule["timezone"] == "Asia/Jakarta"


def test_user_input_reports_problems_without_raising():
    _, problems = RoutineScheduleInput.from_user_input(
        {"type": "daily", "time": "07:00", "timezone": "Nope/Zone"})
    assert problems and any("timezone" in p for p in problems)
    _, problems = RoutineScheduleInput.from_user_input({"type": "daily"})
    assert problems and any("'time'" in p for p in problems)
    _, problems = RoutineScheduleInput.from_user_input({})
    assert problems
