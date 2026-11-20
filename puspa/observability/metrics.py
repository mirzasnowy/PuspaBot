"""In-memory metrics (E-18, plan section 9.2).

Every metric in the plan's minimum list lives here, recorded
in memory only — nothing is exported to an external endpoint:

- turn latency and time to first token;
- fallback rate per chain;
- credential refresh failures (per provider);
- cost per task and per user, plus abandoned cost;
- held orphan numbers (numbers without evidence, held back);
- embedding fallback ratio;
- approval latency;
- sandbox start time (p95 target under 3 seconds) and full run time;
- queue age;
- provider quota remaining fraction;
- kill-drill results.

Durations are recorded in seconds and reported in
milliseconds (queue age is reported in seconds). Histograms
keep a bounded sample ring so a long-lived process cannot grow
memory without bound. :func:`snapshot` returns a JSON-safe
dict for ``GET /health`` and the operator surface.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from typing import Any

__all__ = [
    "MetricsRegistry",
    "get_metrics",
    "reset_metrics",
]

#: Per-task cost entries kept (oldest drop out).
MAX_TASKS_TRACKED = 1000
#: Histogram samples kept per series.
MAX_SAMPLES = 4096
#: Last per-task costs kept in the snapshot.
SNAPSHOT_LAST_COSTS = 10


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(pct / 100.0 * len(ordered))
    return ordered[max(0, min(rank, len(ordered)) - 1)]


def _histogram_summary(values: list[float], *, scale: float = 1.0) -> dict[str, Any]:
    """Count / mean / p50 / p95 / max of samples, scaled for display."""
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "max": None}
    total = sum(values)
    return {
        "count": len(values),
        "mean": round(total / len(values) * scale, 3),
        "p50": round((_percentile(values, 50) or 0.0) * scale, 3),
        "p95": round((_percentile(values, 95) or 0.0) * scale, 3),
        "max": round(max(values) * scale, 3),
    }


class _Histogram:
    """A bounded sample ring with percentile summaries."""

    def __init__(self, *, max_samples: int = MAX_SAMPLES) -> None:
        self._values: deque = deque(maxlen=max_samples)

    def observe(self, value: float) -> None:
        self._values.append(float(value))

    def values(self) -> list[float]:
        return list(self._values)

    def summary(self, *, scale: float = 1.0) -> dict[str, Any]:
        return _histogram_summary(self.values(), scale=scale)


class MetricsRegistry:
    """All E-18 metrics, recorded in memory, thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turn_latency = _Histogram()
        self._turns_by_status: dict[str, int] = {}
        self._ttft = _Histogram()
        self._fallback_chains: dict[str, dict[str, Any]] = {}
        self._refresh_failures: dict[str, int] = {}
        self._refresh_failures_total = 0
        # task_id -> {"rupiah": int, "user_id": str}, insertion-ordered.
        self._cost_per_task: OrderedDict = OrderedDict()
        self._cost_per_user: dict[str, int] = {}
        self._cost_total_rp = 0
        self._abandoned_rp = 0
        self._abandoned_tasks = 0
        self._orphan_held_total = 0
        self._orphan_events = 0
        self._orphan_last_held = 0
        self._embedding_total = 0
        self._embedding_fallback_total = 0
        self._approval_latency = _Histogram()
        self._approval_decisions = 0
        self._sandbox_start = _Histogram()
        self._sandbox_run = _Histogram()
        self._queue_age = _Histogram()
        self._queue_age_max_s = 0.0
        self._quota_remaining: dict[str, float] = {}
        self._kill_drill_runs = 0
        self._kill_drill_failures = 0
        self._kill_drill_last_success: bool | None = None
        self._kill_drill_last_duration_s: float | None = None
        self._kill_drill_last_at: float | None = None

    # -- turns ------------------------------------------------------

    def record_turn_latency(
        self, seconds: float, *, status: str, task_id: str | None = None, user_id: Any = None
    ) -> None:
        """One finished turn: its wall latency and outcome status."""
        with self._lock:
            self._turn_latency.observe(max(0.0, float(seconds)))
            self._turns_by_status[str(status)] = self._turns_by_status.get(str(status), 0) + 1

    def record_ttft(
        self, seconds: float, *, task_id: str | None = None, user_id: Any = None
    ) -> None:
        """Time to first token: first model reply of a turn.

        The model call is not streamed, so the first reply's
        arrival is the first observable token time.
        """
        with self._lock:
            self._ttft.observe(max(0.0, float(seconds)))

    # -- fallback ---------------------------------------------------

    def record_fallback(self, chain: str, *, hops: int, tier_used: str = "") -> None:
        """One fallback run for a chain: how many tiers it hopped."""
        with self._lock:
            entry = self._fallback_chains.setdefault(
                str(chain), {"runs": 0, "with_fallback": 0, "hops_total": 0}
            )
            entry["runs"] += 1
            entry["hops_total"] += int(hops)
            if hops > 0:
                entry["with_fallback"] += 1

    def fallback_rate(self, chain: str) -> float | None:
        """Share of runs on ``chain`` that fell back, or None with no runs."""
        with self._lock:
            entry = self._fallback_chains.get(str(chain))
            if not entry or entry["runs"] == 0:
                return None
            return entry["with_fallback"] / entry["runs"]

    # -- credentials --------------------------------------------------

    def record_credential_refresh_failure(self, provider: str, *, reason: str = "") -> None:
        """One failed OAuth / credential refresh for ``provider``."""
        with self._lock:
            self._refresh_failures_total += 1
            key = str(provider)
            self._refresh_failures[key] = self._refresh_failures.get(key, 0) + 1

    # -- cost ---------------------------------------------------------

    def record_cost(self, task_id: str, user_id: Any, rupiah: int) -> None:
        """Cost of one finished task, attributed to its task and user."""
        amount = int(rupiah)
        user = str(user_id or "unknown")
        with self._lock:
            self._cost_total_rp += amount
            entry = self._cost_per_task.get(str(task_id))
            if entry is None:
                entry = {"rupiah": 0, "user_id": user}
                self._cost_per_task[str(task_id)] = entry
            entry["rupiah"] += amount
            self._cost_per_user[user] = self._cost_per_user.get(user, 0) + amount
            while len(self._cost_per_task) > MAX_TASKS_TRACKED:
                self._cost_per_task.popitem(last=False)

    def record_abandoned_cost(self, task_id: str, user_id: Any, rupiah: int) -> None:
        """Cost already spent when a turn was abandoned (interrupted).

        The tokens were consumed even though the user stopped the
        turn, so the spend is tracked separately from finished-task
        cost. Also recorded via :meth:`record_cost` by the caller
        when the turn itself is charged; this method only tracks
        the abandoned slice.
        """
        with self._lock:
            self._abandoned_rp += int(rupiah)
            self._abandoned_tasks += 1

    def max_cost_per_task_rp(self) -> int:
        """Highest cost of any single tracked task (0 with no tasks)."""
        with self._lock:
            if not self._cost_per_task:
                return 0
            return max(entry["rupiah"] for entry in self._cost_per_task.values())

    # -- orphan numbers -----------------------------------------------

    def record_held_orphan_numbers(self, count: int, *, task_id: str | None = None) -> None:
        """Orphan numbers found without evidence and held back.

        ``count`` is how many numbers one render held for repair
        or dropped from the output.
        """
        with self._lock:
            self._orphan_held_total += max(0, int(count))
            self._orphan_events += 1
            self._orphan_last_held = max(0, int(count))

    # -- embedding ----------------------------------------------------

    def record_embedding_fallback(self, fell_back: bool) -> None:
        """One embedding resolution: True when it fell back to the hash stand-in."""
        with self._lock:
            self._embedding_total += 1
            if fell_back:
                self._embedding_fallback_total += 1

    def embedding_fallback_ratio(self) -> float | None:
        """Share of embedding resolutions that fell back, or None with none."""
        with self._lock:
            if self._embedding_total == 0:
                return None
            return self._embedding_fallback_total / self._embedding_total

    # -- approvals ----------------------------------------------------

    def record_approval_latency(
        self, seconds: float, *, request_id: str | None = None, approved: bool | None = None
    ) -> None:
        """Time from an approval card being shown to the decision."""
        with self._lock:
            self._approval_latency.observe(max(0.0, float(seconds)))
            self._approval_decisions += 1

    # -- sandbox ------------------------------------------------------

    def record_sandbox_start(self, seconds: float, *, source: str = "") -> None:
        """Time to hand over a ready sandbox (warm checkout / cold create)."""
        with self._lock:
            self._sandbox_start.observe(max(0.0, float(seconds)))

    def record_sandbox_run(self, seconds: float, *, user_id: Any = None) -> None:
        """Full wall time of one sandbox run (spawn + execution)."""
        with self._lock:
            self._sandbox_run.observe(max(0.0, float(seconds)))

    def sandbox_start_p95_ms(self) -> float | None:
        """p95 sandbox start time in milliseconds, or None with no starts."""
        with self._lock:
            summary = self._sandbox_start.summary(scale=1000.0)
            return summary["p95"]

    # -- queue ----------------------------------------------------------

    def record_queue_age(self, seconds: float, *, scope: str = "scheduler") -> None:
        """Age of the oldest waiting item observed in a queue."""
        age = max(0.0, float(seconds))
        with self._lock:
            self._queue_age.observe(age)
            self._queue_age_max_s = max(self._queue_age_max_s, age)

    # -- quota ----------------------------------------------------------

    def report_quota(self, provider: str, remaining_fraction: float) -> None:
        """Latest remaining quota fraction for ``provider`` (0..1)."""
        fraction = min(1.0, max(0.0, float(remaining_fraction)))
        with self._lock:
            self._quota_remaining[str(provider)] = fraction

    # -- kill drill -----------------------------------------------------

    def report_kill_drill(self, success: bool, duration_s: float) -> None:
        """Outcome of one kill drill: did the stop finish in time?"""
        with self._lock:
            self._kill_drill_runs += 1
            if not success:
                self._kill_drill_failures += 1
            self._kill_drill_last_success = bool(success)
            self._kill_drill_last_duration_s = max(0.0, float(duration_s))
            self._kill_drill_last_at = time.time()

    # -- snapshot -------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """The full metric snapshot (JSON-safe) for /health and operators."""
        with self._lock:
            fallback_chains = {
                chain: {
                    "runs": entry["runs"],
                    "with_fallback": entry["with_fallback"],
                    "hops_total": entry["hops_total"],
                    "rate": (entry["with_fallback"] / entry["runs"] if entry["runs"] else None),
                }
                for chain, entry in self._fallback_chains.items()
            }
            last_costs = [
                {"task_id": task_id, "user_id": entry["user_id"], "rupiah": entry["rupiah"]}
                for task_id, entry in list(self._cost_per_task.items())[-SNAPSHOT_LAST_COSTS:]
            ]
            return {
                "turns": {
                    "count": sum(self._turns_by_status.values()),
                    "by_status": dict(self._turns_by_status),
                    "latency_ms": self._turn_latency.summary(scale=1000.0),
                    "ttft_ms": self._ttft.summary(scale=1000.0),
                },
                "fallback": {"chains": fallback_chains},
                "credentials": {
                    "refresh_failures": self._refresh_failures_total,
                    "by_provider": dict(self._refresh_failures),
                },
                "cost": {
                    "total_rp": self._cost_total_rp,
                    "tasks": len(self._cost_per_task),
                    "max_cost_per_task_rp": (
                        max((e["rupiah"] for e in self._cost_per_task.values()), default=0)
                    ),
                    "per_user_rp": dict(self._cost_per_user),
                    "last_costs": last_costs,
                    "abandoned_rp": self._abandoned_rp,
                    "abandoned_tasks": self._abandoned_tasks,
                },
                "orphan_numbers": {
                    "held_total": self._orphan_held_total,
                    "events": self._orphan_events,
                    "last_held": self._orphan_last_held,
                },
                "embedding": {
                    "total": self._embedding_total,
                    "fallback_total": self._embedding_fallback_total,
                    "fallback_ratio": (
                        self._embedding_fallback_total / self._embedding_total
                        if self._embedding_total
                        else None
                    ),
                },
                "approvals": {
                    "decisions": self._approval_decisions,
                    "latency_ms": self._approval_latency.summary(scale=1000.0),
                },
                "sandbox": {
                    "start_ms": self._sandbox_start.summary(scale=1000.0),
                    "run_ms": self._sandbox_run.summary(scale=1000.0),
                },
                "queue": {
                    "age_s": self._queue_age.summary(scale=1.0),
                    "max_age_s": round(self._queue_age_max_s, 3),
                },
                "quota": {"providers": dict(self._quota_remaining)},
                "kill_drill": {
                    "runs": self._kill_drill_runs,
                    "failures": self._kill_drill_failures,
                    "last_success": self._kill_drill_last_success,
                    "last_duration_s": self._kill_drill_last_duration_s,
                    "last_at": self._kill_drill_last_at,
                },
            }


_REGISTRY: MetricsRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def get_metrics() -> MetricsRegistry:
    """The process-wide metric registry (created on first use)."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = MetricsRegistry()
    return _REGISTRY


def reset_metrics() -> MetricsRegistry:
    """Replace the process-wide registry with a fresh one (tests use this)."""
    global _REGISTRY
    with _REGISTRY_LOCK:
        _REGISTRY = MetricsRegistry()
        return _REGISTRY
