"""Multi-agent teams: identity and membership (peer agents).

A team is a small group of peer agents that work together in one
shared conversation. Every member is a peer — no member is the boss
of another, and no member is a helper of a single main agent (that
is what ``puspa.subagents`` is for). Each member carries its own:

- ``name`` — the agent's handle inside the team (one token, used
  for ``@name`` handoffs in the shared conversation).
- ``role`` — what the agent does in the team (``researcher``,
  ``writer``, ``planner``, ``executor``, ...). A run needs at
  least two members with at least two distinct roles.
- ``model`` — the model id the member runs on (for example
  ``openai/gpt-4o``). Stored as text; no live provider is
  contacted here, so any non-empty id is accepted.
- ``skills`` — the skill names the member may use.
- ``knowledge`` — the knowledge references the member may read
  (document ids, note titles, or paths).
- ``workspace`` — the member's own workspace path. Recorded, not
  enforced: the sandbox that would isolate one workspace per
  member lives in the hosted runtime, not in this store.
- ``instruction`` — the member's own system prompt.

Rules, all enforced here (never left to the caller):

- A team has at most ``MAX_TEAM_SIZE`` members (8). Adding a
  ninth raises ``TeamFullError``.
- A member name is one token (letters, digits, ``_``, ``-``):
  names appear in ``@name`` handoffs, so spaces and ``@`` would
  make a handoff ambiguous.
- Adding a member whose name already exists raises ``ValueError``;
  remove the old member first, then add the new one.
- Only the team owner (the user who created the team) may add or
  remove members or delete the team.
- ``delete_team`` is owner-only and deletes the team row; members,
  conversations, and messages cascade in the database (``ON DELETE
  CASCADE`` in migration 0044).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.ports import resolve_dsn

from .errors import (
    NotTeamOwnerError,
    TeamFullError,
    TeamMemberNotFoundError,
    TeamNotFoundError,
)

__all__ = [
    "MAX_TEAM_SIZE",
    "MIN_TEAM_SIZE",
    "Team",
    "TeamMember",
    "TeamStore",
    "add_member",
    "create_team",
    "delete_team",
    "get_team",
    "list_members",
    "list_teams_for_user",
    "member_count",
    "remove_member",
]

#: A run needs at least this many members (see the runner).
MIN_TEAM_SIZE = 2

#: Cap on members per team: teams stay small enough for one shared
#: conversation to stay readable.
MAX_TEAM_SIZE = 8

#: A member name is one token so ``@name`` handoffs are unambiguous.
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Team:
    """One team: an id, a name, who owns it, when it was created."""

    id: str
    name: str
    owner_user_id: str
    created_at: datetime


@dataclass(frozen=True)
class TeamMember:
    """One peer agent in a team, with its own role and setup."""

    team_id: str
    name: str
    role: str
    model: str
    skills: list[str] = field(default_factory=list)
    knowledge: list[str] = field(default_factory=list)
    workspace: str = ""
    instruction: str = ""
    position: int = 0
    joined_at: datetime | None = None


def _validate_uuid(value, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{what} is not a valid UUID: {value!r}") from exc


def _validate_name(name, what: str = "member name") -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{what} must be a non-empty string")
    name = name.strip()
    if not _NAME_RE.match(name):
        raise ValueError(f"{what} must be one token (letters, digits, '_', '-'): {name!r}")
    return name


def _validate_non_empty(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string")
    return value.strip()


def _validate_string_list(value, what: str) -> list[str]:
    if isinstance(value, str) or not hasattr(value, "__iter__"):
        raise ValueError(f"{what} must be a list of strings")
    items = list(value)
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{what} must contain only non-empty strings")
    return [item.strip() for item in items]


def _team_row(row) -> Team:
    return Team(
        id=str(row["id"]),
        name=row["name"],
        owner_user_id=str(row["owner_user_id"]),
        created_at=row["created_at"],
    )


def _member_row(row) -> TeamMember:
    return TeamMember(
        team_id=str(row["team_id"]),
        name=row["name"],
        role=row["role"],
        model=row["model"],
        skills=list(row["skills"] or []),
        knowledge=list(row["knowledge"] or []),
        workspace=row["workspace"] or "",
        instruction=row["instruction"] or "",
        position=int(row["position"]),
        joined_at=row["joined_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


class TeamStore:
    """Postgres-backed teams and peer-agent memberships.

    One instance per call site; the DSN is fixed at construction so
    tests can point at a test database without touching env vars.
    Every method opens its own short connection.
    """

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def create_team(self, name: str, owner_user_id: str) -> Team:
        """Create a team; the creator becomes its owner.

        Raises ``ValueError`` for an empty name or a bad user id.
        The team starts empty: add members with :meth:`add_member`.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("team name must be a non-empty string")
        owner = _validate_uuid(owner_user_id, "owner_user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams (name, owner_user_id) VALUES (%s, %s) RETURNING *",
                (name.strip(), owner),
            )
            team = _team_row(cur.fetchone())
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

    def list_teams_for_user(self, owner_user_id: str) -> list[Team]:
        """All teams the user owns, oldest first."""
        owner = _validate_uuid(owner_user_id, "owner_user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM teams WHERE owner_user_id = %s ORDER BY created_at, id",
                (owner,),
            )
            return [_team_row(row) for row in cur.fetchall()]

    def list_members(self, team_id: str) -> list[TeamMember]:
        """All members of a team, in join (position) order."""
        team = self.require_team(team_id)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM team_members WHERE team_id = %s ORDER BY position, name",
                (team.id,),
            )
            return [_member_row(row) for row in cur.fetchall()]

    def member_count(self, team_id: str) -> int:
        """How many members the team has (0 when the team is unknown)."""
        team_id = _validate_uuid(team_id, "team_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM team_members WHERE team_id = %s",
                (team_id,),
            )
            return int(cur.fetchone()["count"])

    def get_member(self, team_id: str, name: str) -> TeamMember | None:
        """Return one member by name, or None when not a member."""
        team_id = _validate_uuid(team_id, "team_id")
        name = _validate_name(name)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM team_members WHERE team_id = %s AND name = %s",
                (team_id, name),
            )
            row = cur.fetchone()
        return _member_row(row) if row is not None else None

    def _require_owner(self, team_id: str, user_id: str) -> Team:
        """Return the team, or raise unless the user owns it."""
        team = self.require_team(team_id)
        if team.owner_user_id != _validate_uuid(user_id, "user_id"):
            raise NotTeamOwnerError(f"user {user_id} does not own team {team_id}")
        return team

    def add_member(
        self,
        team_id: str,
        *,
        name: str,
        role: str,
        model: str,
        skills=(),
        knowledge=(),
        workspace: str = "",
        instruction: str = "",
        added_by: str,
    ) -> TeamMember:
        """Add a peer agent to the team (owner-only).

        The member joins at the end of the speaking order. Adding a
        ninth member raises ``TeamFullError``; adding a name that
        already exists raises ``ValueError``.

        Raises ``NotTeamOwnerError`` when ``added_by`` is not the
        team owner, ``ValueError`` for a bad name, role, model,
        skills, or knowledge list.
        """
        team = self._require_owner(team_id, added_by)
        name = _validate_name(name)
        role = _validate_non_empty(role, "role")
        model = _validate_non_empty(model, "model")
        skills = _validate_string_list(skills, "skills")
        knowledge = _validate_string_list(knowledge, "knowledge")
        if not isinstance(workspace, str):
            raise ValueError("workspace must be a string")
        if not isinstance(instruction, str):
            raise ValueError("instruction must be a string")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM team_members WHERE team_id = %s AND name = %s",
                (team.id, name),
            )
            if cur.fetchone() is not None:
                raise ValueError(f"team {team.id} already has a member named {name!r}")
            cur.execute(
                "SELECT COUNT(*) AS member_count, COALESCE(MAX(position), -1) AS max_position "
                "FROM team_members WHERE team_id = %s",
                (team.id,),
            )
            row = cur.fetchone()
            if int(row["member_count"]) >= MAX_TEAM_SIZE:
                raise TeamFullError(f"team {team.id} already has {MAX_TEAM_SIZE} members")
            cur.execute(
                "INSERT INTO team_members "
                "(team_id, name, role, model, skills, knowledge, workspace, "
                " instruction, position) "
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s) RETURNING *",
                (
                    team.id,
                    name,
                    role,
                    model,
                    _to_json(skills),
                    _to_json(knowledge),
                    workspace,
                    instruction,
                    int(row["max_position"]) + 1,
                ),
            )
            member = _member_row(cur.fetchone())
            conn.commit()
        return member

    def remove_member(self, team_id: str, name: str, *, removed_by: str) -> None:
        """Remove a member (owner-only). Idempotent when the name is
        not a member. The remaining members keep their order.
        """
        team = self._require_owner(team_id, removed_by)
        name = _validate_name(name)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM team_members WHERE team_id = %s AND name = %s",
                (team.id, name),
            )
            conn.commit()

    def require_member(self, team_id: str, name: str) -> TeamMember:
        """Return the member, or raise ``TeamMemberNotFoundError``."""
        member = self.get_member(team_id, name)
        if member is None:
            raise TeamMemberNotFoundError(f"member {name!r} is not in team {team_id}")
        return member

    def delete_team(self, team_id: str, *, requested_by: str) -> None:
        """Delete the team (owner-only). Members, conversations, and
        messages cascade in the database.
        """
        team = self._require_owner(team_id, requested_by)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM teams WHERE id = %s", (team.id,))
            conn.commit()


