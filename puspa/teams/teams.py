"""Teams as their own entity: create/delete, roles, membership.

Pattern (DuskBot ``server/cloud-team*.ts`` + ``server/team-*.ts``,
rewritten as Puspa code): a team is separate from billing (G2 owns
money; this module owns identity and membership). Two roles only:

- ``owner`` — the only role that can delete the team, add or remove
  members, and promote another member to owner. The creator becomes
  the first owner.
- ``member`` — can see the team and use what the team grants; a
  member cannot change any role, including their own.

Rules, all enforced here (never left to the caller):

- A team always has at least one owner. Demoting or removing the
  last owner raises ``LastOwnerError``; the team is never left
  ownerless.
- ``delete_team`` is owner-only and deletes the team row; members,
  invitations, grants, requests, entitlements, and moves cascade
  in the database (``ON DELETE CASCADE`` in ``infra/schema_teams.sql``).
- Role changes are owner-only. A member calling ``set_role`` gets
  ``NotTeamOwnerError`` even when the target is themselves.
- Membership is a (team_id, user_id) pair; adding an existing member
  is idempotent when the role already matches, and a re-add with a
  different role is a role change (owner-only, same rule as
  ``set_role``).

Privilege note: ``create_team`` inserts the team row and the first
owner membership row in one transaction as the table owner (the
connection this module opens). The RLS policies in
``infra/schema_teams.sql`` have no INSERT policy for ``teams`` and
an owner-only INSERT policy for ``team_members`` on the ``puspa_app``
role, so team creation cannot run as the application role — the
same "privileged operation runs as the owner" decision as user
registration in ``puspa/accounts/schema.sql``. All other operations
here run the same SQL the application role would run; the RLS
policies are what stop a non-owner at the database layer too (see
``tests/test_teams.py``).
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
    NotTeamMemberError,
    NotTeamOwnerError,
    TeamNotFoundError,
)

__all__ = [
    "ROLE_MEMBER",
    "ROLE_OWNER",
    "Team",
    "TeamMember",
    "TeamStore",
    "add_member",
    "create_team",
    "delete_team",
    "get_team",
    "is_member",
    "is_owner",
    "list_members",
    "list_teams_for_user",
    "remove_member",
    "role_of",
    "set_role",
]

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"
ROLES = (ROLE_OWNER, ROLE_MEMBER)


@dataclass(frozen=True)
class Team:
    """One team: an id, a name, who created it, when."""

    id: str
    name: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class TeamMember:
    """One membership: which team, which user, which role, since when."""

    team_id: str
    user_id: str
    role: str
    joined_at: datetime


def _validate_uuid(value, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{what} is not a valid UUID: {value!r}") from exc


def _team_row(row) -> Team:
    return Team(
        id=str(row["id"]),
        name=row["name"],
        created_by=str(row["created_by"]),
        created_at=row["created_at"],
    )


def _member_row(row) -> TeamMember:
    return TeamMember(
        team_id=str(row["team_id"]),
        user_id=str(row["user_id"]),
        role=row["role"],
        joined_at=row["joined_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


class TeamStore:
    """Postgres-backed teams and memberships.

    One instance per call site; the DSN is fixed at construction so
    tests can point at ``puspa_test`` without touching env vars.
    Every method opens its own short connection.
    """

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def create_team(self, name: str, created_by_user_id: str) -> Team:
        """Create a team; the creator becomes its first (and only) owner.

        Raises ``ValueError`` for an empty name or a bad user id.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("team name must be a non-empty string")
        creator = _validate_uuid(created_by_user_id, "created_by_user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams (name, created_by) VALUES (%s, %s) RETURNING *",
                (name.strip(), creator),
            )
            team = _team_row(cur.fetchone())
            cur.execute(
                "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, %s)",
                (team.id, creator, ROLE_OWNER),
            )
            conn.commit()
        return team

    def get_team(self, team_id: str) -> Team | None:
        """Return the team, or None when the id does not exist."""
        team_id = _validate_uuid(team_id, "team_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
            row = cur.fetchone()
        return _team_row(row) if row is not None else None

    def require_team(self, team_id: str) -> Team:
        """Return the team, or raise ``TeamNotFoundError``."""
        team = self.get_team(team_id)
        if team is None:
            raise TeamNotFoundError(f"team not found: {team_id}")
        return team

    def list_teams_for_user(self, user_id: str) -> list[Team]:
        """All teams the user belongs to, oldest first."""
        user_id = _validate_uuid(user_id, "user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT t.* FROM teams t JOIN team_members m ON m.team_id = t.id "
                "WHERE m.user_id = %s ORDER BY t.created_at, t.id",
                (user_id,),
            )
            return [_team_row(row) for row in cur.fetchall()]

    def list_members(self, team_id: str) -> list[TeamMember]:
        """All members of a team, owners first, then by join time."""
        team = self.require_team(team_id)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM team_members WHERE team_id = %s "
                "ORDER BY (role = 'owner') DESC, joined_at, user_id",
                (team.id,),
            )
            return [_member_row(row) for row in cur.fetchall()]

    def role_of(self, team_id: str, user_id: str) -> str | None:
        """The user's role in the team, or None when not a member."""
        team_id = _validate_uuid(team_id, "team_id")
        user_id = _validate_uuid(user_id, "user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM team_members WHERE team_id = %s AND user_id = %s",
                (team_id, user_id),
            )
            row = cur.fetchone()
        return row["role"] if row is not None else None

    def is_member(self, team_id: str, user_id: str) -> bool:
        """True when the user is a member (any role) of the team."""
        return self.role_of(team_id, user_id) is not None

    def is_owner(self, team_id: str, user_id: str) -> bool:
        """True when the user is an owner of the team."""
        return self.role_of(team_id, user_id) == ROLE_OWNER

    def _require_owner(self, team_id: str, user_id: str) -> None:
        """Raise unless the user is an owner of the team."""
        self.require_team(team_id)
        if not self.is_owner(team_id, user_id):
            raise NotTeamOwnerError(f"user {user_id} is not an owner of team {team_id}")

    def _owner_count(self, conn, team_id: str) -> int:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM team_members WHERE team_id = %s AND role = %s",
                (team_id, ROLE_OWNER),
            )
            return int(cur.fetchone()["count"])

    def add_member(
        self, team_id: str, user_id: str, *, added_by: str, role: str = ROLE_MEMBER
    ) -> TeamMember:
        """Add a member (owner-only). Re-adding an existing member with
        the same role is a no-op; with a different role it is a role
        change under the same owner-only rule.

        Raises ``NotTeamOwnerError`` when ``added_by`` is not an owner,
        ``ValueError`` for an unknown role.
        """
        if role not in ROLES:
            raise ValueError(f"unknown role: {role!r}")
        team_id = _validate_uuid(team_id, "team_id")
        user_id = _validate_uuid(user_id, "user_id")
        added_by = _validate_uuid(added_by, "added_by")
        self._require_owner(team_id, added_by)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, %s) "
                "ON CONFLICT (team_id, user_id) DO UPDATE SET role = EXCLUDED.role "
                "RETURNING *",
                (team_id, user_id, role),
            )
            member = _member_row(cur.fetchone())
            conn.commit()
        return member

    def set_role(self, team_id: str, user_id: str, role: str, *, changed_by: str) -> TeamMember:
        """Change a member's role (owner-only). Demoting the last owner
        raises ``LastOwnerError``; the team is never left ownerless.
        """
        if role not in ROLES:
            raise ValueError(f"unknown role: {role!r}")
        team_id = _validate_uuid(team_id, "team_id")
        user_id = _validate_uuid(user_id, "user_id")
        changed_by = _validate_uuid(changed_by, "changed_by")
        self._require_owner(team_id, changed_by)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM team_members WHERE team_id = %s AND user_id = %s",
                (team_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise NotTeamMemberError(f"user {user_id} is not a member of team {team_id}")
            if (
                row["role"] == ROLE_OWNER
                and role == ROLE_MEMBER
                and self._owner_count(conn, team_id) <= 1
            ):
                raise LastOwnerError(f"cannot demote the last owner of team {team_id}")
            cur.execute(
                "UPDATE team_members SET role = %s WHERE team_id = %s AND user_id = %s RETURNING *",
                (role, team_id, user_id),
            )
            member = _member_row(cur.fetchone())
            conn.commit()
        return member

    def remove_member(self, team_id: str, user_id: str, *, removed_by: str) -> None:
        """Remove a member. Allowed to a team owner, or to the member
        themselves (leaving the team). Removing the last owner raises
        ``LastOwnerError``. Idempotent when the user is not a member.
        """
        team_id = _validate_uuid(team_id, "team_id")
        user_id = _validate_uuid(user_id, "user_id")
        removed_by = _validate_uuid(removed_by, "removed_by")
        self.require_team(team_id)
        if removed_by != user_id and not self.is_owner(team_id, removed_by):
            raise NotTeamOwnerError(f"user {removed_by} may not remove members of team {team_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM team_members WHERE team_id = %s AND user_id = %s",
                (team_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                return
            if row["role"] == ROLE_OWNER and self._owner_count(conn, team_id) <= 1:
                raise LastOwnerError(f"cannot remove the last owner of team {team_id}")
            cur.execute(
                "DELETE FROM team_members WHERE team_id = %s AND user_id = %s",
                (team_id, user_id),
            )
            conn.commit()

    def delete_team(self, team_id: str, *, requested_by: str) -> None:
        """Delete the team (owner-only). Members, invitations, grants,
        requests, entitlements, and moves cascade in the database.
        """
        team_id = _validate_uuid(team_id, "team_id")
        requested_by = _validate_uuid(requested_by, "requested_by")
        self._require_owner(team_id, requested_by)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM teams WHERE id = %s", (team_id,))
            conn.commit()


# --- Module-level functions (thin wrappers over a fresh store) -----------


def _store(dsn=None) -> TeamStore:
    return TeamStore(dsn)


def create_team(name: str, created_by_user_id: str, dsn=None) -> Team:
    return _store(dsn).create_team(name, created_by_user_id)


def get_team(team_id: str, dsn=None) -> Team | None:
    return _store(dsn).get_team(team_id)


def list_teams_for_user(user_id: str, dsn=None) -> list[Team]:
    return _store(dsn).list_teams_for_user(user_id)


def list_members(team_id: str, dsn=None) -> list[TeamMember]:
    return _store(dsn).list_members(team_id)


def role_of(team_id: str, user_id: str, dsn=None) -> str | None:
    return _store(dsn).role_of(team_id, user_id)


def is_member(team_id: str, user_id: str, dsn=None) -> bool:
    return _store(dsn).is_member(team_id, user_id)


def is_owner(team_id: str, user_id: str, dsn=None) -> bool:
    return _store(dsn).is_owner(team_id, user_id)


def add_member(
    team_id: str, user_id: str, *, added_by: str, role: str = ROLE_MEMBER, dsn=None
) -> TeamMember:
    return _store(dsn).add_member(team_id, user_id, added_by=added_by, role=role)


def set_role(team_id: str, user_id: str, role: str, *, changed_by: str, dsn=None) -> TeamMember:
    return _store(dsn).set_role(team_id, user_id, role, changed_by=changed_by)


def remove_member(team_id: str, user_id: str, *, removed_by: str, dsn=None) -> None:
    _store(dsn).remove_member(team_id, user_id, removed_by=removed_by)


def delete_team(team_id: str, *, requested_by: str, dsn=None) -> None:
    _store(dsn).delete_team(team_id, requested_by=requested_by)
