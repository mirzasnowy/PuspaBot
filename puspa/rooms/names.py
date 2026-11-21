"""Group chat names: per-user display names and per-bot names (V2-27).

The room data layer identifies members only by ``user_id`` UUID,
and the personal agent name ``Puspa`` was hardcoded wherever the
chat surface needed a name. This module is the one shared name
resolver both surfaces read from, so a name is resolved the same
way in member lists, message authors, memory authors, and
decisions:

- **User display names** live on the ``users`` row
  (``display_name``, migration 0032). ``users`` is already the
  identity system every room member belongs to, so no second
  identity system is added. A user edits only their own name;
  the edit is visible on the next render, because every read
  here hits the database (nothing is cached).
- **Bot names** come from the bot's own persona files
  (``puspa/persona/files.py``, V2-25): the ``Name:`` line of
  ``IDENTITY.md``, default ``Puspa``. Each user has their own
  bot with their own home, so two users in one room can have
  differently named bots; the caller passes each member's home
  (``bot_homes``), and a missing entry falls back to the
  process home, which is the single-user local case.
- **Fallbacks never show a raw UUID.** A user with no display
  name is ``Anggota N`` inside a room (N is their position in
  the member list, owners first, then join order, so the name
  is stable when other members rename themselves) and plain
  ``Anggota`` without room context. A bot with no name is
  ``Puspa``. Text that already embeds ids (a decision's
  ``Agreed by: <uuid>, <uuid>`` line, written by
  ``RoomMemoryStore.record_decision``) is rendered through
  :func:`render_names_in_text`, which replaces every UUID-shaped
  token with its resolved name (or the fallback), so no raw
  UUID survives into a rendered surface.

Nothing here writes room data: membership, roles, and memory
rows stay owned by ``rooms.py`` and ``memory.py``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.ports import resolve_dsn
from puspa.persona.files import DEFAULT_NAME as DEFAULT_BOT_NAME
from puspa.persona.files import load_persona_files

from .memory import RoomMemory
from .rooms import RoomStore, _validate_uuid

__all__ = [
    "DEFAULT_BOT_NAME",
    "FALLBACK_MEMBER_NAME",
    "MAX_DISPLAY_NAME_CHARS",
    "DisplayNameStore",
    "NamedRoomMember",
    "NamedRoomMemory",
    "RoomNames",
    "create_schema",
    "get_display_name",
    "get_display_names",
    "render_names_in_text",
    "resolve_bot_name",
    "resolve_memory_authors",
    "resolve_room_bot_names",
    "resolve_room_members",
    "resolve_room_names",
    "resolve_user_names",
    "set_display_name",
    "validate_display_name",
]

#: Longest display name the member list can render.
MAX_DISPLAY_NAME_CHARS = 100

#: What a user with no display name is called without room context.
FALLBACK_MEMBER_NAME = "Anggota"

#: One UUID, as it appears in stored text (lowercase or uppercase).
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def validate_display_name(name) -> str:
    """Clean one display name, or raise ``ValueError``.

    The name is stripped; an empty result or a name longer than
    :data:`MAX_DISPLAY_NAME_CHARS` is rejected. The same cap is
    enforced by the database CHECK in migration 0032.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("display name must be a non-empty string")
    cleaned = name.strip()
    if len(cleaned) > MAX_DISPLAY_NAME_CHARS:
        raise ValueError(f"display name is longer than {MAX_DISPLAY_NAME_CHARS} characters")
    return cleaned


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def create_schema(dsn=None):
    """Apply the display-name column (migration 0032).

    Idempotent; safe to call before every test.
    """
    from puspa.migrations import apply_migrations

    apply_migrations(resolve_dsn(dsn), target="0032")


