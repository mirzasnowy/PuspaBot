"""Shared tasks and chat summaries for a room (B6).

The agent works for the group: it summarises the chat, splits
work into tasks, and reminds owners about deadlines. This module
holds the two deterministic halves of that work:

- **Summarising** (``summarize_messages``) — a pure function over
  a list of chat messages. No model call: it counts messages per
  author, pulls out lines that state a decision (``Decision:``)
  or a task (``TODO:`` / ``- [ ]``), and returns a short plain
  summary the agent can post or store with
  ``RoomMemoryStore.record_summary``. A model-written summary is
  a follow-up; the deterministic summary is what the beta ships,
  so a summary is never a fabricated paraphrase.
- **Splitting tasks** (``split_tasks``) — a pure function that
  turns chat text into task titles: ``TODO: ...`` lines and
  ``- [ ] ...`` checklist lines. Lines without an explicit marker
  are not guessed into tasks.
- **The shared task board** (``RoomTaskStore``) — Postgres-backed
  tasks with an owner, an optional deadline, and a status
  (``open`` / ``done``). Only room members may add, complete, or
  list tasks. ``due_soon`` and ``overdue`` are the reminder
  queries: the caller (a scheduler, B9) sends the reminders — this
  module only answers which tasks need one. A task's owner may be
  empty (unassigned); when a member leaves, their tasks become
  unassigned (see ``RoomStore.leave_room``).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn
from puspa.evidence.outcomes import write_outcome
from puspa.ledger import Ledger

from .errors import GuestReadOnlyError, NotRoomMemberError
from .rooms import ROLE_GUEST, RoomStore, _validate_uuid

__all__ = [
    "RoomTask",
    "RoomTaskStore",
    "add_task",
    "complete_task",
    "due_soon",
    "list_tasks",
    "overdue",
    "split_tasks",
    "summarize_messages",
]

#: A task title is at most this long (chat lines are longer; the
#: title is the first line, trimmed).
MAX_TASK_TITLE_CHARS = 500

_TODO_RE = re.compile(r"^\s*(?:TODO\s*:|- \[\s\]\s*)\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_DECISION_RE = re.compile(r"^\s*Decision\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class RoomTask:
    """One shared task: room, title, owner, deadline, status."""

    id: str
    room_id: str
    title: str
    owner_user_id: str | None
    due_at: datetime | None
    status: str
    created_by: str
    created_at: datetime
    completed_at: datetime | None


def _task_row(row) -> RoomTask:
    return RoomTask(
        id=str(row["id"]),
        room_id=str(row["room_id"]),
        title=row["title"],
        owner_user_id=str(row["owner_user_id"]) if row["owner_user_id"] else None,
        due_at=row["due_at"],
        status=row["status"],
        created_by=str(row["created_by"]),
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def summarize_messages(messages: list[dict]) -> str:
    """Summarise a list of chat messages deterministically.

    Each message is a dict with ``author`` (a display name) and
    ``text``. Returns a short plain summary: how many messages
    each author sent, the decisions stated (``Decision:`` lines),
    and the open tasks stated (``TODO:`` / ``- [ ]`` lines).
    Nothing is invented: a chat with no decisions lists none.
    """
    if not messages:
        return "No messages to summarise."
    authors = Counter(str(m.get("author") or "unknown") for m in messages)
    counts = ", ".join(f"{author} ({n})" for author, n in authors.most_common())
    decisions: list[str] = []
    todos: list[str] = []
    for message in messages:
        text = str(message.get("text") or "")
        decisions.extend(match.group(1) for match in _DECISION_RE.finditer(text))
        todos.extend(match.group(1) for match in _TODO_RE.finditer(text))
    lines = [f"{len(messages)} messages: {counts}."]
    if decisions:
        lines.append("Decisions: " + "; ".join(decisions) + ".")
    else:
        lines.append("No decisions stated.")
    if todos:
        lines.append("Open tasks: " + "; ".join(todos) + ".")
    else:
        lines.append("No tasks stated.")
    return " ".join(lines)


def split_tasks(text: str) -> list[str]:
    """Split chat text into task titles.

    Only explicit markers become tasks: ``TODO: ...`` lines and
    ``- [ ] ...`` checklist lines. Returns the titles in order,
    each trimmed to ``MAX_TASK_TITLE_CHARS``. An empty or
    marker-less text yields an empty list — the agent never guesses
    a task out of ordinary chat.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    titles = []
    for match in _TODO_RE.finditer(text):
        title = match.group(1).strip()
        if title:
            titles.append(title[:MAX_TASK_TITLE_CHARS])
    return titles


