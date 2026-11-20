"""Single-use invitation tokens: invite by email, join by token.

Pattern (DuskBot ``server/cloud-team*.ts`` invitations, rewritten as
Puspa code): an owner invites an email address; the invitee receives
a token and becomes a member by presenting it. The token rules
follow the pairing pattern in ``puspa/hosting/pairing.py``:

- The token is random (``secrets.token_urlsafe(32)``) and returned
  exactly once, at issue time. Only its SHA-256 hash is stored —
  a database leak does not yield usable invitations.
- TTL is 7 days. An expired token is marked ``expired`` on first
  sight and can never be accepted afterwards.
- Single use: accepting consumes the invitation (status ``accepted``)
  in the same transaction that inserts the membership, so a token
  can never mint two memberships.
- Idempotent acceptance: accepting the same token twice (same user,
  or a user who is already a member) still yields exactly one
  membership row. The second accept returns ``already_member=True``
  instead of failing — a double-click or a retried request must not
  error.
- Revocation: an owner can revoke a pending invitation; a revoked
  token is dead immediately.

Privilege note: acceptance inserts into ``team_members``, which the
invitee cannot do through the RLS policy (they are not a member yet
and not an owner). Acceptance therefore runs as the table owner,
like team creation and user registration — it validates the token
hash first, then inserts the membership and marks the invitation
used in one transaction. Invitation issue/revoke/list run the same
SQL the application role would run, with the inviter's ownership
checked in Python first (the RLS policy enforces it at the database
layer too; see ``tests/test_teams_invitations.py``).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn
from puspa.feature_flags import FLAG_TEAMS, require_enabled

from .errors import InvitationError, NotTeamOwnerError
from .teams import ROLE_MEMBER, TeamStore, _validate_uuid

__all__ = [
    "INVITATION_TTL_DAYS",
    "Invitation",
    "InvitationStore",
    "accept_invitation",
    "get_invitation",
    "issue_invitation",
    "list_invitations",
    "purge_expired",
    "revoke_invitation",
]

INVITATION_TTL_DAYS = 7

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REVOKED = "revoked"
STATUS_EXPIRED = "expired"


@dataclass(frozen=True)
class Invitation:
    """One invitation: team, inviter, invited email, status, expiry.

    The token itself is never stored and never appears here.
    """

    id: str
    team_id: str
    inviter_user_id: str
    invited_email: str
    status: str
    created_at: datetime
    expires_at: datetime


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _invitation_row(row) -> Invitation:
    return Invitation(
        id=str(row["id"]),
        team_id=str(row["team_id"]),
        inviter_user_id=str(row["inviter_user_id"]),
        invited_email=row["invited_email"],
        status=row["status"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


class InvitationStore:
    """Postgres-backed invitations (hash-only tokens)."""

    def __init__(self, dsn=None, team_store: TeamStore | None = None):
        self.dsn = resolve_dsn(dsn)
        self.teams = team_store or TeamStore(dsn)

    def issue_invitation(
        self,
        team_id: str,
        *,
        inviter_user_id: str,
        invited_email: str,
        ttl_days: int = INVITATION_TTL_DAYS,
    ) -> dict:
        """Issue an invitation. Returns ``{"invitation_id", "token",
        "expires_at"}`` — the token is returned exactly once here.

        Gated on the ``teams`` flag (E-21, plan 9.6): when the
        flag is off, no invitation can be issued.

        Raises ``NotTeamOwnerError`` when the inviter is not a team
        owner, ``ValueError`` for an empty email or a bad TTL.
        """
        require_enabled(FLAG_TEAMS)
        team_id = _validate_uuid(team_id, "team_id")
        inviter = _validate_uuid(inviter_user_id, "inviter_user_id")
        if not isinstance(invited_email, str) or not invited_email.strip():
            raise ValueError("invited_email must be a non-empty string")
        if ttl_days <= 0:
            raise ValueError("ttl_days must be positive")
        if not self.teams.is_owner(team_id, inviter):
            raise NotTeamOwnerError(f"user {inviter} is not an owner of team {team_id}")
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(days=ttl_days)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO invitations "
                "(team_id, inviter_user_id, invited_email, token_hash, expires_at) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (team_id, inviter, invited_email.strip().lower(), _sha256_hex(token), expires_at),
            )
            invitation_id = str(cur.fetchone()["id"])
            conn.commit()
        return {"invitation_id": invitation_id, "token": token, "expires_at": expires_at}

    def get_invitation(self, invitation_id: str) -> Invitation | None:
        """Return the invitation, or None when the id does not exist."""
        invitation_id = _validate_uuid(invitation_id, "invitation_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM invitations WHERE id = %s", (invitation_id,))
            row = cur.fetchone()
        return _invitation_row(row) if row is not None else None

    def list_invitations(self, team_id: str) -> list[Invitation]:
        """All invitations of a team, newest first."""
        team_id = _validate_uuid(team_id, "team_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM invitations WHERE team_id = %s ORDER BY created_at DESC, id",
                (team_id,),
            )
            return [_invitation_row(row) for row in cur.fetchall()]

    def revoke_invitation(self, invitation_id: str, *, revoked_by: str) -> Invitation:
        """Revoke a pending invitation (owner-only). Revoking an
        already accepted/revoked/expired invitation is a no-op that
        returns the current row.

        Raises ``InvitationError`` for an unknown id,
        ``NotTeamOwnerError`` when the revoker is not a team owner.
        """
        invitation_id = _validate_uuid(invitation_id, "invitation_id")
        revoked_by = _validate_uuid(revoked_by, "revoked_by")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM invitations WHERE id = %s", (invitation_id,))
            row = cur.fetchone()
            if row is None:
                raise InvitationError(f"invitation not found: {invitation_id}")
            invitation = _invitation_row(row)
            if not self.teams.is_owner(invitation.team_id, revoked_by):
                raise NotTeamOwnerError(
                    f"user {revoked_by} is not an owner of team {invitation.team_id}"
                )
            if invitation.status == STATUS_PENDING:
                cur.execute(
                    "UPDATE invitations SET status = %s WHERE id = %s RETURNING *",
                    (STATUS_REVOKED, invitation_id),
                )
                invitation = _invitation_row(cur.fetchone())
            conn.commit()
        return invitation

    def accept_invitation(self, token: str, *, user_id: str) -> dict:
        """Accept an invitation token and become a member.

        Returns ``{"team_id", "invitation_id", "already_member"}``.
        Idempotent: accepting twice (or accepting while already a
        member) still leaves exactly one membership row.

        Raises ``InvitationError`` for an unknown, expired, revoked,
        or already-accepted token. An expired token is marked
        ``expired`` in the same transaction that rejects it, so it
        can never be accepted later.

        Gated on the ``teams`` flag (E-21, plan 9.6): when the
        flag is off, no invitation can be accepted — nobody new
        can join a team.
        """
        require_enabled(FLAG_TEAMS)
        user_id = _validate_uuid(user_id, "user_id")
        if not isinstance(token, str) or not token:
            raise InvitationError("invitation token unknown, expired, or revoked")
        now = datetime.now(UTC)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM invitations WHERE token_hash = %s",
                (_sha256_hex(token),),
            )
            row = cur.fetchone()
            if row is None:
                raise InvitationError("invitation token unknown, expired, or revoked")
            invitation = _invitation_row(row)
            if invitation.status == STATUS_REVOKED:
                raise InvitationError("invitation was revoked")
            if invitation.status == STATUS_ACCEPTED:
                # Idempotent for the user who already accepted (a
                # double-click or a retried request): exactly one
                # membership row exists, so report success. A different
                # user presenting a consumed token is refused — the
                # token was spent by someone else.
                if self.teams.is_member(invitation.team_id, user_id):
                    return {
                        "team_id": invitation.team_id,
                        "invitation_id": invitation.id,
                        "already_member": True,
                    }
                raise InvitationError("invitation was already accepted")
            if invitation.status == STATUS_EXPIRED or invitation.expires_at <= now:
                cur.execute(
                    "UPDATE invitations SET status = %s WHERE id = %s",
                    (STATUS_EXPIRED, invitation.id),
                )
                conn.commit()
                raise InvitationError("invitation expired")
            already_member = self.teams.is_member(invitation.team_id, user_id)
            if not already_member:
                cur.execute(
                    "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, %s) "
                    "ON CONFLICT (team_id, user_id) DO NOTHING",
                    (invitation.team_id, user_id, ROLE_MEMBER),
                )
            cur.execute(
                "UPDATE invitations SET status = %s WHERE id = %s",
                (STATUS_ACCEPTED, invitation.id),
            )
            conn.commit()
        return {
            "team_id": invitation.team_id,
            "invitation_id": invitation.id,
            "already_member": already_member,
        }

    def purge_expired(self) -> int:
        """Mark every pending invitation past its expiry as ``expired``.

        Returns the number of invitations marked. Safe to run on a
        schedule; already-decided invitations are untouched.
        """
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE invitations SET status = %s WHERE status = %s AND expires_at <= now()",
                (STATUS_EXPIRED, STATUS_PENDING),
            )
            marked = cur.rowcount
            conn.commit()
        return marked


def _store(dsn=None) -> InvitationStore:
    return InvitationStore(dsn)


def issue_invitation(
    team_id: str,
    *,
    inviter_user_id: str,
    invited_email: str,
    ttl_days: int = INVITATION_TTL_DAYS,
    dsn=None,
) -> dict:
    return _store(dsn).issue_invitation(
        team_id,
        inviter_user_id=inviter_user_id,
        invited_email=invited_email,
        ttl_days=ttl_days,
    )


def accept_invitation(token: str, *, user_id: str, dsn=None) -> dict:
    return _store(dsn).accept_invitation(token, user_id=user_id)


def revoke_invitation(invitation_id: str, *, revoked_by: str, dsn=None) -> Invitation:
    return _store(dsn).revoke_invitation(invitation_id, revoked_by=revoked_by)


def get_invitation(invitation_id: str, dsn=None) -> Invitation | None:
    return _store(dsn).get_invitation(invitation_id)


def list_invitations(team_id: str, dsn=None) -> list[Invitation]:
    return _store(dsn).list_invitations(team_id)


def purge_expired(dsn=None) -> int:
    return _store(dsn).purge_expired()
