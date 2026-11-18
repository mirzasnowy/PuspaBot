"""Per-member visibility and per-member bot access.

Pattern (DuskBot ``server/team-*.ts`` + ``server/bot-access-gate.ts``
+ ``server/access-requests.ts``, rewritten as Puspa code): visibility
is a FUNCTION of (thread, member), never a global flag on the thread
or on the user. Two gates live here:

1. ``can_see_thread`` — a pure function over a ``ThreadRef``:

   - a personal thread (``team_id`` is None) is visible only to its
     owner;
   - a team thread is visible to every member of that team, and to
     nobody else (not even another team's owner).

   Puspa has no threads table yet, so ``ThreadRef`` is the record
   shape the future table must carry (``thread_id``,
   ``owner_user_id``, ``team_id``). Every thread reader must call
   this gate first, the way every owned reader calls
   ``puspa.accounts.access.require_owner`` first. ``ThreadRef`` is
   deliberately small: the gate must stay decidable from the
   membership list alone, without reading thread content.

2. ``BotAccessGate`` — per-member access to a bot. Team membership
   alone is NOT access: a member sees the team's bots exist, but
   using one needs an explicit grant. The rules:

   - the bot owner always has access (no grant row needed);
   - a member has access only when a grant row exists for
     (bot_id, user_id);
   - a member without access may request it; a team owner (or the
     bot owner, when the lookup is wired) approves or denies;
   - approving inserts the grant and marks the request approved in
     one transaction; approving an already-approved request is a
     no-op that returns the same grant (idempotent);
   - denying a decided request raises ``BotAccessError`` — a
     decision is final, a new request must be opened instead.

   Requests are recorded durably in ``bot_access_requests`` (who
   asked, who decided, when) — that table is the audit trail for
   this iteration. A core-ledger event type for access decisions is
   a follow-up owned by the ledger (no new event types are added
   in G13; the core ledger stays at 31 types, as in G3/G11).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn

from .errors import BotAccessError, NotTeamOwnerError, ThreadNotVisibleError
from .teams import TeamStore, _validate_uuid

__all__ = [
    "BotAccessGate",
    "BotAccessRequest",
    "BotRef",
    "ThreadRef",
    "can_access_bot",
    "can_see_thread",
    "require_thread_visible",
]

REQUEST_PENDING = "pending"
REQUEST_APPROVED = "approved"
REQUEST_DENIED = "denied"


@dataclass(frozen=True)
class ThreadRef:
    """The minimum a visibility decision needs: thread id, personal
    owner, and the team it belongs to (None = personal thread)."""

    thread_id: str
    owner_user_id: str
    team_id: str | None = None


@dataclass(frozen=True)
class BotRef:
    """The minimum an access decision needs: bot id, owner, and the
    team it belongs to (None = a personal bot, never team-shared)."""

    bot_id: str
    owner_user_id: str
    team_id: str | None = None


@dataclass(frozen=True)
class BotAccessRequest:
    """One bot-access request: who asked for which bot, in which team,
    its status, and who decided it (None while pending)."""

    id: str
    bot_id: str
    team_id: str
    requester_user_id: str
    status: str
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


def can_see_thread(
    *,
    user_id: str,
    team_ids: Iterable[str],
    thread: ThreadRef,
) -> bool:
    """True when this member may see this thread.

    Personal thread: only the owner. Team thread: any member of
    that team. Anything else: False — there is no "visible to
    everyone" case, and a missing team membership is never treated
    as visibility.
    """
    if thread.team_id is None:
        return user_id == thread.owner_user_id
    return thread.team_id in set(team_ids)


def require_thread_visible(
    *,
    user_id: str,
    team_ids: Iterable[str],
    thread: ThreadRef,
) -> None:
    """Raise ``ThreadNotVisibleError`` unless the member may see the
    thread. Call this BEFORE reading thread content — the same
    check-first order as ``require_owner`` in ``puspa.accounts``.
    """
    if not can_see_thread(user_id=user_id, team_ids=team_ids, thread=thread):
        raise ThreadNotVisibleError(f"thread {thread.thread_id} is not visible to user {user_id}")


def can_access_bot(
    *,
    user_id: str,
    bot: BotRef,
    granted_user_ids: Iterable[str] = (),
) -> bool:
    """True when this user may use this bot.

    The bot owner always may. A team bot additionally needs an
    explicit grant for this user; team membership alone is not
    access. A personal bot (``team_id`` None) is usable only by its
    owner — there is no grant path for it.
    """
    if user_id == bot.owner_user_id:
        return True
    if bot.team_id is None:
        return False
    return user_id in set(granted_user_ids)


def _request_row(row) -> BotAccessRequest:
    return BotAccessRequest(
        id=str(row["id"]),
        bot_id=row["bot_id"],
        team_id=str(row["team_id"]),
        requester_user_id=str(row["requester_user_id"]),
        status=row["status"],
        decided_by=str(row["decided_by"]) if row["decided_by"] is not None else None,
        decided_at=row["decided_at"],
        created_at=row["created_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


class BotAccessGate:
    """Postgres-backed bot access: grants + access requests.

    ``bot_owner_lookup`` is an injected callable ``(bot_id) ->
    owner_user_id | None`` (default: None, meaning the bot owner is
    unknown to the gate). When provided, the bot owner may also
    approve/deny requests for their own bot; without it, only a
    team owner may decide. The gate never guesses an owner.
    """

    def __init__(
        self,
        dsn=None,
        team_store: TeamStore | None = None,
        bot_owner_lookup: Callable[[str], str | None] | None = None,
    ):
        self.dsn = resolve_dsn(dsn)
        self.teams = team_store or TeamStore(dsn)
        self.bot_owner_lookup = bot_owner_lookup

    # -- grants ----------------------------------------------------------

    def has_access(self, user_id: str, bot_id: str) -> bool:
        """True when a grant row exists for (bot_id, user_id). The bot
        owner is checked by the caller via ``can_access_bot`` — this
        store holds grants only, never bot ownership.
        """
        user_id = _validate_uuid(user_id, "user_id")
        if not isinstance(bot_id, str) or not bot_id.strip():
            raise ValueError("bot_id must be a non-empty string")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM bot_access_grants WHERE bot_id = %s AND user_id = %s",
                (bot_id.strip(), user_id),
            )
            return cur.fetchone() is not None

    def grant_access(self, bot_id: str, user_id: str, *, team_id: str, granted_by: str) -> None:
        """Insert a grant (owner-only). Idempotent: granting twice is
        a no-op, never a second row.
        """
        team_id = _validate_uuid(team_id, "team_id")
        user_id = _validate_uuid(user_id, "user_id")
        granted_by = _validate_uuid(granted_by, "granted_by")
        if not isinstance(bot_id, str) or not bot_id.strip():
            raise ValueError("bot_id must be a non-empty string")
        if not self.teams.is_owner(team_id, granted_by):
            raise NotTeamOwnerError(f"user {granted_by} is not an owner of team {team_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_access_grants (bot_id, user_id, team_id, granted_by) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (bot_id, user_id) DO NOTHING",
                (bot_id.strip(), user_id, team_id, granted_by),
            )
            conn.commit()

    def revoke_access(self, bot_id: str, user_id: str, *, team_id: str, revoked_by: str) -> None:
        """Delete a grant (owner-only). Idempotent when no grant exists."""
        team_id = _validate_uuid(team_id, "team_id")
        user_id = _validate_uuid(user_id, "user_id")
        revoked_by = _validate_uuid(revoked_by, "revoked_by")
        if not isinstance(bot_id, str) or not bot_id.strip():
            raise ValueError("bot_id must be a non-empty string")
        if not self.teams.is_owner(team_id, revoked_by):
            raise NotTeamOwnerError(f"user {revoked_by} is not an owner of team {team_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM bot_access_grants WHERE bot_id = %s AND user_id = %s",
                (bot_id.strip(), user_id),
            )
            conn.commit()

    def list_granted_user_ids(self, bot_id: str) -> list[str]:
        """All user ids with a grant for this bot."""
        if not isinstance(bot_id, str) or not bot_id.strip():
            raise ValueError("bot_id must be a non-empty string")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM bot_access_grants WHERE bot_id = %s ORDER BY granted_at, user_id",
                (bot_id.strip(),),
            )
            return [str(row["user_id"]) for row in cur.fetchall()]

    # -- requests --------------------------------------------------------

    def request_access(
        self, bot_id: str, *, team_id: str, requester_user_id: str
    ) -> BotAccessRequest:
        """Open an access request (team members only). Opening a
        second pending request for the same (bot, requester) returns
        the existing pending request instead of a duplicate.

        Raises ``BotAccessError`` when the requester is not a team
        member, or already has access (nothing to request).
        """
        team_id = _validate_uuid(team_id, "team_id")
        requester = _validate_uuid(requester_user_id, "requester_user_id")
        if not isinstance(bot_id, str) or not bot_id.strip():
            raise ValueError("bot_id must be a non-empty string")
        if not self.teams.is_member(team_id, requester):
            raise BotAccessError(f"user {requester} is not a member of team {team_id}")
        if self.has_access(requester, bot_id.strip()):
            raise BotAccessError(f"user {requester} already has access to bot {bot_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM bot_access_requests WHERE bot_id = %s "
                "AND requester_user_id = %s AND status = %s",
                (bot_id.strip(), requester, REQUEST_PENDING),
            )
            row = cur.fetchone()
            if row is not None:
                return _request_row(row)
            cur.execute(
                "INSERT INTO bot_access_requests (bot_id, team_id, requester_user_id) "
                "VALUES (%s, %s, %s) RETURNING *",
                (bot_id.strip(), team_id, requester),
            )
            request = _request_row(cur.fetchone())
            conn.commit()
        return request

    def get_request(self, request_id: str) -> BotAccessRequest | None:
        """Return the request, or None when the id does not exist."""
        request_id = _validate_uuid(request_id, "request_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM bot_access_requests WHERE id = %s", (request_id,))
            row = cur.fetchone()
        return _request_row(row) if row is not None else None

    def list_requests(self, team_id: str, *, status: str | None = None) -> list[BotAccessRequest]:
        """Requests of a team, newest first; optionally filtered by status."""
        team_id = _validate_uuid(team_id, "team_id")
        if status is not None and status not in (REQUEST_PENDING, REQUEST_APPROVED, REQUEST_DENIED):
            raise ValueError(f"unknown request status: {status!r}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            if status is None:
                cur.execute(
                    "SELECT * FROM bot_access_requests WHERE team_id = %s "
                    "ORDER BY created_at DESC, id",
                    (team_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM bot_access_requests WHERE team_id = %s AND status = %s "
                    "ORDER BY created_at DESC, id",
                    (team_id, status),
                )
            return [_request_row(row) for row in cur.fetchall()]

    def _is_decider(self, request: BotAccessRequest, user_id: str) -> bool:
        """True when the user may decide this request: a team owner,
        or the bot owner when the lookup is wired and knows the bot.
        """
        if self.teams.is_owner(request.team_id, user_id):
            return True
        if self.bot_owner_lookup is None:
            return False
        try:
            owner = self.bot_owner_lookup(request.bot_id)
        except Exception:
            return False
        return owner is not None and str(owner) == user_id

    def approve(self, request_id: str, *, approver_user_id: str) -> BotAccessRequest:
        """Approve a pending request: insert the grant and mark the
        request approved in one transaction. Idempotent: approving
        an already-approved request returns it unchanged (the grant
        already exists). Approving a denied request raises
        ``BotAccessError`` — decisions are final.
        """
        request_id = _validate_uuid(request_id, "request_id")
        approver = _validate_uuid(approver_user_id, "approver_user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM bot_access_requests WHERE id = %s", (request_id,))
            row = cur.fetchone()
            if row is None:
                raise BotAccessError(f"access request not found: {request_id}")
            request = _request_row(row)
            if not self._is_decider(request, approver):
                raise NotTeamOwnerError(
                    f"user {approver} may not decide access requests for team {request.team_id}"
                )
            if request.status == REQUEST_DENIED:
                raise BotAccessError("access request was already denied")
            if request.status == REQUEST_APPROVED:
                return request
            cur.execute(
                "INSERT INTO bot_access_grants (bot_id, user_id, team_id, granted_by) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (bot_id, user_id) DO NOTHING",
                (request.bot_id, request.requester_user_id, request.team_id, approver),
            )
            cur.execute(
                "UPDATE bot_access_requests SET status = %s, decided_by = %s, "
                "decided_at = now() WHERE id = %s RETURNING *",
                (REQUEST_APPROVED, approver, request_id),
            )
            request = _request_row(cur.fetchone())
            conn.commit()
        return request

    def deny(self, request_id: str, *, approver_user_id: str) -> BotAccessRequest:
        """Deny a pending request. Denying a decided request raises
        ``BotAccessError`` — decisions are final.
        """
        request_id = _validate_uuid(request_id, "request_id")
        approver = _validate_uuid(approver_user_id, "approver_user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM bot_access_requests WHERE id = %s", (request_id,))
            row = cur.fetchone()
            if row is None:
                raise BotAccessError(f"access request not found: {request_id}")
            request = _request_row(row)
            if not self._is_decider(request, approver):
                raise NotTeamOwnerError(
                    f"user {approver} may not decide access requests for team {request.team_id}"
                )
            if request.status != REQUEST_PENDING:
                raise BotAccessError(f"access request was already decided ({request.status})")
            cur.execute(
                "UPDATE bot_access_requests SET status = %s, decided_by = %s, "
                "decided_at = now() WHERE id = %s RETURNING *",
                (REQUEST_DENIED, approver, request_id),
            )
            request = _request_row(cur.fetchone())
            conn.commit()
        return request
