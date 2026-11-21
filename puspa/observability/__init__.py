"""Observability for Puspa (E-18, plan section 9.2).

One trace id (the task id) flows through every log, with
OpenTelemetry spans for turns, router tiers, tools, and the
sandbox. Metrics are recorded in memory (no external endpoint,
no telemetry leaves the machine). Logs are auto-redacted and
kept under a bounded retention.

Modules:

- :mod:`puspa.observability.tracing` — trace ids, OTel spans,
  and the local span store.
- :mod:`puspa.observability.metrics` — the in-memory metric
  registry (latency, fallback, cost, embedding, approval,
  sandbox, queue, quota, kill drill). Active alarms are
  reported by the health block only when an alarm store is
  provided by the caller.
- :mod:`puspa.observability.logging` — redacting log formatter
  and handler with bounded retention.
- :mod:`puspa.observability.health` — the observability block
  served at ``GET /health`` and on the operator surface.

Spans and metrics are stored locally only (in memory). Nothing
in this package opens a network connection.
"""

from __future__ import annotations

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
    "MetricsRegistry",
    "RedactingFormatter",
    "clear_spans",
    "configure_tracing",
    "current_trace_id",
    "end_trace",
    "finished_spans",
    "get_metrics",
    "get_observability_logger",
    "install_redacting_handler",
    "observability_block",
    "reset_metrics",
    "span",
    "spans_for_trace",
    "start_trace",
]
