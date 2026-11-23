"""Tests for the P-10 replay timeline (``puspa.pwa.replay``).

Runs against the real Postgres test database: the ledger is
the source of every step, so the tests seed real tasks and
replay them. Covers the timeline (steps in order, reason +
result per step, task status, hash-chain verification), the
step index (scrubbing), the task list, ownership (another
user's agent is always 403, another agent's task reads as
404), the step limit, and the GIF export (deterministic
bytes, one rendered frame per step).

No real screenshot is tested here because none exists: every
step carries ``screenshot: None`` and the GIF frames are
rendered from the steps' own data (see ``puspa/pwa/replay.py``).
"""

from __future__ import annotations

import io
import uuid

from puspa.db import connect as _db_connect
import pytest
from PIL import Image

from puspa.pwa.replay import (
    GIF_FRAME_DURATION_MS,
    GIF_FRAME_HEIGHT,
    GIF_FRAME_WIDTH,
    MAX_REPLAY_STEPS,
    ReplayError,
    build_replay,
    export_replay_gif,
    list_tasks_for_agent,
    render_step_frame,
)


def _task_id() -> str:
    return f"tugas-{uuid.uuid4().hex[:8]}"


def _seed_task(ledger, agent_id, task_id, *, tool: str = "search_listings"):
    """A task that started, called a tool, and finished it."""
    ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="task_created",
        content={"purpose": "compare prices"},
    )
    ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="tool_called",
        content={"tool": tool, "reason": "need fresh prices"},
    )
    ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="tool_completed",
        content={"tool": tool, "result": "3 listings found"},
    )


# ---------------------------------------------------------------------------
# Timeline: steps in order, reason + result, status, chain
# ---------------------------------------------------------------------------


