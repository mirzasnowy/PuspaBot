"""Meeting follow-up reminders (B10).

Owners of meeting action items get reminded. This module only
answers *who needs a reminder about what* — it never sends one
itself (sending is an approval-gated delivery; the scheduler
owns the send).

Reminder queries — the same ``due_soon`` / ``overdue`` questions
the B6 board already answers, filtered to tasks that came from
meeting minutes (their ids are stored in the minutes rows, so
no change to the board schema is needed):

- :func:`due_meeting_actions` — meeting action items due within
  a window (default 1 day).
- :func:`overdue_meeting_actions` — meeting action items past
  their deadline.
- :func:`reminders_for_owner` — one owner's due + overdue items.
- :func:`build_reminder_digest` — the per-owner digest: each
  owner maps to their reminder texts, most urgent first.
- :func:`run_reminder_sweep` — the flat hook a scheduled job
  calls: one entry per (owner, task) that needs a reminder.

Scheduler hook — reuse the G8 general cron scheduler, do not
build a second one:

- :func:`reminder_job_prompt` builds the prompt a fresh-agent
  cron run executes (it calls :func:`run_reminder_sweep` for the
  room and delivers one reminder per owner).
- :func:`register_reminder_job` creates that job in a G8
  ``CronJobStore`` through ``cronjob_manage`` (default: every
  12 hours, Asia/Jakarta). The job's delivery target is the
  caller's: a room chat target or a per-owner target, decided by
  the caller, not here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.ports import resolve_dsn
from puspa.rooms.errors import NotRoomMemberError
from puspa.rooms.rooms import RoomStore, _validate_uuid
from puspa.rooms.tasks import RoomTask, RoomTaskStore

__all__ = [
    "build_reminder_digest",
    "due_meeting_actions",
    "overdue_meeting_actions",
    "register_reminder_job",
    "reminder_job_prompt",
    "reminder_text",
    "reminders_for_owner",
    "run_reminder_sweep",
]

#: How often the reminder job runs by default.
DEFAULT_REMINDER_SCHEDULE = "every 12h"

#: Timezone for the default reminder job (Indonesian users first).
DEFAULT_REMINDER_TIMEZONE = "Asia/Jakarta"


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def meeting_task_ids(room_id: str, *, dsn=None) -> set[str]:
    """Ids of the room's board tasks that came from meeting minutes."""
    room_id = _validate_uuid(room_id, "room_id")
    ids: set[str] = set()
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT action_items FROM meeting_minutes WHERE room_id = %s", (room_id,))
        for row in cur.fetchall():
            for item in row["action_items"] or []:
                if isinstance(item, dict) and item.get("task_id"):
                    ids.add(str(item["task_id"]))
    return ids


def _require_member(room_id: str, viewer_user_id: str, dsn=None) -> None:
    if not RoomStore(dsn).is_member(room_id, viewer_user_id):
        raise NotRoomMemberError(f"user {viewer_user_id} is not a member of room {room_id}")


def due_meeting_actions(
    room_id: str,
    *,
    viewer_user_id: str,
    within: timedelta = timedelta(days=1),
    dsn=None,
) -> list[RoomTask]:
    """Meeting action items due within ``within`` from now.

    Only meeting action items — other board tasks are not
    reminded by this module. Members only.
    """
    room_id = _validate_uuid(room_id, "room_id")
    viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
    _require_member(room_id, viewer, dsn)
    wanted = meeting_task_ids(room_id, dsn=dsn)
    tasks = RoomTaskStore(dsn).due_soon(room_id, viewer_user_id=viewer, within=within)
    return [task for task in tasks if task.id in wanted]


def overdue_meeting_actions(room_id: str, *, viewer_user_id: str, dsn=None) -> list[RoomTask]:
    """Meeting action items whose deadline has already passed."""
    return due_meeting_actions(room_id, viewer_user_id=viewer_user_id, within=timedelta(0), dsn=dsn)


def reminders_for_owner(
    room_id: str,
    *,
    owner_user_id: str,
    viewer_user_id: str,
    within: timedelta = timedelta(days=1),
    dsn=None,
) -> list[RoomTask]:
    """One owner's meeting action items that need a reminder:
    overdue ones first, then due soon, each by deadline."""
    owner = _validate_uuid(owner_user_id, "owner_user_id")
    due = due_meeting_actions(room_id, viewer_user_id=viewer_user_id, within=within, dsn=dsn)
    overdue = overdue_meeting_actions(room_id, viewer_user_id=viewer_user_id, dsn=dsn)
    seen = {task.id for task in overdue}
    owned = [task for task in overdue if task.owner_user_id == owner]
    owned += [task for task in due if task.owner_user_id == owner and task.id not in seen]
    return owned


