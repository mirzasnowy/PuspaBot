"""API tests for the P-09 live view + takeover endpoints.

Covers the HTTP layer only (the hold maths live in
``tests/test_live_view.py``, the turn gate in
``tests/test_takeover_turn_gate.py``): a logged-in user sees
their own agent's frame, can take it over and release it, and
gets 403 for another user's agent; an anonymous user gets
401s, not 404s.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in P-09): the routes are mounted there with the app's
own ``need_user`` login resolver, so the paths, login gate,
status codes, and JSON shapes tested here are the contract
the live PWA serves.
"""

from __future__ import annotations

import psycopg
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.pwa.live_view import TakeoverStore


def _build_app(test_user_id, test_dsn):
    return create_app(test_user_id=test_user_id, dsn=test_dsn)


def _truncate_holds(test_dsn):
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE takeover_holds")


def test_live_view_routes_exist_on_create_app(make_user, test_dsn):
    user = make_user()
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/live-view/{agent_id}" in paths
    assert "/api/live-view/{agent_id}/takeover" in paths
    assert "/api/live-view/{agent_id}/release" in paths
    assert "/api/live-view/{agent_id}/stream" in paths
    assert "/live" in paths
    with TestClient(app) as client:
        assert client.get(f"/api/live-view/{user['agent_id']}").status_code == 200


def test_live_view_routes_require_login(test_dsn):
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/live-view/puspa").status_code == 401
        assert client.post("/api/live-view/puspa/takeover").status_code == 401
        assert client.post("/api/live-view/puspa/release").status_code == 401
        assert client.get("/api/live-view/puspa/stream").status_code == 401


def test_get_frame_idle_for_own_agent(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/live-view/{user['agent_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == user["agent_id"]
    assert body["status"] == "idle"
    assert body["hold"] is None
    assert body["events"] == []
    assert body["screenshot"] is None


def test_get_frame_for_other_users_agent_is_403(make_user, test_dsn):
    owner = make_user()
    stranger = make_user()
    with TestClient(_build_app(stranger["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/live-view/{owner['agent_id']}")
    assert resp.status_code == 403


def test_takeover_release_round_trip_through_api(make_user, test_dsn):
    user = make_user()
    agent_id = user["agent_id"]
    _truncate_holds(test_dsn)
    try:
        with TestClient(_build_app(user["user_id"], test_dsn)) as client:
            taken = client.post(
                f"/api/live-view/{agent_id}/takeover",
                json={"reason": "checking the bot"},
            )
            assert taken.status_code == 200
            assert taken.json()["status"] == "held"
            assert taken.json()["reason"] == "checking the bot"
            assert taken.json()["audit_trail_url"] == "/api/proof/audit-trail"

            frame = client.get(f"/api/live-view/{agent_id}")
            assert frame.status_code == 200
            assert frame.json()["status"] == "held"
            assert frame.json()["hold"]["held_by_user_id"] == user["user_id"]

            again = client.post(f"/api/live-view/{agent_id}/takeover", json={})
            assert again.status_code == 409

            released = client.post(f"/api/live-view/{agent_id}/release")
            assert released.status_code == 200
            assert released.json()["status"] == "released"

            frame = client.get(f"/api/live-view/{agent_id}")
            assert frame.json()["status"] == "idle"
            assert frame.json()["hold"] is None

            released_again = client.post(f"/api/live-view/{agent_id}/release")
            assert released_again.status_code == 404
    finally:
        _truncate_holds(test_dsn)


def test_takeover_other_users_agent_is_403_through_api(make_user, test_dsn):
    owner = make_user()
    stranger = make_user()
    _truncate_holds(test_dsn)
    try:
        with TestClient(_build_app(stranger["user_id"], test_dsn)) as client:
            resp = client.post(f"/api/live-view/{owner['agent_id']}/takeover", json={})
            assert resp.status_code == 403
            assert client.post(f"/api/live-view/{owner['agent_id']}/release").status_code == 403
        assert TakeoverStore(test_dsn).is_held(owner["agent_id"]) is False
    finally:
        _truncate_holds(test_dsn)


def test_live_stream_serves_one_frame_then_heartbeat(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/live-view/{user['agent_id']}/stream")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert '"type": "live_frame"' in resp.text
    assert '"type": "heartbeat"' in resp.text
    assert f'"agent_id": "{user["agent_id"]}"' in resp.text


def test_live_stream_for_other_users_agent_serves_error_frame(make_user, test_dsn):
    owner = make_user()
    stranger = make_user()
    with TestClient(_build_app(stranger["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/live-view/{owner['agent_id']}/stream")
    assert resp.status_code == 200
    assert '"type": "error"' in resp.text
    assert '"status": 403' in resp.text
