"""Room invites: link tokens and one-time codes, hash-only (B6).

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). The room schema is
applied by the autouse fixture from tests.rooms_helpers.
"""

import hashlib

import pytest

from puspa.rooms import RoomInviteStore
from puspa.rooms.errors import InviteError, NotRoomMemberError, RoomFullError
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room, room_store

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room", "room_store"]


@pytest.fixture
def invite_store(test_dsn):
    return RoomInviteStore(test_dsn)


def test_issue_link_returns_token_once_and_stores_only_hash(test_dsn, invite_store, make_room):
    made = make_room()
    issued = invite_store.issue_invite_link(
        made["room"].id, inviter_user_id=made["owner"]["user_id"]
    )
    assert issued["token"]
    assert issued["invite_id"]
    assert issued["join_link"].endswith("/" + issued["token"])
    assert issued["join_link"].startswith("https://puspa.app/join/")
    assert issued["expires_at"] is not None
    invite = invite_store.get_invite(issued["invite_id"])
    assert invite.status == "pending"
    assert invite.kind == "link"
    assert invite.role == "member"
    # Only the hash is in the database — the raw token is nowhere.
    import psycopg

    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT token_hash FROM room_invites WHERE id = %s",
            (issued["invite_id"],),
        )
        stored_hash = cur.fetchone()[0]
    assert stored_hash == hashlib.sha256(issued["token"].encode()).hexdigest()
    assert stored_hash != issued["token"]


def test_issue_code_returns_six_digit_code_and_stores_only_hash(test_dsn, invite_store, make_room):
    made = make_room()
    issued = invite_store.issue_invite_code(
        made["room"].id, inviter_user_id=made["owner"]["user_id"], role="guest"
    )
    assert len(issued["code"]) == 6
    assert issued["code"].isdigit()
    invite = invite_store.get_invite(issued["invite_id"])
    assert invite.kind == "code"
    assert invite.role == "guest"
    import psycopg

    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT token_hash FROM room_invites WHERE id = %s",
            (issued["invite_id"],),
        )
        stored_hash = cur.fetchone()[0]
    assert stored_hash == hashlib.sha256(issued["code"].encode()).hexdigest()
    assert stored_hash != issued["code"]


def test_issue_by_guest_is_refused(invite_store, make_room, add_room_member):
    made = make_room()
    guest = add_room_member(made["room"].id, made["owner"]["user_id"], role="guest")
    with pytest.raises(NotRoomMemberError):
        invite_store.issue_invite_link(made["room"].id, inviter_user_id=guest["user_id"])
    with pytest.raises(NotRoomMemberError):
        invite_store.issue_invite_code(made["room"].id, inviter_user_id=guest["user_id"])


def test_issue_by_member_is_allowed(invite_store, make_room, add_room_member):
    made = make_room()
    member = add_room_member(made["room"].id, made["owner"]["user_id"])
    issued = invite_store.issue_invite_link(made["room"].id, inviter_user_id=member["user_id"])
    assert issued["invite_id"]


def test_issue_rejects_owner_role(invite_store, make_room):
    made = make_room()
    with pytest.raises(ValueError):
        invite_store.issue_invite_link(
            made["room"].id, inviter_user_id=made["owner"]["user_id"], role="owner"
        )


def test_accept_link_makes_invitee_a_member(invite_store, make_room, make_user, room_store):
    made = make_room()
    invitee = make_user()
    issued = invite_store.issue_invite_link(
        made["room"].id, inviter_user_id=made["owner"]["user_id"]
    )
    result = invite_store.accept_invite(issued["token"], user_id=invitee["user_id"])
    assert result["room_id"] == made["room"].id
    assert result["role"] == "member"
    assert result["already_member"] is False
    assert invite_store.get_invite(issued["invite_id"]).status == "accepted"
    assert room_store.role_of(made["room"].id, invitee["user_id"]) == "member"


def test_accept_code_makes_invitee_a_guest(invite_store, make_room, make_user, room_store):
    made = make_room()
    invitee = make_user()
    issued = invite_store.issue_invite_code(
        made["room"].id, inviter_user_id=made["owner"]["user_id"], role="guest"
    )
    result = invite_store.accept_invite_code(issued["code"], user_id=invitee["user_id"])
    assert result["role"] == "guest"
    assert room_store.role_of(made["room"].id, invitee["user_id"]) == "guest"


def test_accept_is_idempotent_for_same_user(invite_store, make_room, make_user, room_store):
    made = make_room()
    invitee = make_user()
    issued = invite_store.issue_invite_link(
        made["room"].id, inviter_user_id=made["owner"]["user_id"]
    )
    first = invite_store.accept_invite(issued["token"], user_id=invitee["user_id"])
    second = invite_store.accept_invite(issued["token"], user_id=invitee["user_id"])
    assert first["already_member"] is False
    assert second["already_member"] is True
    assert room_store.member_count(made["room"].id) == 2