def reminder_text(task: RoomTask, *, now: datetime | None = None) -> str:
    """One plain reminder line for one task."""
    moment = now or datetime.now(UTC)
    if task.due_at is None:
        return f"Reminder: '{task.title}' has no deadline set."
    if task.due_at <= moment:
        return f"Reminder: '{task.title}' is overdue (was due {task.due_at.date().isoformat()})."
    return f"Reminder: '{task.title}' is due {task.due_at.date().isoformat()}."


def build_reminder_digest(
    room_id: str,
    *,
    viewer_user_id: str,
    within: timedelta = timedelta(days=1),
    dsn=None,
) -> list[dict]:
    """Per-owner reminder digest: one entry per owner with a
    reminder due, each with their reminder texts, most urgent
    first. Tasks without an owner are left out — there is nobody
    to remind.
    """
    room_id = _validate_uuid(room_id, "room_id")
    viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
    _require_member(room_id, viewer, dsn)
    due = due_meeting_actions(room_id, viewer_user_id=viewer, within=within, dsn=dsn)
    overdue = overdue_meeting_actions(room_id, viewer_user_id=viewer, dsn=dsn)
    seen = {task.id for task in overdue}
    ordered = overdue + [task for task in due if task.id not in seen]
    by_owner: dict[str, list[RoomTask]] = {}
    for task in ordered:
        if task.owner_user_id is None:
            continue
        by_owner.setdefault(task.owner_user_id, []).append(task)
    digest = []
    overdue_ids = {task.id for task in overdue}
    for owner in sorted(by_owner):
        items = by_owner[owner]
        digest.append(
            {
                "owner_user_id": owner,
                "tasks": [
                    {
                        "task_id": task.id,
                        "title": task.title,
                        "due_at": task.due_at.isoformat() if task.due_at else None,
                        "status": "overdue" if task.id in overdue_ids else "due_soon",
                        "text": reminder_text(task),
                    }
                    for task in items
                ],
            }
        )
    return digest


def run_reminder_sweep(
    room_id: str,
    *,
    viewer_user_id: str,
    within: timedelta = timedelta(days=1),
    dsn=None,
) -> list[dict]:
    """The scheduler hook: flat list of reminders to send.

    One entry per (owner, task): ``owner_user_id``, ``task_id``,
    ``title``, ``due_at``, ``status`` (``overdue`` or
    ``due_soon``), ``text``. The caller (a G8 cron job run, or a
    room routine) delivers each entry to its owner; this function
    never sends anything itself.
    """
    digest = build_reminder_digest(room_id, viewer_user_id=viewer_user_id, within=within, dsn=dsn)
    entries = []
    for owner_entry in digest:
        for item in owner_entry["tasks"]:
            entries.append({"owner_user_id": owner_entry["owner_user_id"], **item})
    return entries


def reminder_job_prompt(room_id: str) -> str:
    """Prompt for the G8 cron job that sends meeting reminders.

    The fresh agent running this prompt calls
    ``run_reminder_sweep`` for the room and delivers one reminder
    per owner; it never invents tasks or deadlines.
    """
    room_id = _validate_uuid(room_id, "room_id")
    return (
        "Meeting follow-up reminders for room " + room_id + ". "
        "Call puspa.meetings.run_reminder_sweep for this room and "
        "deliver each returned reminder text to its owner. "
        "Send only the reminders returned: never invent a task, "
        "an owner, or a deadline, and never remind about a task "
        "that is already done."
    )


def register_reminder_job(
    store,
    room_id: str,
    *,
    deliver_to: str = "",
    schedule: str = DEFAULT_REMINDER_SCHEDULE,
    timezone: str = DEFAULT_REMINDER_TIMEZONE,
) -> dict:
    """Create the G8 cron job that sends meeting reminders.

    ``store`` is a ``puspa.scheduler.cron.CronJobStore``. Returns
    the ``cronjob_manage`` result (``{"action": "create", "job":
    {...}}``). This reuses the G8 scheduler: no second scheduler
    is built here.
    """
    from puspa.scheduler.cron import cronjob_manage

    room_id = _validate_uuid(room_id, "room_id")
    return cronjob_manage(
        store,
        "create",
        prompt=reminder_job_prompt(room_id),
        schedule=schedule,
        deliver_to=deliver_to,
        timezone=timezone,
    )
