"""API tests for shared rooms lite (P-05, plan section 12.1).

Covers the HTTP layer in ``puspa/pwa/rooms_membership_api.py``
against the real ``puspa.pwa.app.create_app``: creating a room,
its detail with members, invites by link and by one-time code
(hash-only, single use, revocation, room-scoped tokens), roles
(owner/member/guest read-only), leaving and removal (a removed
member loses access at once — every later read is a 403, and
their private rows are gone), room memory in its three layers
with "bring to room" as the only explicit bridge from personal
memory, the per-room job queue over HTTP, and room activity on
the existing SSE stream.

The service-level rules live in ``tests/test_rooms*.py`` and
``tests/test_rooms_queue.py``; these tests prove the HTTP layer
enforces them with the right status codes.
"""

import json

import pytest
from fastapi.testclient import TestClient

from puspa.memory import profile as profile_module
from puspa.pwa.app import create_app
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room"]


@pytest.fixture(autouse=True)
def _queue_schema(test_dsn):
    """Apply migration 0024 (the queue table). The room fixture
    applies only 0010; the test database already records 0023
    (the memory loader runs in conftest), so only 0024 lands."""
    from puspa.rooms.job_queue import create_schema

    create_schema(test_dsn)


UNKNOWN_ROOM = "00000000-0000-0000-0000-000000000000"


def _client(user_id, test_dsn):
    return TestClient(create_app(test_user_id=user_id, dsn=test_dsn))


def _sse_events(client):
    """Room-activity frames from GET /sse, parsed as dicts."""
    resp = client.get("/sse")
    assert resp.status_code == 200, resp.text
    events = []
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return [e for e in events if e.get("type") == "room_activity"]


