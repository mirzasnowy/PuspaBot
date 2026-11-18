"""Due classification for scheduled runs (G8).

A run whose due time has passed is not silently dropped:

- ``not_yet``: the due time is still in the future.
- ``due``: due now (within the grace window) — run it.
- ``late``: past due but inside the 12-hour grace window — still
  runs, flagged late so the run is visibly late, not silently on
  time.
- ``missed``: past due by more than the grace window — never runs
  silently; it is recorded as missed (see ``recovery``) and the
  next occurrence is computed from the schedule instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

__all__ = [
    "LATE_GRACE",
    "classify_due",
    "is_due",
    "is_missed",
    "lateness",
    "sort_due_first",
]

#: A late run still executes for this long after its due time.
LATE_GRACE = timedelta(hours=12)


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def lateness(due_at: datetime, now: datetime | None = None) -> timedelta:
    """How far past due a run is (negative when not yet due)."""
    now = _aware(now or datetime.now(UTC))
    return now - _aware(due_at)


def classify_due(due_at: datetime, now: datetime | None = None) -> str:
    """Classify a run: ``not_yet`` | ``due`` | ``late`` | ``missed``.

    ``due`` covers a run at most a moment past its time; ``late``
    covers the rest of the grace window; anything older is
    ``missed`` and must be reported, never run.
    """
    late_by = lateness(due_at, now)
    if late_by <= timedelta(0):
        return "not_yet"
    if late_by <= timedelta(seconds=60):
        return "due"
    if late_by <= LATE_GRACE:
        return "late"
    return "missed"


def is_due(due_at: datetime, now: datetime | None = None) -> bool:
    """True when a run should execute now (due or late, not missed)."""
    return classify_due(due_at, now) in ("due", "late")


def is_missed(due_at: datetime, now: datetime | None = None) -> bool:
    """True when a run is past the grace window and must not run."""
    return classify_due(due_at, now) == "missed"


def sort_due_first(runs, *, now: datetime | None = None) -> list:
    """Sort run dicts (each with ``due_at``) most-overdue first.

    Runs that are not yet due sort last, in due-time order, so a
    worker that takes from the front always picks the most urgent
    runnable run first.
    """
    now = _aware(now or datetime.now(UTC))

    def key(run):
        due_at = _aware(run["due_at"])
        return (0, due_at) if due_at <= now else (1, due_at)

    return sorted(runs, key=key)
