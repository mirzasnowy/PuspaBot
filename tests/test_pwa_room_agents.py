"""API tests for inviting agents into rooms (PWA inbox, 24 Nov 2026).

Rooms used to have human members only (``room_members`` holds
UUID user ids). The inbox create-group flow invites the user's
own agents instead, so rooms gained ``room_agents`` (migration
0046) plus ``POST /api/rooms/{id}/agents`` and
``DELETE /api/rooms/{id}/agents/{agent_id}``, and the room detail
now lists its agents with names.

Rules tested here, against the real ``create_app``: only the
room owner may invite/remove; only the caller's own agents may
be invited (another user's agent is 403, an unknown agent is
400); re-inviting is a no-op; the 7th agent is refused; the
detail lists invited agents with names; and rooms list newest
first, so a room just created shows at the top of the chat list.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.rooms.schema import create_schema, reset_tables


@pytest.fixture(autouse=True)
def _room_schema(test_dsn):
    """Room tables plus the 0046 room_agents table."""
    from puspa.migrations import apply_migrations

    create_schema(test_dsn)
    apply_migrations(test_dsn)
    reset_tables(test_dsn)
    yield
    reset_tables(test_dsn)


def _client(user_id, test_dsn):
    return TestClient(create_app(test_user_id=user_id, dsn=test_dsn))


def _make_room(client, name="Grup Riset"):
    resp = client.post("/api/rooms", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _make_agent(client, name="Scout"):
    resp = client.post("/api/agen", data={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_agent_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/agen" in paths
    assert "/api/rooms/{room_id}/agents" in paths
    assert "/api/rooms/{room_id}/agents/{agent_id}" in paths


def test_list_agents_returns_puspa_first(make_user, test_dsn):
    user_id = make_user()["user_id"]
    client = _client(user_id, test_dsn)
    with client:
        agents = client.get("/api/agen").json()["agents"]
    assert agents[0]["name"] == "Puspa"
    assert agents[0]["required"] is True
    _make_agent(client, "Scout")
    with client:
        names = [a["name"] for a in client.get("/api/agen").json()["agents"]]
    assert names == ["Puspa", "Scout"]


def test_invite_agent_into_room_and_detail_lists_it(make_user, test_dsn):
    user = make_user()
    client = _client(user["user_id"], test_dsn)
    room = _make_room(client)
    agent = _make_agent(client, "Scout")
    with client:
        resp = client.post(
            f"/api/rooms/{room['id']}/agents", json={"agent_id": agent["agent_id"]}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_id"] == agent["agent_id"]
    assert resp.json()["name"] == "Scout"
    with client:
        detail = client.get(f"/api/rooms/{room['id']}").json()
    assert [a["agent_id"] for a in detail["agents"]] == [agent["agent_id"]]
    assert detail["agents"][0]["name"] == "Scout"


def test_reinvite_agent_is_a_noop(make_user, test_dsn):
    user = make_user()
    client = _client(user["user_id"], test_dsn)
    room = _make_room(client)
    with client:
        first = client.post(
            f"/api/rooms/{room['id']}/agents", json={"agent_id": user["agent_id"]}
        )
        second = client.post(
            f"/api/rooms/{room['id']}/agents", json={"agent_id": user["agent_id"]}
        )
        detail = client.get(f"/api/rooms/{room['id']}").json()
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(detail["agents"]) == 1


def test_invite_unknown_agent_is_400_and_others_agent_is_403(make_user, test_dsn):
    owner = make_user()
    other = make_user()
    client = _client(owner["user_id"], test_dsn)
    room = _make_room(client)
    with client:
        unknown = client.post(
            f"/api/rooms/{room['id']}/agents", json={"agent_id": "agen-nope"}
        )
        theirs = client.post(
            f"/api/rooms/{room['id']}/agents",
            json={"agent_id": other["agent_id"]},
        )
    assert unknown.status_code == 400, unknown.text
    assert theirs.status_code == 403, theirs.text


def test_non_owner_cannot_invite_or_remove(make_user, test_dsn):
    owner = make_user()
    member = make_user()
    owner_client = _client(owner["user_id"], test_dsn)
    room = _make_room(owner_client)
    # The member joins as a plain member (owner adds them).
    from puspa.rooms import add_member

    add_member(room["id"], member["user_id"], added_by=owner["user_id"], dsn=test_dsn)
    member_client = _client(member["user_id"], test_dsn)
    with member_client:
        denied = member_client.post(
            f"/api/rooms/{room['id']}/agents",
            json={"agent_id": owner["agent_id"]},
        )
    assert denied.status_code == 403, denied.text
    with owner_client:
        ok = owner_client.post(
            f"/api/rooms/{room['id']}/agents",
            json={"agent_id": owner["agent_id"]},
        )
    assert ok.status_code == 200
    with member_client:
        denied_remove = member_client.delete(
            f"/api/rooms/{room['id']}/agents/{owner['agent_id']}"
        )
    assert denied_remove.status_code == 403


def test_remove_agent_and_agent_cap_at_store(make_user, test_dsn):
    """The 6-agent room cap, tested at the store: the app caps a
    user at 3 agents, so 7 invites from one owner can never
    happen through the API — but two owners could, so the store
    still enforces the cap. Extra agents are seeded straight
    into agent_meta (the app's 3-agent cap is a different rule)."""
    from puspa.accounts.ports import register_agent
    from puspa.db import connect as _db_connect
    from puspa.rooms import RoomStore
    from puspa.rooms.errors import RoomFullError

    user = make_user()
    store = RoomStore(test_dsn)
    room = store.create_room("Grup Riset", user["user_id"])
    # register_agent + agent_meta directly: create_agent caps a
    # user at 3 agents, and the cap under test here is the room's,
    # not the account's. Register first (each call opens and closes
    # its own connection), then insert the meta rows on one
    # connection — SQLite allows a single writer.
    for i in range(6):
        register_agent(f"agen-test-{i}", user["user_id"], dsn=test_dsn)
    with _db_connect(test_dsn) as conn, conn.cursor() as cur:
        for i in range(6):
            cur.execute(
                "INSERT INTO agent_meta (agent_id, user_id, name, peran, purpose, required)"
                " VALUES (%s, %s, %s, %s, %s, FALSE)",
                (f"agen-test-{i}", user["user_id"], f"Agen-{i}", "", ""),
            )
        conn.commit()
    agent_ids = [f"agen-test-{i}" for i in range(6)] + [user["agent_id"]]
    for agent_id in agent_ids[:6]:
        store.add_agent(room.id, agent_id, added_by=user["user_id"])
    with pytest.raises(RoomFullError):
        store.add_agent(room.id, agent_ids[6], added_by=user["user_id"])
    store.remove_agent(room.id, agent_ids[0], removed_by=user["user_id"])
    assert len(store.list_agents(room.id)) == 5


def test_rooms_list_newest_first(make_user, test_dsn):
    user_id = make_user()["user_id"]
    client = _client(user_id, test_dsn)
    _make_room(client, "Grup Lama")
    _make_room(client, "Grup Baru")
    with client:
        names = [r["name"] for r in client.get("/api/rooms").json()["rooms"]]
    assert names == ["Grup Baru", "Grup Lama"]
