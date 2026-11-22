"""API tests for the missions endpoints (V2-42, issue #219).

Covers the HTTP layer only (the store and runner maths live
in ``tests/test_missions.py``): a logged-in user creates a
mission with a checklist, reads it back, queues a run, answers
its step-ask, and cancels it; another user's mission is 404,
an anonymous user gets 401s, and the webhook wake works with
the token alone while an unknown token is 404.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from puspa.pwa.app import create_app

GOAL = "Riset harga laptop"


def _build_app(test_user_id, test_dsn):
    return create_app(test_user_id=test_user_id, dsn=test_dsn)


def _create(client, steps=None):
    resp = client.post(
        "/api/missions",
        json={"goal": GOAL, "steps": steps if steps is not None else ["Cari tiga harga", "Beli"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_missions_routes_exist_on_create_app(make_user, test_dsn):
    user = make_user()
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/missions" in paths
    assert "/api/missions/{mission_id}" in paths
    assert "/api/missions/{mission_id}/run" in paths
    assert "/api/missions/{mission_id}/answer" in paths
    assert "/api/missions/{mission_id}/cancel" in paths
    assert "/api/missions/{mission_id}/schedule" in paths
    assert "/api/missions/{mission_id}/webhook/rotate" in paths
    assert "/api/missions/webhook/{token}" in paths


def test_missions_routes_require_login(test_dsn):
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/missions").status_code == 401
        assert (
            client.post("/api/missions", json={"goal": GOAL, "steps": ["Satu"]}).status_code == 401
        )


def test_create_list_and_get_mission(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        mission = _create(client)
        assert mission["id"].startswith("misi-")
        assert mission["title"] == GOAL
        assert mission["status"] == "active"
        assert mission["webhook_token"]
        assert [s["title"] for s in mission["steps"]] == ["Cari tiga harga", "Beli"]
        assert [s["status"] for s in mission["steps"]] == ["pending", "pending"]
        listed = client.get("/api/missions").json()
        assert [m["id"] for m in listed["missions"]] == [mission["id"]]
        one = client.get(f"/api/missions/{mission['id']}").json()
        assert one["goal"] == GOAL
        assert client.get("/api/missions/misi-tidak-ada").status_code == 404


def test_create_rejects_bad_body(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        assert (
            client.post("/api/missions", json={"goal": " ", "steps": ["Satu"]}).status_code == 400
        )
        assert client.post("/api/missions", json={"goal": GOAL, "steps": []}).status_code == 400
        assert client.post("/api/missions", json={"goal": GOAL}).status_code == 400


def test_other_users_mission_is_404(make_user, test_dsn):
    owner = make_user()
    stranger = make_user()
    with TestClient(_build_app(owner["user_id"], test_dsn)) as client:
        mission = _create(client)
    with TestClient(_build_app(stranger["user_id"], test_dsn)) as client:
        assert client.get(f"/api/missions/{mission['id']}").status_code == 404
        assert client.get("/api/missions").json() == {"missions": []}
        assert client.post(f"/api/missions/{mission['id']}/cancel").status_code == 404


def test_run_queues_one_shot_trigger_and_cancel_stops(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        mission = _create(client)
        queued = client.post(f"/api/missions/{mission['id']}/run")
        assert queued.status_code == 200, queued.text
        assert queued.json()["queued"] is True
        assert queued.json()["trigger_id"].startswith("pemicu-")
        cancelled = client.post(f"/api/missions/{mission['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "stopped"
        assert client.post(f"/api/missions/{mission['id']}/run").status_code == 400
        assert client.post(f"/api/missions/{mission['id']}/cancel").status_code == 400


def test_schedule_registers_a_trigger(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        mission = _create(client)
        resp = client.post(
            f"/api/missions/{mission['id']}/schedule",
            json={"schedule": {"type": "daily", "time": "07:00", "timezone": "Asia/Jakarta"}},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["scheduled"] is True
        assert resp.json()["trigger_id"].startswith("pemicu-")
        bad = client.post(
            f"/api/missions/{mission['id']}/schedule", json={"schedule": {"type": "nope"}}
        )
        assert bad.status_code == 400


def test_answer_flow_through_api(make_user, test_dsn):
    user = make_user()
    steps = [
        {"title": "Pilih toko", "question": "Toko mana?", "options": ["Tokopedia", "Shopee"]},
    ]
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        mission = _create(client, steps=steps)
        # No open question yet: answering is refused.
        assert (
            client.post(
                f"/api/missions/{mission['id']}/answer", json={"answer": "Tokopedia", "go": True}
            ).status_code
            == 400
        )
        # Queue a run; the scheduler has no turn loop in this app,
        # so the step is recorded as not run and stays pending.
        # Open the question through the runner directly (a step
        # with a question pauses before it ever runs), then
        # answer it over HTTP.
        from puspa.missions.runner import advance_mission

        out = advance_mission(mission["id"], user["user_id"], dsn=test_dsn)
        assert out["status"] == "waiting"
        resp = client.post(
            f"/api/missions/{mission['id']}/answer", json={"answer": "Shopee", "go": True}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "active"
        assert resp.json()["steps"][0]["answer"] == "Shopee"


def test_webhook_wake_needs_no_login_but_needs_token(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        mission = _create(client)
        token = mission["webhook_token"]
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.post("/api/missions/webhook/token-salah", json={}).status_code == 404
        resp = client.post(f"/api/missions/webhook/{token}", json={"event": "push"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        assert resp.json()["status"] == "queued"
        assert resp.json()["mission_id"] == mission["id"]


def test_webhook_rotate_invalidates_old_token(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        mission = _create(client)
        old = mission["webhook_token"]
        resp = client.post(f"/api/missions/{mission['id']}/webhook/rotate")
        assert resp.status_code == 200, resp.text
        new = resp.json()["webhook_token"]
        assert new and new != old
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.post(f"/api/missions/webhook/{old}", json={}).status_code == 404
        assert client.post(f"/api/missions/webhook/{new}", json={}).status_code == 200
