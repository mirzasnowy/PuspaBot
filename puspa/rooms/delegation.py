"""Delegated tasks with approval (B9).

A user hands a task to the agent, or to a teammate through the
agent; the agent works, reports, and waits for approval on
irreversible steps. The shared task board is the B6 board
(``puspa.rooms.tasks.RoomTaskStore``): this module extends it,
it never rebuilds a second board.

What lives here:

- **Delegation** (``DelegationService``) — a room task is handed
  to an agent subagent (``delegate_kind="agent"``, the assignee
  is a G25 subagent handle or id) or to a teammate
  (``delegate_kind="teammate"``, the assignee is a room member
  and becomes the task owner). Delegation status is
  ``delegated`` -> ``in_progress`` -> ``done``, with
  ``awaiting_approval`` while an irreversible step waits and
  ``blocked`` while the worker reports it cannot proceed.
  Delegating, starting, reporting, blocking, and resuming are
  all member-only; a guest may never be handed work and may
  never hand work to anyone.
- **Approval-gated steps** — the agent's work is proposed as
  steps (``propose_step``). A step flagged irreversible (or
  recognised by the G4 taxonomy as delete / send / share /
  post / pay / credential change) may only run after approval
  through the existing G4/G5 path: ``ApprovalPolicy.decide``
  classifies it, and when a gate + token are supplied the
  request is filed with ``ApprovalPolicy.request`` so the G5
  ``Gate`` binds the approval to the step's content hash and
  enforces its 24-hour expiry. No parallel approval system is
  invented: a step with no approval path wired in stays
  ``awaiting_approval`` and ``execute_step`` refuses it
  (fail-closed). ``execute_step`` re-reads the step row and
  refuses anything that is not ``approved`` (irreversible) or
  ``proposed`` (reversible); an approved step filed with the
  gate runs through ``Gate.execute``, so a changed content hash
  or an expired token voids the approval at execution time.
  The approver is the task owner (or the creator when the task
  has no owner): the person who delegated the work approves
  its irreversible steps.
- **The board** (``board`` / ``render_board``) — every room
  task with its delegation and its pending-step count, for the
  PWA (``puspa.pwa.rooms_api``) and for the group chat: a group
  message "@puspa board" / "@puspa tasks" is answered with the
  rendered board instead of going to the agent (see
  ``puspa.rooms.routing.handle_group_inbound``).
- **Reminders** — ``delegation_reminders`` answers which
  delegated tasks need a nudge (the B6 ``due_soon`` / ``overdue``
  queries, filtered to delegated tasks); each task's urgency is
  classified with the G8 scheduler's ``classify_due``. This
  module only answers which tasks need a reminder — the caller
  (a scheduler) sends it.

Honest limits: the agent worker itself is not executed here —
``delegate_to_agent`` records the delegation and (when a G25
lifecycle is passed) verifies the subagent handle resolves;
running the child is the caller's job, through
``SubagentLifecycle``. No live group-chat delivery is verified
anywhere in this repo (same as B6): the board reply is tested
with a fake adapter and a fake agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from puspa.accounts.users import resolve_dsn
from puspa.ledger import Ledger
from puspa.policy.approvals import ApprovalPolicy
from puspa.policy.gate import Gate, GateDenied
from puspa.scheduler.due import classify_due

from .errors import NotRoomMemberError
from .rooms import APPROVER_ROLES, RoomStore, _validate_uuid
from .tasks import RoomTask, RoomTaskStore, _task_row

__all__ = [
    "DELEGATION_STATUSES",
    "STEP_STATUSES",
    "BoardRow",
    "DelegatedStep",
    "DelegatedTask",
    "DelegationService",
    "approve_step",
    "board",
    "classify_task_due",
    "complete_delegated_task",
    "delegate_to_agent",
    "delegate_to_teammate",
    "execute_step",
    "is_board_command",
    "propose_step",
    "reject_step",
    "reminder_lines",
    "render_board",
    "report_progress",
    "start_work",
]

#: Delegation statuses (stored on delegated_tasks.status).
DELEGATION_STATUSES = ("delegated", "in_progress", "awaiting_approval", "blocked", "done")

#: Step statuses (stored on delegated_task_steps.status).
STEP_STATUSES = ("proposed", "awaiting_approval", "approved", "rejected", "executed")

#: Steps in these statuses still need work before the task may complete.
_OPEN_STEP_STATUSES = ("proposed", "awaiting_approval")

#: Text that asks for the board in a group chat (after the @Puspa
#: mention is stripped). English and everyday Indonesian forms.
_BOARD_COMMANDS = frozenset(
    {
        "board",
        "tasks",
        "task board",
        "show board",
        "show tasks",
        "tugas",
        "papan tugas",
        "daftar tugas",
        "lihat tugas",
    }
)
_BOARD_COMMAND_RE = re.compile(r"@puspa\b", re.IGNORECASE)


@dataclass(frozen=True)
class DelegatedTask:
    """A room task plus its delegation row."""

    task: RoomTask
    delegate_kind: str
    assignee_ref: str
    status: str
    last_report: str | None
    reported_at: datetime | None
    started_at: datetime | None
    blocked_reason: str | None
    created_at: datetime
    updated_at: datetime
    steps: list[DelegatedStep] = field(default_factory=list)

    @property
    def pending_steps(self) -> int:
        """Steps still waiting for a decision or execution."""
        return sum(1 for step in self.steps if step.status in _OPEN_STEP_STATUSES)


@dataclass(frozen=True)
class DelegatedStep:
    """One proposed unit of work on a delegated task."""

    id: str
    task_id: str
    description: str
    tool_name: str
    irreversible: bool
    status: str
    content: dict
    approval_request_id: str | None
    approved_by: str | None
    decided_at: datetime | None
    executed_at: datetime | None
    result: str | None
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class BoardRow:
    """One row of the shared board: the task, its delegation
    (None when the task was never delegated), and how many of
    its steps are still open."""

    task: RoomTask
    delegation: DelegatedTask | None
    pending_steps: int = 0


def _step_row(row) -> DelegatedStep:
    return DelegatedStep(
        id=str(row["id"]),
        task_id=str(row["task_id"]),
        description=row["description"],
        tool_name=row["tool_name"],
        irreversible=bool(row["irreversible"]),
        status=row["status"],
        content=dict(row["content"] or {}),
        approval_request_id=row["approval_request_id"],
        approved_by=str(row["approved_by"]) if row["approved_by"] else None,
        decided_at=row["decided_at"],
        executed_at=row["executed_at"],
        result=row["result"],
        created_by=str(row["created_by"]),
        created_at=row["created_at"],
    )


def _delegation_row(row, task: RoomTask, steps: list[DelegatedStep]) -> DelegatedTask:
    return DelegatedTask(
        task=task,
        delegate_kind=row["delegate_kind"],
        assignee_ref=row["assignee_ref"],
        status=row["status"],
        last_report=row["last_report"],
        reported_at=row["reported_at"],
        started_at=row["started_at"],
        blocked_reason=row["blocked_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        steps=steps,
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def is_board_command(text: str | None) -> bool:
    """True when a group message asks for the task board.

    The @Puspa mention is stripped first; the rest must be a
    board request ("board", "tasks", "tugas", ...), optionally
    followed by punctuation. Anything else (a summary request,
    a question) is not a board command and goes to the agent.
    """
    if not isinstance(text, str):
        return False
    cleaned = _BOARD_COMMAND_RE.sub("", text).strip().lower().rstrip(".!?").strip()
    return cleaned in _BOARD_COMMANDS


def classify_task_due(task: RoomTask, now: datetime | None = None) -> str:
    """Urgency of a task's deadline (G8 ``classify_due``):
    ``no_deadline`` | ``not_yet`` | ``due`` | ``late`` |
    ``missed``."""
    if task.due_at is None:
        return "no_deadline"
    return classify_due(task.due_at, now)


def _short_id(user_id: str | None) -> str:
    return (user_id or "unassigned")[:8]


def render_board(room_name: str, rows: list[BoardRow], now: datetime | None = None) -> str:
    """Render the shared board as plain text for the group chat.

    One line per task: status, title, owner, deadline with its
    urgency, delegation, and how many steps wait for approval
    or execution. A board with no tasks says so — it never
    invents work. Pure function: no database access.
    """
    now = now or datetime.now(UTC)
    lines = [f"Task board: {room_name}"]
    if not rows:
        lines.append("No tasks yet.")
        return "\n".join(lines)
    for row in rows:
        task = row.task
        due = classify_task_due(task, now)
        due_text = (
            "no deadline" if task.due_at is None else task.due_at.strftime("%Y-%m-%d %H:%M UTC")
        )
        if due in ("due", "late"):
            due_text += " (due soon)"
        elif due == "missed":
            due_text += " (overdue)"
        parts = [
            f"[{task.status}] {task.title}",
            f"owner: {_short_id(task.owner_user_id)}",
            f"due: {due_text}",
        ]
        if row.delegation is not None:
            who = (
                f"agent {row.delegation.assignee_ref}"
                if row.delegation.delegate_kind == "agent"
                else f"teammate {_short_id(row.delegation.assignee_ref)}"
            )
            parts.append(f"delegated to {who} ({row.delegation.status})")
            if row.delegation.last_report:
                parts.append(f"last report: {row.delegation.last_report}")
            if row.delegation.blocked_reason:
                parts.append(f"blocked: {row.delegation.blocked_reason}")
        if row.pending_steps:
            parts.append(f"{row.pending_steps} step(s) waiting")
        lines.append(" - ".join(parts))
    return "\n".join(lines)


def reminder_lines(rows: list[BoardRow], now: datetime | None = None) -> list[str]:
    """One reminder line per delegated task that is due soon or
    overdue. Pure function: the caller (a scheduler) sends
    these; this module only writes them."""
    now = now or datetime.now(UTC)
    lines: list[str] = []
    for row in rows:
        if row.delegation is None:
            continue
        due = classify_task_due(row.task, now)
        if due == "no_deadline":
            continue
        when = "overdue" if due == "missed" else "due soon"
        assignee = row.delegation.assignee_ref
        lines.append(
            f"Reminder: '{row.task.title}' is {when} "
            f"(owner {_short_id(row.task.owner_user_id)}, "
            f"delegated to {assignee}, status {row.delegation.status})."
        )
    return lines


class DelegationService:
    """Delegated tasks on top of the B6 shared task board.

    ``policy`` (G4 ``ApprovalPolicy``) and ``gate`` (G5 ``Gate``)
    are optional: without them an irreversible step can be
    proposed and recorded, but it can never be approved or
    executed — the service fails closed to asking. With them,
    approval goes through the existing path and nothing here
    invents a second one.
    """

    def __init__(
        self,
        dsn=None,
        *,
        room_store: RoomStore | None = None,
        task_store: RoomTaskStore | None = None,
        policy: ApprovalPolicy | None = None,
        gate: Gate | None = None,
    ):
        self.dsn = resolve_dsn(dsn)
        self.rooms = room_store or RoomStore(dsn)
        self.tasks = task_store or RoomTaskStore(dsn)
        self.policy = policy
        self.gate = gate

    # -- reads ----------------------------------------------------------

    def _get_task(self, task_id: str) -> RoomTask | None:
        task_id = _validate_uuid(task_id, "task_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM room_tasks WHERE id = %s", (task_id,))
            row = cur.fetchone()
        return _task_row(row) if row is not None else None

    def _require_task(self, task_id: str) -> RoomTask:
        task = self._get_task(task_id)
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        return task

    def _steps_for(self, task_id: str) -> list[DelegatedStep]:
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM delegated_task_steps WHERE task_id = %s ORDER BY created_at, id",
                (task_id,),
            )
            return [_step_row(row) for row in cur.fetchall()]

    def _get_step(self, step_id: str) -> DelegatedStep | None:
        step_id = _validate_uuid(step_id, "step_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM delegated_task_steps WHERE id = %s", (step_id,))
            row = cur.fetchone()
        return _step_row(row) if row is not None else None

    def _require_step(self, step_id: str) -> DelegatedStep:
        step = self._get_step(step_id)
        if step is None:
            raise ValueError(f"step not found: {step_id}")
        return step

    def get_delegation(self, task_id: str) -> DelegatedTask | None:
        """The delegation row for a task, with its steps, or None
        when the task was never delegated."""
        task = self._require_task(task_id)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM delegated_tasks WHERE task_id = %s", (task.id,))
            row = cur.fetchone()
        if row is None:
            return None
        return _delegation_row(row, task, self._steps_for(task.id))

    def board(self, room_id: str, *, viewer_user_id: str) -> list[BoardRow]:
        """Every task of the room with its delegation, open first.
        Raises ``NotRoomMemberError`` for a non-member."""
        room_id = _validate_uuid(room_id, "room_id")
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        if not self.rooms.is_member(room_id, viewer):
            raise NotRoomMemberError(f"user {viewer} is not a member of room {room_id}")
        return self._board_rows(room_id)

    def board_for_chat(self, room_id: str) -> list[BoardRow]:
        """The board without a viewer check, for the group chat:
        the chat is already linked to the room and the message
        was addressed to Puspa, so the link is the authorisation.
        """
        return self._board_rows(_validate_uuid(room_id, "room_id"))

    def _board_rows(self, room_id: str) -> list[BoardRow]:
        tasks = self.tasks.list_tasks(room_id, viewer_user_id=self._any_member(room_id))
        rows: list[BoardRow] = []
        for task in tasks:
            delegation = self.get_delegation(task.id)
            pending = delegation.pending_steps if delegation is not None else 0
            rows.append(BoardRow(task=task, delegation=delegation, pending_steps=pending))
        return rows

    def _any_member(self, room_id: str) -> str:
        members = self.rooms.list_members(room_id)
        if not members:
            raise NotRoomMemberError(f"room {room_id} has no members")
        return members[0].user_id

    def delegation_reminders(
        self, room_id: str, *, viewer_user_id: str, within: timedelta = timedelta(days=1)
    ) -> list[BoardRow]:
        """Delegated tasks that are due soon or overdue — the
        reminder set for the scheduler. Non-delegated tasks are
        not returned: their owners are reminded by the B6
        ``due_soon`` / ``overdue`` queries instead."""
        room_id = _validate_uuid(room_id, "room_id")
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        if not self.rooms.is_member(room_id, viewer):
            raise NotRoomMemberError(f"user {viewer} is not a member of room {room_id}")
        due = self.tasks.due_soon(room_id, viewer_user_id=viewer, within=within)
        rows: list[BoardRow] = []
        for task in due:
            delegation = self.get_delegation(task.id)
            if delegation is None or delegation.status == "done":
                continue
            rows.append(
                BoardRow(task=task, delegation=delegation, pending_steps=delegation.pending_steps)
            )
        return rows

    # -- permission helpers ---------------------------------------------

    def _require_delegator(self, room_id: str, user_id: str) -> None:
        """Only an owner or member may delegate work; a guest or
        non-member may not."""
        if self.rooms.role_of(room_id, user_id) not in APPROVER_ROLES:
            raise NotRoomMemberError(f"user {user_id} may not delegate tasks in room {room_id}")

    def _require_worker(self, delegation: DelegatedTask, by: str) -> None:
        """Only the assignee, the task creator, or a room owner
        may work on / report on a delegated task."""
        task = delegation.task
        is_assignee = by == delegation.assignee_ref
        is_creator = by == task.created_by
        # The assignee may be a subagent ref, not a user id: only
        # ask the room store about owner-hood for UUID-shaped ids.
        is_owner = _looks_like_uuid(by) and self.rooms.is_owner(task.room_id, by)
        if not (is_assignee or is_creator or is_owner):
            raise NotRoomMemberError(f"user {by} may not work on delegated task {task.id}")

    def _approver_for(self, task: RoomTask) -> str:
        """Who approves irreversible steps: the task owner, or
        the creator when the task has no owner."""
        return task.owner_user_id or task.created_by

    # -- delegation -------------------------------------------------------

    def delegate_to_agent(
        self,
        task_id: str,
        *,
        delegated_by: str,
        subagent_ref: str,
        lifecycle=None,
    ) -> DelegatedTask:
        """Hand a task to the agent. ``subagent_ref`` is the G25
        subagent handle (or id) that will do the work. When a
        lifecycle is passed, the handle must resolve there —
        a forged handle is refused before anything is recorded.
        The task must be open; delegating an already-delegated
        task re-points it at the new subagent.
        """
        task = self._require_task(task_id)
        delegated_by = _validate_uuid(delegated_by, "delegated_by")
        if not isinstance(subagent_ref, str) or not subagent_ref.strip():
            raise ValueError("subagent_ref must be a non-empty string")
        self._require_delegator(task.room_id, delegated_by)
        if task.status != "open":
            raise ValueError(f"only an open task can be delegated (task is {task.status})")
        if lifecycle is not None:
            lifecycle.status(subagent_ref)
        return self._upsert_delegation(
            task, delegate_kind="agent", assignee_ref=subagent_ref.strip()
        )

    def delegate_to_teammate(
        self, task_id: str, *, delegated_by: str, teammate_user_id: str
    ) -> DelegatedTask:
        """Hand a task to a teammate through the agent: the
        teammate becomes the task owner (the B6 board shows the
        new owner) and the delegation records the handover. The
        teammate must be an owner or member of the room — a
        guest is never handed work.
        """
        task = self._require_task(task_id)
        delegated_by = _validate_uuid(delegated_by, "delegated_by")
        teammate = _validate_uuid(teammate_user_id, "teammate_user_id")
        self._require_delegator(task.room_id, delegated_by)
        if task.status != "open":
            raise ValueError(f"only an open task can be delegated (task is {task.status})")
        if self.rooms.role_of(task.room_id, teammate) not in APPROVER_ROLES:
            raise NotRoomMemberError(
                f"user {teammate} may not take delegated tasks in room {task.room_id}"
            )
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE room_tasks SET owner_user_id = %s WHERE id = %s",
                (teammate, task.id),
            )
            conn.commit()
        task = self._require_task(task_id)
        return self._upsert_delegation(task, delegate_kind="teammate", assignee_ref=teammate)

    def _upsert_delegation(
        self, task: RoomTask, *, delegate_kind: str, assignee_ref: str
    ) -> DelegatedTask:
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO delegated_tasks (task_id, room_id, delegate_kind, assignee_ref) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (task_id) DO UPDATE SET "
                "delegate_kind = EXCLUDED.delegate_kind, "
                "assignee_ref = EXCLUDED.assignee_ref, "
                "status = 'delegated', updated_at = now() "
                "RETURNING *",
                (task.id, task.room_id, delegate_kind, assignee_ref),
            )
            row = cur.fetchone()
            conn.commit()
        return _delegation_row(row, task, self._steps_for(task.id))

    def _set_delegation_status(self, task_id: str, status: str, **extra) -> None:
        if status not in DELEGATION_STATUSES:
            raise ValueError(f"unknown delegation status: {status!r}")
        sets = ["status = %s", "updated_at = now()"]
        params: list = [status]
        for column, value in extra.items():
            sets.append(f"{column} = %s")
            params.append(value)
        params.append(task_id)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE delegated_tasks SET {', '.join(sets)} WHERE task_id = %s", params)
            conn.commit()

    def start_work(self, task_id: str, *, by: str) -> DelegatedTask:
        """The assignee starts work: delegated -> in_progress."""
        delegation = self.get_delegation(task_id)
        if delegation is None:
            raise ValueError(f"task {task_id} is not delegated")
        by = _validate_uuid(by, "by") if _looks_like_uuid(by) else by
        self._require_worker(delegation, by)
        if delegation.status not in ("delegated", "blocked"):
            raise ValueError(f"cannot start work on a task that is {delegation.status}")
        self._set_delegation_status(task_id, "in_progress", started_at=datetime.now(UTC))
        result = self.get_delegation(task_id)
        assert result is not None
        return result

    def report_progress(self, task_id: str, *, by: str, note: str) -> DelegatedTask:
        """The worker reports: the note is stored on the
        delegation row and shown on the board, so the group sees
        progress without asking. Reporting never changes the
        delegation status (a blocked task stays blocked until
        resumed)."""
        delegation = self.get_delegation(task_id)
        if delegation is None:
            raise ValueError(f"task {task_id} is not delegated")
        if not isinstance(note, str) or not note.strip():
            raise ValueError("report note must be a non-empty string")
        by = _validate_uuid(by, "by") if _looks_like_uuid(by) else by
        self._require_worker(delegation, by)
        self._set_delegation_status(
            task_id, delegation.status, last_report=note.strip(), reported_at=datetime.now(UTC)
        )
        result = self.get_delegation(task_id)
        assert result is not None
        return result

    def block_task(self, task_id: str, *, by: str, reason: str) -> DelegatedTask:
        """The worker reports it cannot proceed. The reason is
        stored and shown on the board."""
        delegation = self.get_delegation(task_id)
        if delegation is None:
            raise ValueError(f"task {task_id} is not delegated")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("blocked reason must be a non-empty string")
        by = _validate_uuid(by, "by") if _looks_like_uuid(by) else by
        self._require_worker(delegation, by)
        if delegation.status == "done":
            raise ValueError("cannot block a done task")
        self._set_delegation_status(task_id, "blocked", blocked_reason=reason.strip())
        result = self.get_delegation(task_id)
        assert result is not None
        return result

    def resume_task(self, task_id: str, *, by: str) -> DelegatedTask:
        """A blocked task goes back to work (in_progress when it
        was started, delegated when it never started)."""
        delegation = self.get_delegation(task_id)
        if delegation is None:
            raise ValueError(f"task {task_id} is not delegated")
        by = _validate_uuid(by, "by") if _looks_like_uuid(by) else by
        self._require_worker(delegation, by)
        if delegation.status != "blocked":
            raise ValueError(f"only a blocked task can be resumed (task is {delegation.status})")
        next_status = "in_progress" if delegation.started_at is not None else "delegated"
        self._set_delegation_status(task_id, next_status, blocked_reason=None)
        result = self.get_delegation(task_id)
        assert result is not None
        return result

    def complete_task(
        self,
        task_id: str,
        *,
        completed_by: str,
        ledger: Ledger | None = None,
        agent_id: str | None = None,
    ) -> RoomTask | None:
        """Complete a delegated task: refused while any step is
        still proposed or awaiting approval — the work is not
        done until its steps are decided and executed. On
        success the B6 board marks the task done and the
        delegation closes with it.

        When a ledger is given (``agent_id`` required with it),
        the completion is recorded once, through the shared B2
        writer, as a ``task_done`` outcome record: the board
        task plus the delegation's decided steps are the
        evidence. The record is written by the B6 board call
        below, so there is exactly one record per completion.
        """
        if ledger is not None and agent_id is None:
            raise ValueError("agent_id is required when a ledger is given")
        delegation = self.get_delegation(task_id)
        if delegation is None:
            raise ValueError(f"task {task_id} is not delegated")
        if delegation.pending_steps:
            raise ValueError(
                f"cannot complete task {task_id}: {delegation.pending_steps} step(s) still waiting"
            )
        evidence: list[dict] | None = None
        if ledger is not None:
            evidence = [
                {
                    "kind": "board_task",
                    "task_id": delegation.task.id,
                    "title": delegation.task.title,
                    "status": "done",
                    "completed_by": completed_by,
                }
            ]
            for step in delegation.steps:
                evidence.append(
                    {
                        "kind": "delegated_step",
                        "step_id": step.id,
                        "description": step.description,
                        "status": step.status,
                        "result": step.result,
                    }
                )
        done = self.tasks.complete_task(
            task_id, completed_by=completed_by, ledger=ledger, agent_id=agent_id, evidence=evidence
        )
        if done is not None:
            self._set_delegation_status(task_id, "done")
        return done

    # -- steps: propose / approve / reject / execute ----------------------

    def propose_step(
        self,
        task_id: str,
        *,
        by: str,
        description: str,
        tool_name: str,
        irreversible: bool = False,
        content: dict | None = None,
        approval_agent_id: str | None = None,
        token=None,
    ) -> DelegatedStep:
        """Propose one step of work. A reversible step is
        ``proposed`` and may run without approval. An
        irreversible step goes through the G4 policy:

        - no policy wired in -> ``awaiting_approval`` with no
          approval request: nothing may approve or run it until
          a policy is wired in (fail-closed);
        - policy says deny / escalate to a human -> ``rejected``
          at once (credential changes are never approvable);
        - policy says allow (a usable tiered answer) ->
          ``approved`` at once; when a gate + token are also
          supplied the approval is filed with the gate too, so
          execution still goes through ``Gate.execute``;
        - policy says ask -> ``awaiting_approval``; when a gate
          + token are supplied the request is filed through
          ``ApprovalPolicy.request`` (the gate binds it to the
          content hash) and the request id is stored on the step.

        ``content`` is the exact action payload the approval
        binds to; it defaults to the description + task id.
        """
        delegation = self.get_delegation(task_id)
        if delegation is None:
            raise ValueError(f"task {task_id} is not delegated")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("step description must be a non-empty string")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_name must be a non-empty string")
        by = _validate_uuid(by, "by") if _looks_like_uuid(by) else by
        self._require_worker(delegation, by)
        if delegation.status == "done":
            raise ValueError("cannot propose a step on a done task")
        payload = dict(content) if content is not None else {}
        payload.setdefault("description", description.strip())
        payload.setdefault("task_id", delegation.task.id)
        tool_name = tool_name.strip()

        status = "proposed"
        approval_request_id: str | None = None
        if irreversible:
            status, approval_request_id = self._decide_irreversible_step(
                delegation, tool_name, payload, approval_agent_id, token
            )
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO delegated_task_steps "
                "(task_id, description, tool_name, irreversible, status, content, "
                " approval_request_id, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (
                    delegation.task.id,
                    description.strip(),
                    tool_name,
                    irreversible,
                    status,
                    Json(payload),
                    approval_request_id,
                    delegation.task.created_by if not _looks_like_uuid(by) else by,
                ),
            )
            step = _step_row(cur.fetchone())
            conn.commit()
        if status == "awaiting_approval" and delegation.status in ("delegated", "in_progress"):
            self._set_delegation_status(delegation.task.id, "awaiting_approval")
        return step

    def _decide_irreversible_step(
        self,
        delegation: DelegatedTask,
        tool_name: str,
        payload: dict,
        approval_agent_id: str | None,
        token,
    ) -> tuple[str, str | None]:
        """Run one irreversible step through the G4 policy.
        Returns (step status, gate request id or None)."""
        if self.policy is None:
            return "awaiting_approval", None
        decision = self.policy.decide(
            tool_name=tool_name,
            content=payload,
            conversation_id=delegation.task.id,
        )
        if decision.outcome in ("deny", "escalate_to_human"):
            return "rejected", None
        approver = self._approver_for(delegation.task)
        gate_wired = self.gate is not None and getattr(self.policy, "gate", None) is not None
        if decision.outcome == "allow":
            if gate_wired and token is not None and approval_agent_id:
                try:
                    request = self.policy.request(
                        user_id=approver,
                        agent_id=approval_agent_id,
                        token=token,
                        tool_name=tool_name,
                        content=payload,
                        conversation_id=delegation.task.id,
                    )
                except GateDenied:
                    # The approval could not be filed with the gate
                    # (bad token, token does not allow the kind):
                    # the step waits for a human instead of
                    # running on a decision alone. Fail-closed.
                    return "awaiting_approval", None
                return "approved", request.request_id
            return "approved", None
        # "ask": file with the gate when one is wired in.
        if gate_wired and token is not None and approval_agent_id:
            try:
                request = self.policy.request(
                    user_id=approver,
                    agent_id=approval_agent_id,
                    token=token,
                    tool_name=tool_name,
                    content=payload,
                    conversation_id=delegation.task.id,
                )
            except GateDenied:
                return "awaiting_approval", None
            return "awaiting_approval", request.request_id
        return "awaiting_approval", None

    def approve_step(self, step_id: str, *, decided_by: str) -> DelegatedStep:
        """Approve a step waiting for approval. Only the task
        owner (or the creator when there is no owner) may
        approve: the person who delegated the work approves its
        irreversible steps. When the step has a gate request,
        the gate approves it too, so execution still goes
        through ``Gate.execute``."""
        step = self._require_step(step_id)
        if step.status != "awaiting_approval":
            raise ValueError(
                f"only a step awaiting approval can be approved (step is {step.status})"
            )
        task = self._require_task(step.task_id)
        decided_by = _validate_uuid(decided_by, "decided_by")
        if decided_by != self._approver_for(task):
            raise NotRoomMemberError(
                f"user {decided_by} may not approve steps of task {step.task_id}: "
                "only the task owner approves irreversible steps"
            )
        if step.approval_request_id is not None and self.gate is not None:
            self.gate.approve(step.approval_request_id, decided_by)
        self._set_step_status(step.id, "approved", approved_by=decided_by)
        self._sync_delegation_after_step(task.id)
        return self._require_step(step.id)

    def reject_step(self, step_id: str, *, decided_by: str) -> DelegatedStep:
        """Reject a step waiting for approval. Same approver
        rule as ``approve_step``. A rejected step never runs."""
        step = self._require_step(step_id)
        if step.status != "awaiting_approval":
            raise ValueError(
                f"only a step awaiting approval can be rejected (step is {step.status})"
            )
        task = self._require_task(step.task_id)
        decided_by = _validate_uuid(decided_by, "decided_by")
        if decided_by != self._approver_for(task):
            raise NotRoomMemberError(
                f"user {decided_by} may not reject steps of task {step.task_id}: "
                "only the task owner decides irreversible steps"
            )
        self._set_step_status(step.id, "rejected", approved_by=decided_by)
        self._sync_delegation_after_step(task.id)
        return self._require_step(step.id)

    def execute_step(self, step_id: str, *, by: str, executor) -> object:
        """Run a step's action. Fail-closed by construction:

        - an irreversible step that is not ``approved`` raises
          ``GateDenied`` — it never runs while proposed,
          awaiting approval, or rejected;
        - a reversible step must be ``proposed``;
        - an approved step filed with the gate runs through
          ``Gate.execute`` (content-hash + token re-checked);
        - an executed step returns its stored result instead of
          running twice.

        ``executor`` is ``content -> result``; its return value
        is stored on the step row.
        """
        step = self._require_step(step_id)
        if step.status == "executed":
            return step.result
        if step.irreversible and step.status != "approved":
            raise GateDenied(
                f"irreversible step {step.id} is {step.status}: approval is required first"
            )
        if not step.irreversible and step.status != "proposed":
            raise GateDenied(f"step {step.id} is {step.status} and cannot run")
        delegation = self.get_delegation(step.task_id)
        if delegation is None:
            raise ValueError(f"task {step.task_id} is not delegated")
        by = _validate_uuid(by, "by") if _looks_like_uuid(by) else by
        self._require_worker(delegation, by)
        if step.approval_request_id is not None and self.gate is not None:
            result = self.gate.execute(step.approval_request_id, executor=executor)
        elif step.approval_request_id is not None:
            # The approval was filed with the gate: it may only
            # run through the gate. Executing without it would
            # bypass the content-hash and token re-checks.
            raise GateDenied(
                f"step {step.id} was approved through the gate; execute needs the gate"
            )
        else:
            result = executor(dict(step.content))
        self._set_step_status(step.id, "executed", result=None if result is None else str(result))
        self._sync_delegation_after_step(step.task_id)
        return result

    def _set_step_status(self, step_id: str, status: str, **extra) -> None:
        if status not in STEP_STATUSES:
            raise ValueError(f"unknown step status: {status!r}")
        sets = ["status = %s"]
        params: list = [status]
        if status in ("approved", "rejected"):
            sets.append("decided_at = now()")
        if status == "executed":
            sets.append("executed_at = now()")
        for column, value in extra.items():
            if column == "result":
                sets.append("result = %s")
                params.append(value)
            elif column == "approved_by":
                sets.append("approved_by = %s")
                params.append(value)
        params.append(step_id)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE delegated_task_steps SET {', '.join(sets)} WHERE id = %s", params)
            conn.commit()

    def _sync_delegation_after_step(self, task_id: str) -> None:
        """Keep the delegation status honest after a step
        decision: awaiting approval while any step waits,
        back to in_progress when none does."""
        delegation = self.get_delegation(task_id)
        if delegation is None or delegation.status == "done":
            return
        waiting = any(step.status == "awaiting_approval" for step in delegation.steps)
        if waiting and delegation.status in ("delegated", "in_progress"):
            self._set_delegation_status(task_id, "awaiting_approval")
        elif not waiting and delegation.status == "awaiting_approval":
            next_status = "in_progress" if delegation.started_at is not None else "delegated"
            self._set_delegation_status(task_id, next_status)


def _looks_like_uuid(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        import uuid

        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# --- Module-level functions (thin wrappers over a fresh service) -------


def _service(
    dsn=None, *, policy: ApprovalPolicy | None = None, gate: Gate | None = None
) -> DelegationService:
    return DelegationService(dsn, policy=policy, gate=gate)


def delegate_to_agent(
    task_id: str,
    *,
    delegated_by: str,
    subagent_ref: str,
    lifecycle=None,
    dsn=None,
) -> DelegatedTask:
    return _service(dsn).delegate_to_agent(
        task_id, delegated_by=delegated_by, subagent_ref=subagent_ref, lifecycle=lifecycle
    )


def delegate_to_teammate(
    task_id: str, *, delegated_by: str, teammate_user_id: str, dsn=None
) -> DelegatedTask:
    return _service(dsn).delegate_to_teammate(
        task_id, delegated_by=delegated_by, teammate_user_id=teammate_user_id
    )


def start_work(task_id: str, *, by: str, dsn=None) -> DelegatedTask:
    return _service(dsn).start_work(task_id, by=by)


def report_progress(task_id: str, *, by: str, note: str, dsn=None) -> DelegatedTask:
    return _service(dsn).report_progress(task_id, by=by, note=note)


def propose_step(
    task_id: str,
    *,
    by: str,
    description: str,
    tool_name: str,
    irreversible: bool = False,
    content: dict | None = None,
    dsn=None,
    policy: ApprovalPolicy | None = None,
    gate: Gate | None = None,
    approval_agent_id: str | None = None,
    token=None,
) -> DelegatedStep:
    return _service(dsn, policy=policy, gate=gate).propose_step(
        task_id,
        by=by,
        description=description,
        tool_name=tool_name,
        irreversible=irreversible,
        content=content,
        approval_agent_id=approval_agent_id,
        token=token,
    )


def approve_step(
    step_id: str, *, decided_by: str, dsn=None, gate: Gate | None = None
) -> DelegatedStep:
    return _service(dsn, gate=gate).approve_step(step_id, decided_by=decided_by)


def reject_step(step_id: str, *, decided_by: str, dsn=None) -> DelegatedStep:
    return _service(dsn).reject_step(step_id, decided_by=decided_by)


def execute_step(step_id: str, *, by: str, executor, dsn=None, gate: Gate | None = None) -> object:
    return _service(dsn, gate=gate).execute_step(step_id, by=by, executor=executor)


def complete_delegated_task(task_id: str, *, completed_by: str, dsn=None) -> RoomTask | None:
    return _service(dsn).complete_task(task_id, completed_by=completed_by)


def board(room_id: str, *, viewer_user_id: str, dsn=None) -> list[BoardRow]:
    return _service(dsn).board(room_id, viewer_user_id=viewer_user_id)
