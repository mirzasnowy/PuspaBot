"""Usage traces as span trees (G12).

The DuskBot ``server/usage-trace/`` pattern, rewritten for
Puspa: one task's ledger events become a tree of spans — a task
span, tool spans nested under it, message spans as leaves — so
a run can be inspected the way a trace viewer inspects a
request.

Span identity reuses ledger event ids: a span's id is the event
id it came from, so a span can always be traced back to the
hash-chained record. A tool span pairs a ``tool_called`` event
with the next ``tool_completed`` event for the same tool (the
chassis logs both with ``content.tool``); an unpaired call is
still a span, with ``ended_at`` None — it is visible as an
unfinished call, not dropped.

``parent_id`` on ledger events is honoured when it points at
another event of the same task; otherwise the span attaches to
the task span. The tree is built purely from the events given —
no database, no clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

__all__ = ["Span", "Trace", "trace_from_events"]


@dataclass
class Span:
    """One span: a timed unit of work inside a task."""

    span_id: str
    parent_id: str | None
    name: str
    kind: str  # "task" | "tool" | "message" | "event"
    started_at: str | None
    ended_at: str | None = None
    attributes: dict = field(default_factory=dict)
    children: list[Span] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        """Span length in seconds, or None when open-ended."""
        if not self.started_at or not self.ended_at:
            return None
        try:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.ended_at)
        except ValueError:
            return None
        return (end - start).total_seconds()

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "attributes": dict(self.attributes),
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class Trace:
    """The span tree of one task."""

    task_id: str
    root: Span | None = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "root": self.root.to_dict() if self.root is not None else None,
        }

    def to_json(self) -> str:
        """Canonical JSON of the tree (sorted keys, no whitespace)."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def flatten(self) -> list[dict]:
        """All spans in depth-first order, children nested out."""
        flat: list[dict] = []

        def walk(span: Span) -> None:
            data = span.to_dict()
            data.pop("children")
            flat.append(data)
            for child in span.children:
                walk(child)

        if self.root is not None:
            walk(self.root)
        return flat


def _iso(moment) -> str | None:
    if isinstance(moment, datetime):
        return moment.isoformat()
    if isinstance(moment, str):
        return moment
    return None


def _content(event: dict) -> dict:
    content = event.get("content")
    return content if isinstance(content, dict) else {}


def trace_from_events(events: list[dict], task_id: str) -> Trace:
    """Build the span tree of one task from its ledger events.

    Events of other tasks are ignored. Events with no usable
    timestamp still appear (with ``started_at`` None) so the
    trace never silently drops work.
    """
    task_events = [e for e in events if e.get("task_id") == task_id]
    if not task_events:
        return Trace(task_id=task_id, root=None)
    created = next(
        (e for e in task_events if e.get("type") == "task_created"),
        task_events[0],
    )
    terminal = next(
        (
            e
            for e in reversed(task_events)
            if e.get("type") in ("task_completed", "task_failed", "task_stopped")
        ),
        None,
    )
    root = Span(
        span_id=str(created.get("id")),
        parent_id=None,
        name=f"task:{task_id}",
        kind="task",
        started_at=_iso(created.get("created_at")),
        ended_at=_iso(terminal.get("created_at")) if terminal is not None else None,
        attributes={"task_id": task_id},
    )
    spans: dict[str, Span] = {root.span_id: root}
    pending_calls: dict[str, list[Span]] = {}

    def attach(span: Span, event: dict) -> None:
        # Honour parent_id when it names an earlier span of this
        # task; otherwise attach to the task span. (A parent_id
        # naming a later event is not resolvable in one pass and
        # also lands on the task span.)
        parent_id = event.get("parent_id")
        parent = spans.get(str(parent_id)) if parent_id is not None else None
        target = parent if parent is not None else root
        span.parent_id = target.span_id
        target.children.append(span)
        spans[span.span_id] = span

    for event in task_events:
        if event is created:
            continue
        event_type = event.get("type")
        event_id = str(event.get("id"))
        started = _iso(event.get("created_at"))
        content = _content(event)
        if event_type == "tool_called":
            tool = str(content.get("tool") or "?")
            span = Span(
                span_id=event_id,
                parent_id=None,
                name=f"tool:{tool}",
                kind="tool",
                started_at=started,
                attributes={"tool": tool, "task_id": task_id},
            )
            attach(span, event)
            pending_calls.setdefault(tool, []).append(span)
        elif event_type == "tool_completed":
            tool = str(content.get("tool") or "?")
            queue = pending_calls.get(tool) or []
            completed_span: Span | None = queue.pop(0) if queue else None
            if completed_span is None:
                completed_span = Span(
                    span_id=event_id,
                    parent_id=None,
                    name=f"tool:{tool}",
                    kind="tool",
                    started_at=started,
                    attributes={"tool": tool, "task_id": task_id},
                )
                attach(completed_span, event)
            completed_span.ended_at = started
            completed_span.attributes["status"] = str(content.get("status") or "?")
        elif event_type in ("user_message", "agent_message"):
            kind = "message"
            span = Span(
                span_id=event_id,
                parent_id=None,
                name=event_type,
                kind=kind,
                started_at=started,
                ended_at=started,
                attributes={"task_id": task_id},
            )
            attach(span, event)
        elif event is terminal:
            continue
        else:
            span = Span(
                span_id=event_id,
                parent_id=None,
                name=str(event_type),
                kind="event",
                started_at=started,
                ended_at=started,
                attributes={"task_id": task_id},
            )
            attach(span, event)
    return Trace(task_id=task_id, root=root)