class DisplayNameStore:
    """Postgres-backed per-user display names (``users.display_name``).

    One instance per call site; the DSN is fixed at construction
    so tests can point at a test database without touching env
    vars. Every method opens its own short connection, and every
    read hits the database, so a name edit is visible to the
    very next render.
    """

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def set_display_name(self, user_id: str, name: str, *, edited_by: str | None = None) -> str:
        """Set a user's own display name; returns the stored name.

        ``edited_by`` defaults to ``user_id``: a user edits only
        their own name, and an edit by anyone else raises
        ``PermissionError``. Raises ``ValueError`` for an unknown
        user, an empty name, or a name over the length cap.
        """
        user_id = _validate_uuid(user_id, "user_id")
        editor = _validate_uuid(edited_by, "edited_by") if edited_by is not None else user_id
        if editor != user_id:
            raise PermissionError("a display name may only be set by its own user")
        cleaned = validate_display_name(name)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET display_name = %s WHERE id = %s RETURNING display_name",
                (cleaned, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"unknown user: {user_id}")
            conn.commit()
        return row["display_name"]

    def get_display_name(self, user_id: str) -> str | None:
        """The user's display name, or None when none is set."""
        user_id = _validate_uuid(user_id, "user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT display_name FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown user: {user_id}")
        return row["display_name"]

    def get_display_names(self, user_ids) -> dict[str, str]:
        """Display names for a batch of users: only the set ones.

        Unknown ids are skipped silently (a leaver's row may be
        gone); callers that need a name for every id resolve the
        gaps through :func:`resolve_user_names` instead.
        """
        ids = [_validate_uuid(uid, "user_id") for uid in user_ids]
        if not ids:
            return {}
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, display_name FROM users "
                "WHERE id = ANY(%s) AND display_name IS NOT NULL",
                (ids,),
            )
            return {str(row["id"]): row["display_name"] for row in cur.fetchall()}


def _store(dsn=None) -> DisplayNameStore:
    return DisplayNameStore(dsn)


def set_display_name(user_id: str, name: str, *, edited_by: str | None = None, dsn=None) -> str:
    return _store(dsn).set_display_name(user_id, name, edited_by=edited_by)


def get_display_name(user_id: str, dsn=None) -> str | None:
    return _store(dsn).get_display_name(user_id)


def get_display_names(user_ids, dsn=None) -> dict[str, str]:
    return _store(dsn).get_display_names(user_ids)


def resolve_user_names(user_ids, dsn=None) -> dict[str, str]:
    """A display name for every id: the set name, else ``Anggota``.

    Never returns a raw UUID, and never raises for an unknown id
    (a member who left still needs a readable name on the shared
    record they left behind).
    """
    ids = [_validate_uuid(uid, "user_id") for uid in user_ids]
    known = _store(dsn).get_display_names(ids)
    return {uid: known.get(uid, FALLBACK_MEMBER_NAME) for uid in ids}


@dataclass(frozen=True)
class NamedRoomMember:
    """One room member with the name the surface shows for them."""

    room_id: str
    user_id: str
    role: str
    joined_at: object
    display_name: str


def resolve_room_members(
    room_id: str, dsn=None, *, room_store: RoomStore | None = None
) -> list[NamedRoomMember]:
    """Members of a room with display names, in member-list order.

    Owners first, then join order (the same order as
    ``RoomStore.list_members``). A member with no display name
    is ``Anggota N``, where N is their 1-based position in this
    list, so the placeholder is stable when other members rename
    themselves. Raises ``RoomNotFoundError`` for an unknown room.
    """
    store = room_store or RoomStore(dsn)
    members = store.list_members(room_id)
    names = _store(dsn).get_display_names([m.user_id for m in members])
    return [
        NamedRoomMember(
            room_id=member.room_id,
            user_id=member.user_id,
            role=member.role,
            joined_at=member.joined_at,
            display_name=names.get(member.user_id, f"{FALLBACK_MEMBER_NAME} {index + 1}"),
        )
        for index, member in enumerate(members)
    ]


def resolve_bot_name(user_id: str, *, bot_home=None) -> str:
    """The bot name for one user's bot, from its persona files.

    ``bot_home`` is the Puspa home holding that user's bot
    (``IDENTITY.md`` there, V2-25). With no home given, the
    process home is read, which is the single-user local case.
    The persona loader supplies the default ``Puspa`` when no
    file names the bot, so this never returns an empty name.
    """
    _validate_uuid(user_id, "user_id")
    return load_persona_files(home=bot_home).name


