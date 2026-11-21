"""API tests for the local rooms routes (``puspa.pwa.rooms_api``).

Covers the HTTP layer only (the room stores live in
``puspa.rooms``): a logged-in user lists their rooms (empty when
they have none), creates a room, reads its detail/board/memory/
invites/jobs, invites a second user by link or code, and runs a
job; a non-member gets 403, an anonymous caller gets 401, and an
unknown room gets 404. Also covers the providers list with no
vault master key configured: it answers 200 with every provider
``not_logged_in`` instead of the 400 the Settings page used to
log.

These tests run against the real ``puspa.pwa.app.create_app``:
the routes are mounted there with the app's own ``need_user``
login resolver, so the paths, login gate, status codes, and JSON
shapes tested here are the contract the live PWA serves.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from puspa.memory import facts
from puspa.memory.facts import PostgresFactStore
from puspa.pwa.app import create_app
from puspa.rooms import RoomStore, RoomTaskStore
from puspa.rooms.schema import create_schema, reset_tables

UNKNOWN_ROOM = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _room_schema(test_dsn):
    """Room tables (0010) plus the job queue table (0024)."""
    from puspa.migrations import apply_migrations

    create_schema(test_dsn)
    apply_migrations(test_dsn, target="0024")
    reset_tables(test_dsn)
    yield
    reset_tables(test_dsn)


def _client(user_id, test_dsn):
    return TestClient(create_app(test_user_id=user_id, dsn=test_dsn))


def _make_room(client, name="Ruang Keluarga"):
    resp = client.post("/api/rooms", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_rooms_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/rooms" in paths
    assert "/api/rooms" in paths
    assert "/api/rooms/{room_id}" in paths
    assert "/api/rooms/{room_id}/tasks" in paths
    assert "/api/rooms/{room_id}/memory" in paths
    assert "/api/rooms/{room_id}/jobs" in paths
    assert "/api/rooms/{room_id}/invites" in paths
    with _client(user_id, test_dsn) as client:
        assert client.get("/rooms").status_code == 200
        assert client.get("/api/rooms").json() == {"rooms": []}
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/rooms").status_code == 401
        assert client.get(f"/api/rooms/{UNKNOWN_ROOM}").status_code == 401
        assert client.get(f"/api/rooms/{UNKNOWN_ROOM}/tasks").status_code == 401
        assert client.get("/rooms", follow_redirects=False).status_code in (302, 307)


def test_create_room_lists_detail_and_empty_board(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        room = _make_room(client)
        assert room["role"] == "owner"
        assert room["name"] == "Ruang Keluarga"
        rooms = client.get("/api/rooms").json()["rooms"]
        assert [r["id"] for r in rooms] == [room["id"]]
        assert rooms[0]["role"] == "owner"
        detail = client.get(f"/api/rooms/{room['id']}").json()
        assert detail["name"] == "Ruang Keluarga"
        assert detail["role"] == "owner"
        assert [m["user_id"] for m in detail["members"]] == [user_id]
        assert detail["members"][0]["role"] == "owner"
        assert detail["names"][user_id] == "Anggota 1"
        board = client.get(f"/api/rooms/{room['id']}/tasks").json()
        assert board["room_id"] == room["id"]
        assert board["room_name"] == "Ruang Keluarga"
        assert board["tasks"] == []
        assert client.get(f"/api/rooms/{room['id']}/memory").json() == {
            "room_id": room["id"],
            "memories": [],
        }
        assert client.get(f"/api/rooms/{room['id']}/jobs").json() == {
            "room_id": room["id"],
            "jobs": [],
        }
        assert client.get(f"/api/rooms/{room['id']}/invites").json() == {
            "room_id": room["id"],
            "invites": [],
        }
        # Bad input is a 400, not a 500.
        assert client.post("/api/rooms", json={"name": "   "}).status_code == 400


def test_board_shows_tasks_with_null_delegation(make_user, test_dsn):
    user_id = make_user()["user_id"]
    room = RoomStore(test_dsn).create_room("Ruang Kerja", user_id)
    RoomTaskStore(test_dsn).add_task(room.id, title="Bandingkan harga", created_by=user_id)
    with _client(user_id, test_dsn) as client:
        board = client.get(f"/api/rooms/{room.id}/tasks").json()
        assert len(board["tasks"]) == 1
        task = board["tasks"][0]
        assert task["title"] == "Bandingkan harga"
        assert task["status"] == "open"
        assert task["delegation"] is None
        assert task["pending_steps"] == 0


def test_room_forbidden_for_non_member_and_unknown_is_404(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    room = RoomStore(test_dsn).create_room("Ruang Rahasia", owner_id)
    outsider_id = make_user()["user_id"]
    with _client(outsider_id, test_dsn) as client:
        assert client.get(f"/api/rooms/{room.id}").status_code == 403
        assert client.get(f"/api/rooms/{room.id}/tasks").status_code == 403
        assert client.get(f"/api/rooms/{room.id}/memory").status_code == 403
        assert client.get(f"/api/rooms/{room.id}/jobs").status_code == 403
        assert client.get("/api/rooms").json()["rooms"] == []
    with _client(owner_id, test_dsn) as client:
        assert client.get(f"/api/rooms/{UNKNOWN_ROOM}").status_code == 404
        assert client.get(f"/api/rooms/{UNKNOWN_ROOM}/tasks").status_code == 404


def test_invite_link_join_and_leave(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    friend_id = make_user()["user_id"]
    with _client(owner_id, test_dsn) as client:
        room = _make_room(client)
        issued = client.post(
            f"/api/rooms/{room['id']}/invites", json={"kind": "link", "role": "member"}
        )
        assert issued.status_code == 200, issued.text
        body = issued.json()
        assert body["token"]
        assert body["join_link"].endswith(body["token"])
        listed = client.get(f"/api/rooms/{room['id']}/invites").json()["invites"]
        assert len(listed) == 1
        assert "token" not in listed[0] and "code" not in listed[0]
    with _client(friend_id, test_dsn) as friend:
        joined = friend.post("/api/rooms/join", json={"token": body["token"]})
        assert joined.status_code == 200, joined.text
        assert joined.json()["room_id"] == room["id"]
        assert joined.json()["already_member"] is False
        assert [r["id"] for r in friend.get("/api/rooms").json()["rooms"]] == [room["id"]]
        # Joining again with the spent invite reports already_member.
        again = friend.post("/api/rooms/join", json={"token": body["token"]})
        assert again.status_code == 200
        assert again.json()["already_member"] is True
        left = friend.post(f"/api/rooms/{room['id']}/leave")
        assert left.status_code == 200
        assert left.json() == {"left": True, "room_id": room["id"]}
        assert friend.get("/api/rooms").json()["rooms"] == []
    with _client(owner_id, test_dsn) as client:
        # The last owner may not leave their own room.
        assert client.post(f"/api/rooms/{room['id']}/leave").status_code == 400
        # An unknown token is a 400, not a 500.
        assert client.post("/api/rooms/join", json={"token": "tidak-ada"}).status_code == 400


def test_invite_code_join_and_revoke(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    friend_id = make_user()["user_id"]
    with _client(owner_id, test_dsn) as client:
        room = _make_room(client)
        issued = client.post(
            f"/api/rooms/{room['id']}/invites", json={"kind": "code", "role": "guest"}
        ).json()
        assert len(issued["code"]) == 6
        revoked = client.post(f"/api/rooms/{room['id']}/invites/{issued['invite_id']}/revoke")
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"
        issued2 = client.post(
            f"/api/rooms/{room['id']}/invites", json={"kind": "code", "role": "member"}
        ).json()
    with _client(friend_id, test_dsn) as friend:
        assert friend.post("/api/rooms/join", json={"code": issued["code"]}).status_code == 400
        joined = friend.post("/api/rooms/join", json={"code": issued2["code"]})
        assert joined.status_code == 200
        assert joined.json()["role"] == "member"


def test_member_role_change_remove_and_delete(make_user, test_dsn):
    owner_id = make_user()["user_id"]
    friend_id = make_user()["user_id"]
    with _client(owner_id, test_dsn) as client:
        room = _make_room(client)
        token = client.post(
            f"/api/rooms/{room['id']}/invites", json={"kind": "link", "role": "member"}
        ).json()["token"]
    with _client(friend_id, test_dsn) as friend:
        assert friend.post("/api/rooms/join", json={"token": token}).status_code == 200
    with _client(owner_id, test_dsn) as client:
        changed = client.post(
            f"/api/rooms/{room['id']}/members/{friend_id}/role", json={"role": "guest"}
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["role"] == "guest"
        # A guest is read-only: adding room memory is a 403.
        with _client(friend_id, test_dsn) as friend:
            assert (
                friend.post(
                    f"/api/rooms/{room['id']}/memory",
                    json={"content": "catatan tamu", "kind": "note", "visibility": "room"},
                ).status_code
                == 403
            )
        removed = client.post(f"/api/rooms/{room['id']}/members/{friend_id}/remove")
        assert removed.status_code == 200
        assert removed.json() == {
            "removed": True,
            "room_id": room["id"],
            "user_id": friend_id,
        }
        assert client.get(f"/api/rooms/{room['id']}").json()["members"] == [
            m
            for m in client.get(f"/api/rooms/{room['id']}").json()["members"]
            if m["user_id"] == owner_id
        ]
        deleted = client.delete(f"/api/rooms/{room['id']}")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True, "room_id": room["id"]}
        assert client.get("/api/rooms").json()["rooms"] == []


def test_room_memory_add_list_delete_and_bring(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        room = _make_room(client)
        added = client.post(
            f"/api/rooms/{room['id']}/memory",
            json={"content": "Keputusan: pakai toko A", "kind": "decision", "visibility": "room"},
        )
        assert added.status_code == 200, added.text
        assert added.json()["kind"] == "decision"
        memories = client.get(f"/api/rooms/{room['id']}/memory").json()["memories"]
        assert len(memories) == 1
        assert memories[0]["author_display_name"] == "Anggota"
        # An unknown personal fact cannot be brought in.
        assert (
            client.post(
                f"/api/rooms/{room['id']}/memory/bring",
                json={"fact_id": "00000000-0000-0000-0000-000000000000"},
            ).status_code
            == 404
        )
        stored = facts.record_fact(
            user_id,
            "kota",
            "Jakarta",
            store=PostgresFactStore(test_dsn),
            trust_check=False,
            now=datetime(2026, 11, 22, 12, 0, tzinfo=UTC),
        )
        assert stored["stored"] is True
        brought = client.post(
            f"/api/rooms/{room['id']}/memory/bring", json={"fact_id": stored["id"]}
        )
        assert brought.status_code == 200, brought.text
        assert brought.json()["brought_from_fact_id"] == stored["id"]
        assert "Jakarta" in brought.json()["content"]
        deleted = client.delete(f"/api/rooms/{room['id']}/memory/{added.json()['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert len(client.get(f"/api/rooms/{room['id']}/memory").json()["memories"]) == 1


def test_job_queue_note_and_summarize(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        room = _make_room(client)
        assert (
            client.post(
                f"/api/rooms/{room['id']}/jobs", json={"kind": "robot", "payload": {}}
            ).status_code
            == 400
        )
        note = client.post(
            f"/api/rooms/{room['id']}/jobs",
            json={"kind": "note", "payload": {"content": "Beli beras", "visibility": "room"}},
        )
        assert note.status_code == 200, note.text
        assert note.json()["status"] == "queued"
        job_id = note.json()["id"]
        run = client.post(f"/api/rooms/{room['id']}/jobs/{job_id}/run")
        assert run.status_code == 200, run.text
        assert run.json()["status"] == "done"
        assert run.json()["result"]
        assert any(
            m["content"] == "Beli beras"
            for m in client.get(f"/api/rooms/{room['id']}/memory").json()["memories"]
        )
        summary = client.post(
            f"/api/rooms/{room['id']}/jobs",
            json={
                "kind": "summarize",
                "payload": {"messages": [{"author": "A", "text": "TODO: beli beras"}]},
            },
        ).json()
        run2 = client.post(f"/api/rooms/{room['id']}/jobs/{summary['id']}/run")
        assert run2.status_code == 200
        assert run2.json()["status"] == "done"
        assert "beli beras" in (run2.json()["result"] or "").lower()
        jobs = client.get(f"/api/rooms/{room['id']}/jobs").json()["jobs"]
        assert [j["status"] for j in jobs] == ["done", "done"]
        # A finished job cannot run twice.
        assert client.post(f"/api/rooms/{room['id']}/jobs/{job_id}/run").status_code == 400


def test_providers_list_without_vault_master_key(make_user, test_dsn, monkeypatch):
    """No master key configured: the list still answers 200."""
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY_FILE", raising=False)
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        resp = client.get("/api/providers")
        assert resp.status_code == 200, resp.text
        providers = resp.json()["providers"]
        assert providers
        assert all(p["state"] == "not_logged_in" for p in providers)
        assert all("provider" in p and "flow" in p for p in providers)