def test_accept_consumed_token_by_other_user_is_refused(invite_store, make_room, make_user):
    made = make_room()
    invitee = make_user()
    other = make_user()
    issued = invite_store.issue_invite_link(
        made["room"].id, inviter_user_id=made["owner"]["user_id"]
    )
    invite_store.accept_invite(issued["token"], user_id=invitee["user_id"])
    with pytest.raises(InviteError):
        invite_store.accept_invite(issued["token"], user_id=other["user_id"])


def test_accept_unknown_token_is_refused(invite_store, make_user):
    user = make_user()
    with pytest.raises(InviteError):
        invite_store.accept_invite("no-such-token", user_id=user["user_id"])
    with pytest.raises(InviteError):
        invite_store.accept_invite_code("123456", user_id=user["user_id"])


def test_accept_code_with_wrong_kind_is_refused(invite_store, make_room, make_user):
    # A link token presented as a code (and vice versa) must not work:
    # the lookup is scoped by kind.
    made = make_room()
    invitee = make_user()
    issued = invite_store.issue_invite_link(
        made["room"].id, inviter_user_id=made["owner"]["user_id"]
    )
    with pytest.raises(InviteError):
        invite_store.accept_invite_code(issued["token"], user_id=invitee["user_id"])


def test_revoke_kills_pending_invite(invite_store, make_room, make_user):
    made = make_room()
    invitee = make_user()
    issued = invite_store.issue_invite_link(
        made["room"].id, inviter_user_id=made["owner"]["user_id"]
    )
    revoked = invite_store.revoke_invite(issued["invite_id"], revoked_by=made["owner"]["user_id"])
    assert revoked.status == "revoked"
    with pytest.raises(InviteError):
        invite_store.accept_invite(issued["token"], user_id=invitee["user_id"])


def test_revoke_by_guest_is_refused(invite_store, make_room, add_room_member):
    made = make_room()
    guest = add_room_member(made["room"].id, made["owner"]["user_id"], role="guest")
    issued = invite_store.issue_invite_link(
        made["room"].id, inviter_user_id=made["owner"]["user_id"]
    )
    with pytest.raises(NotRoomMemberError):
        invite_store.revoke_invite(issued["invite_id"], revoked_by=guest["user_id"])


def test_expired_invite_is_marked_and_refused(test_dsn, invite_store, make_room, make_user):
    made = make_room()
    invitee = make_user()
    issued = invite_store.issue_invite_link(
        made["room"].id, inviter_user_id=made["owner"]["user_id"], ttl_days=1
    )
    import psycopg

    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "UPDATE room_invites SET expires_at = now() - interval '1 hour' WHERE id = %s",
            (issued["invite_id"],),
        )
        conn.commit()
    with pytest.raises(InviteError, match="expired"):
        invite_store.accept_invite(issued["token"], user_id=invitee["user_id"])
    assert invite_store.get_invite(issued["invite_id"]).status == "expired"
    # Still expired on a second attempt — never usable again.
    with pytest.raises(InviteError):
        invite_store.accept_invite(issued["token"], user_id=invitee["user_id"])


def test_accept_into_full_room_is_refused_and_invite_stays_pending(
    invite_store, make_room, make_user, room_store
):
    made = make_room()
    room_id = made["room"].id
    for _ in range(4):  # owner + 4 = 5, the beta-lite cap
        newcomer = make_user()
        room_store.add_member(room_id, newcomer["user_id"], added_by=made["owner"]["user_id"])
    issued = invite_store.issue_invite_link(room_id, inviter_user_id=made["owner"]["user_id"])
    latecomer = make_user()
    with pytest.raises(RoomFullError):
        invite_store.accept_invite(issued["token"], user_id=latecomer["user_id"])
    assert invite_store.get_invite(issued["invite_id"]).status == "pending"


def test_purge_expired_marks_only_pending_past_expiry(test_dsn, invite_store, make_room):
    made = make_room()
    issued = invite_store.issue_invite_link(
        made["room"].id, inviter_user_id=made["owner"]["user_id"], ttl_days=1
    )
    import psycopg

    with psycopg.connect(test_dsn) as conn:
        conn.execute(
            "UPDATE room_invites SET expires_at = now() - interval '1 hour' WHERE id = %s",
            (issued["invite_id"],),
        )
        conn.commit()
    assert invite_store.purge_expired() == 1
    assert invite_store.get_invite(issued["invite_id"]).status == "expired"
    assert invite_store.purge_expired() == 0


def test_list_invites_newest_first(invite_store, make_room):
    made = make_room()
    first = invite_store.issue_invite_link(
        made["room"].id, inviter_user_id=made["owner"]["user_id"]
    )
    second = invite_store.issue_invite_code(
        made["room"].id, inviter_user_id=made["owner"]["user_id"]
    )
    invites = invite_store.list_invites(made["room"].id)
    assert [i.id for i in invites] == [second["invite_id"], first["invite_id"]]