def test_replay_steps_in_order_with_reason_and_result(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = _task_id()
    _seed_task(ledger, agent_id, task_id)
    timeline = build_replay(user["user_id"], agent_id, task_id, dsn=test_dsn, ledger=ledger)
    assert timeline.agent_id == agent_id
    assert timeline.task_id == task_id
    assert timeline.step_count == 3
    assert timeline.status == "running"
    assert timeline.chain_ok is True
    assert timeline.broken_event_ids == []
    assert timeline.started_at is not None
    assert timeline.ended_at is not None
    assert [step.type for step in timeline.steps] == [
        "task_created",
        "tool_called",
        "tool_completed",
    ]
    assert [step.index for step in timeline.steps] == [0, 1, 2]
    # The reason comes from the event itself, never invented.
    assert timeline.steps[0].reason.startswith("Bot memulai tugas ini.")
    assert "compare prices" in timeline.steps[0].reason
    assert timeline.steps[0].result is None
    assert timeline.steps[1].tool == "search_listings"
    assert "need fresh prices" in timeline.steps[1].reason
    assert timeline.steps[1].result is None
    assert timeline.steps[2].tool == "search_listings"
    assert timeline.steps[2].result == "3 listings found"
    # No real screenshot exists: every step says so.
    assert all(step.screenshot is None for step in timeline.steps)
    # The hash chain rides along, so the screen can show it.
    assert timeline.steps[1].previous_hash == timeline.steps[0].hash
    assert timeline.steps[2].previous_hash == timeline.steps[1].hash


def test_replay_status_from_last_event(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    for last_type, status in (
        ("task_completed", "completed"),
        ("task_failed", "failed"),
        ("task_stopped", "stopped"),
    ):
        task_id = _task_id()
        ledger.append_event(
            task_id=task_id, agent_id=agent_id, event_type="task_created", content={}
        )
        ledger.append_event(
            task_id=task_id,
            agent_id=agent_id,
            event_type=last_type,
            content={"error": "boom"} if last_type == "task_failed" else {},
        )
        timeline = build_replay(user["user_id"], agent_id, task_id, dsn=test_dsn, ledger=ledger)
        assert timeline.status == status
        if last_type == "task_failed":
            assert timeline.steps[-1].result == "boom"


def test_replay_unknown_task_is_404(make_user, test_dsn, ledger):
    user = make_user()
    with pytest.raises(ReplayError) as excinfo:
        build_replay(
            user["user_id"], user["agent_id"], "tugas-tidak-ada", dsn=test_dsn, ledger=ledger
        )
    assert excinfo.value.status == 404


def test_replay_other_users_agent_is_403(make_user, test_dsn, ledger):
    owner = make_user()
    stranger = make_user()
    task_id = _task_id()
    _seed_task(ledger, owner["agent_id"], task_id)
    with pytest.raises(ReplayError) as excinfo:
        build_replay(stranger["user_id"], owner["agent_id"], task_id, dsn=test_dsn, ledger=ledger)
    assert excinfo.value.status == 403


def test_replay_other_agents_task_reads_as_404(make_user, test_dsn, ledger):
    owner = make_user()
    other = make_user()
    task_id = _task_id()
    _seed_task(ledger, other["agent_id"], task_id)
    # The owner asks for the task under their OWN agent id: the
    # task's events belong to another agent, so it reads as
    # unknown — never as someone else's steps.
    with pytest.raises(ReplayError) as excinfo:
        build_replay(owner["user_id"], owner["agent_id"], task_id, dsn=test_dsn, ledger=ledger)
    assert excinfo.value.status == 404


def test_replay_over_limit_is_400(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = _task_id()
    _seed_task(ledger, agent_id, task_id)
    with pytest.raises(ReplayError) as excinfo:
        build_replay(user["user_id"], agent_id, task_id, dsn=test_dsn, ledger=ledger, limit=2)
    assert excinfo.value.status == 400
    assert "3 steps" in str(excinfo.value)


def test_replay_reports_a_broken_chain(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = _task_id()
    _seed_task(ledger, agent_id, task_id)
    events = ledger.events(task_id)
    tampered_id = events[1]["id"]
    with _db_connect(test_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE events SET content = %s::jsonb WHERE id = %s",
            ('{"tool": "tampered"}', tampered_id),
        )
    timeline = build_replay(user["user_id"], agent_id, task_id, dsn=test_dsn, ledger=ledger)
    # The replay still plays, but the break is shown, not hidden.
    assert timeline.chain_ok is False
    assert timeline.broken_event_ids == [tampered_id]
    assert timeline.step_count == 3


def test_replay_reason_falls_back_when_event_records_none(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = _task_id()
    ledger.append_event(task_id=task_id, agent_id=agent_id, event_type="tool_called", content={})
    timeline = build_replay(user["user_id"], agent_id, task_id, dsn=test_dsn, ledger=ledger)
    assert timeline.steps[0].tool is None
    assert "(tidak tercatat)" in timeline.steps[0].reason


# ---------------------------------------------------------------------------
# Step index: scrubbing to an exact position
# ---------------------------------------------------------------------------


def test_step_index_scrubs_forward_and_back(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = _task_id()
    _seed_task(ledger, agent_id, task_id)
    timeline = build_replay(user["user_id"], agent_id, task_id, dsn=test_dsn, ledger=ledger)
    assert timeline.step(2).type == "tool_completed"
    assert timeline.step(0).type == "task_created"
    assert timeline.step(1).type == "tool_called"
    with pytest.raises(ReplayError) as excinfo:
        timeline.step(3)
    assert excinfo.value.status == 404
    with pytest.raises(ReplayError) as excinfo:
        timeline.step(-1)
    assert excinfo.value.status == 404


# ---------------------------------------------------------------------------
# Task list: the picker the screen replays from
# ---------------------------------------------------------------------------


def test_list_tasks_newest_first_with_counts(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    first = _task_id()
    _seed_task(ledger, agent_id, first)
    ledger.append_event(task_id=first, agent_id=agent_id, event_type="task_completed", content={})
    second = _task_id()
    _seed_task(ledger, agent_id, second)
    tasks = list_tasks_for_agent(user["user_id"], agent_id, dsn=test_dsn)
    assert [task["task_id"] for task in tasks] == [second, first]
    assert tasks[0]["event_count"] == 3
    assert tasks[0]["status"] == "running"
    assert tasks[1]["event_count"] == 4
    assert tasks[1]["status"] == "completed"
    assert tasks[0]["first_event_at"] <= tasks[0]["last_event_at"]


def test_list_tasks_other_users_agent_is_403(make_user, test_dsn):
    owner = make_user()
    stranger = make_user()
    with pytest.raises(ReplayError) as excinfo:
        list_tasks_for_agent(stranger["user_id"], owner["agent_id"], dsn=test_dsn)
    assert excinfo.value.status == 403


def test_list_tasks_empty_for_new_agent(make_user, test_dsn):
    user = make_user()
    assert list_tasks_for_agent(user["user_id"], user["agent_id"], dsn=test_dsn) == []


# ---------------------------------------------------------------------------
# Rendered frames + GIF export: deterministic, one frame per step
# ---------------------------------------------------------------------------


def test_rendered_frame_is_deterministic(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = _task_id()
    _seed_task(ledger, agent_id, task_id)
    timeline = build_replay(user["user_id"], agent_id, task_id, dsn=test_dsn, ledger=ledger)
    first = render_step_frame(timeline.steps[1], total_steps=timeline.step_count)
    second = render_step_frame(timeline.steps[1], total_steps=timeline.step_count)
    assert first.size == (GIF_FRAME_WIDTH, GIF_FRAME_HEIGHT)
    assert first.mode == "RGB"
    assert first.tobytes() == second.tobytes()


def test_gif_export_is_deterministic_and_has_one_frame_per_step(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = _task_id()
    _seed_task(ledger, agent_id, task_id)
    timeline = build_replay(user["user_id"], agent_id, task_id, dsn=test_dsn, ledger=ledger)
    first = export_replay_gif(timeline)
    second = export_replay_gif(timeline)
    assert first == second
    assert first[:6] in (b"GIF89a", b"GIF87a")
    with Image.open(io.BytesIO(first)) as gif:
        assert gif.n_frames == timeline.step_count == 3
        assert gif.size == (GIF_FRAME_WIDTH, GIF_FRAME_HEIGHT)
        assert gif.info.get("duration") == GIF_FRAME_DURATION_MS


def test_gif_export_empty_timeline_is_404():
    from puspa.pwa.replay import ReplayTimeline

    with pytest.raises(ReplayError) as excinfo:
        export_replay_gif(ReplayTimeline(agent_id="a", task_id="t"))
    assert excinfo.value.status == 404


def test_max_replay_steps_is_a_sane_cap():
    assert MAX_REPLAY_STEPS == 500
