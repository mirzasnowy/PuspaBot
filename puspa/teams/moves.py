"""Workspace moves with move-freeze: move a bot without double work.

Pattern (DuskBot ``server/bot-moves.ts`` + ``server/move-freeze.ts``,
rewritten as Puspa code): moving a bot (or its threads) from one
workspace/home to another must never let the bot run in both places
at once. The rule is move-freeze: while a move is active, the bot's
automation is frozen — no task, no scheduler run, no cron job may
start for that bot. The freeze is a state derived from the move
record itself (``is_frozen`` reads the ``workspace_moves`` table),
not a separate flag that can drift out of sync with the move.

Statuses (``workspace_moves.status``):

- ``pending`` — the move was requested; the bot is already frozen.
- ``in_progress`` — the move is running; the bot stays frozen.
- ``done`` — the move finished; the freeze is released (the bot now
  lives in the target workspace).
- ``failed`` — the move failed midway; the bot STAYS frozen and the
  record keeps the failing step, the error, and the resumable
  ``state`` payload, so an operator can ``resume_move`` (retry from
  the recorded step) or ``cancel_move`` (abandon the move; the bot
  stays in the source workspace). A failed move never leaves the
  bot half-moved and silently running — that is the whole point.
- ``cancelled`` — the move was abandoned; the freeze is released
  and the bot stays where it was.

Allowed transitions (anything else raises ``MoveError``):

- ``pending -> in_progress`` (``begin_move``),
- ``in_progress -> done`` (``complete_move``),
- ``pending|in_progress -> failed`` (``fail_move``),
- ``failed -> in_progress`` (``resume_move``),
- ``pending|in_progress|failed -> cancelled`` (``cancel_move``).

``advance_move`` records progress (the current step name + an
optional JSON-able ``state`` dict for the resume payload) without
changing the status. The actual copy of bot data between workspaces
is NOT in this module: the mover is an injected callable at the call
site (``MoveCoordinator`` records intent, freeze, and outcome; the
transport that copies bytes lives outside it, because no workspace
transport exists in this repo yet — see ``docs/teams.md``). The
coordinator is still real: the freeze is enforced here, the states
are enforced here, and ``require_not_frozen`` is the call the
scheduler/task runner must make before starting work for a bot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn

from .errors import MoveError, MoveFrozenError
from .teams import TeamStore, _validate_uuid

__all__ = [
    "FROZEN_STATUSES",
    "MOVE_STATUSES",
    "MoveCoordinator",
    "MoveRecord",
]

MOVE_STATUSES = ("pending", "in_progress", "done", "failed", "cancelled")

#: Statuses during which the bot's automation is frozen.
FROZEN_STATUSES = ("pending", "in_progress", "failed")

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

_ALLOWED_TRANSITIONS = {
    STATUS_PENDING: (STATUS_IN_PROGRESS, STATUS_FAILED, STATUS_CANCELLED),
    STATUS_IN_PROGRESS: (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED),
    STATUS_FAILED: (STATUS_IN_PROGRESS, STATUS_CANCELLED),
    STATUS_DONE: (),
    STATUS_CANCELLED: (),
}


@dataclass(frozen=True)
class MoveRecord:
    """One workspace move: which bot, from where to where, who asked,
    which status, which step, what failed, and the resumable state."""

    id: str
    bot_id: str
    team_id: str
    source_workspace: str
    target_workspace: str
    requested_by: str
    status: str
    current_step: str
    error: str | None
    state: dict
    created_at: datetime
    updated_at: datetime


def _move_row(row) -> MoveRecord:
    state = row["state"]
    if isinstance(state, str):
        state = json.loads(state)
    return MoveRecord(
        id=str(row["id"]),
        bot_id=row["bot_id"],
        team_id=str(row["team_id"]),
        source_workspace=row["source_workspace"],
        target_workspace=row["target_workspace"],
        requested_by=str(row["requested_by"]),
        status=row["status"],
        current_step=row["current_step"],
        error=row["error"],
        state=dict(state) if isinstance(state, dict) else {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


class MoveCoordinator:
    """Postgres-backed workspace moves + the move-freeze gate."""

    def __init__(self, dsn=None, team_store: TeamStore | None = None):
        self.dsn = resolve_dsn(dsn)
        self.teams = team_store or TeamStore(dsn)

    # -- lifecycle -------------------------------------------------------

    def start_move(
        self,
        bot_id: str,
        *,
        team_id: str,
        source_workspace: str,
        target_workspace: str,
        requested_by: str,
    ) -> MoveRecord:
        """Request a move. The bot is frozen from this moment on
        (status ``pending`` is a frozen status).

        Raises ``ValueError`` for empty ids/workspaces, ``MoveError``
        when the bot already has an active (frozen) move — one move
        per bot at a time, so two moves can never fight over it.
        """
        team_id = _validate_uuid(team_id, "team_id")
        requested_by = _validate_uuid(requested_by, "requested_by")
        if not isinstance(bot_id, str) or not bot_id.strip():
            raise ValueError("bot_id must be a non-empty string")
        if not isinstance(source_workspace, str) or not source_workspace.strip():
            raise ValueError("source_workspace must be a non-empty string")
        if not isinstance(target_workspace, str) or not target_workspace.strip():
            raise ValueError("target_workspace must be a non-empty string")
        if source_workspace.strip() == target_workspace.strip():
            raise ValueError("source and target workspaces must differ")
        if self.is_frozen(bot_id.strip()):
            raise MoveError(f"bot {bot_id!r} already has an active move")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO workspace_moves "
                "(bot_id, team_id, source_workspace, target_workspace, requested_by) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING *",
                (
                    bot_id.strip(),
                    team_id,
                    source_workspace.strip(),
                    target_workspace.strip(),
                    requested_by,
                ),
            )
            move = _move_row(cur.fetchone())
            conn.commit()
        return move

    def get_move(self, move_id: str) -> MoveRecord | None:
        """Return the move, or None when the id does not exist."""
        move_id = _validate_uuid(move_id, "move_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM workspace_moves WHERE id = %s", (move_id,))
            row = cur.fetchone()
        return _move_row(row) if row is not None else None

    def list_moves(
        self, *, bot_id: str | None = None, status: str | None = None
    ) -> list[MoveRecord]:
        """Moves, newest first; optionally filtered by bot or status."""
        if status is not None and status not in MOVE_STATUSES:
            raise ValueError(f"unknown move status: {status!r}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            if bot_id is not None and status is not None:
                cur.execute(
                    "SELECT * FROM workspace_moves WHERE bot_id = %s AND status = %s "
                    "ORDER BY created_at DESC, id",
                    (bot_id, status),
                )
            elif bot_id is not None:
                cur.execute(
                    "SELECT * FROM workspace_moves WHERE bot_id = %s ORDER BY created_at DESC, id",
                    (bot_id,),
                )
            elif status is not None:
                cur.execute(
                    "SELECT * FROM workspace_moves WHERE status = %s ORDER BY created_at DESC, id",
                    (status,),
                )
            else:
                cur.execute("SELECT * FROM workspace_moves ORDER BY created_at DESC, id")
            return [_move_row(row) for row in cur.fetchall()]

    def _transition(
        self,
        move_id: str,
        target: str,
        *,
        source: str | None = None,
        step: str | None = None,
        error: str | None = None,
        state: dict | None = None,
    ) -> MoveRecord:
        move_id = _validate_uuid(move_id, "move_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM workspace_moves WHERE id = %s", (move_id,))
            row = cur.fetchone()
            if row is None:
                raise MoveError(f"move not found: {move_id}")
            current = _move_row(row)
            if source is not None and current.status != source:
                raise MoveError(
                    f"cannot move {current.status} -> {target} (move {move_id}): "
                    f"this step only runs from {source}"
                )
            if target not in _ALLOWED_TRANSITIONS[current.status]:
                raise MoveError(f"cannot move {current.status} -> {target} (move {move_id})")
            if state is not None:
                json.dumps(state)  # fail fast when the state is not JSON-able
            cur.execute(
                "UPDATE workspace_moves SET status = %s, "
                "current_step = COALESCE(%s, current_step), "
                "error = %s, state = COALESCE(%s::jsonb, state), "
                "updated_at = now() WHERE id = %s RETURNING *",
                (
                    target,
                    step,
                    error,
                    json.dumps(state) if state is not None else None,
                    move_id,
                ),
            )
            move = _move_row(cur.fetchone())
            conn.commit()
        return move

    def begin_move(self, move_id: str, *, step: str = "started") -> MoveRecord:
        """``pending -> in_progress``: the move is now running.

        The source guard matters: ``resume_move`` targets the same
        status (``in_progress``) but only from ``failed``. Without
        it, a pending move could be "resumed" and a failed move
        "begun" — both wrong.
        """
        return self._transition(move_id, STATUS_IN_PROGRESS, source=STATUS_PENDING, step=step)

    def advance_move(self, move_id: str, step: str, *, state: dict | None = None) -> MoveRecord:
        """Record progress without changing the status. The ``state``
        dict is the resumable payload a later ``resume_move`` reads.
        """
        move_id = _validate_uuid(move_id, "move_id")
        if not isinstance(step, str) or not step.strip():
            raise ValueError("step must be a non-empty string")
        if state is not None:
            json.dumps(state)  # fail fast when the state is not JSON-able
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM workspace_moves WHERE id = %s", (move_id,))
            row = cur.fetchone()
            if row is None:
                raise MoveError(f"move not found: {move_id}")
            move = _move_row(row)
            if move.status not in (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_FAILED):
                raise MoveError(f"cannot advance a {move.status} move (move {move_id})")
            cur.execute(
                "UPDATE workspace_moves SET current_step = %s, "
                "state = COALESCE(%s::jsonb, state), updated_at = now() "
                "WHERE id = %s RETURNING *",
                (
                    step.strip(),
                    json.dumps(state) if state is not None else None,
                    move_id,
                ),
            )
            move = _move_row(cur.fetchone())
            conn.commit()
        return move

    def complete_move(self, move_id: str, *, state: dict | None = None) -> MoveRecord:
        """``in_progress -> done``: the bot now lives in the target
        workspace and the freeze is released.
        """
        return self._transition(move_id, STATUS_DONE, step="done", error=None, state=state)

    def fail_move(self, move_id: str, error: str) -> MoveRecord:
        """``pending|in_progress -> failed``: the bot STAYS frozen and
        the record keeps the failing step + error + resumable state,
        so the move can be resumed or cancelled — never silently
        half-moved.
        """
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be a non-empty string")
        return self._transition(move_id, STATUS_FAILED, error=error.strip())

    def resume_move(self, move_id: str) -> MoveRecord:
        """``failed -> in_progress``: retry from the recorded step
        and state. The freeze stays on until the move completes or
        is cancelled. Only a failed move resumes — a pending move
        must be begun, not resumed.
        """
        return self._transition(move_id, STATUS_IN_PROGRESS, source=STATUS_FAILED, step="resumed")

    def cancel_move(self, move_id: str) -> MoveRecord:
        """``pending|in_progress|failed -> cancelled``: abandon the
        move; the freeze is released and the bot stays in the source
        workspace.
        """
        return self._transition(move_id, STATUS_CANCELLED, step="cancelled")

    # -- move-freeze gate --------------------------------------------------

    def is_frozen(self, bot_id: str) -> bool:
        """True when the bot has a move in a frozen status (pending,
        in_progress, or failed). The freeze is derived from the move
        records, never from a separate flag.
        """
        if not isinstance(bot_id, str) or not bot_id.strip():
            raise ValueError("bot_id must be a non-empty string")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM workspace_moves WHERE bot_id = %s AND status = ANY(%s) LIMIT 1",
                (bot_id.strip(), list(FROZEN_STATUSES)),
            )
            return cur.fetchone() is not None

    def require_not_frozen(self, bot_id: str) -> None:
        """Raise ``MoveFrozenError`` when the bot's automation is
        frozen by an active move. The scheduler and the task runner
        must call this before starting work for a bot — it is the
        enforcement point of move-freeze.
        """
        if self.is_frozen(bot_id):
            raise MoveFrozenError(
                f"bot {bot_id!r} automation is frozen by an active workspace move"
            )