class RoomTaskStore:
    """Postgres-backed shared task board for one room."""

    def __init__(self, dsn=None, room_store: RoomStore | None = None):
        self.dsn = resolve_dsn(dsn)
        self.rooms = room_store or RoomStore(dsn)

    def add_task(
        self,
        room_id: str,
        *,
        title: str,
        created_by: str,
        owner_user_id: str | None = None,
        due_at: datetime | None = None,
    ) -> RoomTask:
        """Add a task to the room board. The creator and (when set)
        the owner must both be members of the room.

        Raises ``NotRoomMemberError`` for a non-member creator or
        owner, ``GuestReadOnlyError`` for a guest creator (guests
        are read-only, plan section 12.1), ``ValueError`` for an
        empty or oversize title.
        """
        room_id = _validate_uuid(room_id, "room_id")
        creator = _validate_uuid(created_by, "created_by")
        owner = _validate_uuid(owner_user_id, "owner_user_id") if owner_user_id else None
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        if len(title.strip()) > MAX_TASK_TITLE_CHARS:
            raise ValueError(f"title is longer than {MAX_TASK_TITLE_CHARS} characters")
        if not self.rooms.is_member(room_id, creator):
            raise NotRoomMemberError(f"user {creator} is not a member of room {room_id}")
        if self.rooms.role_of(room_id, creator) == ROLE_GUEST:
            raise GuestReadOnlyError(f"guest {creator} may not add tasks to room {room_id}")
        if owner is not None and not self.rooms.is_member(room_id, owner):
            raise NotRoomMemberError(f"user {owner} is not a member of room {room_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO room_tasks (room_id, title, owner_user_id, due_at, created_by) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING *",
                (room_id, title.strip(), owner, due_at, creator),
            )
            task = _task_row(cur.fetchone())
            conn.commit()
        return task

    def complete_task(
        self,
        task_id: str,
        *,
        completed_by: str,
        ledger: Ledger | None = None,
        agent_id: str | None = None,
        evidence: list[dict] | None = None,
    ) -> RoomTask | None:
        """Mark a task done. Any member of the room may complete a
        task (the owner is reminded, not gated). Completing an
        already-done task is a no-op returning the current row.

        When a ledger is given (``agent_id`` required with it),
        the completion is also recorded as a ``task_done``
        outcome record through the shared B2 writer, with the
        board task itself as the evidence (a caller that holds
        richer proof — a delegation's executed steps, for
        example — passes it as ``evidence`` instead). The
        record is written after the status change, so a ledger
        failure never rolls back the user's work; a completion
        whose record failed to write is not re-recorded by a
        later call, because the later call is a no-op.

        Returns None when the task id does not exist. Raises
        ``NotRoomMemberError`` for a non-member.
        """
        task_id = _validate_uuid(task_id, "task_id")
        completed_by = _validate_uuid(completed_by, "completed_by")
        if ledger is not None and agent_id is None:
            raise ValueError("agent_id is required when a ledger is given")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM room_tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
            if row is None:
                return None
            task = _task_row(row)
            if not self.rooms.is_member(task.room_id, completed_by):
                raise NotRoomMemberError(
                    f"user {completed_by} is not a member of room {task.room_id}"
                )
            if task.status == "done":
                return task
            cur.execute(
                "UPDATE room_tasks SET status = 'done', completed_at = now() "
                "WHERE id = %s RETURNING *",
                (task_id,),
            )
            task = _task_row(cur.fetchone())
            conn.commit()
        if ledger is not None:
            write_outcome(
                ledger,
                task_id=task.id,
                agent_id=agent_id,  # type: ignore[arg-type]
                outcome_type="task_done",
                user_id=completed_by,
                room_id=task.room_id,
                subject_id=task.id,
                summary=f"task done: {task.title}",
                evidence=evidence
                if evidence is not None
                else [
                    {
                        "kind": "board_task",
                        "task_id": task.id,
                        "title": task.title,
                        "status": "done",
                        "completed_by": completed_by,
                    }
                ],
            )
        return task

    def list_tasks(
        self, room_id: str, *, viewer_user_id: str, status: str | None = None
    ) -> list[RoomTask]:
        """Tasks of a room, open first, then by deadline, then by
        creation time. ``status`` filters to ``open`` or ``done``.

        Raises ``NotRoomMemberError`` for a non-member,
        ``ValueError`` for an unknown status.
        """
        room_id = _validate_uuid(room_id, "room_id")
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        if status is not None and status not in ("open", "done"):
            raise ValueError(f"unknown task status: {status!r}")
        if not self.rooms.is_member(room_id, viewer):
            raise NotRoomMemberError(f"user {viewer} is not a member of room {room_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            sql = "SELECT * FROM room_tasks WHERE room_id = %s "
            params: list = [room_id]
            if status is not None:
                sql += "AND status = %s "
                params.append(status)
            sql += "ORDER BY (status = 'done'), due_at NULLS LAST, created_at, id"
            cur.execute(sql, params)
            return [_task_row(row) for row in cur.fetchall()]

    def due_soon(
        self, room_id: str, *, viewer_user_id: str, within: timedelta = timedelta(days=1)
    ) -> list[RoomTask]:
        """Open tasks whose deadline falls within ``within`` from
        now (the reminder set). Raises ``NotRoomMemberError`` for a
        non-member.
        """
        room_id = _validate_uuid(room_id, "room_id")
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        if not self.rooms.is_member(room_id, viewer):
            raise NotRoomMemberError(f"user {viewer} is not a member of room {room_id}")
        horizon = datetime.now(UTC) + within
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM room_tasks WHERE room_id = %s AND status = 'open' "
                "AND due_at IS NOT NULL AND due_at <= %s "
                "ORDER BY due_at, created_at, id",
                (room_id, horizon),
            )
            return [_task_row(row) for row in cur.fetchall()]

    def overdue(self, room_id: str, *, viewer_user_id: str) -> list[RoomTask]:
        """Open tasks whose deadline has already passed."""
        return self.due_soon(room_id, viewer_user_id=viewer_user_id, within=timedelta(0))


