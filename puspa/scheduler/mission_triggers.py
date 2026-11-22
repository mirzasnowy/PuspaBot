"""Mission triggers (V2-42, issue #219): the scheduler side of missions.

A mission trigger is a normal scheduler trigger (see
``puspa.scheduler.scheduler``) whose ``workflow`` field holds
the mission id and whose ``schedule`` field holds the schedule
dict. This module is the mission's own seam, kept out of
``scheduler.py`` on purpose: the scheduler only registers the
``"mission"`` trigger type and hands a fired mission trigger
to :func:`run_mission_trigger` from its regular-task branch —
the same place user routines run.

Each fire advances one checklist step through the turn loop.
A mission that reaches a terminal state deactivates its
trigger (one mission per trigger; a new mission needs a new
trigger). With no turn loop wired in, the step is recorded as
``not_run`` and the trigger is moved back to now, so the next
tick retries it instead of silently waiting a day.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime

from .routines import _resolve_schedule
from .scheduler import (
    SchedulerError,
    Trigger,
    _advance_routine,
    _connect,
    _now,
    _routine_schedule_of,
    create_trigger,
)
from .schedules import next_occurrence

__all__ = ["run_mission_trigger", "schedule_mission"]


def schedule_mission(user_id: str, mission_id: str, schedule, *, dsn=None, now=None) -> Trigger:
    """Register a mission (V2-42) as a trigger.

    ``schedule`` is a schedule dict (``{"type": "daily", "time":
    "07:00", "timezone": "Asia/Jakarta"}``) or a short string
    (``"every 2h"``, ``"0 9 * * 1-5"``, an ISO time for a
    one-shot) — the same shapes a routine takes. The mission
    id goes in the trigger's ``workflow`` field. Each tick
    advances one checklist step through the turn loop; when
    the mission reaches a terminal state its trigger is
    deactivated (one mission per trigger). Raises
    :class:`SchedulerError` for an unknown mission, a bad
    schedule, or a schedule with no future fire time.
    """
    from puspa.missions.missions import MissionError as _MissionError
    from puspa.missions.missions import get_mission

    try:
        get_mission(mission_id, user_id, dsn=dsn)
    except _MissionError as exc:
        raise SchedulerError(str(exc)) from exc
    now = now or _now()
    try:
        schedule_dict = _resolve_schedule(schedule, now=now)
    except Exception as exc:
        raise SchedulerError(str(exc)) from exc
    first_run = next_occurrence(schedule_dict, now)
    if first_run is None:
        raise SchedulerError("schedule has no future occurrence")
    return create_trigger(
        str(user_id),
        "mission",
        first_run,
        schedule=json.dumps(schedule_dict, sort_keys=True),
        workflow=str(mission_id),
        dsn=dsn,
    )


def _deactivate_trigger(trigger_id: str, *, dsn=None) -> None:
    """Switch a trigger off so it never fires again."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE triggers SET active = FALSE WHERE id = %s", (trigger_id,))
        conn.commit()


def _set_next_trigger(trigger_id: str, when: datetime, *, dsn=None) -> None:
    """Move a trigger's next fire time to an explicit moment."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE triggers SET next_trigger_at = %s WHERE id = %s", (when, trigger_id)
            )
        conn.commit()


def run_mission_trigger(
    run: dict, row: dict, outcome: dict, *, turn_loop=None, now, dsn=None
) -> dict:
    """Run one fired mission trigger: advance one checklist step.

    ``row`` is the trigger row (already fetched by the caller).
    The advance status decides the outcome: a step that ran
    (or a mission that finished or is waiting on the user) is
    completed; a step with no turn loop wired in is ``not_run``
    and the trigger stays due; a failed step is failed. A
    mission that reached a terminal state deactivates its
    trigger instead of advancing it.
    """
    from puspa.missions.runner import advance_mission

    try:
        advance = advance_mission(
            row["workflow"],
            str(row["user_id"]),
            dsn=dsn,
            turn_loop=turn_loop,
            task_id=run["task_id"],
        )
    except Exception as exc:
        outcome["status"] = "failed"
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        return outcome
    status = advance.get("status")
    outcome["result"] = advance
    if status in ("advanced", "done", "waiting"):
        outcome["status"] = "completed"
    elif status == "not_run":
        outcome["status"] = "not_run"
        outcome["reason"] = advance.get("reason", "no turn loop wired in")
    elif status == "failed":
        outcome["status"] = "failed"
        outcome["error"] = advance.get("error", "mission step failed")
    else:  # already_finished and anything unexpected: nothing left to do
        outcome["status"] = "completed"
    if status == "not_run":
        # run_trigger already moved the trigger one day
        # forward; move it back to now so the step is retried
        # on the next tick instead of silently waiting a day
        # with no turn loop wired in.
        with contextlib.suppress(Exception):
            _set_next_trigger(run["trigger_id"], now, dsn=dsn)
        return outcome
    mission = advance.get("mission") or {}
    if mission.get("status") in ("done", "failed", "stopped"):
        with contextlib.suppress(Exception):
            _deactivate_trigger(run["trigger_id"], dsn=dsn)
        return outcome
    schedule = _routine_schedule_of(row)
    if schedule is not None:
        with contextlib.suppress(Exception):
            _advance_routine(run["trigger_id"], schedule, now=now, dsn=dsn)
    return outcome
