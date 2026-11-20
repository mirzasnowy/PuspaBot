"""API tests for the P-10 replay timeline endpoints.

Covers the HTTP layer only (the replay maths live in
``tests/test_replay.py``): a logged-in user lists their own
agent's tasks, replays one of them step by step, scrubs to a
single step, and exports the GIF; another user's agent is
403, an unknown task is 404, and an anonymous user gets 401s,
not 404s.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in P-10): the routes are mounted there with the app's
own ``need_user`` login resolver, so the paths, login gate,
status codes, and JSON shapes tested here are the contract
the live PWA serves.
"""

from __future__ import annotations

import io
import uuid

from fastapi.testclient import TestClient
from PIL import Image

from puspa.pwa.app import create_app


def _task_id() -> str:
    return f"tugas-{uuid.uuid4().hex[:8]}"


def _seed_task(ledger, agent_id, task_id):
    ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="task_created",
        content={"purpose": "compare prices"},
    )
    ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="tool_called",
        content={"tool": "search_listings", "reason": "need fresh prices"},
    )
    ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="tool_completed",
        content={"tool": "search_listings", "result": "3 listings found"},
    )


def _build_app(test_user_id, test_dsn):
    return create_app(test_user_id=test_user_id, dsn=test_dsn)


def test_replay_routes_exist_on_create_app(make_user, test_dsn, ledger):
    user = make_user()
    task_id = _task_id()
    _seed_task(ledger, user["agent_id"], task_id)
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/replay/{agent_id}/tasks" in paths
    assert "/api/replay/{agent_id}/{task_id}" in paths
    assert "/api/replay/{agent_id}/{task_id}/steps/{index}" in paths
    assert "/api/replay/{agent_id}/{task_id}/export.gif" in paths
    assert "/replay" in paths
    with TestClient(app) as client:
        assert client.get(f"/api/replay/{user['agent_id']}/tasks").status_code == 200
        assert client.get(f"/api/replay/{user['agent_id']}/{task_id}").status_code == 200
        assert client.get("/replay").status_code == 200


def test_replay_routes_require_login(test_dsn):
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/replay/puspa/tasks").status_code == 401
        assert client.get("/api/replay/puspa/tugas-1").status_code == 401
        assert client.get("/api/replay/puspa/tugas-1/steps/0").status_code == 401
        assert client.get("/api/replay/puspa/tugas-1/export.gif").status_code == 401


def test_tasks_list_for_own_agent(make_user, test_dsn, ledger):
    user = make_user()
    task_id = _task_id()
    _seed_task(ledger, user["agent_id"], task_id)
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/replay/{user['agent_id']}/tasks")
        assert resp.status_code == 200
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == task_id
        assert tasks[0]["event_count"] == 3
        assert tasks[0]["status"] == "running"


def test_timeline_for_own_agent(make_user, test_dsn, ledger):
    user = make_user()
    task_id = _task_id()
    _seed_task(ledger, user["agent_id"], task_id)
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/replay/{user['agent_id']}/{task_id}")
        assert resp.status_code == 200
        timeline = resp.json()
        assert timeline["task_id"] == task_id
        assert timeline["step_count"] == 3
        assert timeline["status"] == "running"
        assert timeline["chain_ok"] is True
        assert timeline["broken_event_ids"] == []
        steps = timeline["steps"]
        assert [step["type"] for step in steps] == [
            "task_created",
            "tool_called",
            "tool_completed",
        ]
        assert steps[1]["reason"].startswith("Bot memanggil tool search_listings")
        assert steps[2]["result"] == "3 listings found"
        assert all(step["screenshot"] is None for step in steps)


def test_timeline_unknown_task_is_404(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/replay/{user['agent_id']}/tugas-tidak-ada")
        assert resp.status_code == 404


def test_timeline_other_users_agent_is_403(make_user, test_dsn, ledger):
    owner = make_user()
    stranger = make_user()
    task_id = _task_id()
    _seed_task(ledger, owner["agent_id"], task_id)
    with TestClient(_build_app(stranger["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/replay/{owner['agent_id']}/{task_id}")
        assert resp.status_code == 403
        resp = client.get(f"/api/replay/{owner['agent_id']}/tasks")
        assert resp.status_code == 403


def test_single_step_scrubs_to_exact_position(make_user, test_dsn, ledger):
    user = make_user()
    task_id = _task_id()
    _seed_task(ledger, user["agent_id"], task_id)
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/replay/{user['agent_id']}/{task_id}/steps/2")
        assert resp.status_code == 200
        step = resp.json()
        assert step["index"] == 2
        assert step["type"] == "tool_completed"
        resp = client.get(f"/api/replay/{user['agent_id']}/{task_id}/steps/0")
        assert resp.json()["type"] == "task_created"
        resp = client.get(f"/api/replay/{user['agent_id']}/{task_id}/steps/3")
        assert resp.status_code == 404


def test_gif_export_is_a_decodable_gif_with_one_frame_per_step(make_user, test_dsn, ledger):
    user = make_user()
    task_id = _task_id()
    _seed_task(ledger, user["agent_id"], task_id)
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        first = client.get(f"/api/replay/{user['agent_id']}/{task_id}/export.gif")
        assert first.status_code == 200
        assert first.headers["content-type"] == "image/gif"
        assert "attachment" in first.headers["content-disposition"]
        assert first.content[:6] in (b"GIF89a", b"GIF87a")
        with Image.open(io.BytesIO(first.content)) as gif:
            assert gif.n_frames == 3
        # Deterministic: a second export is byte-identical.
        second = client.get(f"/api/replay/{user['agent_id']}/{task_id}/export.gif")
        assert second.content == first.content


def test_gif_export_unknown_task_is_404(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/replay/{user['agent_id']}/tugas-tidak-ada/export.gif")
        assert resp.status_code == 404
