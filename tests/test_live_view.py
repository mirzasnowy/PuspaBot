"""Tests for the P-09 live view + takeover (``puspa.pwa.live_view``).

Runs against the real Postgres test database: the durable hold
table (migration 0026), the ledger, and the PWA pool. Covers
the hold round trip, the takeover press (hold row + live work
interrupted + ``takeover_started`` in the ledger), the release
(hold row deleted + ``takeover_released``), ownership (another
user's agent is always 403, never held), the live frame
(statuses, the replayable event list, the ephemeral live tail),
and the frame-capture seam P-10 replays later.

No video stream is tested here because none exists: the frame
carries ``screenshot: None`` until a backend that can produce
frames lands (see ``puspa/pwa/live_view.py``).
"""

from __future__ import annotations

import uuid

from puspa.db import connect as _db_connect
import pytest

from puspa.pwa.live_view import (
    LedgerFrameSource,
    TakeoverError,
    TakeoverStore,
    build_live_frame,
    release_takeover,
    takeover_for_agent,
)
from puspa.runtime.subagents import SubagentPool


@pytest.fixture
def store(test_dsn):
    """Takeover store on the test database, emptied per test."""
    storage = TakeoverStore(test_dsn)
    storage.create_schema()
    with _db_connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE takeover_holds")
    yield storage
    with _db_connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE takeover_holds")


class _FakeLifecycle:
    """A lifecycle that cancels every child it is asked about."""

    def __init__(self):
        self.cancelled: list[str] = []

    def cancel_all_for_agent(self, agent_id):
        self.cancelled.append(agent_id)
        return [{"subagent_id": "sub-1", "state": "cancelled"}]


def _spawn(pool, agent_id, task_id, token_payloads):
    return pool.spawn(
        agent_id=agent_id,
        task_id=task_id,
        permission_class="Pembaca",
        agent_token=token_payloads["agents"],
        subagent_token=token_payloads["sub"],
    )


def _seed_task(ledger, agent_id, task_id, *, tool: str | None = "search_listings"):
    """A task that started (and optionally called a tool) for the agent."""
    ledger.append_event(task_id=task_id, agent_id=agent_id, event_type="task_created", content={})
    if tool is not None:
        ledger.append_event(
            task_id=task_id,
            agent_id=agent_id,
            event_type="tool_called",
            content={"tool": tool},
        )


# ---------------------------------------------------------------------------
# Store: hold round trip
# ---------------------------------------------------------------------------


def test_store_hold_release_round_trip(store):
    assert store.is_held("agen-1") is False
    assert store.get_hold("agen-1") is None
    hold = store.hold("agen-1", "user-1", reason="need to check")
    assert hold.agent_id == "agen-1"
    assert hold.held_by_user_id == "user-1"
    assert hold.reason == "need to check"
    assert hold.held_at
    assert store.is_held("agen-1") is True
    assert store.get_hold("agen-1") == hold
    assert [h.agent_id for h in store.holds_for_user("user-1")] == ["agen-1"]
    released = store.release("agen-1")
    assert released == hold
    assert store.is_held("agen-1") is False
    assert store.release("agen-1") is None


def test_store_double_hold_is_409(store):
    store.hold("agen-1", "user-1")
    with pytest.raises(TakeoverError) as excinfo:
        store.hold("agen-1", "user-2")
    assert excinfo.value.status == 409
    assert store.get_hold("agen-1").held_by_user_id == "user-1"


# ---------------------------------------------------------------------------
# Takeover: hold + interrupt + ledger event
# ---------------------------------------------------------------------------


def test_takeover_holds_interrupts_and_records(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id)
    lifecycle = _FakeLifecycle()
    report = takeover_for_agent(
        user["user_id"],
        agent_id,
        dsn=test_dsn,
        ledger=ledger,
        store=store,
        lifecycle=lifecycle,
        reason="checking the bot",
    )
    assert report["status"] == "held"
    assert report["held_by"] == user["user_id"]
    assert report["task_id"] == task_id
    assert report["interrupted_subagents"] == [{"subagent_id": "sub-1", "state": "cancelled"}]
    assert lifecycle.cancelled == [agent_id]
    assert store.is_held(agent_id) is True
    events = ledger.events(task_id, event_type="takeover_started")
    assert len(events) == 1
    assert events[0]["content"]["held_by"] == user["user_id"]
    assert events[0]["content"]["reason"] == "checking the bot"
    assert events[0]["content"]["user_id"] == user["user_id"]
    ok, broken = ledger.verify_chain(task_id)
    assert ok is True
    assert broken == []


