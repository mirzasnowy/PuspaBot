"""Observability for Puspa (E-18, plan section 9.2).

One trace id (the task id) flows through every log, with
OpenTelemetry spans for turns, router tiers, tools, and the
sandbox. Metrics are recorded in memory (no external endpoint,
no telemetry leaves the machine) and alarms are evaluated from
the metric snapshot. Logs are auto-redacted and kept under a
bounded retention.

Modules:

- :mod:`puspa.observability.tracing` — trace ids, OTel spans,
  and the local span store.
- :mod:`puspa.observability.metrics` — the in-memory metric
  registry (latency, fallback, cost, embedding, approval,
  sandbox, queue, quota, kill drill).
- :mod:`puspa.observability.alarms` — threshold rules over the
  metric snapshot plus the kill-drill runner.
- :mod:`puspa.observability.logging` — redacting log formatter
  and handler with bounded retention.
- :mod:`puspa.observability.health` — the observability block
  served at ``GET /health`` and on the operator surface.

Spans and metrics are stored locally only (in memory). Nothing
in this package opens a network connection.
"""

from __future__ import annotations

from puspa.observability.alarms import (
    COST_PER_TASK_LIMIT_RP,
    KILL_DRILL_MAX_DURATION_S,
    QUOTA_NEAR_EXHAUSTED_FRACTION,
    SANDBOX_START_P95_LIMIT_S,
    AlarmStore,
    evaluate_alarms,
    get_alarms,
    report_kill_drill,
    reset_alarms,
    run_kill_drill,
)
from puspa.observability.health import observability_block
from puspa.observability.logging import (
    RedactingFormatter,
    get_observability_logger,
    install_redacting_handler,
)
from puspa.observability.metrics import (
    MetricsRegistry,
    get_metrics,
    reset_metrics,
)
from puspa.observability.tracing import (
    clear_spans,
    configure_tracing,
    current_trace_id,
    end_trace,
    finished_spans,
    span,
    spans_for_trace,
    start_trace,
)

__all__ = [
    "COST_PER_TASK_LIMIT_RP",
    "KILL_DRILL_MAX_DURATION_S",
    "QUOTA_NEAR_EXHAUSTED_FRACTION",
    "SANDBOX_START_P95_LIMIT_S",
    "AlarmStore",
    "MetricsRegistry",
    "RedactingFormatter",
    "clear_spans",
    "configure_tracing",
    "current_trace_id",
    "end_trace",
    "evaluate_alarms",
    "finished_spans",
    "get_alarms",
    "get_metrics",
    "get_observability_logger",
    "install_redacting_handler",
    "observability_block",
    "report_kill_drill",
    "reset_alarms",
    "reset_metrics",
    "run_kill_drill",
    "span",
    "spans_for_trace",
    "start_trace",
]
