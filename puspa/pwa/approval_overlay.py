"""Visual approval overlay (P-11, plan section 12.10).

Visual layer over the G4 approval gate (``puspa.policy.approvals``):
before the bot clicks or acts, the element it is about to act on
is highlighted in the live view, and the user approves or rejects
that one action. An approved action runs; a rejected one is
cancelled and recorded. This module is NOT a new gate: the
taxonomy, the tiered answers, and the gate's content-hash binding
all stay where they are — this only renders the pending action on
the live frame and records the per-action decision.

Honest scope (same as P-09): there is no real desktop screenshot
to highlight. The "highlight" is a pointer into the event-based
live frame (``LiveFrame``): the frame's last tool call, the open
task, or the matching ``approval_requested`` ledger event. The
overlay never claims a pixel position it does not have, and
``screenshot`` stays null everywhere — see the module docstring
of :mod:`puspa.pwa.live_view`.

How a decision reaches the action:

- The overlay itself is the decision surface: :meth:`OverlayStore.decide`
  records the decision in the ledger (``approval_granted`` /
  ``approval_rejected`` — the closed event set already has both,
  so no migration is needed) and flips the overlay's status, so a
  decided action can never be decided twice.
- When the store is constructed with the turn loop's
  :class:`~puspa.policy.gate.Gate` (``gate=``), a rejection also
  marks the gate request rejected, so :meth:`Gate.execute` can
  never run it afterwards, and an approval approves it there.
  The PWA process holds no Gate today (same as
  ``app.state.approval``), so the PWA-side rejection is the ledger
  event plus the overlay status; the gate enforcement lands when
  the store is constructed with the loop's gate.
- Overlay state is in-memory, like the gate's requests and
  ``app.state.approval``: a process restart loses the pending
  list (the ledger events remain, and a pending approval whose
  process died must be re-proposed). Durability lives in the
  ledger, not in this store.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..evidence.proof import user_agent_ids
from ..ledger import Ledger
from ..policy.approval_cards import render_approval_card
from ..policy.approvals import classify_action
from ..policy.gate import Gate, GateDenied

if TYPE_CHECKING:
    from ..ledger import Ledger as LedgerType
    from .live_view import LiveFrame

__all__ = [
    "ApprovalOverlay",
    "OverlayError",
    "OverlayHighlight",
    "OverlayStore",
    "TargetElement",
    "build_target_element",
    "decide_overlay",
    "highlight_for",
    "list_overlays",
    "propose_overlay",
]

#: Default DSN (peer auth as the OS user, no password).
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: Decisions a user can make on one pending overlay.
DECISIONS = frozenset({"approved", "rejected"})

#: Ledger event types this module appends (all already in the
#: closed set since revision 7 — no migration for P-11).
LEDGER_PROPOSED = "approval_requested"
LEDGER_APPROVED = "approval_granted"
LEDGER_REJECTED = "approval_rejected"

#: Content keys read for the target's short human label, in order.
_TARGET_KEYS = ("recipient", "recipient_name", "to", "target", "domain", "url", "path", "name")


class OverlayError(Exception):
    """An overlay propose/list/decide was refused.

    ``status`` is the HTTP status the API layer answers with:
    403 not your agent, 404 unknown request, 409 already decided
    (or a duplicate pending request id).
    """

    def __init__(self, message: str, *, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class TargetElement:
    """The element the bot is about to act on.

    ``element_kind`` is ``"button"`` when the action names the
    button the bot is about to click, else ``"tool"`` (the tool
    it is about to run). ``label`` is the button label or the tool
    name; ``target`` is the short human label of what the action
    touches (recipient, domain, path — from the structured
    arguments, never from a model narrative); ``detail`` is the
    server-rendered card lines, so the overlay shows exactly what
    the gate binds to.
    """

    element_kind: str
    label: str
    tool_name: str
    target: str
    detail: list[dict] = field(default_factory=list)
    content_hash: str = ""

    def to_dict(self) -> dict:
        """JSON-safe dict of this target element."""
        return {
            "element_kind": self.element_kind,
            "label": self.label,
            "tool_name": self.tool_name,
            "target": self.target,
            "detail": self.detail,
            "content_hash": self.content_hash,
        }


def _target_text(content: dict) -> str:
    """Short human label of what the action touches, or ""."""
    for key in _TARGET_KEYS:
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def build_target_element(
    *,
    tool_name: str,
    content: dict,
    button_label: str | None = None,
    kind: str | None = None,
    request_id: str = "",
) -> TargetElement:
    """The target element for one pending action.

    The kind comes from the G4 classification (``kind`` may be
    None for an unrecognized action — fail-closed to asking, the
    overlay still shows what will run). The detail lines are the
    server-rendered approval card, built from the structured
    arguments only.
    """
    card = render_approval_card(
        kind=kind or "unknown",
        tool_name=tool_name,
        content=content,
        request_id=request_id,
    )
    return TargetElement(
        element_kind="button" if button_label else "tool",
        label=(button_label or "").strip() or tool_name,
        tool_name=tool_name,
        target=_target_text(content),
        detail=[line.to_dict() for line in card.lines],
        content_hash=card.content_hash,
    )


@dataclass
class OverlayHighlight:
    """Where the target element is highlighted in the live frame.

    ``frame_ref`` names the frame element: ``"last_tool"`` (the
    frame's last tool call is this action's tool),
    ``"current_task"`` (the action's task is the frame's open
    task), ``"event"`` (the matching ``approval_requested``
    ledger event is in the frame — ``event_id`` names it), or
    ``"none"`` (the target is not visible in the current frame
    yet; the overlay still shows the target, it just cannot
    point at a frame row). There is no pixel position: no
    screenshot exists to point at.
    """

    frame_ref: str
    event_id: str | None = None
    note: str = ""

    def to_dict(self) -> dict:
        """JSON-safe dict of this highlight."""
        return {
            "frame_ref": self.frame_ref,
            "event_id": self.event_id,
            "note": self.note,
        }


def highlight_for(frame: LiveFrame, overlay: ApprovalOverlay) -> OverlayHighlight:
    """The highlight for one overlay against one live frame.

    Preference order: the last tool call (the action the bot is
    literally about to run), the open task, the matching ledger
    event. ``"none"`` is honest, not an error: the action may
    not have produced a tool call or an event in this frame yet.
    """
    last_tool = frame.last_tool or {}
    if last_tool.get("tool") == overlay.tool_name:
        return OverlayHighlight(
            frame_ref="last_tool",
            note=f"the frame's last tool call is {overlay.tool_name!r}: the highlighted row",
        )
    if overlay.task_id and frame.current_task_id == overlay.task_id:
        return OverlayHighlight(
            frame_ref="current_task",
            note="the action's task is the frame's open task: the highlighted task line",
        )
    for event in frame.events:
        content = event.get("content") or {}
        if event.get("type") == LEDGER_PROPOSED and content.get("request_id") == overlay.request_id:
            return OverlayHighlight(
                frame_ref="event",
                event_id=str(event.get("id", "")),
                note="the matching approval_requested ledger event: the highlighted event row",
            )
    return OverlayHighlight(
        frame_ref="none",
        note=(
            "the target is not visible in the current frame: no matching tool call, "
            "task, or event yet — the overlay still shows the target element itself"
        ),
    )


@dataclass
class ApprovalOverlay:
    """One pending (or decided) action, shown as an overlay.

    ``status`` is ``"pending"``, ``"approved"``, or
    ``"rejected"``. ``gate_rejected`` is True when the rejection
    also marked the gate request rejected, so the action can
    never execute. The card is the server-rendered approval card
    (structured arguments only, never a model narrative).
    """

    request_id: str
    agent_id: str
    task_id: str | None
    kind: str | None
    tool_name: str
    button_label: str | None
    target_element: TargetElement
    card: dict
    status: str = "pending"
    created_at: str = ""
    decided_at: str | None = None
    decided_by: str | None = None
    decision_reason: str = ""
    gate_rejected: bool = False
    proposed_ledger_event_id: str | None = None
    decided_ledger_event_id: str | None = None

    def to_dict(self, *, highlight: OverlayHighlight | None = None) -> dict:
        """JSON-safe dict of this overlay, with its highlight."""
        return {
            "request_id": self.request_id,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "tool_name": self.tool_name,
            "button_label": self.button_label,
            "target_element": self.target_element.to_dict(),
            "card": self.card,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "decision_reason": self.decision_reason,
            "gate_rejected": self.gate_rejected,
            "proposed_ledger_event_id": self.proposed_ledger_event_id,
            "decided_ledger_event_id": self.decided_ledger_event_id,
            "highlight": highlight.to_dict() if highlight is not None else None,
        }


class OverlayStore:
    """The in-memory overlay store: one process, like the gate.

    Pending overlays live here until decided; decided ones stay
    listed so the screen can show what happened. The ledger is
    the durable record — a restart loses this store, never the
    ledger events.
    """

    def __init__(self, dsn: str | None = None, *, gate: Gate | None = None) -> None:
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
        self.gate = gate
        self._overlays: dict[str, ApprovalOverlay] = {}

    # -- helpers ------------------------------------------------------
    def _require_owner(self, user_id: str, agent_id: str) -> None:
        """403 when ``agent_id`` is not one of ``user_id``'s agents."""
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if agent_id not in user_agent_ids(user_id, dsn=self.dsn):
            raise OverlayError(f"agent {agent_id!r} does not belong to this user", status=403)

    def _get(self, request_id: str) -> ApprovalOverlay:
        """The overlay for ``request_id``, or a 404 error."""
        overlay = self._overlays.get(request_id)
        if overlay is None:
            raise OverlayError(f"unknown approval request {request_id!r}", status=404)
        return overlay

    def get(self, request_id: str) -> ApprovalOverlay | None:
        """The overlay for ``request_id``, or None when unknown."""
        return self._overlays.get(request_id)

    # -- propose ------------------------------------------------------
    def propose(
        self,
        user_id: str,
        agent_id: str,
        *,
        tool_name: str,
        content: dict,
        button_label: str | None = None,
        page_text: str | None = None,
        task_id: str | None = None,
        request_id: str | None = None,
        dsn: str | None = None,
        ledger: LedgerType | None = None,
        now: datetime | None = None,
    ) -> ApprovalOverlay:
        """Propose one action for approval: build its overlay.

        The kind comes from the G4 classification of the tool
        name and button label; a credential change is refused
        outright (it can never be approved, so there is nothing
        honest to show); an unrecognized action is proposed with
        kind ``"unknown"`` — fail-closed to asking. The proposal
        is recorded in the ledger as ``approval_requested`` with
        the request id, so the highlight can point at it later.
        A duplicate pending ``request_id`` is refused (409): the
        request id is the idempotency key, and re-proposing a
        decided action needs a new one.
        """
        resolved_dsn = dsn or self.dsn
        self._require_owner(user_id, agent_id)
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name must be a non-empty string")
        if not isinstance(content, dict):
            raise ValueError("content must be a dict of structured arguments")
        if button_label is not None and not isinstance(button_label, str):
            raise ValueError("button_label must be a string")
        if task_id is not None and not isinstance(task_id, str):
            raise ValueError("task_id must be a string")
        classification = classify_action(tool_name, button_label=button_label, page_text=page_text)
        if classification.kind == "change_credentials":
            raise ValueError(
                "credential changes are never approvable; no approval overlay is shown"
            )
        request_id = request_id or uuid.uuid4().hex
        existing = self._overlays.get(request_id)
        if existing is not None and existing.status == "pending":
            raise OverlayError(f"approval request {request_id!r} is already pending", status=409)
        if existing is not None:
            raise OverlayError(
                f"approval request {request_id!r} was already decided ({existing.status})",
                status=409,
            )
        target = build_target_element(
            tool_name=tool_name,
            content=content,
            button_label=button_label,
            kind=classification.kind,
            request_id=request_id,
        )
        card = render_approval_card(
            kind=classification.kind or "unknown",
            tool_name=tool_name,
            content=content,
            request_id=request_id,
        )
        now = now or datetime.now(UTC)
        ledger = ledger if ledger is not None else Ledger(resolved_dsn)
        event = ledger.append_event(
            task_id=task_id or agent_id,
            agent_id=agent_id,
            event_type=LEDGER_PROPOSED,
            content={
                "request_id": request_id,
                "kind": classification.kind,
                "tool_name": tool_name,
                "button_label": button_label,
                "target": target.target,
                "content_hash": target.content_hash,
            },
        )
        overlay = ApprovalOverlay(
            request_id=request_id,
            agent_id=agent_id,
            task_id=task_id,
            kind=classification.kind,
            tool_name=tool_name,
            button_label=button_label,
            target_element=target,
            card=card.to_dict(),
            status="pending",
            created_at=now.isoformat(),
            proposed_ledger_event_id=event["id"],
        )
        self._overlays[request_id] = overlay
        return overlay

    # -- list ---------------------------------------------------------
    def pending_for_agent(self, user_id: str, agent_id: str) -> list[ApprovalOverlay]:
        """Pending overlays for one of ``user_id``'s agents."""
        self._require_owner(user_id, agent_id)
        return [
            o for o in self._overlays.values() if o.agent_id == agent_id and o.status == "pending"
        ]

    def decided_for_agent(self, user_id: str, agent_id: str) -> list[ApprovalOverlay]:
        """Decided overlays for one of ``user_id``'s agents."""
        self._require_owner(user_id, agent_id)
        return [
            o for o in self._overlays.values() if o.agent_id == agent_id and o.status != "pending"
        ]

    # -- decide -------------------------------------------------------
    def decide(
        self,
        user_id: str,
        request_id: str,
        *,
        decision: str,
        reason: str = "",
        dsn: str | None = None,
        ledger: LedgerType | None = None,
        gate: Gate | None = None,
        now: datetime | None = None,
    ) -> ApprovalOverlay:
        """Decide one pending overlay: approve it, or reject it.

        Approve: when a gate is wired and holds this request, the
        gate approves it there too, so the action may run.
        Reject: the action is cancelled — when a gate is wired
        and holds this request, the gate marks it rejected, so
        ``Gate.execute`` can never run it afterwards — and the
        rejection is recorded in the ledger as
        ``approval_rejected`` with the request id and the reason.
        A decided overlay cannot be decided again (409).
        """
        resolved_dsn = dsn or self.dsn
        if decision not in DECISIONS:
            raise ValueError("decision must be 'approved' or 'rejected'")
        if not isinstance(reason, str):
            raise ValueError("reason must be a string")
        overlay = self._get(request_id)
        self._require_owner(user_id, overlay.agent_id)
        if overlay.status != "pending":
            raise OverlayError(
                f"approval request {request_id!r} was already decided ({overlay.status})",
                status=409,
            )
        gate = gate if gate is not None else self.gate
        gate_rejected = False
        if gate is not None:
            try:
                if decision == "approved":
                    gate.approve(request_id, user_id)
                else:
                    gate.reject(request_id, user_id, reason=reason)
                    gate_rejected = True
            except GateDenied as exc:
                raise OverlayError(str(exc), status=409) from exc
        now = now or datetime.now(UTC)
        ledger = ledger if ledger is not None else Ledger(resolved_dsn)
        event_type = LEDGER_APPROVED if decision == "approved" else LEDGER_REJECTED
        event = ledger.append_event(
            task_id=overlay.task_id or overlay.agent_id,
            agent_id=overlay.agent_id,
            event_type=event_type,
            content={
                "request_id": request_id,
                "kind": overlay.kind,
                "tool_name": overlay.tool_name,
                "decided_by": user_id,
                "reason": reason,
                "gate_rejected": gate_rejected,
            },
        )
        overlay.status = decision
        overlay.decided_at = now.isoformat()
        overlay.decided_by = user_id
        overlay.decision_reason = reason
        overlay.gate_rejected = gate_rejected
        overlay.decided_ledger_event_id = event["id"]
        return overlay


