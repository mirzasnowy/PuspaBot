"""Holds and check pacing for scheduled runs (G8).

A *hold* pauses a job (or a whole scope, such as one user) until a
time or until cleared: while held, no run starts. *Quiet hours*
suppress starts inside a daily window. *Check pacing* rate-limits
how often an expensive check (for example a price or bill lookup)
may run for one scope, so a tight schedule cannot hammer an
external source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

__all__ = [
    "HoldStore",
    "Pacer",
    "is_quiet",
    "parse_quiet_window",
]


@dataclass
class _Hold:
    until: datetime | None  # None means held until cleared
    reason: str = ""


class HoldStore:
    """In-memory holds, keyed by scope (job id, user id, ...)."""

    def __init__(self):
        self._holds: dict[str, _Hold] = {}

    def set_hold(self, scope: str, *, until: datetime | None = None,
                 reason: str = "") -> None:
        """Hold ``scope`` until ``until`` (or until cleared)."""
        self._holds[str(scope)] = _Hold(until=until, reason=reason)

    def clear_hold(self, scope: str) -> bool:
        """Clear a hold; True when one existed."""
        return self._holds.pop(str(scope), None) is not None

    def is_held(self, scope: str, now: datetime | None = None) -> bool:
        """True while the hold is in force; expired holds lapse."""
        hold = self._holds.get(str(scope))
        if hold is None:
            return False
        if hold.until is None:
            return True
        now = now or datetime.now(timezone.utc)
        if now >= hold.until:
            del self._holds[str(scope)]
            return False
        return True

    def hold_reason(self, scope: str) -> str:
        """The reason recorded for a hold ("" when none)."""
        hold = self._holds.get(str(scope))
        return hold.reason if hold is not None else ""


class Pacer:
    """Minimum interval between checks, per scope.

    ``should_check`` is True only when the scope has never been
    checked or its last check is at least ``min_interval`` old;
    ``record_check`` stamps the check. A check that is refused
    consumes nothing.
    """

    def __init__(self, min_interval: timedelta):
        if min_interval < timedelta(0):
            raise ValueError("min_interval must not be negative")
        self.min_interval = min_interval
        self._last: dict[str, datetime] = {}

    def should_check(self, scope: str,
                     now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        last = self._last.get(str(scope))
        if last is None:
            return True
        return now - last >= self.min_interval

    def record_check(self, scope: str,
                     now: datetime | None = None) -> None:
        self._last[str(scope)] = now or datetime.now(timezone.utc)

    def next_allowed(self, scope: str) -> datetime | None:
        """Earliest next check time for a scope (None = any time)."""
        last = self._last.get(str(scope))
        if last is None:
            return None
        return last + self.min_interval


def parse_quiet_window(window: str) -> tuple[time, time]:
    """Parse a quiet window ``"HH:MM-HH:MM"`` into two times."""
    try:
        start_text, end_text = window.split("-", 1)
        start = time.fromisoformat(start_text.strip())
        end = time.fromisoformat(end_text.strip())
    except ValueError:
        raise ValueError(
            f"quiet window must be HH:MM-HH:MM, got {window!r}") from None
    return start, end


def is_quiet(now: datetime, quiet_window: str,
             timezone_name: str = "UTC") -> bool:
    """True when ``now`` falls inside the daily quiet window.

    A window whose end is earlier than its start spans midnight
    (for example ``"23:00-07:00"``).
    """
    start, end = parse_quiet_window(quiet_window)
    local = now.astimezone(ZoneInfo(timezone_name))
    current = local.time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= current < end
    return current >= start or current < end