def test_routes_exist_and_require_login(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    for path in (
        "/api/rooms",
        "/api/rooms/{room_id}",
        "/api/rooms/join",
        "/api/rooms/{room_id}/invites",
        "/api/rooms/{room_id}/invites/{invite_id}/revoke",
        "/api/rooms/{room_id}/leave",
        "/api/rooms/{room_id}/members/{member_id}/role",
        "/api/rooms/{room_id}/members/{member_id}/remove",
        "/api/rooms/{room_id}/memory",
        "/api/rooms/{room_id}/memory/bring",
        "/api/rooms/{room_id}/memory/{memory_id}",
        "/api/rooms/{room_id}/jobs",
        "/api/rooms/{room_id}/jobs/{job_id}/run",
    ):
        assert path in paths, path
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.post("/api/rooms", json={"name": "x"}).status_code == 401
        assert client.get(f"/api/rooms/{UNKNOWN_ROOM}").status_code == 401
        assert client.post("/api/rooms/join", json={"token": "x"}).status_code == 401


def test_create_room_and_detail(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        assert client.post("/api/rooms", json={"name": "  "}).status_code == 400
        resp = client.post("/api/rooms", json={"name": "Keluarga"})
        assert resp.status_code == 200, resp.text
        room = resp.json()
        assert room["name"] == "Keluarga"
        assert room["role"] == "owner"
        detail = client.get(f"/api/rooms/{room['id']}").json()
        assert detail["role"] == "owner"
        assert [(m["user_id"], m["role"]) for m in detail["members"]] == [(user_id, "owner")]
        assert client.get(f"/api/rooms/{UNKNOWN_ROOM}").status_code == 404


def test_invite_link_join_and_single_use(make_room, make_user, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    with _client(owner, test_dsn) as client:
        issued = client.post(
            f"/api/rooms/{room_id}/invites", json={"kind": "link", "role": "member"}
        )
        assert issued.status_code == 200, issued.text
        token = issued.json()["token"]
        assert issued.json()["join_link"].endswith(f"/{token}")
        # The list never leaks the secret.
        listed = client.get(f"/api/rooms/{room_id}/invites").json()["invites"]
        assert len(listed) == 1
        assert "token" not in listed[0] and "code" not in listed[0]
    joiner = make_user()["user_id"]
    with _client(joiner, test_dsn) as join_client:
        joined = join_client.post("/api/rooms/join", json={"token": token})
        assert joined.status_code == 200, joined.text
        assert joined.json()["room_id"] == room_id
        assert joined.json()["role"] == "member"
        assert joined.json()["already_member"] is False
        # The same user accepting again is idempotent.
        again = join_client.post("/api/rooms/join", json={"token": token})
        assert again.status_code == 200
        assert again.json()["already_member"] is True
    other = make_user()["user_id"]
    with _client(other, test_dsn) as other_client:
        # The token is spent: a second user cannot use it.
        assert other_client.post("/api/rooms/join", json={"token": token}).status_code == 400


def test_invite_code_join_as_guest_and_revoke(make_room, make_user, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    with _client(owner, test_dsn) as client:
        issued = client.post(
            f"/api/rooms/{room_id}/invites", json={"kind": "code", "role": "guest"}
        ).json()
        code = issued["code"]
        assert len(code) == 6 and code.isdigit()
        joiner = make_user()["user_id"]
        with _client(joiner, test_dsn) as join_client:
            assert join_client.post("/api/rooms/join", json={"code": "000000"}).status_code == 400
            joined = join_client.post("/api/rooms/join", json={"code": code})
            assert joined.status_code == 200, joined.text
            assert joined.json()["role"] == "guest"
        # A revoked invite can never be used.
        second = client.post(
            f"/api/rooms/{room_id}/invites", json={"kind": "code", "role": "member"}
        ).json()
        revoked = client.post(f"/api/rooms/{room_id}/invites/{second['invite_id']}/revoke")
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["status"] == "revoked"
        # Revoking an unknown invite id is a 404, not a 500.
        assert client.post(f"/api/rooms/{room_id}/invites/{UNKNOWN_ROOM}/revoke").status_code == 404
        late = make_user()["user_id"]
        with _client(late, test_dsn) as late_client:
            assert (
                late_client.post("/api/rooms/join", json={"code": second["code"]}).status_code
                == 400
            )


def test_guest_is_read_only(make_room, add_room_member, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    guest = add_room_member(room_id, owner, role="guest")
    with _client(guest["user_id"], test_dsn) as guest_client:
        # Guests may read the room and its board...
        assert guest_client.get(f"/api/rooms/{room_id}").status_code == 200
        assert guest_client.get(f"/api/rooms/{room_id}/tasks").status_code == 200
        # ...but never write to it.
        assert (
            guest_client.post(f"/api/rooms/{room_id}/invites", json={"kind": "link"}).status_code
            == 403
        )
        assert (
            guest_client.post(f"/api/rooms/{room_id}/tasks", json={"title": "x"}).status_code == 403
        )
        assert (
            guest_client.post(
                f"/api/rooms/{room_id}/memory",
                json={"kind": "note", "content": "x", "visibility": "room"},
            ).status_code
            == 403
        )
        assert (
            guest_client.post(
                f"/api/rooms/{room_id}/jobs",
                json={"kind": "note", "payload": {"content": "x"}},
            ).status_code
            == 403
        )


def test_roles_and_last_owner_guard(make_room, add_room_member, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    member = add_room_member(room_id, owner)
    with _client(member["user_id"], test_dsn) as member_client:
        # A member may not change roles.
        assert (
            member_client.post(
                f"/api/rooms/{room_id}/members/{member['user_id']}/role",
                json={"role": "guest"},
            ).status_code
            == 403
        )
    with _client(owner, test_dsn) as owner_client:
        # The owner promotes the member; the last owner cannot be demoted.
        promoted = owner_client.post(
            f"/api/rooms/{room_id}/members/{member['user_id']}/role", json={"role": "owner"}
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["role"] == "owner"
        assert (
            owner_client.post(
                f"/api/rooms/{room_id}/members/{owner}/role", json={"role": "member"}
            ).status_code
            == 200
        )
    with _client(member["user_id"], test_dsn) as member_client:
        assert (
            member_client.post(
                f"/api/rooms/{room_id}/members/{member['user_id']}/role",
                json={"role": "member"},
            ).status_code
            == 400
        )


def test_removed_member_loses_access_at_once(make_room, add_room_member, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    member = add_room_member(room_id, owner)
    member_id = member["user_id"]
    with _client(member_id, test_dsn) as member_client:
        # The member writes a room-visible note and a private note.
        member_client.post(
            f"/api/rooms/{room_id}/memory",
            json={"kind": "note", "content": "shared plan", "visibility": "room"},
        )
        member_client.post(
            f"/api/rooms/{room_id}/memory",
            json={"kind": "note", "content": "my secret", "visibility": "private"},
        )
        assert member_client.get(f"/api/rooms/{room_id}").status_code == 200
        with _client(owner, test_dsn) as owner_client:
            removed = owner_client.post(f"/api/rooms/{room_id}/members/{member_id}/remove")
            assert removed.status_code == 200, removed.text
        # Access is gone at once, on every read surface.
        for path in (
            f"/api/rooms/{room_id}",
            f"/api/rooms/{room_id}/tasks",
            f"/api/rooms/{room_id}/memory",
            f"/api/rooms/{room_id}/jobs",
        ):
            assert member_client.get(path).status_code == 403, path
        # ...and on every write surface.
        assert (
            member_client.post(f"/api/rooms/{room_id}/tasks", json={"title": "x"}).status_code
            == 403
        )
        # The SSE stream no longer carries this room's activity to them.
        assert _sse_events(member_client) == []
    with _client(owner, test_dsn) as owner_client:
        # The private note left with the member; the shared note stays.
        memories = owner_client.get(f"/api/rooms/{room_id}/memory").json()["memories"]
        assert [m["content"] for m in memories] == ["shared plan"]
        detail = owner_client.get(f"/api/rooms/{room_id}").json()
        assert [m["user_id"] for m in detail["members"]] == [owner]


def test_leave_and_delete(make_room, add_room_member, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    member = add_room_member(room_id, owner)
    with _client(member["user_id"], test_dsn) as member_client:
        assert member_client.post(f"/api/rooms/{room_id}/leave").status_code == 200
        assert member_client.get(f"/api/rooms/{room_id}").status_code == 403
        # A member may not delete the room.
        assert member_client.delete(f"/api/rooms/{room_id}").status_code == 403
    with _client(owner, test_dsn) as owner_client:
        # The last owner cannot leave; they delete the room instead.
        assert owner_client.post(f"/api/rooms/{room_id}/leave").status_code == 400
        assert owner_client.delete(f"/api/rooms/{room_id}").status_code == 200
        assert owner_client.get(f"/api/rooms/{room_id}").status_code == 404


def test_room_memory_layers_and_delete_scoping(make_room, add_room_member, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    member = add_room_member(room_id, owner)
    with _client(owner, test_dsn) as owner_client:
        owner_client.post(
            f"/api/rooms/{room_id}/memory",
            json={"kind": "note", "content": "room note", "visibility": "room"},
        )
        private = owner_client.post(
            f"/api/rooms/{room_id}/memory",
            json={"kind": "note", "content": "owner private", "visibility": "private"},
        ).json()
    with _client(member["user_id"], test_dsn) as member_client:
        seen_by_member = member_client.get(f"/api/rooms/{room_id}/memory").json()["memories"]
        assert [m["content"] for m in seen_by_member] == ["room note"]
        # A member may not delete the owner's private row, even by id.
        assert (
            member_client.delete(f"/api/rooms/{room_id}/memory/{private['id']}").status_code == 404
        )
    # A memory from another room cannot be deleted through this room.
    other = make_room()
    with _client(other["owner"]["user_id"], test_dsn) as other_client:
        other_row = other_client.post(
            f"/api/rooms/{other['room'].id}/memory",
            json={"kind": "note", "content": "other room", "visibility": "room"},
        ).json()
    with _client(owner, test_dsn) as owner_client:
        assert (
            owner_client.delete(f"/api/rooms/{room_id}/memory/{other_row['id']}").status_code == 404
        )
        assert (
            owner_client.delete(f"/api/rooms/{room_id}/memory/{private['id']}").status_code == 200
        )
        # Deleting through an unknown room is a 404, not a 500.
        assert (
            owner_client.delete(f"/api/rooms/{UNKNOWN_ROOM}/memory/{private['id']}").status_code
            == 404
        )


def test_bring_personal_memory_to_room(make_room, add_room_member, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    fact = profile_module.add_fact(owner, "hometown", "Karawang", "user_stated", dsn=test_dsn)
    member = add_room_member(room_id, owner)
    with _client(owner, test_dsn) as client:
        resp = client.post(f"/api/rooms/{room_id}/memory/bring", json={"fact_id": fact["id"]})
        assert resp.status_code == 200, resp.text
        brought = resp.json()
        assert brought["visibility"] == "room"
        assert brought["content"].startswith("Karawang")
        assert "(Dibawa dari memori pribadi saya.)" in brought["content"]
        assert brought["brought_from_fact_id"] == fact["id"]
    with _client(member["user_id"], test_dsn) as member_client:
        # Everyone in the room can read what was brought in.
        memories = member_client.get(f"/api/rooms/{room_id}/memory").json()["memories"]
        assert len(memories) == 1
        assert memories[0]["content"].startswith("Karawang")
    with _client(owner, test_dsn) as client:
        # Another member's fact cannot be brought, and neither can a
        # fact that does not exist.
        other_fact = profile_module.add_fact(
            member["user_id"], "hometown", "Jakarta", "user_stated", dsn=test_dsn
        )
        assert (
            client.post(
                f"/api/rooms/{room_id}/memory/bring", json={"fact_id": other_fact["id"]}
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/rooms/{room_id}/memory/bring",
                json={"fact_id": "00000000-0000-0000-0000-000000000000"},
            ).status_code
            == 404
        )


def test_jobs_over_http(make_room, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    with _client(owner, test_dsn) as client:
        assert (
            client.post(
                f"/api/rooms/{room_id}/jobs", json={"kind": "pay", "payload": {}}
            ).status_code
            == 400
        )
        enqueued = client.post(
            f"/api/rooms/{room_id}/jobs",
            json={
                "kind": "summarize",
                "payload": {
                    "messages": [
                        {"author": "A", "text": "Decision: meet Saturday"},
                        {"author": "B", "text": "TODO: bring snacks"},
                    ]
                },
            },
        )
        assert enqueued.status_code == 200, enqueued.text
        job = enqueued.json()
        assert job["status"] == "queued"
        listed = client.get(f"/api/rooms/{room_id}/jobs").json()["jobs"]
        assert [j["id"] for j in listed] == [job["id"]]
        finished = client.post(f"/api/rooms/{room_id}/jobs/{job['id']}/run")
        assert finished.status_code == 200, finished.text
        assert finished.json()["status"] == "done"
        assert "meet Saturday" in (finished.json()["result"] or "")
        memories = client.get(f"/api/rooms/{room_id}/memory").json()["memories"]
        assert len(memories) == 1 and memories[0]["kind"] == "summary"
        # A finished job cannot run again.
        assert client.post(f"/api/rooms/{room_id}/jobs/{job['id']}/run").status_code == 400


def test_full_room_refuses_join(make_room, add_room_member, make_user, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    for _ in range(4):
        add_room_member(room_id, owner)
    with _client(owner, test_dsn) as client:
        token = client.post(
            f"/api/rooms/{room_id}/invites", json={"kind": "link", "role": "member"}
        ).json()["token"]
    sixth = make_user()["user_id"]
    with _client(sixth, test_dsn) as sixth_client:
        assert sixth_client.post("/api/rooms/join", json={"token": token}).status_code == 400


def test_sse_carries_room_activity(make_user, test_dsn):
    owner = make_user()["user_id"]
    with _client(owner, test_dsn) as client:
        room_id = client.post("/api/rooms", json={"name": "Arisan"}).json()["id"]
        client.post(
            f"/api/rooms/{room_id}/memory",
            json={"kind": "note", "content": "hello room", "visibility": "room"},
        )
        events = _sse_events(client)
        kinds = [e["room_activity"]["kind"] for e in events]
        assert "memory_added" in kinds
        assert "room_created" in kinds
        assert all(e["room_activity"]["room_id"] == room_id for e in events)
        # No invite token ever leaks into an activity event.
        assert "token" not in json.dumps(events)