def resolve_room_bot_names(
    room_id: str,
    dsn=None,
    *,
    room_store: RoomStore | None = None,
    bot_homes: dict[str, str | Path] | None = None,
) -> dict[str, str]:
    """Each member's bot name, keyed by user id.

    ``bot_homes`` maps a user id to the Puspa home holding that
    user's bot; a member with no entry reads the process home.
    Two members whose bots are named differently get different
    names here, so no two bots in one room show the same
    hardcoded name.
    """
    store = room_store or RoomStore(dsn)
    members = store.list_members(room_id)
    homes = bot_homes or {}
    return {
        member.user_id: resolve_bot_name(member.user_id, bot_home=homes.get(member.user_id))
        for member in members
    }


@dataclass(frozen=True)
class RoomNames:
    """Every name a room surface needs, resolved in one place.

    ``members`` carries the display names in member-list order,
    ``bot_names`` maps each member to their bot's name, and
    ``names`` maps each member to their display name (including
    the ``Anggota N`` fallbacks). :meth:`name_of` answers "what
    do we call this user id" for any id, member or not.
    """

    members: list[NamedRoomMember]
    bot_names: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)

    def name_of(self, user_id: str) -> str:
        """The display name for one id, or ``Anggota`` when unknown."""
        return self.names.get(str(user_id), FALLBACK_MEMBER_NAME)


def resolve_room_names(
    room_id: str,
    dsn=None,
    *,
    room_store: RoomStore | None = None,
    bot_homes: dict[str, str | Path] | None = None,
) -> RoomNames:
    """Resolve a room's member names and bot names together."""
    members = resolve_room_members(room_id, dsn, room_store=room_store)
    bot_names = resolve_room_bot_names(room_id, dsn, room_store=room_store, bot_homes=bot_homes)
    return RoomNames(
        members=members,
        bot_names=bot_names,
        names={m.user_id: m.display_name for m in members},
    )


def render_names_in_text(text: str, names: dict[str, str]) -> str:
    """Replace UUID-shaped tokens in stored text with names.

    A decision's ``Agreed by: <uuid>, <uuid>`` line is the main
    case: every id the caller resolved is replaced with its
    name, and any other UUID-shaped token becomes ``Anggota``,
    so rendered text never shows a raw UUID. Text with no ids
    passes through unchanged.
    """
    if not isinstance(text, str) or not _UUID_RE.search(text):
        return text

    def _replace(match: re.Match) -> str:
        token = match.group(0)
        try:
            key = str(uuid.UUID(token))
        except ValueError:
            return token
        return names.get(key, names.get(token, FALLBACK_MEMBER_NAME))

    return _UUID_RE.sub(_replace, text)


@dataclass(frozen=True)
class NamedRoomMemory:
    """One room-memory row with the names the surface shows.

    ``author_display_name`` is who wrote the row; ``content``
    is the stored text with every embedded id (a decision's
    agreed-by list) replaced by its resolved name.
    """

    id: str
    room_id: str
    author_user_id: str
    author_display_name: str
    kind: str
    content: str
    visibility: str
    created_at: object


def resolve_memory_authors(memories: list[RoomMemory], dsn=None) -> list[NamedRoomMemory]:
    """Attach resolved author names and rendered content to rows.

    The author of every row gets a display name (``Anggota``
    when unset), and the content is rendered through
    :func:`render_names_in_text` with the same name map, so a
    decision's agreed-by list shows names instead of ids.
    """
    ids = {m.author_user_id for m in memories}
    for memory in memories:
        for token in _UUID_RE.findall(memory.content):
            ids.add(str(uuid.UUID(token)))
    names = resolve_user_names(sorted(ids), dsn=dsn)
    return [
        NamedRoomMemory(
            id=memory.id,
            room_id=memory.room_id,
            author_user_id=memory.author_user_id,
            author_display_name=names.get(memory.author_user_id, FALLBACK_MEMBER_NAME),
            kind=memory.kind,
            content=render_names_in_text(memory.content, names),
            visibility=memory.visibility,
            created_at=memory.created_at,
        )
        for memory in memories
    ]
