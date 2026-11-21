"""The observability block for /health and the operator surface.

One function builds the block both surfaces serve, so the
health endpoint and the operator status can never disagree
about the alarms: the current metric snapshot, the alarms
reported for it, and the tracing state (current trace id and
how many spans are recorded locally).
"""

from __future__ import annotations

from typing import Any

from puspa.observability.metrics import MetricsRegistry, get_metrics
from puspa.observability.tracing import current_trace_id, finished_spans

__all__ = ["observability_block"]


def observability_block(
    *,
    metrics: MetricsRegistry | None = None,
    alarms: Any | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    """The observability block: metrics snapshot + active alarms + tracing.

    ``alarms`` is an optional alarm store: any object with an
    ``evaluate(snapshot, *, environment=...)`` method. When none is
    given, the block reports no active alarms.
    """
    registry = metrics or get_metrics()
    snapshot = registry.snapshot()
    if alarms is None:
        active: list[dict[str, Any]] = []
    else:
        active = alarms.evaluate(snapshot, environment=environment)
    return {
        "metrics": snapshot,
        "alarms": active,
        "tracing": {
            "trace_id": current_trace_id(),
            "spans_recorded": len(finished_spans()),
        },
    }
