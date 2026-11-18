"""Tests for holds, quiet hours, and check pacing (holds.py). In-memory."""

from datetime import UTC, datetime, time, timedelta

import pytest

from puspa.scheduler.holds import (
    HoldStore,
    Pacer,
    is_quiet,
    parse_quiet_window,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


# --- holds ------------------------------------------------------------------


def test_hold_blocks_until_cleared():
    holds = HoldStore()
    assert holds.is_held("job-1", NOW) is False
    holds.set_hold("job-1", reason="quota exhausted")
    assert holds.is_held("job-1", NOW) is True
    assert holds.hold_reason("job-1") == "quota exhausted"
    assert holds.clear_hold("job-1") is True
    assert holds.is_held("job-1", NOW) is False
    assert holds.clear_hold("job-1") is False


def test_hold_with_deadline_lapses_after_it():
    holds = HoldStore()
    holds.set_hold("job-1", until=NOW + timedelta(hours=1))
    assert holds.is_held("job-1", NOW) is True
    assert holds.is_held("job-1", NOW + timedelta(hours=1, seconds=1)) is False
    # Lapsed holds are gone, not stuck.
    assert holds.is_held("job-1", NOW + timedelta(hours=2)) is False


def test_holds_are_scoped():
    holds = HoldStore()
    holds.set_hold("job-1")
    assert holds.is_held("job-2", NOW) is False


# --- pacing -------------------------------------------------------------------


def test_pacer_allows_first_check_then_enforces_interval():
    pacer = Pacer(min_interval=timedelta(hours=1))
    assert pacer.should_check("price", NOW) is True
    pacer.record_check("price", NOW)
    assert pacer.should_check("price", NOW + timedelta(minutes=30)) is False
    assert pacer.should_check("price", NOW + timedelta(hours=1)) is True
    assert pacer.next_allowed("price") == NOW + timedelta(hours=1)


def test_pacer_scopes_are_independent():
    pacer = Pacer(min_interval=timedelta(hours=1))
    pacer.record_check("price", NOW)
    assert pacer.should_check("bill", NOW) is True
    assert pacer.next_allowed("bill") is None


def test_pacer_rejects_negative_interval():
    with pytest.raises(ValueError):
        Pacer(min_interval=timedelta(seconds=-1))


# --- quiet hours --------------------------------------------------------------


def test_quiet_window_parsing():
    assert parse_quiet_window("23:00-07:00") == (time(23, 0), time(7, 0))
    with pytest.raises(ValueError):
        parse_quiet_window("23:00")


def test_is_quiet_inside_and_outside_window():
    assert is_quiet(datetime(2026, 11, 18, 0, 30, tzinfo=UTC), "23:00-07:00") is True
    assert is_quiet(datetime(2026, 11, 18, 12, 0, tzinfo=UTC), "23:00-07:00") is False
    assert is_quiet(datetime(2026, 11, 18, 12, 0, tzinfo=UTC), "12:00-13:00") is True
    assert is_quiet(datetime(2026, 11, 18, 13, 0, tzinfo=UTC), "12:00-13:00") is False


def test_is_quiet_applies_timezone():
    # 00:30 UTC is 07:30 Asia/Jakarta: outside a 23:00-07:00 window.
    assert (
        is_quiet(datetime(2026, 11, 18, 0, 30, tzinfo=UTC), "23:00-07:00", "Asia/Jakarta") is False
    )
