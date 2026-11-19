"""Room invitations: invite by link or by one-time code (B6).

Pattern (``puspa/teams/invitations.py``, rewritten for rooms):
an owner or member invites someone into a room; the invitee
receives a secret and becomes a member by presenting it. Only
the SHA-256 hash of the secret is stored — a database leak does
not yield usable invitations.

Two kinds of invite, one table:

- ``link`` — a random token (``secrets.token_urlsafe(32)``),
  returned exactly once at issue time together with a join link.
  TTL is 7 days.
- ``code`` — a 6-digit one-time code, meant to be read out in a
  group chat or over the phone. TTL is 24 hours. The code is
  returned exactly once at issue time and is never written
  anywhere; only its hash is stored.

Both kinds share the same rules:

- Single use: accepting consumes the invitation (status
  ``accepted``) in the same transaction that inserts the
  membership, so one invite can never mint two memberships.
- Idempotent acceptance: accepting the same invite twice (same
  user, or a user who is already a member) still yields exactly
  one membership row. The second accept returns
  ``already_member=True`` instead of failing — a double-click or
  a retried request must not error.
- Revocation: an owner or member can revoke a pending invitation;
  a revoked invite is dead immediately.
- Expiry: an expired invite is marked ``expired`` on first sight
  and can never be accepted afterwards.
- Role: the invite carries the role the invitee gets (``member``
  or ``guest``). Guests are invited, never owners: ownership is
  granted only by an owner through ``RoomStore.set_role``.
- Room size: accepting an invite into a full room (5 members)
  raises ``RoomFullError`` and leaves the invite pending, so the
  invite can still be used after someone leaves.

Privilege note: acceptance inserts into ``room_members``, which
the invitee cannot do through the RLS policy (they are not a
member yet). Acceptance therefore runs as the table owner, like
room creation and team invitation acceptance — it validates the
hash first, then inserts the membership and marks the invitation
used in one transaction. Invitation issue/revoke/list run the
same SQL the application role would run, with the inviter's
membership checked in Python first (the RLS policy enforces it
at the database layer too).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn

from .errors import InviteError, NotRoomMemberError, RoomFullError
from .rooms import (
    MAX_ROOM_SIZE,
    ROLE_GUEST,
    ROLE_MEMBER,
    RoomStore,
    _validate_uuid,
)

__all__ = [
    "CODE_TTL_HOURS",
    "INVITE_LINK_TTL_DAYS",
    "JOIN_LINK_BASE",
    "RoomInvite",
    "RoomInviteStore",
    "accept_invite",
    "accept_invite_code",
    "get_invite",
    "issue_invite_code",
    "issue_invite_link",
    "list_invites",
    "purge_expired",
    "revoke_invite",
]

INVITE_LINK_TTL_DAYS = 7
CODE_TTL_HOURS = 24
JOIN_LINK_BASE = "https://puspa.app/join"

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REVOKED = "revoked"
STATUS_EXPIRED = "expired"

INVITE_ROLES = (ROLE_MEMBER, ROLE_GUEST)


@dataclass(frozen=True)
class RoomInvite:
    """One room invitation: room, inviter, kind, role, status, expiry.

    The token / code itself is never stored and never appears here.
    """

    id: str
    room_id: str
    inviter_user_id: str
    kind: str
    role: str
    status: str
    created_at: datetime
    expires_at: datetime


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _invite_row(row) -> RoomInvite:
    return RoomInvite(
        id=str(row["id"]),
        room_id=str(row["room_id"]),
        inviter_user_id=str(row["inviter_user_id"]),
        kind=row["kind"],
        role=row["role"],
        status=row["status"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _new_code() -> str:
    """A 6-digit one-time code (zero-padded, may start with 0)."""
    return f"{secrets.randbelow(1_000_000):06d}"


class RoomInviteStore:
    """Postgres-backed room invitations (hash-only secrets)."""

    def __init__(self, dsn=None, room_store: RoomStore | None = None):
        self.dsn = resolve_dsn(dsn)
        self.rooms = room_store or RoomStore(dsn)

    def issue_invite_link(
        self,
        room_id: str,
        *,
        inviter_user_id: str,
        role: str = ROLE_MEMBER,
        ttl_days: int = INVITE_LINK_TTL_DAYS,
        join_link_base: str = JOIN_LINK_BASE,
    ) -> dict:
        """Issue a link invite. Returns ``{"invite_id", "token",
        "join_link", "expires_at"}`` — the token is returned exactly
        once here, inside the join link.

        Raises ``NotRoomMemberError`` when the inviter is not a
        member (guests may not invite), ``ValueError`` for a bad
        role or TTL.
        """
        room_id = _validate_uuid(room_id, "room_id")
        inviter = _validate_uuid(inviter_user_id, "inviter_user_id")
        if role not in INVITE_ROLES:
            raise ValueError(f"invite role must be one of {INVITE_ROLES}: {role!r}")
        if ttl_days <= 0:
            raise ValueError("ttl_days must be positive")
        if not self.rooms.can_invite(room_id, inviter):
            raise NotRoomMemberError(f"user {inviter} may not invite into room {room_id}")
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(days=ttl_days)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO room_invites "
                "(room_id, inviter_user_id, kind, role, token_hash, expires_at) "
                "VALUES (%s, %s, 'link', %s, %s, %s) RETURNING id",
                (room_id, inviter, role, _sha256_hex(token), expires_at),
            )
            invite_id = str(cur.fetchone()["id"])
            conn.commit()
        return {
            "invite_id": invite_id,
            "token": token,
            "join_link": f"{join_link_base.rstrip('/')}/{token}",
            "expires_at": expires_at,
        }

    def issue_invite_code(
        self,
        room_id: str,
        *,
        inviter_user_id: str,
        role: str = ROLE_MEMBER,
        ttl_hours: int = CODE_TTL_HOURS,
    ) -> dict:
        """Issue a one-time 6-digit code invite. Returns
        ``{"invite_id", "code", "expires_at"}`` — the code is
        returned exactly once here and never stored.

        Same permission and role rules as ``issue_invite_link``.
        """
        room_id = _validate_uuid(room_id, "room_id")
        inviter = _validate_uuid(inviter_user_id, "inviter_user_id")
        if role not in INVITE_ROLES:
            raise ValueError(f"invite role must be one of {INVITE_ROLES}: {role!r}")
        if ttl_hours <= 0:
            raise ValueError("ttl_hours must be positive")
        if not self.rooms.can_invite(room_id, inviter):
            raise NotRoomMemberError(f"user {inviter} may not invite into room {room_id}")
        code = _new_code()
        expires_at = datetime.now(UTC) + timedelta(hours=ttl_hours)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO room_invites "
                "(room_id, inviter_user_id, kind, role, token_hash, expires_at) "
                "VALUES (%s, %s, 'code', %s, %s, %s) RETURNING id",
                (room_id, inviter, role, _sha256_hex(code), expires_at),
            )
            invite_id = str(cur.fetchone()["id"])
            conn.commit()
        return {"invite_id": invite_id, "code": code, "expires_at": expires_at}

    def get_invite(self, invite_id: str) -> RoomInvite | None:
        """Return the invite, or None when the id does not exist."""
        invite_id = _validate_uuid(invite_id, "invite_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM room_invites WHERE id = %s", (invite_id,))
            row = cur.fetchone()
        return _invite_row(row) if row is not None else None

    def list_invites(self, room_id: str) -> list[RoomInvite]:
        """All invites of a room, newest first."""
        room_id = _validate_uuid(room_id, "room_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM room_invites WHERE room_id = %s ORDER BY created_at DESC, id",
                (room_id,),
            )
            return [_invite_row(row) for row in cur.fetchall()]

    def revoke_invite(self, invite_id: str, *, revoked_by: str) -> RoomInvite:
        """Revoke a pending invite (owner or member only). Revoking
        an already accepted/revoked/expired invite is a no-op that
        returns the current row.

        Raises ``InviteError`` for an unknown id,
        ``NotRoomMemberError`` when the revoker may not invite.
        """
        invite_id = _validate_uuid(invite_id, "invite_id")
        revoked_by = _validate_uuid(revoked_by, "revoked_by")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM room_invites WHERE id = %s", (invite_id,))
            row = cur.fetchone()
            if row is None:
                raise InviteError(f"invite not found: {invite_id}")
            invite = _invite_row(row)
            if not self.rooms.can_invite(invite.room_id, revoked_by):
                raise NotRoomMemberError(
                    f"user {revoked_by} may not revoke invites of room {invite.room_id}"
                )
            if invite.status == STATUS_PENDING:
                cur.execute(
                    "UPDATE room_invites SET status = %s WHERE id = %s RETURNING *",
                    (STATUS_REVOKED, invite_id),
                )
                invite = _invite_row(cur.fetchone())
            conn.commit()
        return invite

    def accept_invite(self, token: str, *, user_id: str) -> dict:
        """Accept a link invite token and join the room.

        Returns ``{"room_id", "invite_id", "role", "already_member"}``.
        Idempotent: accepting twice (or accepting while already a
        member) still leaves exactly one membership row.

        Raises ``InviteError`` for an unknown, expired, revoked, or
        already-accepted token, ``RoomFullError`` when the room is
        full (the invite stays pending in that case).
        """
        return self._accept(token, user_id=user_id, kind="link")

    def accept_invite_code(self, code: str, *, user_id: str) -> dict:
        """Accept a one-time 6-digit code invite and join the room.

        Same return value and rules as ``accept_invite``.
        """
        if not isinstance(code, str) or not code.strip().isdigit():
            raise InviteError("invite code unknown, expired, or revoked")
        return self._accept(code.strip(), user_id=user_id, kind="code")

    def _accept(self, secret: str, *, user_id: str, kind: str) -> dict:
        user_id = _validate_uuid(user_id, "user_id")
        if not isinstance(secret, str) or not secret:
            raise InviteError("invite unknown, expired, or revoked")
        now = datetime.now(UTC)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM room_invites WHERE token_hash = %s AND kind = %s",
                (_sha256_hex(secret), kind),
            )
            row = cur.fetchone()
            if row is None:
                raise InviteError("invite unknown, expired, or revoked")
            invite = _invite_row(row)
            if invite.status == STATUS_REVOKED:
                raise InviteError("invite was revoked")
            if invite.status == STATUS_ACCEPTED:
                # Idempotent for the user who already accepted (a
                # double-click or a retried request): exactly one
                # membership row exists, so report success. A different
                # user presenting a consumed invite is refused — the
                # invite was spent by someone else.
                if self.rooms.is_member(invite.room_id, user_id):
                    return {
                        "room_id": invite.room_id,
                        "invite_id": invite.id,
                        "role": invite.role,
                        "already_member": True,
                    }
                raise InviteError("invite was already accepted")
            if invite.status == STATUS_EXPIRED or invite.expires_at <= now:
                cur.execute(
                    "UPDATE room_invites SET status = %s WHERE id = %s",
                    (STATUS_EXPIRED, invite.id),
                )
                conn.commit()
                raise InviteError("invite expired")
            already_member = self.rooms.is_member(invite.room_id, user_id)
            if not already_member:
                cur.execute(
                    "SELECT COUNT(*) FROM room_members WHERE room_id = %s",
                    (invite.room_id,),
                )
                if int(cur.fetchone()["count"]) >= MAX_ROOM_SIZE:
                    conn.commit()
                    raise RoomFullError(
                        f"room {invite.room_id} already has {MAX_ROOM_SIZE} members"
                    )
                cur.execute(
                    "INSERT INTO room_members (room_id, user_id, role) VALUES (%s, %s, %s) "
                    "ON CONFLICT (room_id, user_id) DO NOTHING",
                    (invite.room_id, user_id, invite.role),
                )
            cur.execute(
                "UPDATE room_invites SET status = %s WHERE id = %s",
                (STATUS_ACCEPTED, invite.id),
            )
            conn.commit()
        return {
            "room_id": invite.room_id,
            "invite_id": invite.id,
            "role": invite.role,
            "already_member": already_member,
        }

    def purge_expired(self) -> int:
        """Mark every pending invite past its expiry as ``expired``.

        Returns the number of invites marked. Safe to run on a
        schedule; already-decided invites are untouched.
        """
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE room_invites SET status = %s WHERE status = %s AND expires_at <= now()",
                (STATUS_EXPIRED, STATUS_PENDING),
            )
            marked = cur.rowcount
            conn.commit()
        return marked


def _store(dsn=None) -> RoomInviteStore:
    return RoomInviteStore(dsn)


def issue_invite_link(
    room_id: str,
    *,
    inviter_user_id: str,
    role: str = ROLE_MEMBER,
    ttl_days: int = INVITE_LINK_TTL_DAYS,
    dsn=None,
) -> dict:
    return _store(dsn).issue_invite_link(
        room_id, inviter_user_id=inviter_user_id, role=role, ttl_days=ttl_days
    )


def issue_invite_code(
    room_id: str,
    *,
    inviter_user_id: str,
    role: str = ROLE_MEMBER,
    ttl_hours: int = CODE_TTL_HOURS,
    dsn=None,
) -> dict:
    return _store(dsn).issue_invite_code(
        room_id, inviter_user_id=inviter_user_id, role=role, ttl_hours=ttl_hours
    )


def accept_invite(token: str, *, user_id: str, dsn=None) -> dict:
    return _store(dsn).accept_invite(token, user_id=user_id)


def accept_invite_code(code: str, *, user_id: str, dsn=None) -> dict:
    return _store(dsn).accept_invite_code(code, user_id=user_id)


def revoke_invite(invite_id: str, *, revoked_by: str, dsn=None) -> RoomInvite:
    return _store(dsn).revoke_invite(invite_id, revoked_by=revoked_by)


def get_invite(invite_id: str, dsn=None) -> RoomInvite | None:
    return _store(dsn).get_invite(invite_id)


def list_invites(room_id: str, dsn=None) -> list[RoomInvite]:
    return _store(dsn).list_invites(room_id)


def purge_expired(dsn=None) -> int:
    return _store(dsn).purge_expired()
