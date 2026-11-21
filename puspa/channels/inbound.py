"""The inbound pipeline: one shared path for every platform (G14).

An adapter hands the pipeline a normalised :class:`MessageEvent`;
the pipeline runs the same steps whatever the platform:

1. **Bot-loop guard** — a message from a bot, from Puspa itself,
   or from an unlinked group is never answered (groups are
   answered only through a room link, B6 — see ``group_handler``
   below).
2. **Pairing command** — a DM whose text looks like a 6-digit
   pairing code is a pairing attempt, not a task: it is verified
   against :class:`ChannelPairing` and answered with a short
   confirmation (or a short failure notice). No agent call.
3. **Idempotency** — a message whose ``dedup_key`` is already in
   the delivery ledger is dropped silently (exactly-once).
4. **Authorisation** — allowlist or pairing, else the message is
   dropped silently (the sender is told nothing, but the drop is
   recorded in the ledger as a failed inbound record so it is
   visible to the operator).
5. **Agent** — the event goes to the :class:`ChannelAgent` seam
   (TaskRunner) and the reply comes back through the adapter that
   received the message.

The pipeline never raises for a dropped message: it returns a
:class:`InboundOutcome` describing what happened (``answered``,
``dropped_bot``, ``dropped_group``, ``dropped_not_addressed``,
``dropped_duplicate``, ``dropped_unauthorized``, ``paired``,
``pairing_failed``). Only a delivery failure raises, and only after
the ledger records it — the caller decides whether to retry.

``group_handler`` (B6) is an optional callable for group
messages: ``group_handler(event, adapter=..., agent=...,
ledger=..., delivery=...) -> InboundOutcome | None``. When set,
a group message is offered to it first; a non-None outcome is
returned as-is, and None falls through to ``dropped_group``.
Without it, every group message is dropped exactly as before.
The room handler lives in the hosted service and is wired in
by the caller — the pipeline itself knows nothing about rooms.

Ledger writes are best-effort: a ledger failure must never drop
a user message, so every ledger call here is wrapped in
``contextlib.suppress``.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass

from .authz import Allowlist, ChannelPairing
from .base import ChannelAdapter
from .delivery import DeliveryService
from .errors import DeliveryError, PairingError, UnauthorizedChatError
from .events import MessageEvent

__all__ = ["InboundOutcome", "handle_inbound", "is_pairing_code_text"]

_PAIRING_CODE_RE = re.compile(r"^\s*(\d{6})\s*$")

#: Outcomes handle_inbound can return (never raises for a drop).
OUTCOMES = (
    "answered",
    "dropped_bot",
    "dropped_group",
    "dropped_not_addressed",
    "dropped_duplicate",
    "dropped_unauthorized",
    "paired",
    "pairing_failed",
)


@dataclass(frozen=True)
class InboundOutcome:
    """What the pipeline did with one inbound message."""

    outcome: str
    user_id: str | None = None
    reply: str | None = None
    detail: str = ""


def is_pairing_code_text(text: str) -> str | None:
    """The 6-digit code in a DM, or None when the text is not a code."""
    match = _PAIRING_CODE_RE.match(text or "")
    return match.group(1) if match else None


def handle_inbound(
    event: MessageEvent,
    *,
    adapter: ChannelAdapter,
    agent,
    pairing: ChannelPairing,
    allowlist: Allowlist | None = None,
    ledger=None,
    delivery: DeliveryService | None = None,
    group_handler=None,
) -> InboundOutcome:
    """Run one normalised event through the shared pipeline."""
    delivery_service = delivery or (DeliveryService(ledger) if ledger is not None else None)

    # 1. Bot-loop guard: never answer a bot or ourselves. A group
    # message goes to the group handler when one is wired in (B6:
    # a linked room answers @Puspa messages); without a handler,
    # or when the handler declines (None), it is dropped.
    if event.is_bot:
        return InboundOutcome("dropped_bot", detail="sender is a bot")
    if event.is_group:
        if group_handler is not None:
            handled = group_handler(
                event,
                adapter=adapter,
                agent=agent,
                ledger=ledger,
                delivery=delivery_service,
            )
            if handled is not None:
                return handled
        return InboundOutcome("dropped_group", detail="group messages are not answered yet")

    # 2. Pairing command: a bare 6-digit code in a DM is a pairing
    # attempt, answered directly without calling the agent.
    code = is_pairing_code_text(event.text)
    if code is not None:
        try:
            bound_user_id = pairing.verify(code, platform=event.platform, chat_id=event.chat_id)
        except PairingError as exc:
            _safe_send(
                delivery_service,
                adapter,
                event.chat_id,
                "That pairing code did not work. Please check the code in the app and try again.",
            )
            return InboundOutcome("pairing_failed", detail=str(exc))
        _safe_send(
            delivery_service,
            adapter,
            event.chat_id,
            "Paired. This chat is now connected to your Puspa account.",
        )
        return InboundOutcome("paired", user_id=bound_user_id)

    # 3. Idempotency: the same message is processed once only.
    if ledger is not None:
        with contextlib.suppress(Exception):
            if ledger.seen(event.dedup_key):
                return InboundOutcome("dropped_duplicate", detail=event.dedup_key)

    # 4. Authorisation: allowlist or pairing, else drop silently.
    try:
        user_id: str | None = pairing.authorize(event.platform, event.chat_id, allowlist=allowlist)
    except UnauthorizedChatError:
        _record_inbound(ledger, event, status="failed", error="unauthorized chat")
        return InboundOutcome("dropped_unauthorized", detail=f"{event.platform}:{event.chat_id}")

    _record_inbound(ledger, event, status="sent")

    # 5. Agent, then reply through the adapter the message came from.
    reply = agent.answer(event, user_id=user_id)
    if delivery_service is not None:
        delivery_service.send(
            adapter,
            chat_id=event.chat_id,
            text=reply,
            reply_to_dedup_key=event.dedup_key,
        )
    else:
        adapter.send_text(event.chat_id, reply)
    return InboundOutcome("answered", user_id=user_id, reply=reply)


def _record_inbound(ledger, event: MessageEvent, *, status: str, error: str | None = None) -> None:
    if ledger is None:
        return
    with contextlib.suppress(Exception):
        ledger.record(
            platform=event.platform,
            chat_id=event.chat_id,
            direction="inbound",
            status=status,
            dedup_key=event.dedup_key,
            error=error,
        )


def _safe_send(delivery, adapter, chat_id: str, text: str) -> None:
    """Best-effort send for pairing answers (never raises)."""
    with contextlib.suppress(DeliveryError, Exception):
        if delivery is not None:
            delivery.send(adapter, chat_id=chat_id, text=text)
        else:
            adapter.send_text(chat_id, text)
