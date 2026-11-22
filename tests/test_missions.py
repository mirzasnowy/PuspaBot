"""Missions with checklist (V2-42, issue #219).

Covers the store, the step runner (including the step-ask
pause/resume), the webhook wake, and the scheduler wiring:
a scheduled mission advances one step per tick through the
turn loop, and its trigger deactivates when the mission ends.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from puspa.missions.missions import (
    MissionError,
    cancel_mission,
    create_mission,
    get_mission,
    list_missions,
)
from puspa.missions.runner import advance_mission, answer_step_ask
from puspa.missions.webhooks import rotate_webhook_token, wake_mission_by_token
from puspa.scheduler.mission_triggers import schedule_mission
from puspa.scheduler.scheduler import SchedulerError, tick
from puspa.turns.loop import TurnResult

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def _runner(results=None):
    """A fake step runner recording prompts; one result per step."""
    calls = []
    queue = list(results or [])

    def run(prompt, *, task_id=None):
        calls.append((prompt, task_id))
        if queue:
            return queue.pop(0)
        return {"status": "completed", "text": "hasil langkah"}

    run.calls = calls
    return run


class _FakeLoop:
    """A fake turn loop recording prompts, always completing."""

    def __init__(self):
        self.prompts = []

    def run_turn(self, messages, *, user_id=None, task_id=None, **kwargs):
        text = ""
        if messages and messages[0].blocks:
            text = messages[0].blocks[0].get("text", "")
        self.prompts.append(text)
        return TurnResult(status="completed", text="hasil putaran")


def _mission(make_user, test_dsn, steps=None):
    user = make_user()
    mission = create_mission(
        user["user_id"],
        "Riset harga laptop",
        steps if steps is not None else ["Cari tiga harga", "Bandingkan", "Beli"],
        dsn=test_dsn,
    )
    return user, mission


def test_create_defaults_title_from_goal_and_mints_token(make_user, test_dsn):
    user, mission = _mission(make_user, test_dsn)
    assert mission.id.startswith("misi-")
    assert mission.title == "Riset harga laptop"
    assert mission.status == "active"
    assert mission.webhook_token
    assert [s.status for s in mission.steps] == ["pending", "pending", "pending"]
    again = get_mission(mission.id, user["user_id"], dsn=test_dsn)
    assert again.goal == "Riset harga laptop"


def test_create_rejects_empty_goal_no_steps_and_bad_step(make_user, test_dsn):
    user = make_user()
    with pytest.raises(MissionError):
        create_mission(user["user_id"], "  ", ["Satu langkah"], dsn=test_dsn)
    with pytest.raises(MissionError):
        create_mission(user["user_id"], "Goal", [], dsn=test_dsn)
    with pytest.raises(MissionError):
        create_mission(user["user_id"], "Goal", ["  "], dsn=test_dsn)
    with pytest.raises(MissionError):
        create_mission(user["user_id"], "Goal", [42], dsn=test_dsn)


def test_get_mission_wrong_user_raises_and_list_is_newest_first(make_user, test_dsn):
    user, first = _mission(make_user, test_dsn)
    other = make_user()
    with pytest.raises(MissionError):
        get_mission(first.id, other["user_id"], dsn=test_dsn)
    second = create_mission(user["user_id"], "Goal kedua", ["Langkah"], dsn=test_dsn)
    ids = [m.id for m in list_missions(user["user_id"], dsn=test_dsn)]
    assert ids == [second.id, first.id]
    assert list_missions(other["user_id"], dsn=test_dsn) == []


def test_advance_runs_steps_in_order_and_completes(make_user, test_dsn, ledger):
    user, mission = _mission(make_user, test_dsn)
    runner = _runner()
    out = advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=runner)
    assert out["status"] == "advanced"
    assert "Cari tiga harga" in runner.calls[0][0]
    assert "Riset harga laptop" in runner.calls[0][0]
    out = advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=runner)
    assert out["status"] == "advanced"
    out = advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=runner)
    assert out["status"] == "done"
    assert out["mission"]["status"] == "done"
    assert [s["status"] for s in out["mission"]["steps"]] == ["done", "done", "done"]
    done = ledger.events(mission.id, "task_completed")
    kinds = [e["content"].get("kind") for e in done]
    assert kinds.count("mission_step") == 3
    assert kinds.count("mission") == 1
    assert done[0]["content"]["step"] == "Cari tiga harga"
    again = advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=runner)
    assert again["status"] == "already_finished"


def test_advance_without_runner_is_not_run(make_user, test_dsn):
    user, mission = _mission(make_user, test_dsn)
    out = advance_mission(mission.id, user["user_id"], dsn=test_dsn)
    assert out["status"] == "not_run"
    assert get_mission(mission.id, user["user_id"], dsn=test_dsn).status == "active"
    assert get_mission(mission.id, user["user_id"], dsn=test_dsn).steps[0].status == "pending"


def test_advance_failed_step_fails_mission(make_user, test_dsn, ledger):
    user, mission = _mission(make_user, test_dsn)
    runner = _runner([{"status": "failed", "text": "", "message": "model menolak"}])
    out = advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=runner)
    assert out["status"] == "failed"
    assert out["mission"]["status"] == "failed"
    assert out["mission"]["steps"][0]["status"] == "failed"
    failed = ledger.events(mission.id, "task_failed")
    assert len(failed) == 1
    assert failed[0]["content"]["error"] == "model menolak"


def test_step_ask_pauses_answer_resumes(make_user, test_dsn, ledger):
    steps = [
        "Cari tiga harga",
        {"title": "Pilih toko", "question": "Toko mana?", "options": ["Tokopedia", "Shopee"]},
        "Beli",
    ]
    user, mission = _mission(make_user, test_dsn, steps=steps)
    runner = _runner()
    advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=runner)
    out = advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=runner)
    assert out["status"] == "waiting"
    assert out["question"] == "Toko mana?"
    assert out["options"] == ["Tokopedia", "Shopee"]
    assert len(runner.calls) == 1  # the question step did not run
    waiting = get_mission(mission.id, user["user_id"], dsn=test_dsn)
    assert waiting.status == "waiting"
    assert waiting.steps[1].status == "active"
    asked = ledger.events(mission.id, "approval_requested")
    assert len(asked) == 1
    assert asked[0]["content"]["question"] == "Toko mana?"
    # Advancing while waiting asks again instead of running ahead.
    again = advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=runner)
    assert again["status"] == "waiting"
    assert len(runner.calls) == 1
    answered = answer_step_ask(
        mission.id, user["user_id"], answer="Tokopedia", go=True, dsn=test_dsn
    )
    assert answered.status == "active"
    assert answered.steps[1].answer == "Tokopedia"
    granted = ledger.events(mission.id, "approval_granted")
    assert len(granted) == 1
    out = advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=runner)
    assert out["status"] == "advanced"
    assert "Tokopedia" in runner.calls[1][0]
    out = advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=runner)
    assert out["status"] == "done"


def test_step_ask_decline_stops_mission(make_user, test_dsn, ledger):
    steps = [{"title": "Pilih toko", "question": "Toko mana?", "options": ["A", "B"]}]
    user, mission = _mission(make_user, test_dsn, steps=steps)
    out = advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=_runner())
    assert out["status"] == "waiting"
    stopped = answer_step_ask(
        mission.id, user["user_id"], answer="tidak jadi", go=False, dsn=test_dsn
    )
    assert stopped.status == "stopped"
    assert stopped.steps[0].status == "skipped"
    rejected = ledger.events(mission.id, "approval_rejected")
    assert len(rejected) == 1
    assert rejected[0]["content"]["answer"] == "tidak jadi"


def test_answer_when_not_waiting_raises(make_user, test_dsn):
    user, mission = _mission(make_user, test_dsn)
    with pytest.raises(MissionError):
        answer_step_ask(mission.id, user["user_id"], answer="x", dsn=test_dsn)


def test_cancel_mission_and_cancel_finished_raises(make_user, test_dsn, ledger):
    user, mission = _mission(make_user, test_dsn)
    stopped = cancel_mission(mission.id, user["user_id"], dsn=test_dsn)
    assert stopped.status == "stopped"
    assert ledger.events(mission.id, "task_stopped")
    with pytest.raises(MissionError):
        cancel_mission(mission.id, user["user_id"], dsn=test_dsn)


def test_webhook_wake_queues_without_runner_and_runs_with_runner(make_user, test_dsn, ledger):
    _user, mission = _mission(make_user, test_dsn)
    queued = wake_mission_by_token(mission.webhook_token, {"event": "push"}, dsn=test_dsn)
    assert queued["status"] == "queued"
    assert queued["mission_id"] == mission.id
    assert queued["trigger_id"].startswith("pemicu-")
    evidence = ledger.events(mission.id, "evidence_recorded")
    assert len(evidence) == 1
    assert "push" in evidence[0]["content"]["payload"]
    runner = _runner()
    out = wake_mission_by_token(
        mission.webhook_token, {"event": "push"}, dsn=test_dsn, step_runner=runner
    )
    assert out["status"] == "advanced"
    assert len(runner.calls) == 1
    with pytest.raises(MissionError):
        wake_mission_by_token("token-salah", {}, dsn=test_dsn)


def test_webhook_rotate_invalidates_old_token(make_user, test_dsn):
    user, mission = _mission(make_user, test_dsn)
    old = mission.webhook_token
    new = rotate_webhook_token(mission.id, user["user_id"], dsn=test_dsn)
    assert new and new != old
    with pytest.raises(MissionError):
        wake_mission_by_token(old, {}, dsn=test_dsn)
    out = wake_mission_by_token(new, {}, dsn=test_dsn, step_runner=_runner())
    assert out["status"] == "advanced"


def test_webhook_wake_finished_mission_records_but_does_not_run(make_user, test_dsn, ledger):
    user, mission = _mission(make_user, test_dsn, steps=["Satu langkah"])
    advance_mission(mission.id, user["user_id"], dsn=test_dsn, step_runner=_runner())
    out = wake_mission_by_token(mission.webhook_token, {}, dsn=test_dsn, step_runner=_runner())
    assert out["status"] == "already_finished"
    assert ledger.events(mission.id, "evidence_recorded")


def test_schedule_mission_rejects_unknown_mission_and_past_schedule(make_user, test_dsn):
    user = make_user()
    with pytest.raises(SchedulerError):
        schedule_mission(
            user["user_id"], "misi-tidak-ada", {"type": "daily", "time": "07:00"}, dsn=test_dsn
        )
    _, mission = _mission(make_user, test_dsn)
    with pytest.raises(SchedulerError):
        schedule_mission(
            mission.user_id,
            mission.id,
            {"type": "once", "at": (NOW - timedelta(hours=1)).isoformat()},
            dsn=test_dsn,
            now=NOW,
        )


def test_scheduled_mission_advances_one_step_per_tick(make_user, test_dsn):
    user, mission = _mission(make_user, test_dsn, steps=["Satu langkah", "Dua langkah"])
    trigger = schedule_mission(
        user["user_id"],
        mission.id,
        {"type": "interval", "seconds": 60},
        dsn=test_dsn,
        now=NOW,
    )
    loop = _FakeLoop()
    runs = tick(dsn=test_dsn, now=NOW + timedelta(minutes=6), turn_loop=loop)
    assert len(runs) == 1
    assert runs[0]["trigger_id"] == trigger.id
    assert runs[0]["job"]["job"] == "regular_task"
    assert runs[0]["job"]["status"] == "completed"
    assert runs[0]["job"]["result"]["status"] == "advanced"
    assert len(loop.prompts) == 1
    assert "Satu langkah" in loop.prompts[0]
    assert get_mission(mission.id, user["user_id"], dsn=test_dsn).steps[0].status == "done"
    # Second tick finishes the mission and deactivates its trigger.
    runs = tick(dsn=test_dsn, now=NOW + timedelta(minutes=7), turn_loop=loop)
    assert runs[0]["job"]["result"]["status"] == "done"
    assert get_mission(mission.id, user["user_id"], dsn=test_dsn).status == "done"
    assert tick(dsn=test_dsn, now=NOW + timedelta(minutes=8), turn_loop=loop) == []


def test_scheduled_mission_without_loop_stays_due(make_user, test_dsn):
    user, mission = _mission(make_user, test_dsn, steps=["Satu langkah"])
    schedule_mission(
        user["user_id"],
        mission.id,
        {"type": "once", "at": (NOW + timedelta(minutes=5)).isoformat()},
        dsn=test_dsn,
        now=NOW,
    )
    runs = tick(dsn=test_dsn, now=NOW + timedelta(minutes=6))
    assert len(runs) == 1
    assert runs[0]["job"]["status"] == "not_run"
    assert get_mission(mission.id, user["user_id"], dsn=test_dsn).status == "active"
    # Still due: the next tick retries instead of dropping the step.
    runs = tick(dsn=test_dsn, now=NOW + timedelta(minutes=7), turn_loop=_FakeLoop())
    assert runs[0]["job"]["result"]["status"] == "done"
