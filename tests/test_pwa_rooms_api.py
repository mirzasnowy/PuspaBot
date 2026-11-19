"""API tests for the B9 room-board endpoints (``puspa/pwa/rooms_api.py``).

Covers the HTTP layer only (the delegation maths live in
``tests/test_rooms_delegation_b9.py``): a logged-in member gets
their rooms and the shared board, a non-member gets 403, an
anonymous caller gets 401, creating a task with delegation
lands on the board, and proposing + deciding a step goes
through the task owner.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in B9): the routes are mounted there with the app's own
``need_user`` login resolver, so the paths, login gate, status
codes, and JSON shapes tested here are the contract the live
PWA serves. The room schema is applied by the autouse fixture
from tests.rooms_helpers.
"""

from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room"]


def _client(user_id, test_dsn):
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    return TestClient(app)


def test_rooms_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/rooms" in paths
    assert "/api/rooms/{room_id}/tasks" in paths
    with _client(user_id, test_dsn) as client:
        assert client.get("/api/rooms").status_code == 200
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/rooms").status_code == 401
        assert (
            client.get("/api/rooms/00000000-0000-0000-0000-000000000000/tasks").status_code == 401
        )


def test_list_rooms_and_empty_board(make_room, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    with _client(owner, test_dsn) as client:
        rooms = client.get("/api/rooms").json()["rooms"]
        assert [r["id"] for r in rooms] == [made["room"].id]
        assert rooms[0]["role"] == "owner"
        assert rooms[0]["name"] == made["room"].name
        board = client.get(f"/api/rooms/{made['room'].id}/tasks").json()
        assert board["room_id"] == made["room"].id
        assert board["room_name"] == made["room"].name
        assert board["tasks"] == []


def test_board_forbidden_for_non_member(make_room, make_user, test_dsn):
    made = make_room()
    outsider = make_user()["user_id"]
    with _client(outsider, test_dsn) as client:
        assert client.get(f"/api/rooms/{made['room'].id}/tasks").status_code == 403
        assert client.get("/api/rooms").json()["rooms"] == []


def test_board_unknown_room_is_404(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        resp = client.get("/api/rooms/00000000-0000-0000-0000-000000000000/tasks")
        assert resp.status_code == 404


def test_create_task_with_teammate_delegation(make_room, add_room_member, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    member = add_room_member(made["room"].id, owner)
    with _client(owner, test_dsn) as client:
        resp = client.post(
            f"/api/rooms/{made['room'].id}/tasks",
            json={
                "title": "Compare prices",
                "delegate_kind": "teammate",
                "assignee_ref": member["user_id"],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["title"] == "Compare prices"
        assert body["owner_user_id"] == member["user_id"]
        assert body["status"] == "open"
        assert body["delegation"]["delegate_kind"] == "teammate"
        assert body["delegation"]["status"] == "delegated"
        assert body["pending_steps"] == 0
        board = client.get(f"/api/rooms/{made['room'].id}/tasks").json()
        assert [t["id"] for t in board["tasks"]] == [body["id"]]
        # Bad input is a 400, not a 500.
        assert (
            client.post(f"/api/rooms/{made['room'].id}/tasks", json={"title": "  "}).status_code
            == 400
        )
        assert (
            client.post(
                f"/api/rooms/{made['room'].id}/tasks",
                json={"title": "x", "delegate_kind": "robot", "assignee_ref": "y"},
            ).status_code
            == 400
        )


def test_create_task_with_agent_delegation(make_room, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    with _client(owner, test_dsn) as client:
        resp = client.post(
            f"/api/rooms/{made['room'].id}/tasks",
            json={
                "title": "Research sellers",
                "delegate_kind": "agent",
                "assignee_ref": "sub-abc123",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["delegation"]["delegate_kind"] == "agent"
        assert resp.json()["delegation"]["assignee_ref"] == "sub-abc123"


def test_propose_and_decide_step(make_room, add_room_member, test_dsn):
    made = make_room()
    owner = made["owner"]["user_id"]
    member = add_room_member(made["room"].id, owner)
    with _client(owner, test_dsn) as client:
        task = client.post(
            f"/api/rooms/{made['room'].id}/tasks",
            json={
                "title": "Send shortlist",
                "delegate_kind": "agent",
                "assignee_ref": "sub-abc123",
            },
        ).json()
        step = client.post(
            f"/api/rooms/{made['room'].id}/tasks/{task['id']}/steps",
            json={
                "description": "send the shortlist to the group",
                "tool_name": "gmail.send",
                "irreversible": True,
            },
        )
        assert step.status_code == 200, step.text
        assert step.json()["status"] == "awaiting_approval"
        step_id = step.json()["id"]
        # A member who is not the task owner may not approve it.
        with _client(member["user_id"], test_dsn) as member_client:
            assert (
                member_client.post(
                    f"/api/rooms/{made['room'].id}/tasks/{task['id']}/steps/{step_id}/approve"
                ).status_code
                == 403
            )
        approved = client.post(
            f"/api/rooms/{made['room'].id}/tasks/{task['id']}/steps/{step_id}/approve"
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"
        board = client.get(f"/api/rooms/{made['room'].id}/tasks").json()
        assert board["tasks"][0]["pending_steps"] == 0
