"""Sentinel thresholds (G12).

The same five defaults DuskBot's sentinel uses, as configuration
— queue wait 5 minutes, cost $5, retry minimum 3, 7-day lookback,
30-minute rescan — plus the two Puspa-side values the detectors
need (how many tool errors make an issue, how long a task may sit
without a terminal event before it counts as stalled).

``idr_per_usd`` converts the Rupiah costs the ledger already
records (``content.cost_rp``, see ``puspa/runtime/budget.py``)
into the dollars the cost threshold is expressed in. It is a
configuration value, not a market claim: change it when the
accounting rate changes; the cost detector never fetches a rate.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SentinelThresholds"]


@dataclass(frozen=True)
class SentinelThresholds:
    """Thresholds for the five detectors plus scan bookkeeping."""

    #: A trigger whose task starts later than this is a queue wait.
    queue_wait_minutes: float = 5.0
    #: Cost overrun: one task costing at least this much (USD) in
    #: the lookback window raises an issue.
    cost_usd: float = 5.0
    #: A tool called this many times in one task without a single
    #: success is a retry loop.
    retry_min: int = 3
    #: A tool failing this many times in the lookback window is a
    #: tool-error issue.
    tool_error_min: int = 3
    #: How far back the detectors look (days).
    lookback_days: int = 7
    #: Minimum time between two scans; a scan requested sooner is
    #: a no-op unless forced.
    rescan_minutes: float = 30.0
    #: A task with no terminal event and no activity for this long
    #: (hours) counts as a stalled turn.
    stalled_hours: float = 24.0
    #: Rupiah per US dollar, for the cost threshold only. Config,
    #: not a market rate.
    idr_per_usd: float = 16000.0
    #: Decision-log retention (days). DuskBot default: 180.
    decision_retention_days: int = 180

    def validate(self) -> None:
        """Fail fast on nonsense configuration, never silently."""
        if self.queue_wait_minutes <= 0:
            raise ValueError("queue_wait_minutes must be positive")
        if self.cost_usd <= 0:
            raise ValueError("cost_usd must be positive")
        if self.retry_min < 2:
            raise ValueError("retry_min must be at least 2")
        if self.tool_error_min < 1:
            raise ValueError("tool_error_min must be at least 1")
        if self.lookback_days <= 0:
            raise ValueError("lookback_days must be positive")
        if self.rescan_minutes < 0:
            raise ValueError("rescan_minutes must not be negative")
        if self.stalled_hours <= 0:
            raise ValueError("stalled_hours must be positive")
        if self.idr_per_usd <= 0:
            raise ValueError("idr_per_usd must be positive")
        if self.decision_retention_days <= 0:
            raise ValueError("decision_retention_days must be positive")
