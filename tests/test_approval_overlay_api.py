"""API tests for the P-11 visual approval overlay endpoints.

Covers the HTTP layer only (the overlay maths live in
``tests/test_approval_overlay.py``): a logged-in user lists
their own agent's overlays with highlights, proposes one,
approves or rejects it per action, and gets 403 for another
user's agent; an anonymous user gets 401s, not 404s.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in P-11): the routes are mounted there with the app's
own ``need_user`` login resolver, so the paths, login gate,
status codes, and JSON shapes tested here are the contract
the live PWA serves.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from puspa.pwa.app import create_app


def _build_app(test_user_id, test_dsn):
    return create_app(test_user_id=test_user_id, dsn=test_dsn)


def _propose_body(**overrides):
    body = {
        "tool_name": "gmail.kirim",
        "content": {"recipient": "sari@contoh.id", "text": "halo"},
        "button_label": "Kirim",
    }
    body.update(overrides)
    return body


def test_overlay_routes_exist_on_create_app(make_user, test_dsn):
    user = make_user()
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/live-view/{agent_id}/approvals" in paths
    assert "/api/live-view/{agent_id}/approvals/{request_id}/approve" in paths
    assert "/api/live-view/{agent_id}/approvals/{request_id}/reject" in paths
    assert "/live" in paths
    with TestClient(app) as client:
        resp = client.get(f"/api/live-view/{user['agent_id']}/approvals")
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == user["agent_id"]
        assert resp.json()["pending"] == []
        assert resp.json()["decided"] == []


def test_overlay_routes_require_login(test_dsn):
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/live-view/puspa/approvals").status_code == 401
        assert (
            client.post("/api/live-view/puspa/approvals", json=_propose_body()).status_code == 401
        )
        assert (
            client.post("/api/live-view/puspa/approvals/req-1/approve", json={}).status_code == 401
        )
        assert (
            client.post("/api/live-view/puspa/approvals/req-1/reject", json={}).status_code == 401
        )


def test_propose_lists_with_highlight_then_approve(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    ledger.append_event(task_id=task_id, agent_id=agent_id, event_type="task_created", content={})
    ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type="tool_called",
        content={"tool": "gmail.kirim"},
    )
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        proposed = client.post(
            f"/api/live-view/{agent_id}/approvals",
            json=_propose_body(task_id=task_id),
        )
        assert proposed.status_code == 200
        body = proposed.json()
        assert body["status"] == "pending"
        assert body["kind"] == "send_to_other"
        assert body["target_element"]["element_kind"] == "button"
        assert body["target_element"]["label"] == "Kirim"
        assert body["target_element"]["target"] == "sari@contoh.id"
        assert body["proposed_ledger_event_id"]
        request_id = body["request_id"]

        listed = client.get(f"/api/live-view/{agent_id}/approvals")
        assert listed.status_code == 200
        pending = listed.json()["pending"]
        assert len(pending) == 1
        assert pending[0]["request_id"] == request_id
        # The frame's last tool call is this action's tool.
        assert pending[0]["highlight"]["frame_ref"] == "last_tool"

        approved = client.post(
            f"/api/live-view/{agent_id}/approvals/{request_id}/approve",
            json={"reason": "oke"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        assert approved.json()["decided_by"] == user["user_id"]
        assert approved.json()["decided_ledger_event_id"]

        listed = client.get(f"/api/live-view/{agent_id}/approvals")
        assert listed.json()["pending"] == []
        assert len(listed.json()["decided"]) == 1
        assert listed.json()["decided"][0]["status"] == "approved"

        # Already decided: a second decision is refused.
        again = client.post(f"/api/live-view/{agent_id}/approvals/{request_id}/approve", json={})
        assert again.status_code == 409


def test_reject_cancels_action_and_is_recorded(make_user, test_dsn, ledger):
    user = make_user()
    agent_id = user["agent_id"]
    task_id = f"tugas-{uuid.uuid4().hex[:8]}"
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        proposed = client.post(
            f"/api/live-view/{agent_id}/approvals",
            json=_propose_body(task_id=task_id),
        )
        request_id = proposed.json()["request_id"]
        rejected = client.post(
            f"/api/live-view/{agent_id}/approvals/{request_id}/reject",
            json={"reason": "jangan kirim dulu"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["decision_reason"] == "jangan kirim dulu"
        assert rejected.json()["decided_ledger_event_id"]
    events = ledger.events(task_id, event_type="approval_rejected")
    assert len(events) == 1
    assert events[0]["content"]["request_id"] == request_id
    assert events[0]["content"]["reason"] == "jangan kirim dulu"


def test_propose_credential_change_is_400(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.post(
            f"/api/live-view/{user['agent_id']}/approvals",
            json={"tool_name": "change_password", "content": {"target": "akun"}},
        )
        assert resp.status_code == 400
        assert "never approvable" in resp.json()["error"]


def test_propose_bad_body_is_400(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        assert (
            client.post(
                f"/api/live-view/{user['agent_id']}/approvals",
                json={"tool_name": "gmail.kirim"},
            ).status_code
            == 400
        )
        assert (
            client.post(
                f"/api/live-view/{user['agent_id']}/approvals",
                json={"tool_name": "", "content": {}},
            ).status_code
            == 400
        )
        assert (
            client.post(
                f"/api/live-view/{user['agent_id']}/approvals",
                json={"tool_name": "gmail.kirim", "content": "bukan-dict"},
            ).status_code
            == 400
        )


def test_decide_unknown_is_404(make_user, test_dsn):
    user = make_user()
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        assert (
            client.post(
                f"/api/live-view/{user['agent_id']}/approvals/req-tidak-ada/approve", json={}
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/live-view/{user['agent_id']}/approvals/req-tidak-ada/reject", json={}
            ).status_code
            == 404
        )


def test_overlay_other_users_agent_is_403(make_user, test_dsn):
    owner = make_user()
    stranger = make_user()
    with TestClient(_build_app(stranger["user_id"], test_dsn)) as client:
        assert client.get(f"/api/live-view/{owner['agent_id']}/approvals").status_code == 403
        assert (
            client.post(
                f"/api/live-view/{owner['agent_id']}/approvals", json=_propose_body()
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/live-view/{owner['agent_id']}/approvals/req-1/approve", json={}
            ).status_code
            == 404
        )


def test_propose_duplicate_request_id_is_409(make_user, test_dsn):
    user = make_user()
    agent_id = user["agent_id"]
    request_id = f"req-{uuid.uuid4().hex[:8]}"
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        first = client.post(
            f"/api/live-view/{agent_id}/approvals",
            json=_propose_body(request_id=request_id),
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/live-view/{agent_id}/approvals",
            json=_propose_body(request_id=request_id),
        )
        assert second.status_code == 409