# -- module-level helpers (same shape as live_view's) -------------------


def propose_overlay(
    user_id: str,
    agent_id: str,
    *,
    tool_name: str,
    content: dict,
    button_label: str | None = None,
    page_text: str | None = None,
    task_id: str | None = None,
    request_id: str | None = None,
    dsn: str | None = None,
    ledger: LedgerType | None = None,
    store: OverlayStore | None = None,
    now: datetime | None = None,
) -> ApprovalOverlay:
    """Propose one action for approval through a store (default: fresh)."""
    store = store if store is not None else OverlayStore(dsn)
    return store.propose(
        user_id,
        agent_id,
        tool_name=tool_name,
        content=content,
        button_label=button_label,
        page_text=page_text,
        task_id=task_id,
        request_id=request_id,
        dsn=dsn,
        ledger=ledger,
        now=now,
    )


def decide_overlay(
    user_id: str,
    request_id: str,
    *,
    decision: str,
    reason: str = "",
    dsn: str | None = None,
    ledger: LedgerType | None = None,
    store: OverlayStore | None = None,
    gate: Gate | None = None,
    now: datetime | None = None,
) -> ApprovalOverlay:
    """Decide one pending overlay through a store (default: fresh)."""
    store = store if store is not None else OverlayStore(dsn)
    return store.decide(
        user_id,
        request_id,
        decision=decision,
        reason=reason,
        dsn=dsn,
        ledger=ledger,
        gate=gate,
        now=now,
    )


def list_overlays(
    user_id: str,
    agent_id: str,
    *,
    dsn: str | None = None,
    store: OverlayStore | None = None,
    frame: LiveFrame | None = None,
) -> dict:
    """Pending + decided overlays for one agent, with highlights.

    ``frame`` is the agent's current live frame when the caller
    has one: each overlay's highlight is resolved against it, so
    the screen knows which frame row to mark. Without a frame the
    overlays carry ``highlight: None`` and the screen shows the
    target element without a frame pointer.
    """
    store = store if store is not None else OverlayStore(dsn)
    pending = store.pending_for_agent(user_id, agent_id)
    decided = store.decided_for_agent(user_id, agent_id)

    def _with_highlight(overlay: ApprovalOverlay) -> dict:
        highlight = highlight_for(frame, overlay) if frame is not None else None
        return overlay.to_dict(highlight=highlight)

    return {
        "agent_id": agent_id,
        "pending": [_with_highlight(o) for o in pending],
        "decided": [_with_highlight(o) for o in decided],
    }
