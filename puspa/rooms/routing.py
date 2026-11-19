"""Routing group-chat messages to a room (B6).

The channels are DM-only by design in G14: the inbound pipeline
drops every group message (``puspa/channels/inbound.py``). This
module is the group half that G14 left open, and the pipeline
calls it through the ``group_handler`` hook — a group message is
answered only when all three of these hold:

1. **The group chat is linked to a room.** A member links a
   Telegram group (or a WhatsApp group chat id) to a room with
   ``link_channel``; the link is stored in
   ``room_channel_links``. An unlinked group is still dropped:
   Puspa never answers a group it was not invited into.
2. **The message is addressed to Puspa.** A group message counts
   as addressed when it mentions the bot (``MessageEvent.
   mentions_bot``, set by the adapters from the platform payload)
   or when its text contains ``@puspa`` (case-insensitive). A
   message that is only group chatter is dropped as
   ``dropped_not_addressed`` — the agent does not read along, and
   does not answer unprompted.
3. **The room is the principal.** The agent is called with
   ``user_id="room:<room_id>"`` — the room, not the sender — so
   the answer is the group's answer. The sender still matters for
   permission checks inside the agent (who asked, who may approve
   money), and the raw event carries the sender id.

The adapter -> MessageEvent -> core -> delivery-ledger pattern is
unchanged: this handler records the inbound message in the ledger
(best-effort, like the DM path), answers through the same agent
seam, and replies through the adapter the message came from.
Idempotency uses the same ``dedup_key`` as the DM path.

Transport notes (honest limits, see ``docs/rooms-b6.md``):

- Telegram groups work through the existing adapter: the adapter
  already marks non-private chats ``is_group=True``.
- WhatsApp groups are a postponed surface (plan section 12.4):
  the WhatsApp Cloud API has no group messages, so the adapter
  never marks a WhatsApp event ``is_group=True``. The link table
  accepts ``whatsapp`` links anyway, so the routing works the day
  a group-capable WhatsApp transport exists — but no live
  WhatsApp-group delivery is verified in this iteration, and no
  live Telegram/WhatsApp bot is verified anywhere in this repo
  (channel tests use ``MockTransport``, same as G14).
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn
from puspa.channels.delivery import DeliveryService
from puspa.channels.events import MessageEvent
from puspa.channels.inbound import InboundOutcome

from .delegation import is_board_command, render_board
from .errors import NotRoomMemberError
from .rooms import RoomStore, _validate_uuid

__all__ = [
    "RoomChannelLink",
    "RoomChannelLinker",
    "handle_group_inbound",
    "is_addressed_to_puspa",
    "link_channel",
    "resolve_room",
    "room_principal_id",
    "unlink_channel",
]

_MENTION_RE = re.compile(r"@puspa\b", re.IGNORECASE)

PLATFORMS = ("telegram", "whatsapp")


def room_principal_id(room_id: str) -> str:
    """The principal id the agent sees for a room (``room:<id>``)."""
    return f"room:{_validate_uuid(room_id, 'room_id')}"


def is_addressed_to_puspa(event: MessageEvent) -> bool:
    """True when a group message is addressed to Puspa: the adapter
    flagged a bot mention, or the text contains ``@puspa``
    (case-insensitive)."""
    if event.mentions_bot:
        return True
    return bool(_MENTION_RE.search(event.text or ""))


@dataclass(frozen=True)
class RoomChannelLink:
    """One link: a platform group chat belongs to a room."""

    platform: str
    chat_id: str
    room_id: str
    linked_by: str


def _link_row(row) -> RoomChannelLink:
    return RoomChannelLink(
        platform=row["platform"],
        chat_id=row["chat_id"],
        room_id=str(row["room_id"]),
        linked_by=str(row["linked_by"]),
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


class RoomChannelLinker:
    """Postgres-backed links from platform group chats to rooms."""

    def __init__(self, dsn=None, room_store: RoomStore | None = None):
        self.dsn = resolve_dsn(dsn)
        self.rooms = room_store or RoomStore(dsn)

    def link_channel(
        self, room_id: str, *, platform: str, chat_id: str, linked_by: str
    ) -> RoomChannelLink:
        """Link a platform group chat to a room. The linker must be
        a member of the room. Re-linking the same chat to the same
        room is a no-op; linking it to a different room moves the
        link (a group chat belongs to exactly one room).

        Raises ``NotRoomMemberError`` for a non-member,
        ``ValueError`` for an unknown platform or an empty chat id.
        """
        room_id = _validate_uuid(room_id, "room_id")
        linker = _validate_uuid(linked_by, "linked_by")
        if platform not in PLATFORMS:
            raise ValueError(f"unknown platform: {platform!r}")
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("chat_id must be a non-empty string")
        if not self.rooms.is_member(room_id, linker):
            raise NotRoomMemberError(f"user {linker} is not a member of room {room_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO room_channel_links (platform, chat_id, room_id, linked_by) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (platform, chat_id) DO UPDATE "
                "SET room_id = EXCLUDED.room_id, linked_by = EXCLUDED.linked_by "
                "RETURNING *",
                (platform, chat_id.strip(), room_id, linker),
            )
            link = _link_row(cur.fetchone())
            conn.commit()
        return link

    def unlink_channel(self, *, platform: str, chat_id: str, unlinked_by: str) -> bool:
        """Remove a channel link. The un-linker must be a member of
        the room the chat is linked to. Returns True when a link
        was removed, False when none existed.
        """
        if platform not in PLATFORMS:
            raise ValueError(f"unknown platform: {platform!r}")
        unlinked_by = _validate_uuid(unlinked_by, "unlinked_by")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM room_channel_links WHERE platform = %s AND chat_id = %s",
                (platform, chat_id),
            )
            row = cur.fetchone()
            if row is None:
                return False
            link = _link_row(row)
            if not self.rooms.is_member(link.room_id, unlinked_by):
                raise NotRoomMemberError(
                    f"user {unlinked_by} is not a member of room {link.room_id}"
                )
            cur.execute(
                "DELETE FROM room_channel_links WHERE platform = %s AND chat_id = %s",
                (platform, chat_id),
            )
            conn.commit()
        return True

    def resolve_room(self, *, platform: str, chat_id: str) -> str | None:
        """The room id a platform group chat is linked to, or None
        when the chat is not linked to any room."""
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT room_id FROM room_channel_links WHERE platform = %s AND chat_id = %s",
                (platform, chat_id),
            )
            row = cur.fetchone()
        return str(row["room_id"]) if row is not None else None

    def list_links(self, room_id: str) -> list[RoomChannelLink]:
        """All channel links of a room."""
        room_id = _validate_uuid(room_id, "room_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM room_channel_links WHERE room_id = %s ORDER BY platform, chat_id",
                (room_id,),
            )
            return [_link_row(row) for row in cur.fetchall()]


def handle_group_inbound(
    event: MessageEvent,
    *,
    adapter,
    agent,
    linker: RoomChannelLinker,
    ledger=None,
    delivery: DeliveryService | None = None,
    delegation=None,
) -> InboundOutcome | None:
    """Handle one group message for the room pipeline.

    Returns None when the event is not a group message (the caller
    falls through to its own handling). Otherwise returns an
    :class:`InboundOutcome`: ``answered`` (room principal),
    ``dropped_group`` (chat not linked to any room),
    ``dropped_not_addressed`` (no @Puspa mention), or
    ``dropped_duplicate`` (already seen). A bot sender is dropped
    as ``dropped_bot``. The handler never raises for a dropped
    message; only a delivery failure raises, after the ledger
    records it — same contract as ``handle_inbound``.

    Board command (B9): when ``delegation`` (a
    ``DelegationService``) is passed and the message asks for the
    board ("@puspa board" / "@puspa tasks" / "@puspa tugas"),
    the reply is the rendered shared task board and the agent is
    never called — the board is data, not a turn.
    """
    if not event.is_group:
        return None
    if event.is_bot:
        return InboundOutcome("dropped_bot", detail="sender is a bot")
    delivery_service = delivery or (DeliveryService(ledger) if ledger is not None else None)

    room_id = linker.resolve_room(platform=event.platform, chat_id=event.chat_id)
    if room_id is None:
        return InboundOutcome(
            "dropped_group", detail=f"no room linked to {event.platform}:{event.chat_id}"
        )
    if not is_addressed_to_puspa(event):
        return InboundOutcome("dropped_not_addressed", detail="message does not mention @puspa")

    if ledger is not None:
        with contextlib.suppress(Exception):
            if ledger.seen(event.dedup_key):
                return InboundOutcome("dropped_duplicate", detail=event.dedup_key)
    _record_inbound(ledger, event, status="sent")

    if delegation is not None and is_board_command(event.text):
        room = delegation.rooms.get_room(room_id)
        rows = delegation.board_for_chat(room_id)
        reply = render_board(room.name if room is not None else room_id, rows)
        if delivery_service is not None:
            delivery_service.send(
                adapter,
                chat_id=event.chat_id,
                text=reply,
                reply_to_dedup_key=event.dedup_key,
            )
        else:
            adapter.send_text(event.chat_id, reply)
        return InboundOutcome("answered", user_id=room_principal_id(room_id), reply=reply)

    principal = room_principal_id(room_id)
    reply = agent.answer(event, user_id=principal)
    if delivery_service is not None:
        delivery_service.send(
            adapter,
            chat_id=event.chat_id,
            text=reply,
            reply_to_dedup_key=event.dedup_key,
        )
    else:
        adapter.send_text(event.chat_id, reply)
    return InboundOutcome("answered", user_id=principal, reply=reply)


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


def _linker(dsn=None) -> RoomChannelLinker:
    return RoomChannelLinker(dsn)


def link_channel(
    room_id: str, *, platform: str, chat_id: str, linked_by: str, dsn=None
) -> RoomChannelLink:
    return _linker(dsn).link_channel(
        room_id, platform=platform, chat_id=chat_id, linked_by=linked_by
    )


def unlink_channel(*, platform: str, chat_id: str, unlinked_by: str, dsn=None) -> bool:
    return _linker(dsn).unlink_channel(platform=platform, chat_id=chat_id, unlinked_by=unlinked_by)


def resolve_room(*, platform: str, chat_id: str, dsn=None) -> str | None:
    return _linker(dsn).resolve_room(platform=platform, chat_id=chat_id)
