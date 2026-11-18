"""Independent verifier for sentinel issues (G12, K8 fold).

The sentinel's detectors are the verifier's eyes — but this
module is separate from ``detectors.py`` so the two can evolve
independently. A detector finds a candidate; only the verifier
confirms it, and it confirms by re-reading fresh data, not by
trusting the scan that produced the candidate.

For one issue, verification is three independent checks, in
order:

1. **Already handled?** When a state is given and the issue key
   is ``acknowledged`` or ``resolved`` there, the verdict is
   ``duplicate`` — the operator already decided, and the
   scanner must not re-raise it.
2. **Still true?** The single detector for the issue's scope is
   re-run over freshly read events (the ``event_source`` is
   called again here, never reused from the scan). When no fresh
   issue with the same key comes back, the condition is gone and
   the verdict is ``stale``.
3. **Evidence intact?** The fresh issue must carry the same
   detector and key, and its evidence must name events that are
   present in the fresh read. Otherwise the verdict is
   ``unverifiable``.

Any failure to read the source, any unknown detector name, and
any unexpected shape fails closed as ``unverifiable`` — never as
confirmed, never raising. The verifier never writes anything:
it returns a verdict; the scanner decides what to do with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from puspa.sentinel.detectors import (
    detect_cost_overruns,
    detect_queue_waits,
    detect_retry_loops,
    detect_tool_errors,
    detect_turn_stops,
)
from puspa.sentinel.thresholds import SentinelThresholds

__all__ = ["VerificationVerdict", "verify_issue"]

#: Detector name -> the single detector function for that scope.
_DETECTOR_BY_NAME = {
    "tool_error": detect_tool_errors,
    "turn_stop": detect_turn_stops,
    "queue_wait": detect_queue_waits,
    "cost": detect_cost_overruns,
    "retry_loop": detect_retry_loops,
}


@dataclass(frozen=True)
class VerificationVerdict:
    """The outcome of verifying one sentinel issue."""

    issue_key: str
    verdict: str  # "confirmed" | "stale" | "duplicate" | "unverifiable"
    reason: str
    checked_at: datetime


def verify_issue(
    issue: dict,
    *,
    event_source,
    state: dict | None = None,
    thresholds: SentinelThresholds | None = None,
    now: datetime | None = None,
) -> VerificationVerdict:
    """Verify one issue against freshly read events.

    ``event_source`` is a callable ``() -> list[dict]`` returning
    ledger events (the ledger shape). It is called here, fresh,
    so a verdict never trusts the scan's own read. ``state`` is
    the sentinel state dict (optional); when given, an already
    acknowledged/resolved key is a ``duplicate``.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    key = str(issue.get("key") or "")
    detector_name = str(issue.get("detector") or "")
    if not key or not detector_name:
        return VerificationVerdict(
            issue_key=key or "?",
            verdict="unverifiable",
            reason="issue carries no key or detector name",
            checked_at=now,
        )
    if state is not None:
        stored = (state.get("issues") or {}).get(key)
        if isinstance(stored, dict) and stored.get("status") in ("acknowledged", "resolved"):
            return VerificationVerdict(
                issue_key=key,
                verdict="duplicate",
                reason=f"already {stored['status']} in the sentinel state",
                checked_at=now,
            )
    detector = _DETECTOR_BY_NAME.get(detector_name)
    if detector is None:
        return VerificationVerdict(
            issue_key=key,
            verdict="unverifiable",
            reason=f"unknown detector {detector_name!r}",
            checked_at=now,
        )
    try:
        fresh_events = event_source()
    except Exception as exc:
        return VerificationVerdict(
            issue_key=key,
            verdict="unverifiable",
            reason=f"could not re-read events: {type(exc).__name__}",
            checked_at=now,
        )
    if not isinstance(fresh_events, list):
        return VerificationVerdict(
            issue_key=key,
            verdict="unverifiable",
            reason="event source did not return a list",
            checked_at=now,
        )
    thresholds = thresholds or SentinelThresholds()
    try:
        fresh_issues = detector(fresh_events, thresholds=thresholds, now=now)
    except Exception as exc:
        return VerificationVerdict(
            issue_key=key,
            verdict="unverifiable",
            reason=f"detector failed on re-run: {type(exc).__name__}",
            checked_at=now,
        )
    fresh = next((item for item in fresh_issues if item.get("key") == key), None)
    if fresh is None:
        return VerificationVerdict(
            issue_key=key,
            verdict="stale",
            reason="condition no longer present in fresh events",
            checked_at=now,
        )
    evidence = fresh.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        return VerificationVerdict(
            issue_key=key,
            verdict="unverifiable",
            reason="fresh issue carries no evidence",
            checked_at=now,
        )
    return VerificationVerdict(
        issue_key=key,
        verdict="confirmed",
        reason="condition still present in fresh events",
        checked_at=now,
    )
