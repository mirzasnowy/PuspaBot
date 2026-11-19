"""Rooms: create/delete, roles, membership, leaving (B6).

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). The room schema is
applied by the autouse fixture from tests.rooms_helpers.
"""

import pytest

from puspa.rooms.errors import (
    LastOwnerError,
    NotRoomOwnerError,
    RoomFullError,
    RoomNotFoundError,
)
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room, room_store

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room", "room_store"]


def test_create_room_makes_creator_first_owner(room_store, make_user):
    owner = make_user()
    room = room_store.create_room("  Family trip  ", owner["user_id"])
    assert room.name == "Family trip"  # trimmed
    assert room.created_by == owner["user_id"]
    assert room_store.role_of(room.id, owner["user_id"]) == "owner"
    assert room_store.member_count(room.id) == 1


def test_create_room_rejects_empty_name(room_store, make_user):
    owner = make_user()
    with pytest.raises(ValueError):
        room_store.create_room("   ", owner["user_id"])


def test_get_room_unknown_returns_none(room_store):
    assert room_store.get_room("00000000-0000-0000-0000-000000000000") is None


def test_require_room_unknown_raises(room_store):
    with pytest.raises(RoomNotFoundError):
        room_store.require_room("00000000-0000-0000-0000-000000000000")


def test_add_member_by_owner_and_list_order(make_room, add_room_member, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    guest = add_room_member(made["room"].id, made["owner"]["user_id"], role="guest")
    members = room_store.list_members(made["room"].id)
    assert [m.role for m in members] == ["owner", "member", "guest"]
    assert [m.user_id for m in members] == [
        made["owner"]["user_id"],
        member["user_id"],
        guest["user_id"],
    ]


def test_add_member_by_non_owner_is_refused(make_room, make_user, room_store):
    made = make_room()
    outsider = make_user()
    with pytest.raises(NotRoomOwnerError):
        room_store.add_member(made["room"].id, outsider["user_id"], added_by=outsider["user_id"])


def test_add_member_rejects_unknown_role(make_room, make_user, room_store):
    made = make_room()
    newcomer = make_user()
    with pytest.raises(ValueError):
        room_store.add_member(
            made["room"].id, newcomer["user_id"], added_by=made["owner"]["user_id"], role="admin"
        )


def test_add_member_is_idempotent_for_same_role(make_room, add_room_member, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    again = room_store.add_member(
        made["room"].id, member["user_id"], added_by=made["owner"]["user_id"], role="member"
    )
    assert again.role == "member"
    assert room_store.member_count(made["room"].id) == 2


def test_room_cap_is_five_members(make_room, make_user, room_store):
    made = make_room()
    room_id = made["room"].id
    for _ in range(4):  # owner + 4 = 5, the beta-lite cap
        newcomer = make_user()
        room_store.add_member(room_id, newcomer["user_id"], added_by=made["owner"]["user_id"])
    assert room_store.member_count(room_id) == 5
    sixth = make_user()
    with pytest.raises(RoomFullError):
        room_store.add_member(room_id, sixth["user_id"], added_by=made["owner"]["user_id"])
    assert room_store.member_count(room_id) == 5


def test_set_role_promote_and_demote(make_room, add_room_member, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    promoted = room_store.set_role(
        made["room"].id, member["user_id"], "owner", changed_by=made["owner"]["user_id"]
    )
    assert promoted.role == "owner"
    demoted = room_store.set_role(
        made["room"].id, member["user_id"], "guest", changed_by=made["owner"]["user_id"]
    )
    assert demoted.role == "guest"


def test_set_role_cannot_demote_last_owner(make_room, room_store):
    made = make_room()
    with pytest.raises(LastOwnerError):
        room_store.set_role(
            made["room"].id,
            made["owner"]["user_id"],
            "member",
            changed_by=made["owner"]["user_id"],
        )


def test_set_role_by_member_is_refused(make_room, add_room_member, make_user, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    other = make_user()
    with pytest.raises(NotRoomOwnerError):
        room_store.set_role(
            made["room"].id, other["user_id"], "member", changed_by=member["user_id"]
        )


def test_guest_cannot_invite(make_room, add_room_member, room_store):
    made = make_room()
    guest = add_room_member(made["room"].id, made["owner"]["user_id"], role="guest")
    assert room_store.can_invite(made["room"].id, guest["user_id"]) is False
    assert room_store.can_invite(made["room"].id, made["owner"]["user_id"]) is True


def test_leave_removes_membership(make_room, add_room_member, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    room_store.leave_room(made["room"].id, user_id=member["user_id"])
    assert room_store.is_member(made["room"].id, member["user_id"]) is False
    assert room_store.member_count(made["room"].id) == 1


def test_leave_is_idempotent_for_non_member(make_room, make_user, room_store):
    made = make_room()
    outsider = make_user()
    room_store.leave_room(made["room"].id, user_id=outsider["user_id"])  # no error
    assert room_store.member_count(made["room"].id) == 1


def test_last_owner_cannot_leave(make_room, room_store):
    made = make_room()
    with pytest.raises(LastOwnerError):
        room_store.leave_room(made["room"].id, user_id=made["owner"]["user_id"])


def test_owner_can_leave_when_another_owner_remains(make_room, add_room_member, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    room_store.set_role(
        made["room"].id, member["user_id"], "owner", changed_by=made["owner"]["user_id"]
    )
    room_store.leave_room(made["room"].id, user_id=made["owner"]["user_id"])
    assert room_store.is_member(made["room"].id, made["owner"]["user_id"]) is False
    assert room_store.is_owner(made["room"].id, member["user_id"]) is True


def test_remove_member_by_owner(make_room, add_room_member, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    room_store.remove_member(
        made["room"].id, member["user_id"], removed_by=made["owner"]["user_id"]
    )
    assert room_store.is_member(made["room"].id, member["user_id"]) is False


def test_remove_member_by_non_owner_is_refused(make_room, add_room_member, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    other = add_room_member(made["room"].id, made["owner"]["user_id"])
    with pytest.raises(NotRoomOwnerError):
        room_store.remove_member(made["room"].id, member["user_id"], removed_by=other["user_id"])


def test_delete_room_by_owner_cascades(make_room, add_room_member, room_store):
    made = make_room()
    add_room_member(made["room"].id, made["owner"]["user_id"])
    room_store.delete_room(made["room"].id, requested_by=made["owner"]["user_id"])
    assert room_store.get_room(made["room"].id) is None
    assert room_store.list_rooms_for_user(made["owner"]["user_id"]) == []


def test_delete_room_by_member_is_refused(make_room, add_room_member, room_store):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    with pytest.raises(NotRoomOwnerError):
        room_store.delete_room(made["room"].id, requested_by=member["user_id"])
    assert room_store.get_room(made["room"].id) is not None


def test_list_rooms_for_user(make_room, room_store):
    first = make_room("one")
    second = make_room("two")
    rooms = room_store.list_rooms_for_user(first["owner"]["user_id"])
    assert [r.id for r in rooms] == [first["room"].id]
    assert room_store.list_rooms_for_user(second["owner"]["user_id"])[0].id == second["room"].id
