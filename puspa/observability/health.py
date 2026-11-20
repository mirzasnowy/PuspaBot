"""The observability block for /health and the operator surface (E-18).

One function builds the block both surfaces serve, so the
health endpoint and the operator status can never disagree
about the alarms: the current metric snapshot, the alarms
evaluated from it, and the tracing state (current trace id and
how many spans are recorded locally).
"""

from __future__ import annotations

from typing import Any

from puspa.observability.alarms import AlarmStore, evaluate_alarms
from puspa.observability.metrics import MetricsRegistry, get_metrics
from puspa.observability.tracing import current_trace_id, finished_spans

__all__ = ["observability_block"]


def observability_block(
    *,
    metrics: MetricsRegistry | None = None,
    alarms: AlarmStore | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    """The observability block: metrics snapshot + active alarms + tracing."""
    registry = metrics or get_metrics()
    snapshot = registry.snapshot()
    active = evaluate_alarms(snapshot, environment=environment, alarms=alarms)
    return {
        "metrics": snapshot,
        "alarms": active,
        "tracing": {
            "trace_id": current_trace_id(),
            "spans_recorded": len(finished_spans()),
        },
    }
