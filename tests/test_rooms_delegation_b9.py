"""Delegated tasks with approval (B9).

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). The room schema is
applied by the autouse fixture from tests.rooms_helpers.

The approval tests use a real G4 ``ApprovalPolicy`` + G5
``Gate`` with a dict token payload (the gate accepts an
already-verified dict payload; no signing key is needed for
these tests). No live subagent runs anywhere here: the agent
worker is a subagent ref string, and the lifecycle check is
proved with a stub that refuses unknown handles.
"""

import time
from datetime import UTC, datetime, timedelta

import pytest

from puspa.policy import Gate, GateDenied
from puspa.policy.approvals import ApprovalPolicy
from puspa.rooms import DelegationService, RoomTaskStore
from puspa.rooms.delegation import (
    classify_task_due,
    is_board_command,
    reminder_lines,
    render_board,
)
from puspa.rooms.errors import NotRoomMemberError
from puspa.subagents.errors import UnknownHandleError
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room"]


@pytest.fixture
def service(test_dsn):
    return DelegationService(test_dsn)


@pytest.fixture
def task_store(test_dsn):
    return RoomTaskStore(test_dsn)


def _token_payload(user_id, agent_id="agen-puspa"):
    now = int(time.time())
    return {
        "user_id": user_id,
        "agent_id": agent_id,
        "subagent_id": None,
        "tools": ["delete", "send", "gmail.kirim", "prepare_payment"],
        "domains": [],
        "connectors": {},
        "budget": {"step_max": 25, "token_max": 100000, "rupiah_max": 1000000},
        "exp": now + 3600,
        "iat": now,
        "parent": None,
    }


def _service_with_approval(test_dsn):
    gate = Gate(log=None, spending=None, public_key=None)
    policy = ApprovalPolicy(gate=gate)
    return DelegationService(test_dsn, policy=policy, gate=gate), gate, policy


def _make_task(service, make_room, **overrides):
    made = make_room()
    task = service.tasks.add_task(
        made["room"].id,
        title=overrides.get("title", "Compare prices"),
        created_by=made["owner"]["user_id"],
        owner_user_id=overrides.get("owner_user_id", made["owner"]["user_id"]),
        due_at=overrides.get("due_at"),
    )
    return made, task


class _StubLifecycle:
    """A G25 lifecycle stub: only the handle "sub-ok" resolves."""

    def status(self, handle):
        if handle != "sub-ok":
            raise UnknownHandleError("unknown handle")
        return {"subagent_id": "sub-ok", "state": "RUNNING"}


# --- delegation: agent and teammate ---


def test_delegate_to_agent_records_delegation(service, make_room):
    made, task = _make_task(service, make_room)
    delegation = service.delegate_to_agent(
        task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok"
    )
    assert delegation.delegate_kind == "agent"
    assert delegation.assignee_ref == "sub-ok"
    assert delegation.status == "delegated"
    assert delegation.task.owner_user_id == made["owner"]["user_id"]
    assert delegation.task.due_at is None
    assert service.get_delegation(task.id) is not None


def test_delegate_to_agent_verifies_handle_with_lifecycle(service, make_room):
    made, task = _make_task(service, make_room)
    with pytest.raises(UnknownHandleError):
        service.delegate_to_agent(
            task.id,
            delegated_by=made["owner"]["user_id"],
            subagent_ref="sub-forged",
            lifecycle=_StubLifecycle(),
        )
    assert service.get_delegation(task.id) is None
    delegation = service.delegate_to_agent(
        task.id,
        delegated_by=made["owner"]["user_id"],
        subagent_ref="sub-ok",
        lifecycle=_StubLifecycle(),
    )
    assert delegation.assignee_ref == "sub-ok"


