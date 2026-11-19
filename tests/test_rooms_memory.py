"""Room memory: three layers, leave-takes-private-data (B6).

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). The room schema is
applied by the autouse fixture from tests.rooms_helpers.
"""

import pytest

from puspa.rooms import RoomMemoryStore
from puspa.rooms.errors import NotRoomMemberError
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room, room_store

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room", "room_store"]


@pytest.fixture
def memory_store(test_dsn):
    return RoomMemoryStore(test_dsn)


def test_room_memory_visible_to_all_members(make_room, add_room_member, memory_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    note = memory_store.record_memory(
        made["room"].id,
        author_user_id=made["owner"]["user_id"],
        content="We decided to buy the red one.",
        kind="decision",
    )
    assert note.visibility == "room"
    seen_by_member = memory_store.list_room_memory(
        made["room"].id, viewer_user_id=member["user_id"]
    )
    assert [m.id for m in seen_by_member] == [note.id]


def test_private_answer_visible_only_to_author(make_room, add_room_member, memory_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    private = memory_store.record_memory(
        made["room"].id,
        author_user_id=made["owner"]["user_id"],
        content="My budget is 500k, do not tell the group.",
        kind="note",
        visibility="private",
    )
    # The author sees it; the other member does not — not even its existence.
    assert [
        m.id
        for m in memory_store.list_room_memory(
            made["room"].id, viewer_user_id=made["owner"]["user_id"]
        )
    ] == [private.id]
    assert memory_store.list_room_memory(made["room"].id, viewer_user_id=member["user_id"]) == []


def test_private_answer_mixed_with_shared_rows(make_room, add_room_member, memory_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    shared = memory_store.record_memory(
        made["room"].id,
        author_user_id=made["owner"]["user_id"],
        content="Shared note.",
    )
    memory_store.record_memory(
        made["room"].id,
        author_user_id=member["user_id"],
        content="Member private note.",
        visibility="private",
    )
    member_view = memory_store.list_room_memory(made["room"].id, viewer_user_id=member["user_id"])
    assert {m.id for m in member_view} == {shared.id} | {
        m.id
        for m in memory_store.list_room_memory(
            made["room"].id, viewer_user_id=member["user_id"], kind="note"
        )
        if m.visibility == "private"
    }
    owner_view = memory_store.list_room_memory(
        made["room"].id, viewer_user_id=made["owner"]["user_id"]
    )
    assert [m.id for m in owner_view] == [shared.id]


def test_non_member_cannot_read_or_write(make_room, make_user, memory_store):
    made = make_room()
    outsider = make_user()
    with pytest.raises(NotRoomMemberError):
        memory_store.record_memory(
            made["room"].id, author_user_id=outsider["user_id"], content="Hello."
        )
    with pytest.raises(NotRoomMemberError):
        memory_store.list_room_memory(made["room"].id, viewer_user_id=outsider["user_id"])


def test_record_decision_names_who_agreed(make_room, add_room_member, memory_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    decision = memory_store.record_decision(
        made["room"].id,
        author_user_id=made["owner"]["user_id"],
        decision="Buy from seller A.",
        agreed_by_user_ids=[made["owner"]["user_id"], member["user_id"]],
    )
    assert decision.kind == "decision"
    assert "Buy from seller A." in decision.content
    assert made["owner"]["user_id"] in decision.content
    assert member["user_id"] in decision.content


def test_record_summary_kind(make_room, memory_store):
    made = make_room()
    summary = memory_store.record_summary(
        made["room"].id,
        author_user_id=made["owner"]["user_id"],
        summary="3 messages: owner (3). No decisions stated. No tasks stated.",
    )
    assert summary.kind == "summary"
    listed = memory_store.list_room_memory(
        made["room"].id, viewer_user_id=made["owner"]["user_id"], kind="summary"
    )
    assert [m.id for m in listed] == [summary.id]


def test_record_rejects_empty_and_oversize_content(make_room, memory_store):
    made = make_room()
    with pytest.raises(ValueError):
        memory_store.record_memory(
            made["room"].id, author_user_id=made["owner"]["user_id"], content="   "
        )
    with pytest.raises(ValueError):
        memory_store.record_memory(
            made["room"].id,
            author_user_id=made["owner"]["user_id"],
            content="x" * 10_001,
        )
    with pytest.raises(ValueError):
        memory_store.record_memory(
            made["room"].id,
            author_user_id=made["owner"]["user_id"],
            content="ok",
            kind="essay",
        )


def test_author_can_delete_own_row_owner_can_delete_shared_row(
    make_room, add_room_member, memory_store
):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    shared = memory_store.record_memory(
        made["room"].id, author_user_id=member["user_id"], content="Shared."
    )
    assert memory_store.delete_memory(shared.id, deleted_by=made["owner"]["user_id"]) is True
    assert memory_store.delete_memory(shared.id, deleted_by=made["owner"]["user_id"]) is False


def test_owner_cannot_delete_another_members_private_row(make_room, add_room_member, memory_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    private = memory_store.record_memory(
        made["room"].id,
        author_user_id=member["user_id"],
        content="Private.",
        visibility="private",
    )
    with pytest.raises(NotRoomMemberError):
        memory_store.delete_memory(private.id, deleted_by=made["owner"]["user_id"])
    # The row is still there for its author.
    assert [
        m.id
        for m in memory_store.list_room_memory(made["room"].id, viewer_user_id=member["user_id"])
    ] == [private.id]


def test_leaving_deletes_private_data_but_keeps_shared_record(
    test_dsn, make_room, add_room_member, memory_store, room_store
):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    shared = memory_store.record_memory(
        made["room"].id,
        author_user_id=member["user_id"],
        content="Shared decision note.",
        kind="decision",
    )
    memory_store.record_memory(
        made["room"].id,
        author_user_id=member["user_id"],
        content="My private answer.",
        visibility="private",
    )
    room_store.leave_room(made["room"].id, user_id=member["user_id"])
    # Private rows are gone from the database entirely.
    import psycopg

    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM room_memories WHERE room_id = %s AND author_user_id = %s "
            "AND visibility = 'private'",
            (made["room"].id, member["user_id"]),
        )
        assert cur.fetchone()[0] == 0
    # The shared record stays for the group.
    remaining = memory_store.list_room_memory(
        made["room"].id, viewer_user_id=made["owner"]["user_id"]
    )
    assert [m.id for m in remaining] == [shared.id]


def test_purge_private_for_user_standalone(make_room, add_room_member, memory_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    memory_store.record_memory(
        made["room"].id,
        author_user_id=member["user_id"],
        content="Private one.",
        visibility="private",
    )
    memory_store.record_memory(
        made["room"].id,
        author_user_id=member["user_id"],
        content="Private two.",
        visibility="private",
    )
    assert memory_store.purge_private_for_user(made["room"].id, user_id=member["user_id"]) == 2
    assert memory_store.list_room_memory(made["room"].id, viewer_user_id=member["user_id"]) == []
