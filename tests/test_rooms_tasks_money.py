"""Room tasks, summaries, and the money gate (B6).

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). The room schema is
applied by the autouse fixture from tests.rooms_helpers.
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.rooms import MoneyGate, RoomTaskStore, split_tasks, summarize_messages
from puspa.rooms.errors import NotRoomMemberError
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room, room_store

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room", "room_store"]


@pytest.fixture
def task_store(test_dsn):
    return RoomTaskStore(test_dsn)


@pytest.fixture
def money_gate(test_dsn):
    return MoneyGate(test_dsn)


# --- deterministic summariser / task splitter (pure functions) ---


def test_summarize_messages_counts_decisions_and_tasks():
    messages = [
        {"author": "Ana", "text": "Hi all, should we buy it?"},
        {"author": "Budi", "text": "Decision: buy from seller A."},
        {"author": "Ana", "text": "TODO: compare prices by Friday"},
        {"author": "Citra", "text": "- [ ] ask seller about warranty"},
    ]
    summary = summarize_messages(messages)
    assert "4 messages" in summary
    assert "Ana (2)" in summary
    assert "buy from seller A" in summary
    assert "compare prices by Friday" in summary
    assert "ask seller about warranty" in summary


def test_summarize_messages_empty_and_no_decisions():
    assert summarize_messages([]) == "No messages to summarise."
    summary = summarize_messages([{"author": "Ana", "text": "hello"}])
    assert "No decisions stated." in summary
    assert "No tasks stated." in summary


def test_split_tasks_only_explicit_markers():
    text = "TODO: compare prices\n- [ ] ask seller\njust chatting here\nTODO :  call the shop  "
    assert split_tasks(text) == ["compare prices", "ask seller", "call the shop"]
    assert split_tasks("nothing to do here") == []
    assert split_tasks("") == []
    assert split_tasks("TODO:") == []


# --- shared task board ---


def test_add_and_list_tasks(make_room, add_room_member, task_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    task = task_store.add_task(
        made["room"].id,
        title="Compare prices",
        created_by=made["owner"]["user_id"],
        owner_user_id=member["user_id"],
        due_at=datetime.now(UTC) + timedelta(days=2),
    )
    assert task.status == "open"
    assert task.owner_user_id == member["user_id"]
    tasks = task_store.list_tasks(made["room"].id, viewer_user_id=member["user_id"])
    assert [t.id for t in tasks] == [task.id]


def test_add_task_rejects_non_member_owner(make_room, make_user, task_store):
    made = make_room()
    outsider = make_user()
    with pytest.raises(NotRoomMemberError):
        task_store.add_task(
            made["room"].id,
            title="Do it",
            created_by=made["owner"]["user_id"],
            owner_user_id=outsider["user_id"],
        )
    with pytest.raises(NotRoomMemberError):
        task_store.add_task(made["room"].id, title="Do it", created_by=outsider["user_id"])


def test_add_task_rejects_empty_title(make_room, task_store):
    made = make_room()
    with pytest.raises(ValueError):
        task_store.add_task(made["room"].id, title="  ", created_by=made["owner"]["user_id"])


def test_complete_task_and_status_filter(make_room, add_room_member, task_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    task = task_store.add_task(
        made["room"].id, title="Call seller", created_by=made["owner"]["user_id"]
    )
    done = task_store.complete_task(task.id, completed_by=member["user_id"])
    assert done.status == "done"
    assert done.completed_at is not None
    # Completing twice is a no-op.
    assert task_store.complete_task(task.id, completed_by=member["user_id"]).status == "done"
    assert (
        task_store.list_tasks(
            made["room"].id, viewer_user_id=made["owner"]["user_id"], status="open"
        )
        == []
    )
    assert (
        len(
            task_store.list_tasks(
                made["room"].id, viewer_user_id=made["owner"]["user_id"], status="done"
            )
        )
        == 1
    )
    assert (
        task_store.complete_task(
            "00000000-0000-0000-0000-000000000000", completed_by=made["owner"]["user_id"]
        )
        is None
    )


def test_due_soon_and_overdue(make_room, task_store):
    made = make_room()
    owner = made["owner"]["user_id"]
    soon = task_store.add_task(
        made["room"].id,
        title="Due soon",
        created_by=owner,
        due_at=datetime.now(UTC) + timedelta(hours=12),
    )
    later = task_store.add_task(
        made["room"].id,
        title="Due later",
        created_by=owner,
        due_at=datetime.now(UTC) + timedelta(days=5),
    )
    past = task_store.add_task(
        made["room"].id,
        title="Overdue",
        created_by=owner,
        due_at=datetime.now(UTC) - timedelta(hours=1),
    )
    task_store.add_task(made["room"].id, title="No deadline", created_by=owner)
    due = task_store.due_soon(made["room"].id, viewer_user_id=owner)
    assert {t.id for t in due} == {soon.id, past.id}
    assert {t.id for t in task_store.overdue(made["room"].id, viewer_user_id=owner)} == {past.id}
    assert later.id not in {t.id for t in due}


def test_leaving_unassigns_tasks(make_room, add_room_member, task_store, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    task = task_store.add_task(
        made["room"].id,
        title="Member task",
        created_by=made["owner"]["user_id"],
        owner_user_id=member["user_id"],
    )
    room_store.leave_room(made["room"].id, user_id=member["user_id"])
    remaining = task_store.list_tasks(made["room"].id, viewer_user_id=made["owner"]["user_id"])
    assert len(remaining) == 1
    assert remaining[0].id == task.id
    assert remaining[0].owner_user_id is None  # work stays, owner leaves


# --- money gate: two people, or the owner ---


def test_owner_alone_approves_money(make_room, money_gate):
    made = make_room()
    assert (
        money_gate.is_money_approved(
            made["room"].id, "pay-1", requester_user_id=made["owner"]["user_id"]
        )
        is True
    )


def test_member_needs_second_person(make_room, add_room_member, money_gate):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    other = add_room_member(made["room"].id, made["owner"]["user_id"])
    action = "pay-2"
    assert (
        money_gate.is_money_approved(made["room"].id, action, requester_user_id=member["user_id"])
        is False
    )
    money_gate.approve_money_action(made["room"].id, action, approver_user_id=member["user_id"])
    # Requester approved, but nobody else has: still not enough.
    assert (
        money_gate.is_money_approved(made["room"].id, action, requester_user_id=member["user_id"])
        is False
    )
    money_gate.approve_money_action(made["room"].id, action, approver_user_id=other["user_id"])
    assert (
        money_gate.is_money_approved(made["room"].id, action, requester_user_id=member["user_id"])
        is True
    )


def test_member_approval_without_requester_approval_is_not_enough(
    make_room, add_room_member, money_gate
):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    other = add_room_member(made["room"].id, made["owner"]["user_id"])
    action = "pay-3"
    money_gate.approve_money_action(made["room"].id, action, approver_user_id=other["user_id"])
    # The other member approved, but the requester has not: not approved.
    assert (
        money_gate.is_money_approved(made["room"].id, action, requester_user_id=member["user_id"])
        is False
    )


def test_guest_approval_never_counts(make_room, add_room_member, money_gate):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    guest = add_room_member(made["room"].id, made["owner"]["user_id"], role="guest")
    action = "pay-4"
    with pytest.raises(NotRoomMemberError):
        money_gate.approve_money_action(made["room"].id, action, approver_user_id=guest["user_id"])
    assert (
        money_gate.is_money_approved(made["room"].id, action, requester_user_id=guest["user_id"])
        is False
    )
    money_gate.approve_money_action(made["room"].id, action, approver_user_id=member["user_id"])
    # Only the requester approved; the guest cannot be the second person.
    assert (
        money_gate.is_money_approved(made["room"].id, action, requester_user_id=member["user_id"])
        is False
    )


def test_double_approval_counts_once(make_room, add_room_member, money_gate):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    action = "pay-5"
    first = money_gate.approve_money_action(
        made["room"].id, action, approver_user_id=member["user_id"]
    )
    second = money_gate.approve_money_action(
        made["room"].id, action, approver_user_id=member["user_id"]
    )
    assert first == 1
    assert second == 1
    assert money_gate.list_money_approvals(made["room"].id, action) == [member["user_id"]]


def test_leaving_removes_money_approvals(make_room, add_room_member, money_gate, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    other = add_room_member(made["room"].id, made["owner"]["user_id"])
    action = "pay-6"
    money_gate.approve_money_action(made["room"].id, action, approver_user_id=member["user_id"])
    money_gate.approve_money_action(made["room"].id, action, approver_user_id=other["user_id"])
    assert (
        money_gate.is_money_approved(made["room"].id, action, requester_user_id=member["user_id"])
        is True
    )
    room_store.leave_room(made["room"].id, user_id=other["user_id"])
    # The second approver left: the approval is gone with them.
    assert (
        money_gate.is_money_approved(made["room"].id, action, requester_user_id=member["user_id"])
        is False
    )
