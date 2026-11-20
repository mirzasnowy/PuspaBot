"""Tests for the P-11 visual approval overlay (``puspa.pwa.approval_overlay``).

Runs against the real Postgres test database: the ledger (the
durable record of every proposal and decision) and ownership
via ``make_user``. Covers proposing (target element + server-
rendered card + ``approval_requested`` in the ledger), the
highlight resolution against a P-09 live frame (last tool call
/ open task / matching ledger event / none), deciding per action
(approve records ``approval_granted``; reject records
``approval_rejected`` AND cancels the action at the gate, so
``Gate.execute`` can never run it), and the refusal paths (403
not theirs, 404 unknown, 409 already decided, credential
changes never get an overlay).

No screenshot is tested here because none exists: the highlight
is a pointer into the event-based frame, never a pixel position
(see ``puspa/pwa/approval_overlay.py``).
"""

from __future__ import annotations

import time
import uuid

import pytest

from puspa.policy.gate import Gate, GateDenied
from puspa.pwa.approval_overlay import (
    OverlayError,
    OverlayStore,
    build_target_element,
    highlight_for,
    list_overlays,
)
from puspa.pwa.live_view import TakeoverStore, build_live_frame


@pytest.fixture
def store(test_dsn):
    """Overlay store on the test database (in-memory overlays)."""
    return OverlayStore(test_dsn)


def _token(user_id, agent_id):
    """Token payload that allows gmail.kirim (a ``send`` action)."""
    now = int(time.time())
    return {
        "user_id": user_id,
        "agent_id": agent_id,
        "subagent_id": None,
        "tools": ["gmail.kirim"],
        "domains": [],
        "connectors": {},
        "budget": {},
        "exp": now + 3600,
        "iat": now,
    }


def _seed_task(ledger, agent_id, task_id, *, tool: str | None = "gmail.kirim"):
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
# Propose: target element + card + ledger record
# ---------------------------------------------------------------------------


