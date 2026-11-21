"""Outcome watcher (Kosmos K8): keep monitoring after "done".

A task marked done is not necessarily done right: a bill
recorded as paid can bounce, a price recorded as checked can
move against the prediction. The watcher keeps a watch on an
outcome record and re-verifies it after a delay, by re-reading
fresh data (the verifier pattern from ``verifier.py``: never
trust the scan that produced the candidate).

One watched outcome, one recheck:

- ``watch(outcome, recheck=...)`` registers an outcome record
  (a plain dict: ``outcome_id``, ``task``, ``predicted``,
  ``observed``, ``user_id``, ``task_id``) with a ``recheck``
  callable. The recheck takes no arguments and returns the
  fresh observed value as a string, or ``None`` when it cannot
  observe anything right now.
- ``check_due(now)`` re-checks every outcome whose delay has
  passed. A fresh value equal to the recorded one is ``ok``.
  A different fresh value is a **late failure**: the watcher
  writes a new outcome record of type ``"late_failure"``
  through the injected writer, feeds the learning loop through
  ``record_misprediction`` (the recorded outcome was the
  prediction, the fresh value is what the world actually
  showed), and raises a sentinel issue (the same shape the
  ``outcome_failure`` detector builds, so the two agree).
  A recheck that raises, or returns ``None``, is
  ``unverifiable`` — a broken recheck never kills a scan and
  never counts as a failure.

Seams (so the watcher never imports a broken chain):

- The ledger writer is injected: ``record_outcome`` is a
  callable with the ``write_outcome`` keyword shape
  (``task_id``, ``agent_id``, ``outcome_type``, ``user_id``,
  ``summary``, ``evidence``, ``details``). With no writer the
  late failure is still fed to the loop and reported in the
  returned result, but no ledger record is written and no
  issue is built — an issue only ever names a record that
  exists, so the detector (which reads the ledger) stays the
  only source of issues in a scheduled scan. The eval-harness
  follow-up wires the real writer; this module never imports
  ``puspa.evidence.outcomes`` directly, so the watcher stays
  free of the ledger and database dependency and its tests
  need no database.
- The learning loop defaults to the module-level
  ``record_misprediction`` (in-memory, same seam the world
  model uses); a test may inject a fake loop instead. The
  loop's guardrail runs for real on every path: its lazy
  import of the evidence renderer resolves through the
  public ``puspa.policy`` allowlist (the private sandbox the
  split removed is gone from the chain), so a misprediction
  is never dropped by a broken import.

One failure boundary: every injected or arbitrary callable in
this module (the recheck, the writer, the loop) goes through
``_attempt``. A failure there degrades the check — the
recheck becomes ``unverifiable``, the writer/loop failure is
reported with the record/issue left unset — and never breaks
a scan. That is the single blind-except handler in this file
(recorded in the BLE001 baseline, like the one in
``puspa/world/speeds.py``).

All state is in memory. No database, no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from puspa.memory.learning_loop import record_misprediction as _default_record_misprediction
from puspa.sentinel.detectors import LATE_FAILURE_OUTCOME_TYPE, outcome_failure_issue

__all__ = [
    "CheckResult",
    "OutcomeWatcher",
    "WatchedOutcome",
]

#: How long after watching an outcome is due its first recheck,
#: when the caller gives no delay. One day matches the daily
#: sentinel scan, so a watched outcome is re-verified on the
#: next day's scan at the latest.
DEFAULT_RECHECK_DELAY = timedelta(days=1)

#: Keys a watched outcome dict must carry.
_WATCH_KEYS = ("outcome_id", "task", "predicted", "observed", "user_id", "task_id")


def _attempt(call):
    """Call ``call``; return ``(True, value)`` or ``(False, error)``.

    The single failure boundary of this module (see the module
    docstring): an injected or arbitrary callable that raises
    degrades the check instead of breaking the scan.
    """
    try:
        return True, call()
    except Exception as exc:
        return False, exc


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC).isoformat()
    return moment.astimezone(UTC).isoformat()


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


@dataclass
class WatchedOutcome:
    """One outcome under watch, with its recheck and due time."""

    outcome_id: str
    task: str
    predicted: str
    observed: str
    user_id: str
    task_id: str
    recheck: object = field(repr=False)
    due_at: datetime = field(default_factory=lambda: datetime.now(UTC) + DEFAULT_RECHECK_DELAY)
    watched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    agent_id: str = "puspa"
    room_id: str | None = None
    subject_id: str | None = None


@dataclass
class CheckResult:
    """The outcome of re-checking one watched outcome."""

    #: "ok" | "late_failure" | "unverifiable".
    status: str
    watched: WatchedOutcome
    #: The fresh observed value, when the recheck observed one.
    fresh_observed: str | None = None
    #: The late-failure outcome record, when one was written.
    outcome_record: object = None
    #: The learning-loop result, when the loop was fed.
    misprediction: dict | None = None
    #: The sentinel issue, when one was raised.
    issue: dict | None = None
    #: Why an "unverifiable" check could not be verified.
    reason: str = ""


class OutcomeWatcher:
    """Re-verify outcome records after they are marked done.

    ``record_outcome`` is the injected ledger writer (the
    ``write_outcome`` keyword shape); with none, late failures
    are fed to the loop and reported, but no ledger record is
    written and no issue is built. ``learning_loop`` is an
    object with ``record_misprediction(record, ...)``; it
    defaults to the module-level loop function.
    """

    def __init__(self, *, record_outcome=None, learning_loop=None, agent_id: str = "puspa"):
        self._record_outcome = record_outcome
        if learning_loop is None:
            self._record_misprediction = _default_record_misprediction
        elif hasattr(learning_loop, "record_misprediction"):
            self._record_misprediction = learning_loop.record_misprediction
        elif callable(learning_loop):
            self._record_misprediction = learning_loop
        else:
            raise ValueError("learning_loop must have record_misprediction or be callable")
        self._agent_id = agent_id
        self._pending: dict[str, WatchedOutcome] = {}

    def __len__(self) -> int:
        """How many outcomes are currently under watch."""
        return len(self._pending)

    def watch(
        self, outcome, *, recheck, delay: timedelta | None = None, now: datetime | None = None
    ) -> WatchedOutcome:
        """Watch one outcome record, due for recheck after ``delay``.

        ``outcome`` is a dict with ``outcome_id``, ``task``,
        ``predicted``, ``observed``, ``user_id`` and ``task_id``
        (the ledger chain the record sits on). ``recheck`` is a
        callable taking no arguments, returning the fresh
        observed value as a string, or ``None`` when it cannot
        observe. Watching the same ``outcome_id`` again replaces
        the old watch (a fresh delay from now).
        """
        if not isinstance(outcome, dict):
            raise ValueError("outcome must be a dict")
        missing = [key for key in _WATCH_KEYS if not outcome.get(key)]
        if missing:
            raise ValueError(f"outcome is missing keys: {missing}")
        if not callable(recheck):
            raise ValueError("recheck must be callable")
        if delay is None:
            delay = DEFAULT_RECHECK_DELAY
        if delay <= timedelta(0):
            raise ValueError("delay must be positive")
        now = _as_utc(now or datetime.now(UTC))
        watched = WatchedOutcome(
            outcome_id=str(outcome["outcome_id"]),
            task=str(outcome["task"]),
            predicted=str(outcome["predicted"]),
            observed=str(outcome["observed"]),
            user_id=str(outcome["user_id"]),
            task_id=str(outcome["task_id"]),
            recheck=recheck,
            due_at=now + delay,
            watched_at=now,
            agent_id=str(outcome.get("agent_id") or self._agent_id),
            room_id=outcome.get("room_id"),
            subject_id=outcome.get("subject_id"),
        )
        self._pending[watched.outcome_id] = watched
        return watched

    def pending(self) -> list[WatchedOutcome]:
        """Outcomes still under watch, in due order."""
        return sorted(self._pending.values(), key=lambda item: item.due_at)

    def check_due(self, now: datetime | None = None) -> list[CheckResult]:
        """Re-check every outcome whose delay has passed.

        Each due outcome is checked once and dropped from the
        watch, whatever the result: a caller that wants another
        recheck watches it again. Returns one result per due
        outcome, in due order.
        """
        now = _as_utc(now or datetime.now(UTC))
        due = [item for item in self.pending() if item.due_at <= now]
        results: list[CheckResult] = []
        for watched in due:
            del self._pending[watched.outcome_id]
            ok, fresh = _attempt(watched.recheck)
            if not ok:
                results.append(
                    CheckResult(
                        status="unverifiable",
                        watched=watched,
                        reason=f"recheck failed: {type(fresh).__name__}",
                    )
                )
                continue
            if fresh is None:
                results.append(
                    CheckResult(
                        status="unverifiable",
                        watched=watched,
                        reason="recheck observed nothing",
                    )
                )
                continue
            fresh_text = fresh if isinstance(fresh, str) else str(fresh)
            if fresh_text == watched.observed:
                results.append(CheckResult(status="ok", watched=watched, fresh_observed=fresh_text))
                continue
            results.append(self._late_failure(watched, fresh_text, now))
        return results

    def _late_failure(self, watched: WatchedOutcome, fresh_text: str, now: datetime) -> CheckResult:
        """Record one late failure: ledger, loop, issue."""
        checked_at = _iso(now)
        summary = (
            f"Outcome {watched.outcome_id} failed after it was marked done: "
            f"recorded {watched.observed!r}, recheck found {fresh_text!r}"
        )
        details = {
            "original_outcome_id": watched.outcome_id,
            "original_observed": watched.observed,
            "fresh_observed": fresh_text,
            "checked_at": checked_at,
        }
        outcome_record = None
        if self._record_outcome is not None:
            ok, record = _attempt(
                lambda: self._record_outcome(
                    task_id=watched.task_id,
                    agent_id=watched.agent_id,
                    outcome_type=LATE_FAILURE_OUTCOME_TYPE,
                    user_id=watched.user_id,
                    room_id=watched.room_id,
                    subject_id=watched.subject_id,
                    summary=summary,
                    evidence=[
                        {
                            "kind": "recheck",
                            "outcome_id": watched.outcome_id,
                            "checked_at": checked_at,
                        }
                    ],
                    details=details,
                )
            )
            if ok:
                outcome_record = record
        record = {
            "task": watched.task,
            "predicted": watched.observed,
            "observed": fresh_text,
            "error": (
                f"late change after done: recorded {watched.observed!r}, "
                f"recheck found {fresh_text!r}"
            ),
            "context": {
                "outcome_id": watched.outcome_id,
                "kind": LATE_FAILURE_OUTCOME_TYPE,
                "checked_at": checked_at,
            },
        }
        misprediction = None
        ok, fed = _attempt(
            lambda: self._record_misprediction(record, evidence_event_id=watched.outcome_id)
        )
        if ok:
            misprediction = fed
        issue = None
        if outcome_record is not None:
            record_id = _record_id(outcome_record)
            issue = outcome_failure_issue(
                outcome_id=watched.outcome_id,
                task_id=watched.task_id,
                summary=summary,
                evidence={
                    "outcome_id": watched.outcome_id,
                    "late_failure_ids": [record_id] if record_id else [],
                    "fresh_observed": fresh_text,
                    "original_observed": watched.observed,
                },
                first_seen=now,
                last_seen=now,
            )
        return CheckResult(
            status="late_failure",
            watched=watched,
            fresh_observed=fresh_text,
            outcome_record=outcome_record,
            misprediction=misprediction,
            issue=issue,
        )


def _record_id(record) -> str | None:
    """The ledger event id of a written outcome record, if any."""
    if record is None:
        return None
    if isinstance(record, dict):
        value = record.get("outcome_id") or record.get("id")
        return str(value) if value else None
    outcome_id = getattr(record, "outcome_id", None)
    return str(outcome_id) if outcome_id else None
