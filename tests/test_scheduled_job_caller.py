"""Tests for the production scheduler caller and user routines.

No live model and no network: every turn runs through a real
``TurnLoop`` with a fake ``model_call``. The database tests use
the repo-wide ``test_dsn`` fixture (Postgres).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from puspa.db import connect as _db_connect
from puspa.scheduler.routines import (
    RoutineError,
    create_routine,
    list_routines,
    pause_routine,
    remove_routine,
    resume_routine,
)
from puspa.scheduler.scheduler import (
    create_trigger,
    run_missed,
    schedule_dream,
    tick,
)
from puspa.scheduler.serve import run_scheduler_loop
from puspa.turns.loop import TurnLoop
from puspa.turns.turn_api import ModelReply

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
FIRE = datetime(2026, 11, 20, 7, 5, tzinfo=UTC)


def _stub_loop(reply_text="Routine done."):
    """A real turn loop whose model call is a fake that answers at once."""
    seen = []

    def model_call(entry, wire_messages, tools):
        seen.append(wire_messages)
        return ModelReply(text=reply_text, input_tokens=1, output_tokens=1)

    loop = TurnLoop(model_call=model_call)
    loop.seen_messages = seen
    return loop


def _trigger_row(test_dsn, trigger_id):
    with _db_connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT next_trigger_at, active FROM triggers WHERE id = %s", (trigger_id,))
        return cur.fetchone()


# --- the core promise: a due trigger fires with no human present --------


def test_tick_fires_due_routine_through_turn_loop_with_no_human_present(make_user, test_dsn):
    user = make_user()
    routine = create_routine(
        user["user_id"],
        "Summarise my unread bills.",
        {"type": "daily", "time": "07:00", "timezone": "UTC"},
        dsn=test_dsn,
        now=NOW,
    )
    assert routine.next_run == datetime(2026, 11, 20, 7, 0, tzinfo=UTC)
    loop = _stub_loop("Your bills total Rp50.000.")
    runs = tick(sink=None, dsn=test_dsn, now=FIRE, turn_loop=loop)
    assert len(runs) == 1
    job = runs[0]["job"]
    assert job["job"] == "regular_task"
    assert job["status"] == "completed"
    assert job["turn_status"] == "completed"
    assert job["text"] == "Your bills total Rp50.000."
    # The turn really ran the routine text, with no human message.
    assert len(loop.seen_messages) == 1
    assert "Summarise my unread bills." in str(loop.seen_messages[0])
    # The next fire is tomorrow at the same time, not a blind +1 day
    # from a wrong base: it is recomputed from the schedule.
    next_run, active = _trigger_row(test_dsn, routine.id)
    assert next_run == datetime(2026, 11, 21, 7, 0, tzinfo=UTC)
    assert active is True
    # Firing twice at the same moment runs nothing: one fire, one run.
    assert tick(sink=None, dsn=test_dsn, now=FIRE, turn_loop=loop) == []


def test_tick_runs_system_job_directly(make_user, test_dsn):
    user = make_user()
    trigger = schedule_dream(user["user_id"], hour=3, dsn=test_dsn)
    fire = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=3, minute=5, second=0, microsecond=0
    )
    runs = tick(sink=None, dsn=test_dsn, now=fire)
    assert len(runs) == 1
    job = runs[0]["job"]
    assert job["job"] == "dream"
    assert job["status"] == "completed"
    assert isinstance(job["result"], dict)
    assert trigger.id == runs[0]["trigger_id"]


def test_tick_continues_when_one_job_fails(make_user, test_dsn, monkeypatch):
    user = make_user()
    dream = create_trigger(
        user["user_id"],
        "dream",
        datetime(2026, 11, 20, 3, 0, tzinfo=UTC),
        schedule="daily 03:00",
        dsn=test_dsn,
    )
    routine = create_routine(
        user["user_id"],
        "Say hello.",
        {"type": "daily", "time": "07:00", "timezone": "UTC"},
        dsn=test_dsn,
        now=NOW,
    )
    import puspa.scheduler.scheduler as scheduler_mod

    original = scheduler_mod.run_scheduled_job

    def flaky(run, *, dsn=None):
        if run["trigger_id"] == dream.id:
            raise RuntimeError("backup store is down")
        return original(run, dsn=dsn)

    monkeypatch.setattr(scheduler_mod, "run_scheduled_job", flaky)
    fire = datetime(2026, 11, 20, 7, 5, tzinfo=UTC)
    # The dream trigger fires tomorrow 03:00, so tick at 07:05 sees
    # both: the dream (due since 03:00) and the routine (due 07:00).
    runs = tick(sink=None, dsn=test_dsn, now=fire, turn_loop=_stub_loop())
    assert len(runs) == 2
    by_trigger = {run["trigger_id"]: run["job"] for run in runs}
    assert by_trigger[dream.id]["status"] == "failed"
    assert "backup store is down" in by_trigger[dream.id]["error"]
    assert by_trigger[routine.id]["status"] == "completed"


def test_tick_without_turn_loop_records_routine_as_not_run(make_user, test_dsn):
    user = make_user()
    routine = create_routine(
        user["user_id"],
        "Say hello.",
        {"type": "daily", "time": "07:00", "timezone": "UTC"},
        dsn=test_dsn,
        now=NOW,
    )
    runs = tick(sink=None, dsn=test_dsn, now=FIRE)
    assert len(runs) == 1
    job = runs[0]["job"]
    assert job["job"] == "regular_task"
    assert job["status"] == "not_run"
    assert "no turn loop" in job["reason"]
    assert routine.id == runs[0]["trigger_id"]


def test_plain_trigger_is_skipped_not_a_routine(make_user, test_dsn):
    user = make_user()
    trigger = create_trigger(
        user["user_id"],
        "schedule",
        FIRE - timedelta(minutes=5),
        schedule="daily 03:00",
        workflow="some workflow id",
        dsn=test_dsn,
    )
    loop = _stub_loop()
    runs = tick(sink=None, dsn=test_dsn, now=FIRE, turn_loop=loop)
    assert len(runs) == 1
    assert runs[0]["job"]["status"] == "skipped"
    assert loop.seen_messages == []
    assert trigger.id == runs[0]["trigger_id"]


def test_missed_routine_runs_once_on_wake(make_user, test_dsn):
    user = make_user()
    routine = create_routine(
        user["user_id"],
        "Say hello.",
        {"type": "daily", "time": "07:00", "timezone": "UTC"},
        dsn=test_dsn,
        now=NOW,
    )
    # The host was down two days: move the fire time back.
    with _db_connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE triggers SET next_trigger_at = %s WHERE id = %s",
            (datetime(2026, 11, 18, 7, 0, tzinfo=UTC), routine.id),
        )
        conn.commit()
    loop = _stub_loop()
    wake = datetime(2026, 11, 20, 7, 5, tzinfo=UTC)
    runs = run_missed(sink=None, dsn=test_dsn, now=wake, turn_loop=loop)
    assert len(runs) == 1
    assert runs[0]["missed"] is True
    assert runs[0]["job"]["status"] == "completed"
    assert len(loop.seen_messages) == 1
    assert run_missed(sink=None, dsn=test_dsn, now=wake, turn_loop=loop) == []


def test_once_routine_deactivates_after_firing(make_user, test_dsn):
    user = make_user()
    routine = create_routine(
        user["user_id"],
        "Remind me once.",
        {"type": "once", "at": "2026-11-20T07:00:00+00:00", "timezone": "UTC"},
        dsn=test_dsn,
        now=NOW,
    )
    runs = tick(sink=None, dsn=test_dsn, now=FIRE, turn_loop=_stub_loop())
    assert len(runs) == 1
    assert runs[0]["job"]["status"] == "completed"
    _next_run, active = _trigger_row(test_dsn, routine.id)
    assert active is False
    assert tick(sink=None, dsn=test_dsn, now=FIRE, turn_loop=_stub_loop()) == []


# --- routine management -----------------------------------------------------


def test_routine_create_list_pause_resume_remove(make_user, test_dsn):
    user = make_user()
    routine = create_routine(
        user["user_id"],
        "Check the price every two hours.",
        "every 2h",
        dsn=test_dsn,
        now=NOW,
    )
    assert routine.next_run == NOW + timedelta(hours=2)
    assert [r.id for r in list_routines(user["user_id"], dsn=test_dsn)] == [routine.id]
    paused = pause_routine(routine.id, user["user_id"], dsn=test_dsn)
    assert paused.active is False
    assert tick(sink=None, dsn=test_dsn, now=NOW + timedelta(hours=3)) == []
    resumed = resume_routine(routine.id, user["user_id"], dsn=test_dsn)
    assert resumed.active is True
    assert remove_routine(routine.id, user["user_id"], dsn=test_dsn) is True
    assert list_routines(user["user_id"], dsn=test_dsn) == []
    assert remove_routine(routine.id, user["user_id"], dsn=test_dsn) is False


def test_routine_create_rejects_bad_input(make_user, test_dsn):
    user = make_user()
    with pytest.raises(RoutineError):
        create_routine(user["user_id"], "   ", "every 2h", dsn=test_dsn, now=NOW)
    with pytest.raises(RoutineError):
        create_routine(
            user["user_id"],
            "Do it.",
            {"type": "daily", "time": "25:00", "timezone": "UTC"},
            dsn=test_dsn,
            now=NOW,
        )
    with pytest.raises(RoutineError):
        create_routine(
            user["user_id"],
            "Do it.",
            {"type": "once", "at": "2026-11-01T07:00:00+00:00", "timezone": "UTC"},
            dsn=test_dsn,
            now=NOW,
        )
    with pytest.raises(RoutineError):
        pause_routine("routine-nope", user["user_id"], dsn=test_dsn)


# --- the serve loop ---------------------------------------------------------


def test_run_scheduler_loop_runs_capped_ticks_and_survives_tick_failure(monkeypatch):
    import puspa.scheduler.serve as serve_mod

    calls = {"n": 0}
    events = []

    def flaky_tick(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick exploded")

    monkeypatch.setattr(serve_mod, "tick", flaky_tick)
    ticks = run_scheduler_loop(
        interval_seconds=0,
        max_ticks=2,
        turn_loop=object(),
        sink=lambda event_type, content: events.append(event_type),
    )
    assert ticks == 2
    assert calls["n"] == 2
    assert events == ["scheduler_tick_failed"]