def test_propose_builds_overlay_with_target_and_card(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    overlay = store.propose(
        user["user_id"],
        agent_id,
        tool_name="gmail.kirim",
        content={"recipient": "sari@contoh.id", "text": "halo, ini draf balasan"},
        button_label="Kirim",
        task_id=task_id,
        dsn=test_dsn,
        ledger=ledger,
    )
    assert overlay.status == "pending"
    assert overlay.kind == "send_to_other"
    assert overlay.tool_name == "gmail.kirim"
    assert overlay.button_label == "Kirim"
    assert overlay.task_id == task_id
    assert overlay.target_element.element_kind == "button"
    assert overlay.target_element.label == "Kirim"
    assert overlay.target_element.tool_name == "gmail.kirim"
    assert overlay.target_element.target == "sari@contoh.id"
    assert overlay.target_element.content_hash
    assert overlay.card["title"] == "Approve send"
    assert overlay.card["content_hash"] == overlay.target_element.content_hash
    assert overlay.proposed_ledger_event_id
    events = ledger.events(task_id, event_type="approval_requested")
    assert len(events) == 1
    assert events[0]["content"]["request_id"] == overlay.request_id
    assert events[0]["content"]["kind"] == "send_to_other"
    assert events[0]["content"]["target"] == "sari@contoh.id"
    assert events[0]["id"] == overlay.proposed_ledger_event_id
    ok, broken = ledger.verify_chain(task_id)
    assert ok is True
    assert broken == []


def test_propose_pay_action_shows_recipient_and_amount(make_user, test_dsn, ledger, store):
    user = make_user()
    overlay = store.propose(
        user["user_id"],
        user["agent_id"],
        tool_name="transfer",
        content={"recipient": "Toko Kopi", "nominal": 150000},
        dsn=test_dsn,
        ledger=ledger,
    )
    assert overlay.kind == "pay"
    assert overlay.target_element.element_kind == "tool"
    assert overlay.target_element.label == "transfer"
    assert overlay.target_element.target == "Toko Kopi"
    lines = {line["label"]: line["value"] for line in overlay.card["lines"]}
    assert lines["Recipient"] == "Toko Kopi"
    assert lines["Amount"] == "Rp150.000"


def test_propose_credential_change_is_refused(make_user, test_dsn, ledger, store):
    user = make_user()
    with pytest.raises(ValueError, match="never approvable"):
        store.propose(
            user["user_id"],
            user["agent_id"],
            tool_name="change_password",
            content={"target": "akun"},
            dsn=test_dsn,
            ledger=ledger,
        )
    assert store.pending_for_agent(user["user_id"], user["agent_id"]) == []
    assert ledger.events(user["agent_id"], event_type="approval_requested") == []


def test_propose_unrecognized_action_is_unknown_but_shown(make_user, test_dsn, ledger, store):
    user = make_user()
    overlay = store.propose(
        user["user_id"],
        user["agent_id"],
        tool_name="do_it",
        content={"thing": "misterius"},
        dsn=test_dsn,
        ledger=ledger,
    )
    # Fail-closed to asking: kind None, but the overlay still
    # shows what will run, so the user can reject it.
    assert overlay.kind is None
    assert overlay.status == "pending"
    assert overlay.card["lines"]


def test_propose_duplicate_pending_request_id_is_409(make_user, test_dsn, ledger, store):
    user = make_user()
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    store.propose(
        user["user_id"],
        user["agent_id"],
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        request_id=request_id,
        dsn=test_dsn,
        ledger=ledger,
    )
    with pytest.raises(OverlayError) as excinfo:
        store.propose(
            user["user_id"],
            user["agent_id"],
            tool_name="gmail.kirim",
            content={"recipient": "b@contoh.id"},
            request_id=request_id,
            dsn=test_dsn,
            ledger=ledger,
        )
    assert excinfo.value.status == 409


def test_propose_other_users_agent_is_403_and_records_nothing(make_user, test_dsn, ledger, store):
    owner = make_user()
    stranger = make_user()
    with pytest.raises(OverlayError) as excinfo:
        store.propose(
            stranger["user_id"],
            owner["agent_id"],
            tool_name="gmail.kirim",
            content={"recipient": "a@contoh.id"},
            dsn=test_dsn,
            ledger=ledger,
        )
    assert excinfo.value.status == 403
    assert ledger.events(owner["agent_id"], event_type="approval_requested") == []


def test_build_target_element_without_button_is_the_tool(make_user, test_dsn, ledger, store):
    target = build_target_element(
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        request_id="req-1",
    )
    assert target.element_kind == "tool"
    assert target.label == "gmail.kirim"
    assert target.target == "a@contoh.id"


# ---------------------------------------------------------------------------
# Decide: approve records granted, reject cancels + records rejected
# ---------------------------------------------------------------------------


def test_decide_approve_records_granted_in_ledger(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    overlay = store.propose(
        user["user_id"],
        agent_id,
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        task_id=task_id,
        dsn=test_dsn,
        ledger=ledger,
    )
    decided = store.decide(
        user["user_id"],
        overlay.request_id,
        decision="approved",
        reason="oke, kirim",
        dsn=test_dsn,
        ledger=ledger,
    )
    assert decided.status == "approved"
    assert decided.decided_by == user["user_id"]
    assert decided.decision_reason == "oke, kirim"
    assert decided.decided_ledger_event_id
    assert decided.gate_rejected is False
    events = ledger.events(task_id, event_type="approval_granted")
    assert len(events) == 1
    assert events[0]["content"]["request_id"] == overlay.request_id
    assert events[0]["content"]["decided_by"] == user["user_id"]
    assert events[0]["id"] == decided.decided_ledger_event_id
    assert store.pending_for_agent(user["user_id"], agent_id) == []
    assert store.decided_for_agent(user["user_id"], agent_id) == [decided]


def test_decide_reject_records_rejected_and_cancels_gate_action(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    gate = Gate()
    gate_request = gate.request_approval(
        user_id=user["user_id"],
        agent_id=agent_id,
        token=_token(user["user_id"], agent_id),
        kind="send",
        content={"recipient": "a@contoh.id"},
    )
    store_with_gate = OverlayStore(test_dsn, gate=gate)
    overlay = store_with_gate.propose(
        user["user_id"],
        agent_id,
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        task_id=task_id,
        request_id=gate_request.request_id,
        dsn=test_dsn,
        ledger=ledger,
    )
    decided = store_with_gate.decide(
        user["user_id"],
        overlay.request_id,
        decision="rejected",
        reason="jangan kirim dulu",
        dsn=test_dsn,
        ledger=ledger,
    )
    assert decided.status == "rejected"
    assert decided.gate_rejected is True
    assert decided.decision_reason == "jangan kirim dulu"
    # The rejection is in the ledger, with the reason.
    events = ledger.events(task_id, event_type="approval_rejected")
    assert len(events) == 1
    assert events[0]["content"]["request_id"] == overlay.request_id
    assert events[0]["content"]["reason"] == "jangan kirim dulu"
    # And the gate request itself is rejected: the action can
    # never execute afterwards, even if someone tries.
    assert gate_request.status == "rejected"
    called = []
    with pytest.raises(GateDenied):
        gate.execute(gate_request.request_id, executor=called.append)
    assert called == []


def test_decide_approve_with_gate_approves_gate_request(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    gate = Gate()
    gate_request = gate.request_approval(
        user_id=user["user_id"],
        agent_id=agent_id,
        token=_token(user["user_id"], agent_id),
        kind="send",
        content={"recipient": "a@contoh.id"},
    )
    store_with_gate = OverlayStore(test_dsn, gate=gate)
    overlay = store_with_gate.propose(
        user["user_id"],
        agent_id,
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        request_id=gate_request.request_id,
        dsn=test_dsn,
        ledger=ledger,
    )
    store_with_gate.decide(
        user["user_id"], overlay.request_id, decision="approved", dsn=test_dsn, ledger=ledger
    )
    assert gate_request.status == "approved"
    assert gate.execute(gate_request.request_id, executor=lambda content: "sent") == "sent"


def test_decide_twice_is_409_and_records_once(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    overlay = store.propose(
        user["user_id"],
        agent_id,
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        task_id=task_id,
        dsn=test_dsn,
        ledger=ledger,
    )
    store.decide(
        user["user_id"], overlay.request_id, decision="approved", dsn=test_dsn, ledger=ledger
    )
    with pytest.raises(OverlayError) as excinfo:
        store.decide(
            user["user_id"], overlay.request_id, decision="rejected", dsn=test_dsn, ledger=ledger
        )
    assert excinfo.value.status == 409
    assert len(ledger.events(task_id, event_type="approval_granted")) == 1
    assert ledger.events(task_id, event_type="approval_rejected") == []


def test_decide_unknown_is_404(make_user, test_dsn, ledger, store):
    user = make_user()
    with pytest.raises(OverlayError) as excinfo:
        store.decide(
            user["user_id"], "req-tidak-ada", decision="approved", dsn=test_dsn, ledger=ledger
        )
    assert excinfo.value.status == 404


def test_decide_other_users_agent_is_403(make_user, test_dsn, ledger, store):
    owner = make_user()
    stranger = make_user()
    overlay = store.propose(
        owner["user_id"],
        owner["agent_id"],
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        dsn=test_dsn,
        ledger=ledger,
    )
    with pytest.raises(OverlayError) as excinfo:
        store.decide(
            stranger["user_id"],
            overlay.request_id,
            decision="approved",
            dsn=test_dsn,
            ledger=ledger,
        )
    assert excinfo.value.status == 403
    assert store.pending_for_agent(owner["user_id"], owner["agent_id"]) == [overlay]


def test_gate_reject_standalone_marks_rejected_and_blocks_execute():
    gate = Gate()
    request = gate.request_approval(
        user_id="user-1",
        agent_id="agen-1",
        token=_token("user-1", "agen-1"),
        kind="send",
        content={"recipient": "a@contoh.id"},
    )
    rejected = gate.reject(request.request_id, "user-1", reason="tidak")
    assert rejected.status == "rejected"
    called = []
    with pytest.raises(GateDenied):
        gate.execute(request.request_id, executor=called.append)
    assert called == []
    # A rejected request cannot be approved later.
    with pytest.raises(GateDenied):
        gate.approve(request.request_id, "user-1")


def test_gate_reject_unknown_and_wrong_owner():
    gate = Gate()
    with pytest.raises(GateDenied):
        gate.reject("req-tidak-ada", "user-1")
    request = gate.request_approval(
        user_id="user-1",
        agent_id="agen-1",
        token=_token("user-1", "agen-1"),
        kind="send",
        content={"recipient": "a@contoh.id"},
    )
    with pytest.raises(GateDenied):
        gate.reject(request.request_id, "user-lain")


# ---------------------------------------------------------------------------
# Highlight: where the target is pointed at in the live frame
# ---------------------------------------------------------------------------


def test_highlight_points_at_last_tool(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id, tool="gmail.kirim")
    overlay = store.propose(
        user["user_id"],
        agent_id,
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        button_label="Kirim",
        task_id=task_id,
        dsn=test_dsn,
        ledger=ledger,
    )
    frame = build_live_frame(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger)
    assert highlight_for(frame, overlay).frame_ref == "last_tool"


def test_highlight_points_at_current_task(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id, tool=None)
    overlay = store.propose(
        user["user_id"],
        agent_id,
        tool_name="delete",
        content={"target": "catatan lama"},
        task_id=task_id,
        dsn=test_dsn,
        ledger=ledger,
    )
    frame = build_live_frame(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger)
    assert highlight_for(frame, overlay).frame_ref == "current_task"


def test_highlight_points_at_matching_ledger_event(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    other_task = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id, tool="search_listings")
    overlay = store.propose(
        user["user_id"],
        agent_id,
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        task_id=other_task,
        dsn=test_dsn,
        ledger=ledger,
    )
    frame = build_live_frame(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger)
    highlight = highlight_for(frame, overlay)
    assert highlight.frame_ref == "event"
    assert highlight.event_id == overlay.proposed_ledger_event_id


def test_highlight_none_when_nothing_matches(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    other_task = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id, tool="search_listings")
    overlay = store.propose(
        user["user_id"],
        agent_id,
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        task_id=other_task,
        dsn=test_dsn,
        ledger=ledger,
    )
    # Narrow the frame to the open task only: the proposal event
    # (other task) is out of view, the tool differs, the task differs.
    frame = build_live_frame(
        user["user_id"], agent_id, dsn=test_dsn, ledger=ledger, task_id=task_id
    )
    highlight = highlight_for(frame, overlay)
    assert highlight.frame_ref == "none"
    assert "not visible" in highlight.note


def test_list_overlays_attaches_highlights(make_user, test_dsn, ledger, store):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    _seed_task(ledger, agent_id, task_id, tool="gmail.kirim")
    overlay = store.propose(
        user["user_id"],
        agent_id,
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        task_id=task_id,
        dsn=test_dsn,
        ledger=ledger,
    )
    frame = build_live_frame(user["user_id"], agent_id, dsn=test_dsn, ledger=ledger)
    listed = list_overlays(user["user_id"], agent_id, dsn=test_dsn, store=store, frame=frame)
    assert listed["agent_id"] == agent_id
    assert len(listed["pending"]) == 1
    assert listed["pending"][0]["request_id"] == overlay.request_id
    assert listed["pending"][0]["highlight"]["frame_ref"] == "last_tool"
    assert listed["decided"] == []
    store.decide(
        user["user_id"], overlay.request_id, decision="approved", dsn=test_dsn, ledger=ledger
    )
    listed = list_overlays(user["user_id"], agent_id, dsn=test_dsn, store=store, frame=frame)
    assert listed["pending"] == []
    assert len(listed["decided"]) == 1
    assert listed["decided"][0]["status"] == "approved"


def test_list_overlays_without_frame_has_no_highlight(make_user, test_dsn, ledger, store):
    user = make_user()
    store.propose(
        user["user_id"],
        user["agent_id"],
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        dsn=test_dsn,
        ledger=ledger,
    )
    listed = list_overlays(user["user_id"], user["agent_id"], dsn=test_dsn, store=store)
    assert listed["pending"][0]["highlight"] is None


def test_takeover_store_untouched_by_overlay(make_user, test_dsn, ledger, store):
    """The overlay never holds the agent: it only asks per action."""
    user = make_user()
    takeover_store = TakeoverStore(test_dsn)
    takeover_store.create_schema()
    store.propose(
        user["user_id"],
        user["agent_id"],
        tool_name="gmail.kirim",
        content={"recipient": "a@contoh.id"},
        dsn=test_dsn,
        ledger=ledger,
    )
    assert takeover_store.is_held(user["agent_id"]) is False
