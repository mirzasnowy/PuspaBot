"""Alarms over the metric snapshot (E-18, plan section 9.2).

Rules (plan 9.2, with the plan's numbers):

- cost per task above Rp2.000 (``COST_PER_TASK_LIMIT_RP``);
- embedding fallback above zero — in production only, because
  the hash stand-in is the normal development default;
- sandbox start p95 above 3 seconds (``SANDBOX_START_P95_LIMIT_S``);
- provider quota nearly exhausted (under 10% remaining);
- kill drill failed (the drill raised, or the stop took longer
  than 5 seconds, ``KILL_DRILL_MAX_DURATION_S``).

:func:`evaluate_alarms` reads a metric snapshot and updates the
process-wide :class:`AlarmStore`: an alarm stays active while
its condition holds and clears when the condition lapses (a
later passing kill drill clears the kill-drill alarm). Alarms
are data for ``GET /health`` and the operator surface; nothing
here sends mail, pages, or calls out — the operator reads them
from those surfaces.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from puspa.observability.metrics import MetricsRegistry, get_metrics

__all__ = [
    "COST_PER_TASK_LIMIT_RP",
    "KILL_DRILL_MAX_DURATION_S",
    "QUOTA_NEAR_EXHAUSTED_FRACTION",
    "SANDBOX_START_P95_LIMIT_S",
    "Alarm",
    "AlarmStore",
    "evaluate_alarms",
    "get_alarms",
    "report_kill_drill",
    "reset_alarms",
    "run_kill_drill",
]

#: Alarm when one task costs more than this (rupiah, plan 9.2).
COST_PER_TASK_LIMIT_RP = 2000
#: Alarm when sandbox start p95 exceeds this (seconds, plan 9.2).
SANDBOX_START_P95_LIMIT_S = 3.0
#: Alarm when a provider's remaining quota fraction drops below this.
QUOTA_NEAR_EXHAUSTED_FRACTION = 0.10
#: A kill drill fails when the stop takes longer than this (seconds, plan 6.6).
KILL_DRILL_MAX_DURATION_S = 5.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


@dataclass
class Alarm:
    """One active alarm: what fired, how bad, since when."""

    name: str
    severity: str  # "critical" | "warning"
    message: str
    value: Any = None
    threshold: Any = None
    detail: dict[str, Any] = field(default_factory=dict)
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe view for /health and the operator surface."""
        return {
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
            "value": self.value,
            "threshold": self.threshold,
            "detail": dict(self.detail),
            "first_seen": _iso(self.first_seen),
            "last_seen": _iso(self.last_seen),
        }