def _store(dsn=None) -> RoomTaskStore:
    return RoomTaskStore(dsn)


def add_task(
    room_id: str,
    *,
    title: str,
    created_by: str,
    owner_user_id: str | None = None,
    due_at: datetime | None = None,
    dsn=None,
) -> RoomTask:
    return _store(dsn).add_task(
        room_id, title=title, created_by=created_by, owner_user_id=owner_user_id, due_at=due_at
    )


def complete_task(
    task_id: str,
    *,
    completed_by: str,
    ledger: Ledger | None = None,
    agent_id: str | None = None,
    evidence: list[dict] | None = None,
    dsn=None,
) -> RoomTask | None:
    return _store(dsn).complete_task(
        task_id, completed_by=completed_by, ledger=ledger, agent_id=agent_id, evidence=evidence
    )


def list_tasks(
    room_id: str, *, viewer_user_id: str, status: str | None = None, dsn=None
) -> list[RoomTask]:
    return _store(dsn).list_tasks(room_id, viewer_user_id=viewer_user_id, status=status)


def due_soon(
    room_id: str, *, viewer_user_id: str, within: timedelta = timedelta(days=1), dsn=None
) -> list[RoomTask]:
    return _store(dsn).due_soon(room_id, viewer_user_id=viewer_user_id, within=within)


def overdue(room_id: str, *, viewer_user_id: str, dsn=None) -> list[RoomTask]:
    return _store(dsn).overdue(room_id, viewer_user_id=viewer_user_id)
