"""Failure recovery for scheduled runs (G8).

A failed run is requeued exactly once. A second failure is
terminal: it is recorded as a failure fact and the owner is
notified. There is no unbounded retry — a run that keeps failing
stops consuming work and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

__all__ = [
    "MAX_REQUEUE",
    "FailureRecord",
    "handle_failure",
    "should_requeue",
]

#: A failed run is requeued this many times at most (once).
MAX_REQUEUE = 1


@dataclass
class FailureRecord:
    """One recorded run failure (the failure fact)."""

    job_id: str
    run_id: str
    error: str
    attempts: int
    requeued: bool
    owner_notified: bool
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "error": self.error,
            "attempts": self.attempts,
            "requeued": self.requeued,
            "owner_notified": self.owner_notified,
            "recorded_at": self.recorded_at.isoformat(),
        }


def should_requeue(attempts: int) -> bool:
    """True only while the run has a requeue left (at most one)."""
    return attempts < MAX_REQUEUE


def handle_failure(
    *,
    job_id: str,
    run_id: str,
    error: str,
    attempts: int,
    sink=None,
    notify_owner=None,
    requeue=None,
) -> FailureRecord:
    """Handle one failed run: requeue once, else record + notify.

    ``requeue`` is an optional callable ``(job_id, run_id) -> None``
    that puts the run back on the queue; it is called only when a
    requeue is still allowed. ``notify_owner`` is an optional
    callable ``(record_dict) -> None``; it is called only for a
    terminal failure. ``sink`` is an optional event sink
    ``(event_type, content)`` that always receives a
    ``run_failed`` event (and ``run_requeued`` when requeued).
    The returned record states what happened.
    """
    requeued = should_requeue(attempts)
    terminal = not requeued
    record = FailureRecord(
        job_id=job_id,
        run_id=run_id,
        error=str(error),
        attempts=attempts,
        requeued=requeued,
        owner_notified=False,
    )
    if requeued and requeue is not None:
        requeue(job_id, run_id)
    if sink is not None:
        try:
            sink("run_failed", record.to_dict())
            if requeued:
                sink("run_requeued", {"job_id": job_id, "run_id": run_id, "attempts": attempts})
        except Exception:
            pass
    if terminal and notify_owner is not None:
        try:
            notify_owner(record.to_dict())
            record.owner_notified = True
        except Exception:
            pass
    return record
