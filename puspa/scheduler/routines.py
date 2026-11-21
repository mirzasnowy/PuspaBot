"""User-defined recurring tasks (scheduled routines).

A routine is a task the user writes once, plus a schedule that
says when it repeats: every day at a time, every few hours, on
weekdays, a cron pattern, or once at a set time. The routine is
stored as a normal scheduler trigger (see
``puspa.scheduler.scheduler``): the task text goes in the
trigger's ``workflow`` field and the schedule, as JSON, in its
``schedule`` field. No new table is needed.

When the tick fires (see ``run_trigger_job`` in
``puspa.scheduler.scheduler``), a due routine runs through the
same turn loop as a chat turn, with no human present: the task
text becomes the turn's first message and the turn's reply is
the routine's result. The result rides on the tick's returned
run (under ``"job"``) and the serve loop reports it through
its sink, so a channel or digest can deliver it to the user.

A routine is only a routine when its ``schedule`` field holds
a schedule dict (the union from ``puspa.scheduler.schedules``).
Plain triggers made by other callers keep their old behaviour:
the tick records them as skipped, never as routines.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg

from .scheduler import Trigger, create_trigger
from .schedules import next_occurrence, schedule_from_record, validate_schedule

__all__ = [
    "Routine",
    "RoutineError",
    "create_routine",
    "list_routines",
    "pause_routine",
    "remove_routine",
    "resume_routine",
    "routine_schedule",
]

#: The trigger type every routine carries.
ROUTINE_TRIGGER_TYPE = "schedule"


class RoutineError(Exception):
    """A routine could not be created, found, or changed."""


@dataclass
class Routine:
    """One user-defined recurring task."""

    id: str
    user_id: str
    task: str
    schedule: dict
    next_run: datetime | None = None
    last_run: datetime | None = None
    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "task": self.task,
            "schedule": dict(self.schedule),
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "active": self.active,
            "created_at": self.created_at.isoformat(),
        }


def routine_schedule(trigger: Trigger) -> dict | None:
    """The schedule dict of a routine trigger, or None.

    A trigger counts as a routine only when its ``schedule``
    field parses as JSON holding a valid schedule dict. Legacy
    schedule strings (``"daily 03:00"``, ``"alur:..."``) return
    None, so old triggers are never mistaken for routines.
    """
    try:
        record = json.loads(trigger.schedule or "")
    except (ValueError, TypeError):
        return None
    return schedule_from_record(record)


def _resolve_schedule(spec, *, now: datetime) -> dict:
    """Turn a user schedule (dict or short string) into a union dict.

    A dict is validated as-is. A string is parsed the same way
    the cron scheduler parses one (``"30m"``, ``"every 2h"``,
    a cron pattern, or an ISO time). Raises
    :class:`RoutineError` with a plain reason when invalid.
    """
    if isinstance(spec, dict):
        problems = validate_schedule(spec)
        if problems:
            raise RoutineError("; ".join(problems))
        return dict(spec)
    if isinstance(spec, str):
        from .cron import parse_schedule_spec

        try:
            return parse_schedule_spec(spec, now=now)
        except Exception as exc:  # CronError and friends: plain reason out
            raise RoutineError(str(exc)) from exc
    raise RoutineError("schedule must be a schedule dict or a schedule string")


def create_routine(
    user_id: str,
    task: str,
    schedule,
    *,
    dsn=None,
    now: datetime | None = None,
) -> Routine:
    """Define a recurring task for a user.

    ``task`` is the plain-language text the agent runs each
    time (for example, "Summarise my unread bills"). ``schedule``
    is a schedule dict (``{"type": "daily", "time": "07:00",
    "timezone": "Asia/Jakarta"}``) or a short string
    (``"every 2h"``, ``"0 9 * * 1-5"``). Raises
    :class:`RoutineError` for an empty task, a bad schedule, or
    a schedule with no future fire time.
    """
    task_text = str(task or "").strip()
    if not task_text:
        raise RoutineError("create needs a non-empty task")
    now = now or datetime.now(UTC)
    schedule_dict = _resolve_schedule(schedule, now=now)
    first_run = next_occurrence(schedule_dict, now)
    if first_run is None:
        raise RoutineError("schedule has no future occurrence")
    trigger = create_trigger(
        str(user_id),
        ROUTINE_TRIGGER_TYPE,
        first_run,
        schedule=json.dumps(schedule_dict, sort_keys=True),
        workflow=task_text,
        dsn=dsn,
    )
    return Routine(
        id=trigger.id,
        user_id=trigger.user_id,
        task=task_text,
        schedule=schedule_dict,
        next_run=trigger.next_trigger_at,
        active=True,
    )


def _routine_from_row(row: dict) -> Routine | None:
    """Build a Routine from a triggers row, or None if not a routine."""
    trigger = Trigger(
        id=row["id"],
        user_id=str(row["user_id"]),
        event_type=row["type"],
        schedule=row["schedule"] or "",
        workflow=row["workflow"] or "",
        next_trigger_at=row["next_trigger_at"],
        active=row["active"],
    )
    schedule = routine_schedule(trigger)
    if schedule is None or not trigger.workflow.strip():
        return None
    return Routine(
        id=trigger.id,
        user_id=trigger.user_id,
        task=trigger.workflow,
        schedule=schedule,
        next_run=trigger.next_trigger_at,
        active=trigger.active,
    )


def list_routines(user_id: str, *, dsn=None) -> list[Routine]:
    """All routines of one user, soonest first (paused ones included)."""
    from .scheduler import _connect

    with _connect(dsn) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT id, user_id, type, schedule, workflow, next_trigger_at, active"
            " FROM triggers WHERE user_id = %s AND type = %s"
            " ORDER BY next_trigger_at ASC",
            (str(user_id), ROUTINE_TRIGGER_TYPE),
        )
        rows = cur.fetchall()
    routines = []
    for row in rows:
        routine = _routine_from_row(row)
        if routine is not None:
            routines.append(routine)
    return routines


def _set_active(routine_id: str, user_id: str, active: bool, *, dsn=None) -> Routine:
    """Pause (active=False) or resume (active=True) one routine."""
    from .scheduler import _connect

    with _connect(dsn) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "UPDATE triggers SET active = %s WHERE id = %s AND user_id = %s AND type = %s"
            " RETURNING id, user_id, type, schedule, workflow, next_trigger_at, active",
            (active, str(routine_id), str(user_id), ROUTINE_TRIGGER_TYPE),
        )
        row = cur.fetchone()
        if row is None:
            raise RoutineError(f"no such routine: {routine_id!r}")
        routine = _routine_from_row(row)
        if routine is None:
            raise RoutineError(f"no such routine: {routine_id!r}")
        conn.commit()
    return routine


def pause_routine(routine_id: str, user_id: str, *, dsn=None) -> Routine:
    """Pause a routine: it stays stored but never fires."""
    return _set_active(routine_id, user_id, False, dsn=dsn)


def resume_routine(routine_id: str, user_id: str, *, dsn=None) -> Routine:
    """Resume a paused routine."""
    return _set_active(routine_id, user_id, True, dsn=dsn)


def remove_routine(routine_id: str, user_id: str, *, dsn=None) -> bool:
    """Delete a routine. Returns True when a row was deleted."""
    from .scheduler import _connect

    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM triggers WHERE id = %s AND user_id = %s AND type = %s",
            (str(routine_id), str(user_id), ROUTINE_TRIGGER_TYPE),
        )
        deleted = cur.rowcount > 0
        conn.commit()
    return deleted
