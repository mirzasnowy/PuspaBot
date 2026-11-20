"""Shared rooms: create/delete, roles, membership, leaving (B6).

A room is a small group (beta-lite: 2-5 people) that shares one
Puspa agent. The room — not any single member — is the principal
the agent works for (see ``puspa/rooms/routing.py``). This module
owns identity and membership only; invites live in
``invites.py``, group memory in ``memory.py``, shared tasks in
``tasks.py``, and the money-approval rule in ``money.py``.

Three roles:

- ``owner`` — the only role that can delete the room, remove
  members, and approve a money action alone. The creator becomes
  the first owner.
- ``member`` — can invite others, record room memory, work on
  shared tasks, and approve money actions together with one other
  person.
- ``guest`` — can read the room and take part in the chat, but
  cannot invite, cannot change anything, and cannot approve a
  money action.

Rules, all enforced here (never left to the caller):

- A room has at most ``MAX_ROOM_SIZE`` members (5, beta-lite).
  Adding a sixth member raises ``RoomFullError``.
- A room always has at least one owner. Removing or demoting the
  last owner raises ``LastOwnerError``; the room is never left
  ownerless. An owner who wants to leave a room they solely own
  must delete the room instead.
- ``delete_room`` is owner-only and deletes the room row; members,
  invites, memories, tasks, approvals, splits, and channel links cascade
  in the database (``ON DELETE CASCADE`` in
  migration 0010_rooms).
- Leaving is always allowed for a non-owner member, and for an
  owner when another owner remains. Leaving is private-data
  aware: the leaver's private room memories (visibility
  ``private``) and their money approvals are deleted in the same
  transaction as the membership row, so their private data leaves
  with them. Shared contributions (room-visible notes, decisions,
  summaries) stay: they are the group's record, not the leaver's
  private data. Tasks owned by the leaver become unassigned
  instead of vanishing — the group still owes the work.
- Adding an existing member is idempotent when the role already
  matches; a re-add with a different role is a role change
  (owner-only, same rule as ``set_role``).

Privilege note: ``create_room`` inserts the room row and the first
owner membership row in one transaction as the table owner (the
connection this module opens). The RLS policies in
migration 0010_rooms have no INSERT policy for ``rooms``
and an owner-only INSERT policy for ``room_members`` on the
``puspa_app`` role, so room creation cannot run as the application
role — the same "privileged operation runs as the owner" decision
as team creation in ``puspa/teams/teams.py``. All other operations
here run the same SQL the application role would run; the RLS
policies are what stop a non-owner at the database layer too.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn

from .errors import (
    LastOwnerError,
    NotRoomMemberError,
    NotRoomOwnerError,
    RoomFullError,
    RoomNotFoundError,
)

__all__ = [
    "MAX_ROOM_SIZE",
    "ROLE_GUEST",
    "ROLE_MEMBER",
    "ROLE_OWNER",
    "Room",
    "RoomMember",
    "RoomStore",
    "add_member",
    "create_room",
    "delete_room",
    "get_room",
    "is_member",
    "is_owner",
    "leave_room",
    "list_members",
    "list_rooms_for_user",
    "member_count",
    "remove_member",
    "role_of",
    "set_role",
]

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"
ROLE_GUEST = "guest"
ROLES = (ROLE_OWNER, ROLE_MEMBER, ROLE_GUEST)

#: Beta-lite cap (plan section 12.1 / ticket B6): 2-5 people, no
#: shared funds. The minimum of 2 is a product rule for inviting,
#: not a schema fact: a room is created by one owner, who then
#: invites the second person.
MAX_ROOM_SIZE = 5

#: Roles that may invite others into the room (guests may not).
INVITER_ROLES = (ROLE_OWNER, ROLE_MEMBER)

#: Roles whose money approval counts (guests may not approve).
APPROVER_ROLES = (ROLE_OWNER, ROLE_MEMBER)


@dataclass(frozen=True)
class Room:
    """One shared room: an id, a name, who created it, when."""

    id: str
    name: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class RoomMember:
    """One membership: which room, which user, which role, since when."""

    room_id: str
    user_id: str
    role: str
    joined_at: datetime


def _validate_uuid(value, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{what} is not a valid UUID: {value!r}") from exc


def _room_row(row) -> Room:
    return Room(
        id=str(row["id"]),
        name=row["name"],
        created_by=str(row["created_by"]),
        created_at=row["created_at"],
    )


def _member_row(row) -> RoomMember:
    return RoomMember(
        room_id=str(row["room_id"]),
        user_id=str(row["user_id"]),
        role=row["role"],
        joined_at=row["joined_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


class RoomStore:
    """Postgres-backed rooms and memberships.

    One instance per call site; the DSN is fixed at construction so
    tests can point at a test database without touching env vars.
    Every method opens its own short connection.
    """

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def create_room(self, name: str, created_by_user_id: str) -> Room:
        """Create a room; the creator becomes its first (and only) owner.

        Raises ``ValueError`` for an empty name or a bad user id.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("room name must be a non-empty string")
        creator = _validate_uuid(created_by_user_id, "created_by_user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rooms (name, created_by) VALUES (%s, %s) RETURNING *",
                (name.strip(), creator),
            )
            room = _room_row(cur.fetchone())
            cur.execute(
                "INSERT INTO room_members (room_id, user_id, role) VALUES (%s, %s, %s)",
                (room.id, creator, ROLE_OWNER),
            )
            conn.commit()
        return room

    def get_room(self, room_id: str) -> Room | None:
        """Return the room, or None when the id does not exist."""
        room_id = _validate_uuid(room_id, "room_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM rooms WHERE id = %s", (room_id,))
            row = cur.fetchone()
        return _room_row(row) if row is not None else None

    def require_room(self, room_id: str) -> Room:
        """Return the room, or raise ``RoomNotFoundError``."""
        room = self.get_room(room_id)
        if room is None:
            raise RoomNotFoundError(f"room not found: {room_id}")
        return room

    def list_rooms_for_user(self, user_id: str) -> list[Room]:
        """All rooms the user belongs to, oldest first."""
        user_id = _validate_uuid(user_id, "user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT r.* FROM rooms r JOIN room_members m ON m.room_id = r.id "
                "WHERE m.user_id = %s ORDER BY r.created_at, r.id",
                (user_id,),
            )
            return [_room_row(row) for row in cur.fetchall()]

    def list_members(self, room_id: str) -> list[RoomMember]:
        """All members of a room, owners first, then by join time."""
        room = self.require_room(room_id)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM room_members WHERE room_id = %s "
                "ORDER BY (role = 'owner') DESC, (role = 'member') DESC, joined_at, user_id",
                (room.id,),
            )
            return [_member_row(row) for row in cur.fetchall()]

    def member_count(self, room_id: str) -> int:
        """How many members the room has (0 when the room is unknown)."""
        room_id = _validate_uuid(room_id, "room_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM room_members WHERE room_id = %s",
                (room_id,),
            )
            return int(cur.fetchone()["count"])

    def role_of(self, room_id: str, user_id: str) -> str | None:
        """The user's role in the room, or None when not a member."""
        room_id = _validate_uuid(room_id, "room_id")
        user_id = _validate_uuid(user_id, "user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM room_members WHERE room_id = %s AND user_id = %s",
                (room_id, user_id),
            )
            row = cur.fetchone()
        return row["role"] if row is not None else None

    def is_member(self, room_id: str, user_id: str) -> bool:
        """True when the user is a member (any role) of the room."""
        return self.role_of(room_id, user_id) is not None

    def is_owner(self, room_id: str, user_id: str) -> bool:
        """True when the user is an owner of the room."""
        return self.role_of(room_id, user_id) == ROLE_OWNER

    def can_invite(self, room_id: str, user_id: str) -> bool:
        """True when the user may invite others (owner or member)."""
        return self.role_of(room_id, user_id) in INVITER_ROLES

    def _require_owner(self, room_id: str, user_id: str) -> None:
        """Raise unless the user is an owner of the room."""
        self.require_room(room_id)
        if not self.is_owner(room_id, user_id):
            raise NotRoomOwnerError(f"user {user_id} is not an owner of room {room_id}")

    def _owner_count(self, conn, room_id: str) -> int:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM room_members WHERE room_id = %s AND role = %s",
                (room_id, ROLE_OWNER),
            )
            return int(cur.fetchone()["count"])

    def add_member(
        self, room_id: str, user_id: str, *, added_by: str, role: str = ROLE_MEMBER
    ) -> RoomMember:
        """Add a member (owner-only). Re-adding an existing member with
        the same role is a no-op; with a different role it is a role
        change under the same owner-only rule. Adding a sixth member
        raises ``RoomFullError``.

        Raises ``NotRoomOwnerError`` when ``added_by`` is not an owner,
        ``ValueError`` for an unknown role.
        """
        if role not in ROLES:
            raise ValueError(f"unknown role: {role!r}")
        room_id = _validate_uuid(room_id, "room_id")
        user_id = _validate_uuid(user_id, "user_id")
        added_by = _validate_uuid(added_by, "added_by")
        self._require_owner(room_id, added_by)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM room_members WHERE room_id = %s AND user_id = %s",
                (room_id, user_id),
            )
            existing = cur.fetchone()
            if existing is None and self._member_count_in(conn, room_id) >= MAX_ROOM_SIZE:
                raise RoomFullError(f"room {room_id} already has {MAX_ROOM_SIZE} members")
            cur.execute(
                "INSERT INTO room_members (room_id, user_id, role) VALUES (%s, %s, %s) "
                "ON CONFLICT (room_id, user_id) DO UPDATE SET role = EXCLUDED.role "
                "RETURNING *",
                (room_id, user_id, role),
            )
            member = _member_row(cur.fetchone())
            conn.commit()
        return member

    def _member_count_in(self, conn, room_id: str) -> int:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM room_members WHERE room_id = %s", (room_id,))
            return int(cur.fetchone()["count"])

    def set_role(self, room_id: str, user_id: str, role: str, *, changed_by: str) -> RoomMember:
        """Change a member's role (owner-only). Demoting the last owner
        raises ``LastOwnerError``; the room is never left ownerless.
        Promoting a guest to owner is allowed; the room may then have
        more than one owner.
        """
        if role not in ROLES:
            raise ValueError(f"unknown role: {role!r}")
        room_id = _validate_uuid(room_id, "room_id")
        user_id = _validate_uuid(user_id, "user_id")
        changed_by = _validate_uuid(changed_by, "changed_by")
        self._require_owner(room_id, changed_by)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM room_members WHERE room_id = %s AND user_id = %s",
                (room_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise NotRoomMemberError(f"user {user_id} is not a member of room {room_id}")
            if (
                row["role"] == ROLE_OWNER
                and role != ROLE_OWNER
                and self._owner_count(conn, room_id) <= 1
            ):
                raise LastOwnerError(f"cannot demote the last owner of room {room_id}")
            cur.execute(
                "UPDATE room_members SET role = %s WHERE room_id = %s AND user_id = %s RETURNING *",
                (role, room_id, user_id),
            )
            member = _member_row(cur.fetchone())
            conn.commit()
        return member

    def remove_member(self, room_id: str, user_id: str, *, removed_by: str) -> None:
        """Remove a member. Allowed to a room owner, or to the member
        themselves (leaving the room — see ``leave_room``). Removing
        the last owner raises ``LastOwnerError``. Idempotent when the
        user is not a member. Private data leaves with the removed
        member (see ``leave_room``).
        """
        room_id = _validate_uuid(room_id, "room_id")
        user_id = _validate_uuid(user_id, "user_id")
        removed_by = _validate_uuid(removed_by, "removed_by")
        self.require_room(room_id)
        if removed_by != user_id and not self.is_owner(room_id, removed_by):
            raise NotRoomOwnerError(f"user {removed_by} may not remove members of room {room_id}")
        self._remove_member_row(room_id, user_id)

    def leave_room(self, room_id: str, *, user_id: str) -> None:
        """Leave the room. The leaver's private room memories
        (visibility ``private``) and their money approvals are
        deleted in the same transaction as the membership row, so
        their private data leaves with them. Shared contributions
        (room-visible notes, decisions, summaries) stay: they are
        the group's record. Tasks owned by the leaver become
        unassigned. Leaving as the last owner raises
        ``LastOwnerError`` — delete the room instead. Idempotent
        when the user is not a member.
        """
        room_id = _validate_uuid(room_id, "room_id")
        user_id = _validate_uuid(user_id, "user_id")
        self.require_room(room_id)
        self._remove_member_row(room_id, user_id)

    def _remove_member_row(self, room_id: str, user_id: str) -> None:
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM room_members WHERE room_id = %s AND user_id = %s",
                (room_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                return
            if row["role"] == ROLE_OWNER and self._owner_count(conn, room_id) <= 1:
                raise LastOwnerError(f"cannot remove the last owner of room {room_id}")
            # Private data leaves with the member: private memories
            # and money approvals are deleted with the membership.
            cur.execute(
                "DELETE FROM room_memories WHERE room_id = %s AND author_user_id = %s "
                "AND visibility = 'private'",
                (room_id, user_id),
            )
            cur.execute(
                "DELETE FROM room_money_approvals WHERE room_id = %s AND approver_user_id = %s",
                (room_id, user_id),
            )
            # Tasks the leaver owned become unassigned; the work stays.
            cur.execute(
                "UPDATE room_tasks SET owner_user_id = NULL "
                "WHERE room_id = %s AND owner_user_id = %s",
                (room_id, user_id),
            )
            cur.execute(
                "DELETE FROM room_members WHERE room_id = %s AND user_id = %s",
                (room_id, user_id),
            )
            conn.commit()

    def delete_room(self, room_id: str, *, requested_by: str) -> None:
        """Delete the room (owner-only). Members, invites, memories,
        tasks, approvals, and channel links cascade in the database.
        """
        room_id = _validate_uuid(room_id, "room_id")
        requested_by = _validate_uuid(requested_by, "requested_by")
        self._require_owner(room_id, requested_by)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM rooms WHERE id = %s", (room_id,))
            conn.commit()


# --- Module-level functions (thin wrappers over a fresh store) -----------


def _store(dsn=None) -> RoomStore:
    return RoomStore(dsn)


def create_room(name: str, created_by_user_id: str, dsn=None) -> Room:
    return _store(dsn).create_room(name, created_by_user_id)


def get_room(room_id: str, dsn=None) -> Room | None:
    return _store(dsn).get_room(room_id)


def list_rooms_for_user(user_id: str, dsn=None) -> list[Room]:
    return _store(dsn).list_rooms_for_user(user_id)


def list_members(room_id: str, dsn=None) -> list[RoomMember]:
    return _store(dsn).list_members(room_id)


def member_count(room_id: str, dsn=None) -> int:
    return _store(dsn).member_count(room_id)


def role_of(room_id: str, user_id: str, dsn=None) -> str | None:
    return _store(dsn).role_of(room_id, user_id)


def is_member(room_id: str, user_id: str, dsn=None) -> bool:
    return _store(dsn).is_member(room_id, user_id)


def is_owner(room_id: str, user_id: str, dsn=None) -> bool:
    return _store(dsn).is_owner(room_id, user_id)


def add_member(
    room_id: str, user_id: str, *, added_by: str, role: str = ROLE_MEMBER, dsn=None
) -> RoomMember:
    return _store(dsn).add_member(room_id, user_id, added_by=added_by, role=role)


def set_role(room_id: str, user_id: str, role: str, *, changed_by: str, dsn=None) -> RoomMember:
    return _store(dsn).set_role(room_id, user_id, role, changed_by=changed_by)


def remove_member(room_id: str, user_id: str, *, removed_by: str, dsn=None) -> None:
    _store(dsn).remove_member(room_id, user_id, removed_by=removed_by)


def leave_room(room_id: str, *, user_id: str, dsn=None) -> None:
    _store(dsn).leave_room(room_id, user_id=user_id)


def delete_room(room_id: str, *, requested_by: str, dsn=None) -> None:
    _store(dsn).delete_room(room_id, requested_by=requested_by)
