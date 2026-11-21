"""API tests for the artifacts browser endpoints.

Covers the HTTP layer only (the store maths live in
``tests/test_artifacts.py``): a logged-in user lists their
own agent's artifacts and downloads one back byte for byte;
another user's agent is 403, an unknown artifact is 404, and
an anonymous user gets 401s, not 404s. The done-criterion
seed is here too: one run with an artifact and a ledger log,
exporting its replay GIF records the GIF as an artifact of
that run, so the browser shows both halves from one seed.

These tests run against the real ``puspa.pwa.app.create_app``:
the routes are mounted there with the app's own ``need_user``
login resolver, so the paths, login gate, status codes, and
JSON shapes tested here are the contract the live PWA serves.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.pwa.artifacts import ArtifactStore


def _task_id() -> str:
    return f"tugas-{uuid.uuid4().hex[:8]}"


def _seed_run(ledger, store, user, task_id):
    ledger.append_event(
        task_id=task_id,
        agent_id=user["agent_id"],
        event_type="task_created",
        content={"purpose": "compare prices"},
    )
    ledger.append_event(
        task_id=task_id,
        agent_id=user["agent_id"],
        event_type="tool_called",
        content={"tool": "search_listings", "reason": "need fresh prices"},
    )
    ledger.append_event(
        task_id=task_id,
        agent_id=user["agent_id"],
        event_type="tool_completed",
        content={"tool": "search_listings", "result": "3 listings found"},
    )
    return store.record_artifact(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        run_id=task_id,
        name="laporan.md",
        kind="markdown",
        media_type="text/markdown",
        content=b"# hasil\n",
    )


def _build_app(test_user_id, test_dsn):
    return create_app(test_user_id=test_user_id, dsn=test_dsn)


def test_artifacts_routes_exist_on_create_app(make_user, test_dsn, ledger):
    user = make_user()
    task_id = _task_id()
    store = ArtifactStore(test_dsn)
    store.create_schema()
    _seed_run(ledger, store, user, task_id)
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/artifacts/{agent_id}" in paths
    assert "/api/artifacts/{agent_id}/{artifact_id}" in paths
    assert "/artifacts" in paths
    with TestClient(app) as client:
        assert client.get(f"/api/artifacts/{user['agent_id']}").status_code == 200
        assert client.get("/artifacts").status_code == 200


def test_artifacts_routes_require_login(test_dsn):
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/artifacts/puspa").status_code == 401
        assert client.get("/api/artifacts/puspa/artefak-1").status_code == 401


def test_list_returns_seeded_artifact_with_name_and_size(make_user, test_dsn, ledger):
    user = make_user()
    task_id = _task_id()
    store = ArtifactStore(test_dsn)
    store.create_schema()
    artifact = _seed_run(ledger, store, user, task_id)
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/artifacts/{user['agent_id']}")
        assert resp.status_code == 200
        artifacts = resp.json()
        assert len(artifacts) == 1
        assert artifacts[0]["artifact_id"] == artifact.artifact_id
        assert artifacts[0]["name"] == "laporan.md"
        assert artifacts[0]["kind"] == "markdown"
        assert artifacts[0]["size_bytes"] == 8
        assert artifacts[0]["run_id"] == task_id
        assert "content" not in artifacts[0]


def test_download_returns_the_same_bytes(make_user, test_dsn, ledger):
    user = make_user()
    task_id = _task_id()
    store = ArtifactStore(test_dsn)
    store.create_schema()
    artifact = _seed_run(ledger, store, user, task_id)
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/artifacts/{user['agent_id']}/{artifact.artifact_id}")
        assert resp.status_code == 200
        assert resp.content == b"# hasil\n"
        assert resp.headers["content-type"].startswith("text/markdown")
        assert "laporan.md" in resp.headers["content-disposition"]


def test_list_other_users_agent_is_403(make_user, test_dsn, ledger):
    owner = make_user()
    stranger = make_user()
    task_id = _task_id()
    store = ArtifactStore(test_dsn)
    store.create_schema()
    _seed_run(ledger, store, owner, task_id)
    with TestClient(_build_app(stranger["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/artifacts/{owner['agent_id']}")
        assert resp.status_code == 403


def test_download_unknown_artifact_is_404(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get(f"/api/artifacts/{user['agent_id']}/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


def test_gif_export_records_an_artifact_of_the_run(make_user, test_dsn, ledger):
    user = make_user()
    task_id = _task_id()
    store = ArtifactStore(test_dsn)
    store.create_schema()
    _seed_run(ledger, store, user, task_id)
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        gif = client.get(f"/api/replay/{user['agent_id']}/{task_id}/export.gif")
        assert gif.status_code == 200
        assert gif.content[:6] in (b"GIF89a", b"GIF87a")
        resp = client.get(f"/api/artifacts/{user['agent_id']}")
        assert resp.status_code == 200
        artifacts = resp.json()
        names = [row["name"] for row in artifacts]
        assert f"replay-{task_id}.gif" in names
        recorded = next(row for row in artifacts if row["name"] == f"replay-{task_id}.gif")
        assert recorded["kind"] == "gif"
        assert recorded["run_id"] == task_id
        assert recorded["size_bytes"] == len(gif.content)


def test_seeded_run_log_is_visible_beside_its_artifact(make_user, test_dsn, ledger):
    user = make_user()
    task_id = _task_id()
    store = ArtifactStore(test_dsn)
    store.create_schema()
    _seed_run(ledger, store, user, task_id)
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        artifacts = client.get(f"/api/artifacts/{user['agent_id']}").json()
        assert len(artifacts) == 1
        timeline = client.get(f"/api/replay/{user['agent_id']}/{task_id}").json()
        assert timeline["step_count"] == 3
        assert timeline["steps"][2]["result"] == "3 listings found"
        assert artifacts[0]["run_id"] == timeline["task_id"]
