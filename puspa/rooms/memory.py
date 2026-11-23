"""Group memory: three layers, one room (B6).

The agent works for the group, not for one person, so a room has
its own memory — separate from every member's personal memory:

1. **Personal layer** — each member's own memory in
   ``puspa/memory`` (episodic history, profile facts). It is never
   copied into a room and never shown to other members. This
   module never reads it.
2. **Room layer** — ``room_memories`` rows with visibility
   ``room``: notes, decisions, and summaries every member of the
   room may read. This is the group's shared record.
3. **Private-answer layer** — ``room_memories`` rows with
   visibility ``private``: a member's answer or note that only
   they may read, even inside the room. The agent may use it to
   answer that member privately, but must never repeat it to the
   group. Private rows are deleted when their author leaves the
   room (see ``RoomStore.leave_room``); the group never keeps a
   copy.

Kinds: ``note`` (a shared note), ``decision`` (a recorded group
decision, with who agreed — see ``record_decision``), ``summary``
(a chat summary the agent wrote for the room).

Rules, all enforced here:

- Only a room member may write room memory, and only for
  themselves (``author_user_id`` is always the writer).
- Only a room member may read room memory. A reader sees every
  room-visible row plus their own private rows — never another
  member's private rows. The filter is in the SQL, not in the
  caller, so a caller cannot forget it.
- A member may delete their own rows; an owner may delete any
  room-visible row (moderation). Private rows of another member
  cannot be deleted by an owner through this module — they are
  deleted only by their author, or by leaving (``leave_room``).
- Content is plain text, at most 10,000 characters: room memory
  is a short shared record, not a document store.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from puspa.accounts.ports import resolve_dsn
from puspa.db import connect as _db_connect
from puspa.db import dict_row

from .errors import GuestReadOnlyError, NotRoomMemberError
from .rooms import ROLE_GUEST, RoomStore, _validate_uuid

__all__ = [
    "MAX_MEMORY_CONTENT_CHARS",
    "MEMORY_KINDS",
    "RoomMemory",
    "RoomMemoryStore",
    "delete_memory",
    "list_room_memory",
    "record_decision",
    "record_memory",
    "record_summary",
]

MEMORY_KINDS = ("note", "decision", "summary")
VISIBILITIES = ("room", "private")
MAX_MEMORY_CONTENT_CHARS = 10_000


@dataclass(frozen=True)
class RoomMemory:
    """One room-memory row: room, author, kind, content, visibility."""

    id: str
    room_id: str
    author_user_id: str
    kind: str
    content: str
    visibility: str
    created_at: datetime


def _memory_row(row) -> RoomMemory:
    return RoomMemory(
        id=str(row["id"]),
        room_id=str(row["room_id"]),
        author_user_id=str(row["author_user_id"]),
        kind=row["kind"],
        content=row["content"],
        visibility=row["visibility"],
        created_at=row["created_at"],
    )


def _connect(dsn=None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


class RoomMemoryStore:
    """Postgres-backed room memory (three layers, one table)."""

    def __init__(self, dsn=None, room_store: RoomStore | None = None):
        self.dsn = resolve_dsn(dsn)
        self.rooms = room_store or RoomStore(dsn)

    def record_memory(
        self,
        room_id: str,
        *,
        author_user_id: str,
        content: str,
        kind: str = "note",
        visibility: str = "room",
    ) -> RoomMemory:
        """Record one room-memory row. The author must be a member;
        the row is always attributed to the author.

        Raises ``NotRoomMemberError`` for a non-member,
        ``GuestReadOnlyError`` for a guest (guests are read-only,
        plan section 12.1), ``ValueError`` for an empty/oversize
        content, an unknown kind, or an unknown visibility.
        """
        room_id = _validate_uuid(room_id, "room_id")
        author = _validate_uuid(author_user_id, "author_user_id")
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind: {kind!r}")
        if visibility not in VISIBILITIES:
            raise ValueError(f"unknown visibility: {visibility!r}")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        if len(content) > MAX_MEMORY_CONTENT_CHARS:
            raise ValueError(f"content is longer than {MAX_MEMORY_CONTENT_CHARS} characters")
        if not self.rooms.is_member(room_id, author):
            raise NotRoomMemberError(f"user {author} is not a member of room {room_id}")
        if self.rooms.role_of(room_id, author) == ROLE_GUEST:
            raise GuestReadOnlyError(f"guest {author} may not write memory to room {room_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO room_memories (room_id, author_user_id, kind, content, visibility) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING *",
                (room_id, author, kind, content.strip(), visibility),
            )
            memory = _memory_row(cur.fetchone())
            conn.commit()
        return memory

    def record_decision(
        self,
        room_id: str,
        *,
        author_user_id: str,
        decision: str,
        agreed_by_user_ids: list[str] | None = None,
    ) -> RoomMemory:
        """Record a group decision, with who agreed.

        The stored content names the decision and the members who
        agreed (by user id — the chat surface resolves names). A
        decision with no agreement list is still a decision; the
        agent records what the room decided, not a vote it forced.
        """
        agreed = [_validate_uuid(uid, "agreed_by_user_id") for uid in (agreed_by_user_ids or [])]
        content = decision.strip()
        if agreed:
            content += "\nAgreed by: " + ", ".join(agreed)
        return self.record_memory(
            room_id, author_user_id=author_user_id, content=content, kind="decision"
        )

    def record_summary(self, room_id: str, *, author_user_id: str, summary: str) -> RoomMemory:
        """Record a chat summary for the room (kind ``summary``)."""
        return self.record_memory(
            room_id, author_user_id=author_user_id, content=summary, kind="summary"
        )

    def list_room_memory(
        self, room_id: str, *, viewer_user_id: str, kind: str | None = None
    ) -> list[RoomMemory]:
        """Room memory visible to one member: every room-visible row
        plus the viewer's own private rows — never another member's
        private rows. Newest first. ``kind`` filters to one kind.

        Raises ``NotRoomMemberError`` for a non-member,
        ``ValueError`` for an unknown kind.
        """
        room_id = _validate_uuid(room_id, "room_id")
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        if kind is not None and kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind: {kind!r}")
        if not self.rooms.is_member(room_id, viewer):
            raise NotRoomMemberError(f"user {viewer} is not a member of room {room_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            sql = (
                "SELECT * FROM room_memories WHERE room_id = %s "
                "AND (visibility = 'room' OR author_user_id = %s) "
            )
            params: list = [room_id, viewer]
            if kind is not None:
                sql += "AND kind = %s "
                params.append(kind)
            sql += "ORDER BY created_at DESC, id"
            cur.execute(sql, params)
            return [_memory_row(row) for row in cur.fetchall()]

    def delete_memory(self, memory_id: str, *, deleted_by: str) -> bool:
        """Delete one memory row. Allowed to the author, or to a room
        owner when the row is room-visible. An owner may not delete
        another member's private row through this method.

        Returns True when a row was deleted, False when the id does
        not exist. Raises ``NotRoomMemberError`` when the deleter
        may not delete the row.
        """
        memory_id = _validate_uuid(memory_id, "memory_id")
        deleted_by = _validate_uuid(deleted_by, "deleted_by")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM room_memories WHERE id = %s", (memory_id,))
            row = cur.fetchone()
            if row is None:
                return False
            memory = _memory_row(row)
            is_author = memory.author_user_id == deleted_by
            is_owner = self.rooms.is_owner(memory.room_id, deleted_by)
            if not is_author and not (is_owner and memory.visibility == "room"):
                raise NotRoomMemberError(f"user {deleted_by} may not delete memory {memory_id}")
            cur.execute("DELETE FROM room_memories WHERE id = %s", (memory_id,))
            conn.commit()
        return True

    def purge_private_for_user(self, room_id: str, *, user_id: str) -> int:
        """Delete one user's private room memories in a room.

        Used by ``RoomStore.leave_room`` (which calls the same
        DELETE in its own transaction); this method is the
        standalone form for callers that need it outside a leave.
        Returns the number of rows deleted.
        """
        room_id = _validate_uuid(room_id, "room_id")
        user_id = _validate_uuid(user_id, "user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM room_memories WHERE room_id = %s AND author_user_id = %s "
                "AND visibility = 'private'",
                (room_id, user_id),
            )
            deleted = cur.rowcount
            conn.commit()
        return deleted


def _store(dsn=None) -> RoomMemoryStore:
    return RoomMemoryStore(dsn)


def record_memory(
    room_id: str,
    *,
    author_user_id: str,
    content: str,
    kind: str = "note",
    visibility: str = "room",
    dsn=None,
) -> RoomMemory:
    return _store(dsn).record_memory(
        room_id,
        author_user_id=author_user_id,
        content=content,
        kind=kind,
        visibility=visibility,
    )


def record_decision(
    room_id: str,
    *,
    author_user_id: str,
    decision: str,
    agreed_by_user_ids: list[str] | None = None,
    dsn=None,
) -> RoomMemory:
    return _store(dsn).record_decision(
        room_id,
        author_user_id=author_user_id,
        decision=decision,
        agreed_by_user_ids=agreed_by_user_ids,
    )


def record_summary(room_id: str, *, author_user_id: str, summary: str, dsn=None) -> RoomMemory:
    return _store(dsn).record_summary(room_id, author_user_id=author_user_id, summary=summary)


def list_room_memory(
    room_id: str, *, viewer_user_id: str, kind: str | None = None, dsn=None
) -> list[RoomMemory]:
    return _store(dsn).list_room_memory(room_id, viewer_user_id=viewer_user_id, kind=kind)


def delete_memory(memory_id: str, *, deleted_by: str, dsn=None) -> bool:
    return _store(dsn).delete_memory(memory_id, deleted_by=deleted_by)