class AlarmStore:
    """Active alarms, evaluated from metric snapshots."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, Alarm] = {}

    def evaluate(
        self, snapshot: dict[str, Any], *, environment: str | None = None
    ) -> list[dict[str, Any]]:
        """Evaluate every rule against ``snapshot``; return active alarms.

        ``environment`` defaults to ``PUSPA_ENVIRONMENT`` (or
        ``development``): the embedding-fallback alarm only fires
        in production, where the hash stand-in must never serve.
        Alarms whose condition no longer holds are cleared.
        """
        env = (environment or os.environ.get("PUSPA_ENVIRONMENT") or "development").strip().lower()
        now = time.time()
        fired: dict[str, Alarm] = {}

        cost = snapshot.get("cost") or {}
        max_cost = cost.get("max_cost_per_task_rp") or 0
        if max_cost > COST_PER_TASK_LIMIT_RP:
            fired["cost_per_task"] = Alarm(
                name="cost_per_task",
                severity="critical",
                message=f"cost per task Rp{max_cost} is above the Rp{COST_PER_TASK_LIMIT_RP} limit",
                value=max_cost,
                threshold=COST_PER_TASK_LIMIT_RP,
            )

        embedding = snapshot.get("embedding") or {}
        fallback_total = embedding.get("fallback_total") or 0
        if env == "production" and fallback_total > 0:
            fired["embedding_fallback"] = Alarm(
                name="embedding_fallback",
                severity="critical",
                message=f"embedding fell back {fallback_total} time(s) in production",
                value=fallback_total,
                threshold=0,
            )

        sandbox = snapshot.get("sandbox") or {}
        start_summary = sandbox.get("start_ms") or {}
        p95_ms = start_summary.get("p95")
        if p95_ms is not None and p95_ms > SANDBOX_START_P95_LIMIT_S * 1000:
            fired["sandbox_start_p95"] = Alarm(
                name="sandbox_start_p95",
                severity="warning",
                message=(
                    f"sandbox start p95 {p95_ms} ms is above the "
                    f"{int(SANDBOX_START_P95_LIMIT_S * 1000)} ms limit"
                ),
                value=p95_ms,
                threshold=int(SANDBOX_START_P95_LIMIT_S * 1000),
            )

        quota = snapshot.get("quota") or {}
        providers = quota.get("providers") or {}
        low = {
            name: fraction
            for name, fraction in providers.items()
            if isinstance(fraction, (int, float)) and fraction < QUOTA_NEAR_EXHAUSTED_FRACTION
        }
        if low:
            worst = min(low.values())
            fired["provider_quota"] = Alarm(
                name="provider_quota",
                severity="warning",
                message="provider quota nearly exhausted: "
                + ", ".join(
                    f"{name} ({fraction:.0%} left)" for name, fraction in sorted(low.items())
                ),
                value=worst,
                threshold=QUOTA_NEAR_EXHAUSTED_FRACTION,
                detail={"providers": low},
            )

        drill = snapshot.get("kill_drill") or {}
        last_success = drill.get("last_success")
        last_duration = drill.get("last_duration_s")
        if last_success is False:
            fired["kill_drill_failed"] = Alarm(
                name="kill_drill_failed",
                severity="critical",
                message="kill drill failed: the stop did not finish in time",
                value=last_duration,
                threshold=KILL_DRILL_MAX_DURATION_S,
            )
        elif last_success is True and (
            last_duration is not None and last_duration > KILL_DRILL_MAX_DURATION_S
        ):
            fired["kill_drill_failed"] = Alarm(
                name="kill_drill_failed",
                severity="critical",
                message=f"kill drill too slow: {last_duration}s for a stop",
                value=last_duration,
                threshold=KILL_DRILL_MAX_DURATION_S,
            )

        with self._lock:
            for name, alarm in fired.items():
                existing = self._active.get(name)
                if existing is not None:
                    alarm.first_seen = existing.first_seen
                alarm.last_seen = now
                self._active[name] = alarm
            for name in [name for name in self._active if name not in fired]:
                del self._active[name]
            return [
                alarm.to_dict() for alarm in sorted(self._active.values(), key=lambda a: a.name)
            ]

    def active_alarms(self) -> list[dict[str, Any]]:
        """Currently active alarms, as JSON-safe dicts."""
        with self._lock:
            return [
                alarm.to_dict() for alarm in sorted(self._active.values(), key=lambda a: a.name)
            ]

    def clear(self) -> None:
        """Drop all active alarms (tests use this)."""
        with self._lock:
            self._active.clear()


_STORE: AlarmStore | None = None
_STORE_LOCK = threading.Lock()


def get_alarms() -> AlarmStore:
    """The process-wide alarm store (created on first use)."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = AlarmStore()
    return _STORE


def reset_alarms() -> AlarmStore:
    """Replace the process-wide store with a fresh one (tests use this)."""
    global _STORE
    with _STORE_LOCK:
        _STORE = AlarmStore()
        return _STORE


def evaluate_alarms(
    snapshot: dict[str, Any],
    *,
    environment: str | None = None,
    alarms: AlarmStore | None = None,
) -> list[dict[str, Any]]:
    """Evaluate the rules against ``snapshot`` on the shared store."""
    return (alarms or get_alarms()).evaluate(snapshot, environment=environment)


def report_kill_drill(
    success: bool,
    duration_s: float,
    *,
    metrics: MetricsRegistry | None = None,
    alarms: AlarmStore | None = None,
    environment: str | None = None,
) -> list[dict[str, Any]]:
    """Record one kill-drill outcome and re-evaluate the alarms.

    Returns the active alarms afterwards, so a caller that just
    ran a drill can see immediately whether it raised the alarm.
    """
    registry = metrics or get_metrics()
    registry.report_kill_drill(success, duration_s)
    return evaluate_alarms(registry.snapshot(), environment=environment, alarms=alarms)


def run_kill_drill(
    stop_fn,
    *,
    deadline_s: float = KILL_DRILL_MAX_DURATION_S,
    metrics: MetricsRegistry | None = None,
    alarms: AlarmStore | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    """Run one kill drill: time ``stop_fn`` and record the outcome.

    ``stop_fn`` is the operator's stop-everything callable (it
    revokes tokens and stops the running agents; its return value
    is reported back as ``stopped``). The drill succeeds when the
    call returns without raising within ``deadline_s``. A
    failure (raise, or too slow) raises the ``kill_drill_failed``
    alarm; a later passing drill clears it.
    """
    started = time.monotonic()
    stopped: Any = None
    error: str | None = None
    try:
        stopped = stop_fn()
    except Exception as exc:  # a drill that raises is a failed drill
        error = f"{type(exc).__name__}: {exc}"
    duration_s = time.monotonic() - started
    success = error is None and duration_s <= deadline_s
    active = report_kill_drill(
        success, duration_s, metrics=metrics, alarms=alarms, environment=environment
    )
    return {
        "success": success,
        "duration_s": round(duration_s, 3),
        "stopped": stopped,
        "error": error,
        "alarms": active,
    }
