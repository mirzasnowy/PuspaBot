"""Trace ids and OpenTelemetry spans (E-18, plan section 9.2).

The trace id IS the task id: every log line and every span
carries the same id, so one task can be followed end to end.
Task ids look like ``tugas-<12 hex>`` and are not valid
OpenTelemetry trace ids (those need 32 hex chars), so the OTel
trace id is derived deterministically from the task id
(``sha256(task_id)`` truncated to 128 bits) and every span also
carries the task id as the ``puspa.trace_id`` attribute. The
canonical id in logs and in the local span store is always the
task id itself.

Spans are stored locally in two places:

- the local span store in this module (a bounded in-memory
  ring, always recorded, even when the OTel SDK is missing);
- the OTel SDK in-memory exporter, when
  :func:`configure_tracing` installed a recording provider
  (``opentelemetry-sdk``). No exporter sends anything over the
  network: there is no OTLP endpoint, no collector, no external
  telemetry.

Span kinds: ``turn`` (one conversational turn), ``router``
(one fallback tier attempt), ``tool`` (one registry tool call),
``sandbox`` (one sandbox start / run).
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import random
import threading
import time
from collections import deque
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

__all__ = [
    "TASK_ID_ATTR",
    "TRACE_ID_ATTR",
    "clear_spans",
    "configure_tracing",
    "current_trace_id",
    "end_trace",
    "finished_spans",
    "otel_trace_id",
    "span",
    "spans_for_trace",
    "start_trace",
]

#: Span attribute carrying the canonical trace id (the task id).
TRACE_ID_ATTR = "puspa.trace_id"
#: Span attribute carrying the task id (same value as the trace id).
TASK_ID_ATTR = "puspa.task_id"

#: Span kinds this package records.
SPAN_KINDS = frozenset({"turn", "router", "tool", "sandbox"})

#: How many finished spans the local store keeps (oldest drop out).
MAX_SPANS = 1000

_OTEL_KINDS = {
    "turn": SpanKind.INTERNAL,
    "router": SpanKind.INTERNAL,
    "tool": SpanKind.CLIENT,
    "sandbox": SpanKind.INTERNAL,
}

#: Current trace id for this context (set by :func:`start_trace`).
_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "puspa_trace_id", default=None
)

#: Finished local spans (bounded ring) + lock.
_SPAN_STORE: deque = deque(maxlen=MAX_SPANS)
_SPAN_LOCK = threading.Lock()

#: True once :func:`configure_tracing` installed a provider.
_CONFIGURED = False
#: The SDK in-memory exporter, when one is installed (None otherwise).
_EXPORTER: Any = None


def otel_trace_id(task_id: str) -> int:
    """Derive a 128-bit OTel trace id from a task id.

    Deterministic: the same task id always maps to the same
    OTel trace id, so OTel spans for one task share one trace.
    """
    digest = hashlib.sha256(str(task_id).encode("utf-8")).digest()
    value = int.from_bytes(digest[:16], "big")
    return value or 1


def _random_span_id() -> int:
    return random.getrandbits(64) or 1


def configure_tracing(*, max_spans: int = MAX_SPANS) -> bool:
    """Install a local-only OTel tracer provider, if the SDK is present.

    The provider uses an in-memory span exporter: spans are kept
    in this process and never sent anywhere. Returns True when a
    recording provider is active, False when the SDK is missing
    or a provider is already installed (the local span store in
    this module still records every span either way).

    Idempotent: a second call is a no-op returning the current state.
    """
    global _CONFIGURED, _EXPORTER, _SPAN_STORE
    if _CONFIGURED:
        return _EXPORTER is not None
    if max_spans != MAX_SPANS:
        with _SPAN_LOCK:
            _SPAN_STORE = deque(_SPAN_STORE, maxlen=max_spans)
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )
    except ImportError:
        return False
    try:
        provider = trace.get_tracer_provider()
        if not hasattr(provider, "add_span_processor"):
            provider = TracerProvider(resource=Resource.create({"service.name": "puspa"}))
            trace.set_tracer_provider(provider)
        exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        _EXPORTER = exporter
        _CONFIGURED = True
        return True
    except Exception:
        return False


def installed_exporter() -> Any:
    """The SDK in-memory exporter, or None when none is installed."""
    return _EXPORTER


def start_trace(task_id: str) -> contextvars.Token:
    """Set the current trace id to ``task_id``; returns a reset token."""
    return _trace_id_var.set(str(task_id))


def end_trace(token: contextvars.Token) -> None:
    """Reset the current trace id (undo :func:`start_trace`)."""
    _trace_id_var.reset(token)


def current_trace_id() -> str | None:
    """The trace id of the current context, or None when outside a trace."""
    return _trace_id_var.get()


class _SpanHandle:
    """Handle yielded by :func:`span` for setting attributes."""

    def __init__(self, otel_span: Any | None, attributes: dict[str, Any]) -> None:
        self._otel_span = otel_span
        self.attributes = attributes

    def set_attribute(self, key: str, value: Any) -> None:
        """Set one attribute on the span (OTel span + local record)."""
        self.attributes[key] = value
        if self._otel_span is not None:
            with contextlib.suppress(Exception):
                self._otel_span.set_attribute(key, value)


def _parent_context(trace_id: str) -> Any:
    """An OTel context whose trace id is derived from ``trace_id``."""
    span_context = trace.SpanContext(
        trace_id=otel_trace_id(trace_id),
        span_id=_random_span_id(),
        is_remote=True,
        trace_flags=trace.TraceFlags(trace.TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(trace.NonRecordingSpan(span_context))


@contextlib.contextmanager
def span(
    name: str,
    *,
    kind: str = "turn",
    attributes: dict[str, Any] | None = None,
    task_id: str | None = None,
):
    """Record one span, locally and (when available) in OTel.

    The span is always appended to the local store, even when
    the OTel SDK is missing or its tracer fails: observability
    must never break the work it observes. ``kind`` is one of
    ``turn`` / ``router`` / ``tool`` / ``sandbox`` (anything else
    is recorded as ``turn``). ``task_id`` defaults to the current
    trace id; the span attributes always carry both
    ``puspa.trace_id`` and ``puspa.task_id``.
    """
    resolved_kind = kind if kind in SPAN_KINDS else "turn"
    trace_id = str(task_id or current_trace_id() or "unknown")
    attrs: dict[str, Any] = {TRACE_ID_ATTR: trace_id, TASK_ID_ATTR: trace_id}
    if attributes:
        attrs.update(attributes)
    started_mono = time.monotonic()
    started_wall = time.time()
    otel_span: Any = None
    otel_cm: Any = None
    try:
        tracer = trace.get_tracer("puspa.observability")
        otel_cm = tracer.start_as_current_span(
            name,
            context=_parent_context(trace_id),
            kind=_OTEL_KINDS[resolved_kind],
            attributes=dict(attrs),
        )
        otel_span = otel_cm.__enter__()
    except Exception:
        otel_span = None
        otel_cm = None
    status = "ok"
    error: str | None = None
    try:
        yield _SpanHandle(otel_span, attrs)
    except BaseException as exc:
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
        if otel_span is not None:
            with contextlib.suppress(Exception):
                otel_span.set_status(Status(StatusCode.ERROR, str(exc)))
        raise
    finally:
        ended_mono = time.monotonic()
        if otel_cm is not None:
            with contextlib.suppress(Exception):
                otel_cm.__exit__(None, None, None)
        record = {
            "name": name,
            "kind": resolved_kind,
            "trace_id": trace_id,
            "started_at": started_wall,
            "duration_ms": round((ended_mono - started_mono) * 1000, 3),
            "attributes": dict(attrs),
            "status": status,
            "error": error,
        }
        with _SPAN_LOCK:
            _SPAN_STORE.append(record)


def finished_spans() -> list[dict[str, Any]]:
    """A copy of the local finished-span store, oldest first."""
    with _SPAN_LOCK:
        return list(_SPAN_STORE)


def spans_for_trace(trace_id: str) -> list[dict[str, Any]]:
    """Finished spans for one trace id, in recording order."""
    wanted = str(trace_id)
    return [span for span in finished_spans() if span["trace_id"] == wanted]


def clear_spans() -> None:
    """Drop all locally recorded spans (tests use this)."""
    with _SPAN_LOCK:
        _SPAN_STORE.clear()
