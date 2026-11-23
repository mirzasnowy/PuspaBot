"""Audit log for desktop hands (V2-35): every computer action
written to the Puspa ledger.

There is exactly one ledger in Puspa (:mod:`puspa.ledger`),
and this module writes there: it invents no second log. Each
computer action is one ``tool_called`` event on the shared
``computer`` task stream, with everything the issue asks for in
its content:

- ``computer_action``: what ran (``click``, ``type``, ``drag``,
  ...; the same names the MCP tools use);
- ``target``: which target (a ref like ``n3``, a window id, an
  app name). Never clipboard content, never typed text: those
  appear only as ``length`` (how many chars), never as content;
- ``tier``: ``read`` / ``normal`` / ``destructive``;
- ``decision``: ``allow`` / ``deny`` / ``stop``;
- ``outcome``: ``performed`` / ``not_performed`` / ``denied`` /
  ``stopped`` / ``failed``;
- the timestamp is the event's own ``created_at``.

Stop transitions follow the kill-switch precedent (existing
event types only, the closed set is untouched): requesting the
stop is ``task_stopped`` with ``computer_stop`` set, clearing
it is ``user_message`` with the same marker.

Writing is best-effort on purpose: the audit must never wedge
the hands. When the ledger is unreachable (no database on a
plain desktop install), :meth:`LedgerComputerAudit.record`
returns False and remembers the error on ``last_error`` instead
of raising, and the action still runs. The CLI list command
(``puspa computer audit``) says so plainly when the ledger
cannot be read. Tests use :class:`InMemoryComputerAudit`,
which keeps the same entries in memory with no database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from puspa.db import Error as _db_Error
from puspa.ledger import Ledger

__all__ = [
    "InMemoryComputerAudit",
    "LedgerComputerAudit",
    "audit_entry",
]

#: The ledger task stream every computer audit event shares.
AUDIT_TASK_ID = "computer"

#: The ledger agent id for computer audit events (a local tool
#: stream, not a user agent).
AUDIT_AGENT_ID = "computer"

#: Event type for one computer action attempt.
AUDIT_EVENT_TYPE = "tool_called"

#: Event type for a stop request (kill-switch precedent).
STOP_EVENT_TYPE = "task_stopped"

#: Event type for clearing the stop (kill-switch precedent).
CLEAR_EVENT_TYPE = "user_message"


def audit_entry(
    action: str,
    *,
    target: str,
    tier: str,
    decision: str,
    outcome: str,
    app: str | None = None,
    length: int | None = None,
    reason: str = "",
) -> dict:
    """One audit entry as a plain dict (the ledger content).

    ``target`` is a ref, window id, or app name: never typed
    text, never clipboard content. ``length`` is the only trace
    of typed or clipboard text: how many chars, never what they
    were.
    """
    entry: dict[str, Any] = {
        "tool": f"computer.{action}",
        "computer_action": action,
        "target": target,
        "tier": tier,
        "decision": decision,
        "outcome": outcome,
    }
    if app is not None:
        entry["app"] = app
    if length is not None:
        entry["length"] = length
    if reason:
        entry["reason"] = reason
    return entry


class LedgerComputerAudit:
    """Audit sink that writes to the Puspa ledger."""

    def __init__(
        self,
        ledger: Ledger | None = None,
        *,
        task_id: str = AUDIT_TASK_ID,
        agent_id: str = AUDIT_AGENT_ID,
    ) -> None:
        self.ledger = ledger if ledger is not None else Ledger()
        self.task_id = task_id
        self.agent_id = agent_id
        #: The last ledger error, or None when the last write
        #: worked (or nothing was written yet).
        self.last_error: str | None = None

    def record(self, entry: dict) -> bool:
        """Append one audit entry. Returns True when it landed;
        False (with ``last_error`` set) when the ledger could
        not be reached. Never raises."""
        try:
            self.ledger.append_event(
                task_id=self.task_id,
                agent_id=self.agent_id,
                event_type=AUDIT_EVENT_TYPE,
                content=dict(entry),
            )
        except (*_db_Error, OSError, ValueError) as exc:
            self.last_error = str(exc)
            return False
        self.last_error = None
        return True

    def record_stop_requested(self, *, actor: str = "user", reason: str = "") -> bool:
        """Record a stop request (``task_stopped``)."""
        return self._record_marker(STOP_EVENT_TYPE, "requested", actor=actor, reason=reason)

    def record_stop_cleared(self, *, actor: str = "user", reason: str = "") -> bool:
        """Record clearing the stop (``user_message``)."""
        return self._record_marker(CLEAR_EVENT_TYPE, "cleared", actor=actor, reason=reason)

    def _record_marker(self, event_type: str, state: str, *, actor: str, reason: str) -> bool:
        try:
            self.ledger.append_event(
                task_id=self.task_id,
                agent_id=self.agent_id,
                event_type=event_type,
                content={"computer_stop": state, "actor": actor, "reason": reason},
            )
        except (*_db_Error, OSError, ValueError) as exc:
            self.last_error = str(exc)
            return False
        self.last_error = None
        return True

    def recent(self, limit: int = 50) -> list[dict]:
        """Newest-first computer audit entries (actions and stop
        transitions). Returns [] when the ledger cannot be read;
        ``last_error`` says why."""
        try:
            events = self.ledger.recent_events(datetime(1970, 1, 1, tzinfo=UTC), limit=1000)
        except (*_db_Error, OSError, ValueError) as exc:
            self.last_error = str(exc)
            return []
        self.last_error = None
        out: list[dict] = []
        for event in events:
            content = event.get("content") or {}
            if "computer_action" in content or "computer_stop" in content:
                out.append(
                    {
                        "id": event["id"],
                        "created_at": event["created_at"].isoformat(),
                        "type": event["type"],
                        **content,
                    }
                )
        out.reverse()
        return out[: max(limit, 0)]


class InMemoryComputerAudit:
    """Audit sink that keeps entries in memory (tests)."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.last_error: str | None = None

    def record(self, entry: dict) -> bool:
        self.entries.append(dict(entry))
        return True

    def record_stop_requested(self, *, actor: str = "user", reason: str = "") -> bool:
        self.entries.append({"computer_stop": "requested", "actor": actor, "reason": reason})
        return True

    def record_stop_cleared(self, *, actor: str = "user", reason: str = "") -> bool:
        self.entries.append({"computer_stop": "cleared", "actor": actor, "reason": reason})
        return True

    def recent(self, limit: int = 50) -> list[dict]:
        return list(reversed(self.entries))[: max(limit, 0)]