def _to_json(items: list[str]) -> str:
    return json.dumps(items)


# --- Module-level functions (thin wrappers over a fresh store) -----------


def _store(dsn=None) -> TeamStore:
    return TeamStore(dsn)


def create_team(name: str, owner_user_id: str, dsn=None) -> Team:
    return _store(dsn).create_team(name, owner_user_id)


def get_team(team_id: str, dsn=None) -> Team | None:
    return _store(dsn).get_team(team_id)


def list_teams_for_user(owner_user_id: str, dsn=None) -> list[Team]:
    return _store(dsn).list_teams_for_user(owner_user_id)


def list_members(team_id: str, dsn=None) -> list[TeamMember]:
    return _store(dsn).list_members(team_id)


def member_count(team_id: str, dsn=None) -> int:
    return _store(dsn).member_count(team_id)


def add_member(
    team_id: str,
    *,
    name: str,
    role: str,
    model: str,
    skills=(),
    knowledge=(),
    workspace: str = "",
    instruction: str = "",
    added_by: str,
    dsn=None,
) -> TeamMember:
    return _store(dsn).add_member(
        team_id,
        name=name,
        role=role,
        model=model,
        skills=skills,
        knowledge=knowledge,
        workspace=workspace,
        instruction=instruction,
        added_by=added_by,
    )


def remove_member(team_id: str, name: str, *, removed_by: str, dsn=None) -> None:
    _store(dsn).remove_member(team_id, name, removed_by=removed_by)


def delete_team(team_id: str, *, requested_by: str, dsn=None) -> None:
    _store(dsn).delete_team(team_id, requested_by=requested_by)