def test_delegate_to_teammate_reassigns_owner(service, make_room, add_room_member):
    made, task = _make_task(service, make_room)
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    delegation = service.delegate_to_teammate(
        task.id,
        delegated_by=made["owner"]["user_id"],
        teammate_user_id=member["user_id"],
    )
    assert delegation.delegate_kind == "teammate"
    assert delegation.assignee_ref == member["user_id"]
    assert delegation.task.owner_user_id == member["user_id"]
    assert delegation.status == "delegated"


def test_guest_may_not_delegate_or_receive_work(service, make_room, add_room_member):
    made, task = _make_task(service, make_room)
    guest = add_room_member(made["room"].id, made["owner"]["user_id"], role="guest")
    with pytest.raises(NotRoomMemberError):
        service.delegate_to_agent(task.id, delegated_by=guest["user_id"], subagent_ref="sub-ok")
    with pytest.raises(NotRoomMemberError):
        service.delegate_to_teammate(
            task.id,
            delegated_by=made["owner"]["user_id"],
            teammate_user_id=guest["user_id"],
        )
    assert service.get_delegation(task.id) is None


def test_delegate_non_open_task_refused(service, make_room, task_store):
    made, task = _make_task(service, make_room)
    task_store.complete_task(task.id, completed_by=made["owner"]["user_id"])
    with pytest.raises(ValueError, match="only an open task"):
        service.delegate_to_agent(
            task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok"
        )


def test_delegate_unknown_task_refused(service):
    with pytest.raises(ValueError, match="task not found"):
        service.delegate_to_agent(
            "00000000-0000-0000-0000-000000000000",
            delegated_by="00000000-0000-0000-0000-000000000001",
            subagent_ref="sub-ok",
        )


# --- work cycle: start / report / block / resume ---