def test_takeover_without_open_task_uses_agent_id_as_ledger_task(
    make_user, test_dsn, ledger, store
):
    user = make_user()
    report = takeover_for_agent(
        user["user_id"], user["agent_id"], dsn=test_dsn, ledger=ledger, store=store
    )
    assert report["task_id"] is None
    events = ledger.events(user["agent_id"], event_type="takeover_started")
    assert len(events) == 1


def test_takeover_stops_pool_subagents_but_keeps_working(
    make_user, test_dsn, ledger, store, token_payloads
):
    user = make_user()
    agent_id = user["agent_id"]
    pool = SubagentPool()
    _spawn(pool, agent_id, "tugas-1", token_payloads)
    assert pool.count_active() == 1
    report = takeover_for_agent(
        user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, store=store, pool=pool
    )
    assert report["pool_stopped"] == 1
    assert report["pool_wired"] is True
    assert pool.count_active() == 0
    # The agent itself is only held, not revoked: releasing lets
    # it work again (the resume path is tested below).
    assert store.is_held(agent_id) is True


def test_takeover_twice_is_409_and_does_not_interrupt_again(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    lifecycle = _FakeLifecycle()
    takeover_for_agent(
        user["user_id"],
        agent_id,
        dsn=test_dsn,
        ledger=ledger,
        store=store,
        lifecycle=lifecycle,
    )
    with pytest.raises(TakeoverError) as excinfo:
        takeover_for_agent(
            user["user_id"],
            agent_id,
            dsn=test_dsn,
            ledger=ledger,
            store=store,
            lifecycle=lifecycle,
        )
    assert excinfo.value.status == 409
    # The second press cancelled nothing: the first press did.
    assert lifecycle.cancelled == [agent_id]
    assert len(ledger.events(agent_id, event_type="takeover_started")) == 1


def test_takeover_other_users_agent_is_403_and_holds_nothing(make_user, test_dsn, ledger, store):
    owner = make_user()
    stranger = make_user()
    lifecycle = _FakeLifecycle()
    with pytest.raises(TakeoverError) as excinfo:
        takeover_for_agent(
            stranger["user_id"],
            owner["agent_id"],
            dsn=test_dsn,
            ledger=ledger,
            store=store,
            lifecycle=lifecycle,
        )
    assert excinfo.value.status == 403
    assert store.is_held(owner["agent_id"]) is False
    assert lifecycle.cancelled == []
    assert ledger.events(owner["agent_id"], event_type="takeover_started") == []


# ---------------------------------------------------------------------------
# Release: hold deleted + release recorded
# ---------------------------------------------------------------------------


def test_release_deletes_hold_and_records_event(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    takeover_for_agent(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, store=store)
    report = release_takeover(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, store=store)
    assert report["status"] == "released"
    assert report["held_by"] == user["user_id"]
    assert store.is_held(agent_id) is False
    events = ledger.events(agent_id, event_type="takeover_released")
    assert len(events) == 1
    assert events[0]["content"]["held_by"] == user["user_id"]


def test_release_without_hold_is_404(make_user, test_dsn, ledger, store):
    user = make_user()
    with pytest.raises(TakeoverError) as excinfo:
        release_takeover(
            user["user_id"], user["agent_id"], dsn=test_dsn, ledger=ledger, store=store
        )
    assert excinfo.value.status == 404


def test_release_other_users_agent_is_403(make_user, test_dsn, ledger, store):
    owner = make_user()
    stranger = make_user()
    takeover_for_agent(
        owner["user_id"], owner["agent_id"], dsn=test_dsn, ledger=ledger, store=store
    )
    with pytest.raises(TakeoverError) as excinfo:
        release_takeover(
            stranger["user_id"], owner["agent_id"], dsn=test_dsn, ledger=ledger, store=store
        )
    assert excinfo.value.status == 403
    assert store.is_held(owner["agent_id"]) is True


# ---------------------------------------------------------------------------
# Live frame: status, replayable events, live tail
# ---------------------------------------------------------------------------


def test_frame_idle_with_no_events(make_user, test_dsn, ledger, store):
    user = make_user()
    frame = build_live_frame(
        user["user_id"], user["agent_id"], dsn=test_dsn, ledger=ledger, store=store
    )
    assert frame.status == "idle"
    assert frame.current_task_id is None
    assert frame.last_tool is None
    assert frame.events == []
    assert frame.screenshot is None


def test_frame_working_with_open_task_and_last_tool(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id, tool="search_listings")
    ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="tool_completed",
        content={"tool": "search_listings"},
    )
    frame = build_live_frame(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, store=store)
    assert frame.status == "working"
    assert frame.current_task_id == task_id
    assert frame.last_tool["tool"] == "search_listings"
    assert frame.last_tool["type"] == "tool_completed"
    assert [event["type"] for event in frame.events] == [
        "task_created",
        "tool_called",
        "tool_completed",
    ]
    assert all(event["source"] == "ledger" for event in frame.events)
    assert all(event["hash"] for event in frame.events)


def test_frame_completed_task_is_idle_not_working(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id)
    ledger.append_event(task_id=task_id, agent_id=agent_id, event_type="task_completed", content={})
    frame = build_live_frame(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, store=store)
    assert frame.status == "idle"
    assert frame.current_task_id is None


def test_frame_held_while_takeover_active(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id)
    takeover_for_agent(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, store=store)
    frame = build_live_frame(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, store=store)
    assert frame.status == "held"
    assert frame.hold is not None
    assert frame.hold.held_by_user_id == user["user_id"]
    assert frame.current_task_id == task_id
    release_takeover(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, store=store)
    frame = build_live_frame(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, store=store)
    assert frame.status == "working"
    assert frame.hold is None


def test_frame_events_replay_in_oldest_first_order(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id)
    ledger.append_event(task_id=task_id, agent_id=agent_id, event_type="task_completed", content={})
    frame = build_live_frame(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, store=store)
    assert [event["type"] for event in frame.events] == [
        "task_created",
        "tool_called",
        "task_completed",
    ]
    # The replay carries the hash chain, so P-10 can verify it.
    assert frame.events[1]["previous_hash"] == frame.events[0]["hash"]
    assert frame.events[2]["previous_hash"] == frame.events[1]["hash"]


def test_frame_merges_live_tail_and_marks_it_ephemeral(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    live_events = [
        {
            "type": "tool_called",
            "task_id": "tugas-live",
            "agent_id": agent_id,
            "content": {"tool": "search_listings"},
            "created_at": 1793000000.0,
        },
        {
            "type": "tool_called",
            "task_id": "tugas-live",
            "agent_id": "agen-lain",
            "content": {"tool": "search_listings"},
            "created_at": 1793000000.0,
        },
    ]
    frame = build_live_frame(
        user["user_id"],
        agent_id,
        dsn=test_dsn,
        ledger=ledger,
        store=store,
        live_events=live_events,
    )
    assert frame.events == []
    assert len(frame.live_tail) == 1
    assert frame.live_tail[0]["source"] == "live"
    assert frame.live_tail[0]["agent_id"] == agent_id
    assert frame.live_tail[0]["hash"] == ""


def test_frame_for_other_users_agent_is_403(make_user, test_dsn, ledger, store):
    owner = make_user()
    stranger = make_user()
    with pytest.raises(TakeoverError) as excinfo:
        build_live_frame(
            stranger["user_id"], owner["agent_id"], dsn=test_dsn, ledger=ledger, store=store
        )
    assert excinfo.value.status == 403


def test_frame_source_capture_is_ledger_backed_and_has_no_screenshot(
    make_user, test_dsn, ledger, store
):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id)
    source = LedgerFrameSource(test_dsn)
    frame = source.capture_frame(agent_id)
    assert frame is not None
    assert frame.agent_id == agent_id
    assert frame.status == "working"
    assert frame.screenshot is None
    assert [event["type"] for event in frame.events] == ["task_created", "tool_called"]
