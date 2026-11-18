"""Tests for due classification (due.py). In-memory."""

from datetime import datetime, timedelta, timezone

from puspa.scheduler.due import (
    classify_due,
    is_due,
    is_missed,
    lateness,
    sort_due_first,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def test_not_yet_before_due_time():
    assert classify_due(NOW + timedelta(hours=1), NOW) == "not_yet"
    assert is_due(NOW + timedelta(hours=1), NOW) is False
    assert is_missed(NOW + timedelta(hours=1), NOW) is False


def test_due_just_after_due_time():
    assert classify_due(NOW - timedelta(seconds=30), NOW) == "due"
    assert is_due(NOW - timedelta(seconds=30), NOW) is True


def test_late_inside_12h_grace_still_runs():
    assert classify_due(NOW - timedelta(hours=11), NOW) == "late"
    assert is_due(NOW - timedelta(hours=11), NOW) is True
    assert is_missed(NOW - timedelta(hours=11), NOW) is False
    assert classify_due(NOW - timedelta(hours=12), NOW) == "late"


def test_missed_after_12h_grace_never_runs():
    assert classify_due(NOW - timedelta(hours=12, seconds=1), NOW) == "missed"
    assert is_due(NOW - timedelta(hours=13), NOW) is False
    assert is_missed(NOW - timedelta(hours=13), NOW) is True


def test_lateness_is_signed():
    assert lateness(NOW - timedelta(hours=2), NOW) == timedelta(hours=2)
    assert lateness(NOW + timedelta(hours=2), NOW) == timedelta(hours=-2)


def test_sort_due_first_most_overdue_first():
    runs = [
        {"id": "future", "due_at": NOW + timedelta(hours=2)},
        {"id": "recent", "due_at": NOW - timedelta(minutes=5)},
        {"id": "oldest", "due_at": NOW - timedelta(hours=5)},
        {"id": "soon", "due_at": NOW + timedelta(minutes=10)},
    ]
    ordered = [r["id"] for r in sort_due_first(runs, now=NOW)]
    assert ordered == ["oldest", "recent", "soon", "future"]