def test_start_report_block_resume(service, make_room):
    made, task = _make_task(service, make_room)
    service.delegate_to_agent(task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok")
    started = service.start_work(task.id, by="sub-ok")
    assert started.status == "in_progress"
    assert started.started_at is not None
    reported = service.report_progress(task.id, by="sub-ok", note="checked 3 sellers")
    assert reported.last_report == "checked 3 sellers"
    assert reported.status == "in_progress"
    blocked = service.block_task(task.id, by="sub-ok", reason="seller did not reply")
    assert blocked.status == "blocked"
    assert blocked.blocked_reason == "seller did not reply"
    # Reporting while blocked keeps the blocked status.
    reported = service.report_progress(task.id, by="sub-ok", note="still waiting")
    assert reported.status == "blocked"
    resumed = service.resume_task(task.id, by="sub-ok")
    assert resumed.status == "in_progress"
    assert resumed.blocked_reason is None


def test_only_worker_creator_or_owner_may_report(service, make_room, add_room_member):
    made, task = _make_task(service, make_room)
    other = add_room_member(made["room"].id, made["owner"]["user_id"])
    service.delegate_to_agent(task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok")
    with pytest.raises(NotRoomMemberError):
        service.report_progress(task.id, by=other["user_id"], note="not my task")
    # The creator (here also the owner) may report.
    assert (
        service.report_progress(
            task.id, by=made["owner"]["user_id"], note="owner update"
        ).last_report
        == "owner update"
    )


def test_propose_step_on_undelegated_task_refused(service, make_room):
    made, task = _make_task(service, make_room)
    with pytest.raises(ValueError, match="not delegated"):
        service.propose_step(
            task.id,
            by=made["owner"]["user_id"],
            description="read the listing",
            tool_name="read_listing",
        )


# --- steps: reversible steps run without approval ---


def test_reversible_step_executes_without_approval(service, make_room):
    made, task = _make_task(service, make_room)
    service.delegate_to_agent(task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok")
    step = service.propose_step(
        task.id,
        by="sub-ok",
        description="read the listing",
        tool_name="read_listing",
    )
    assert step.status == "proposed"
    assert step.irreversible is False
    calls = []
    result = service.execute_step(
        step.id, by="sub-ok", executor=lambda c: calls.append(c) or "read ok"
    )
    assert result == "read ok"
    assert len(calls) == 1
    # Executing again returns the stored result, not a second run.
    assert (
        service.execute_step(step.id, by="sub-ok", executor=lambda c: calls.append(c) or "again")
        == "read ok"
    )
    assert len(calls) == 1


# --- steps: irreversible without an approval path fails closed ---


def test_irreversible_step_without_policy_waits_and_never_runs(service, make_room):
    made, task = _make_task(service, make_room)
    service.delegate_to_agent(task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok")
    step = service.propose_step(
        task.id,
        by="sub-ok",
        description="send the shortlist to the group",
        tool_name="gmail.send",
        irreversible=True,
        content={"recipient": "group", "target": "shortlist"},
    )
    assert step.status == "awaiting_approval"
    assert step.approval_request_id is None
    with pytest.raises(GateDenied, match="approval is required"):
        service.execute_step(step.id, by="sub-ok", executor=lambda c: "sent")
    # The owner may still approve it directly (no gate was filed).
    approved = service.approve_step(step.id, decided_by=made["owner"]["user_id"])
    assert approved.status == "approved"
    assert service.execute_step(step.id, by="sub-ok", executor=lambda c: "sent") == "sent"


def test_irreversible_step_rejected_by_non_owner_and_approved_by_owner(
    service, make_room, add_room_member, test_dsn
):
    made, task = _make_task(service, make_room)
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    service.delegate_to_agent(task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok")
    step = service.propose_step(
        task.id,
        by="sub-ok",
        description="delete the draft",
        tool_name="delete",
        irreversible=True,
    )
    with pytest.raises(NotRoomMemberError):
        service.approve_step(step.id, decided_by=member["user_id"])
    rejected = service.reject_step(step.id, decided_by=made["owner"]["user_id"])
    assert rejected.status == "rejected"
    with pytest.raises(GateDenied, match="approval is required"):
        service.execute_step(step.id, by="sub-ok", executor=lambda c: "deleted")


# --- steps: irreversible through the G4/G5 path ---


def test_irreversible_step_files_gate_request_and_runs_after_approval(service, make_room, test_dsn):
    approved_service, _gate, _policy = _service_with_approval(test_dsn)
    made, task = _make_task(approved_service, make_room)
    approved_service.delegate_to_agent(
        task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok"
    )
    step = approved_service.propose_step(
        task.id,
        by="sub-ok",
        description="delete the draft",
        tool_name="delete",
        irreversible=True,
        content={"target": "draft"},
        approval_agent_id="agen-puspa",
        token=_token_payload(made["owner"]["user_id"]),
    )
    assert step.status == "awaiting_approval"
    assert step.approval_request_id is not None
    # The delegation waits while the step waits.
    assert approved_service.get_delegation(task.id).status == "awaiting_approval"
    with pytest.raises(GateDenied):
        approved_service.execute_step(step.id, by="sub-ok", executor=lambda c: "deleted")
    approved = approved_service.approve_step(step.id, decided_by=made["owner"]["user_id"])
    assert approved.status == "approved"
    assert approved_service.get_delegation(task.id).status == "delegated"
    calls = []
    result = approved_service.execute_step(
        step.id, by="sub-ok", executor=lambda c: calls.append(c) or "deleted"
    )
    assert result == "deleted"
    assert len(calls) == 1
    # The gate executed it exactly once; a second call returns the stored result.
    assert (
        approved_service.execute_step(
            step.id, by="sub-ok", executor=lambda c: calls.append(c) or "again"
        )
        == "deleted"
    )
    assert len(calls) == 1


def test_credential_change_step_is_rejected_at_once(service, make_room, test_dsn):
    approved_service, _gate, _policy = _service_with_approval(test_dsn)
    made, task = _make_task(approved_service, make_room)
    approved_service.delegate_to_agent(
        task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok"
    )
    step = approved_service.propose_step(
        task.id,
        by="sub-ok",
        description="change the account password",
        tool_name="change_password",
        irreversible=True,
        content={"target": "account"},
        approval_agent_id="agen-puspa",
        token=_token_payload(made["owner"]["user_id"]),
    )
    assert step.status == "rejected"
    with pytest.raises(GateDenied, match="approval is required"):
        approved_service.execute_step(step.id, by="sub-ok", executor=lambda c: "changed")


def test_tiered_answer_allows_irreversible_step_at_once(service, make_room, test_dsn):
    approved_service, _gate, policy = _service_with_approval(test_dsn)
    policy.record_answer("delete", "always")
    made, task = _make_task(approved_service, make_room)
    approved_service.delegate_to_agent(
        task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok"
    )
    step = approved_service.propose_step(
        task.id,
        by="sub-ok",
        description="delete the draft",
        tool_name="delete",
        irreversible=True,
        content={"target": "draft"},
        approval_agent_id="agen-puspa",
        token=_token_payload(made["owner"]["user_id"]),
    )
    assert step.status == "approved"
    assert (
        approved_service.execute_step(step.id, by="sub-ok", executor=lambda c: "deleted")
        == "deleted"
    )


# --- completing a delegated task ---


def test_complete_blocked_while_steps_wait(service, make_room):
    made, task = _make_task(service, make_room)
    service.delegate_to_agent(task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok")
    service.propose_step(
        task.id, by="sub-ok", description="read the listing", tool_name="read_listing"
    )
    with pytest.raises(ValueError, match="step\\(s\\) still waiting"):
        service.complete_task(task.id, completed_by=made["owner"]["user_id"])
    step = service.get_delegation(task.id).steps[0]
    service.execute_step(step.id, by="sub-ok", executor=lambda c: "read ok")
    done = service.complete_task(task.id, completed_by=made["owner"]["user_id"])
    assert done is not None and done.status == "done"
    assert service.get_delegation(task.id).status == "done"


# --- board: rendering, commands, reminders ---


def test_board_lists_tasks_with_delegation(service, make_room, add_room_member):
    made, task = _make_task(service, make_room, due_at=datetime.now(UTC) + timedelta(days=2))
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    plain = service.tasks.add_task(
        made["room"].id, title="Undelegated", created_by=made["owner"]["user_id"]
    )
    service.delegate_to_teammate(
        task.id,
        delegated_by=made["owner"]["user_id"],
        teammate_user_id=member["user_id"],
    )
    rows = service.board(made["room"].id, viewer_user_id=member["user_id"])
    assert {r.task.id for r in rows} == {task.id, plain.id}
    delegated = next(r for r in rows if r.task.id == task.id)
    assert delegated.delegation is not None
    assert delegated.delegation.delegate_kind == "teammate"
    assert delegated.pending_steps == 0
    assert next(r for r in rows if r.task.id == plain.id).delegation is None
    with pytest.raises(NotRoomMemberError):
        service.board(made["room"].id, viewer_user_id="00000000-0000-0000-0000-000000000000")


def test_render_board_text(service, make_room):
    made, task = _make_task(service, make_room, due_at=datetime.now(UTC) + timedelta(days=2))
    service.delegate_to_agent(task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok")
    service.report_progress(task.id, by="sub-ok", note="checked 3 sellers")
    service.propose_step(
        task.id,
        by="sub-ok",
        description="send the shortlist",
        tool_name="gmail.send",
        irreversible=True,
    )
    rows = service.board(made["room"].id, viewer_user_id=made["owner"]["user_id"])
    text = render_board(made["room"].name, rows)
    assert "Compare prices" in text
    assert "delegated to agent sub-ok" in text
    assert "checked 3 sellers" in text
    assert "1 step(s) waiting" in text
    assert render_board("Empty room", []) == "Task board: Empty room\nNo tasks yet."


def test_is_board_command():
    assert is_board_command("@Puspa board") is True
    assert is_board_command("@puspa tasks") is True
    assert is_board_command("@Puspa tugas") is True
    assert is_board_command("@puspa papan tugas.") is True
    assert is_board_command("@puspa summarise the chat") is False
    assert is_board_command("just chatting") is False
    assert is_board_command(None) is False


def test_classify_task_due_no_deadline(service, make_room):
    _made, task = _make_task(service, make_room)
    assert classify_task_due(task) == "no_deadline"
    assert classify_task_due(task, datetime.now(UTC)) == "no_deadline"


def test_delegation_reminders_only_delegated_due_tasks(service, make_room):
    made, task = _make_task(service, make_room, due_at=datetime.now(UTC) + timedelta(hours=12))
    service.tasks.add_task(
        made["room"].id,
        title="Plain due task",
        created_by=made["owner"]["user_id"],
        due_at=datetime.now(UTC) + timedelta(hours=12),
    )
    service.delegate_to_agent(task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok")
    rows = service.delegation_reminders(made["room"].id, viewer_user_id=made["owner"]["user_id"])
    assert [r.task.id for r in rows] == [task.id]
    lines = reminder_lines(rows)
    assert len(lines) == 1
    assert "Compare prices" in lines[0]
    assert "due soon" in lines[0]


def test_reminder_lines_overdue(service, make_room):
    made, task = _make_task(service, make_room, due_at=datetime.now(UTC) - timedelta(days=2))
    service.delegate_to_agent(task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok")
    rows = service.delegation_reminders(made["room"].id, viewer_user_id=made["owner"]["user_id"])
    lines = reminder_lines(rows)
    assert len(lines) == 1
    assert "overdue" in lines[0]


# --- group chat: the board command answers without the agent ---


def test_board_command_answers_from_group_chat(service, make_room, test_dsn):
    from puspa.channels.events import MessageEvent
    from puspa.rooms import RoomChannelLinker, handle_group_inbound

    made, task = _make_task(service, make_room)
    service.delegate_to_agent(task.id, delegated_by=made["owner"]["user_id"], subagent_ref="sub-ok")
    linker = RoomChannelLinker(test_dsn)
    linker.link_channel(
        made["room"].id,
        platform="telegram",
        chat_id="-100123",
        linked_by=made["owner"]["user_id"],
    )

    class FakeAdapter:
        platform = "telegram"

        def __init__(self):
            self.sent = []

        def send_text(self, chat_id, text):
            self.sent.append((chat_id, text))
            return {"ok": True}

    class FakeAgent:
        def __init__(self):
            self.calls = []

        def answer(self, event, *, user_id):
            self.calls.append((event, user_id))
            return "room reply"

    adapter = FakeAdapter()
    agent = FakeAgent()
    event = MessageEvent(
        platform="telegram",
        chat_id="-100123",
        sender_id="user-1",
        text="@Puspa board",
        message_id="m-board",
        is_group=True,
        mentions_bot=True,
    )
    outcome = handle_group_inbound(
        event, adapter=adapter, agent=agent, linker=linker, delegation=service
    )
    assert outcome is not None and outcome.outcome == "answered"
    assert agent.calls == []  # the board is data, not a turn
    assert len(adapter.sent) == 1
    assert "Compare prices" in adapter.sent[0][1]
    assert "delegated to agent sub-ok" in adapter.sent[0][1]

    # A non-board message still goes to the agent.
    event2 = MessageEvent(
        platform="telegram",
        chat_id="-100123",
        sender_id="user-1",
        text="@Puspa summarise the chat",
        message_id="m-chat",
        is_group=True,
        mentions_bot=True,
    )
    outcome2 = handle_group_inbound(
        event2, adapter=adapter, agent=agent, linker=linker, delegation=service
    )
    assert outcome2 is not None and outcome2.outcome == "answered"
    assert len(agent.calls) == 1
    assert adapter.sent[-1][1] == "room reply"
