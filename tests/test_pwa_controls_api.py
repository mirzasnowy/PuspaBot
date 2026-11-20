"""API tests for the P-02 control endpoints (``puspa.pwa.controls_api``).

Covers the HTTP layer only (the dial rules live in
``tests/test_autonomy_dial.py`` and ``tests/test_autonomy_controls.py``,
the report maths in ``tests/test_weekly_report.py``, the stop
package in ``tests/test_stop_all.py``): a logged-in user gets
the dial state, can move a ceiling only with evidence, can stop
everything with one press, and gets their weekly report; an
anonymous user gets 401s, not 404s.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in P-02): the routes are mounted there with the app's
own ``need_user`` login resolver, so the paths, login gate,
status codes, and JSON shapes tested here are the contract
the live PWA serves.
"""

from __future__ import annotations

import psycopg
from fastapi.testclient import TestClient

from puspa.policy import RevocationList
from puspa.pwa.app import create_app


def _build_app(test_user_id, test_dsn):
    return create_app(test_user_id=test_user_id, dsn=test_dsn)


def _revocations(test_dsn):
    """Durable revocation list on the test database, emptied per test."""
    store = RevocationList(test_dsn)
    store.create_schema()
    with psycopg.connect(test_dsn, autocommit=True) as conn:
        conn.execute("TRUNCATE token_revocations, agent_revocations")
    return store


def test_control_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/stop-all" in paths
    assert "/api/autonomy-dial" in paths
    assert "/api/autonomy-dial/{area}/ceiling" in paths
    assert "/api/weekly-report" in paths
    with TestClient(app) as client:
        assert client.get("/api/autonomy-dial").status_code == 200
        assert client.get("/api/weekly-report").status_code == 200


def test_control_routes_require_login(test_dsn):
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/autonomy-dial").status_code == 401
        assert client.get("/api/weekly-report").status_code == 401
        assert client.post("/api/stop-all").status_code == 401
        assert client.post("/api/autonomy-dial/messaging/ceiling").status_code == 401


def test_get_dial_lists_five_areas_with_money_locked(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn)) as client:
        resp = client.get("/api/autonomy-dial")
    assert resp.status_code == 200
    body = resp.json()
    assert [area["area"] for area in body["areas"]] == [
        "money",
        "messaging",
        "files",
        "scheduling",
        "shopping",
    ]
    assert body["threshold"] == 10
    assert body["threshold_text"] == "10 accepted drafts in a row"
    money = body["areas"][0]
    assert money["label"] == "Money"
    assert money["locked"] is True
    assert money["ceiling"] == "ask_first"
    assert money["effective_stage"] == "propose"
    assert money["suggestion"] is None
    messaging = body["areas"][1]
    assert messaging["streak"] == 0
    assert messaging["suggestion"] is None


def test_raising_money_ceiling_is_forbidden(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn)) as client:
        resp = client.post("/api/autonomy-dial/money/ceiling", json={"ceiling": "balanced"})
    assert resp.status_code == 403
    assert "locked" in resp.json()["error"]


def test_raising_without_evidence_is_a_conflict(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn)) as client:
        resp = client.post("/api/autonomy-dial/messaging/ceiling", json={"ceiling": "balanced"})
    assert resp.status_code == 409
    assert "10 accepted drafts" in resp.json()["error"]


def test_raising_with_ten_accepted_drafts_succeeds(make_user, test_dsn, ledger):
    user = make_user()
    for index in range(10):
        ledger.append_event(
            task_id=f"task-{index}",
            agent_id=user["agent_id"],
            event_type="approval_granted",
            content={"kind": "send", "request_id": f"req-{index}"},
        )
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        before = client.get("/api/autonomy-dial").json()
        messaging = next(area for area in before["areas"] if area["area"] == "messaging")
        assert messaging["streak"] == 10
        assert messaging["suggestion"]["to_ceiling"] == "balanced"
        resp = client.post("/api/autonomy-dial/messaging/ceiling", json={"ceiling": "balanced"})
    assert resp.status_code == 200
    assert resp.json()["ceiling"] == "balanced"
    assert resp.json()["area"] == "messaging"


def test_ceiling_endpoints_reject_bad_input(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn)) as client:
        assert (
            client.post(
                "/api/autonomy-dial/teleportation/ceiling", json={"ceiling": "balanced"}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/autonomy-dial/messaging/ceiling", json={"ceiling": "turbo"}
            ).status_code
            == 400
        )
        assert (
            client.post("/api/autonomy-dial/messaging/ceiling", json={"ceiling": ""}).status_code
            == 400
        )


def test_lowering_a_ceiling_is_always_allowed(make_user, test_dsn, ledger):
    user = make_user()
    for index in range(10):
        ledger.append_event(
            task_id=f"task-{index}",
            agent_id=user["agent_id"],
            event_type="approval_granted",
            content={"kind": "send", "request_id": f"req-{index}"},
        )
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        raised = client.post("/api/autonomy-dial/messaging/ceiling", json={"ceiling": "balanced"})
        assert raised.status_code == 200
        lowered = client.post("/api/autonomy-dial/messaging/ceiling", json={"ceiling": "ask_first"})
    assert lowered.status_code == 200
    assert lowered.json()["ceiling"] == "ask_first"


def test_stop_all_stops_the_users_agents(make_user, test_dsn, ledger):
    user = make_user()
    _revocations(test_dsn)
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.post("/api/stop-all")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["agents_stopped"]) == 1
    assert body["agents_stopped"][0]["agent_id"] == user["agent_id"]
    assert body["agents_stopped"][0]["refresh_blocked"] is True
    assert body["ledger_recorded"] is True
    assert body["audit_trail_url"] == "/api/proof/audit-trail"
    assert body["elapsed_seconds"] < 5.0
    events = ledger.events(body["task_id"], event_type="task_stopped")
    assert len(events) == 1


def test_weekly_report_counts_the_users_week(make_user, test_dsn, ledger):
    user = make_user()
    ledger.append_event(
        task_id="task-a", agent_id=user["agent_id"], event_type="task_completed", content={}
    )
    ledger.append_event(
        task_id="task-a",
        agent_id=user["agent_id"],
        event_type="approval_rejected",
        content={"request_id": "req-1"},
    )
    with TestClient(_build_app(user["user_id"], test_dsn)) as client:
        resp = client.get("/api/weekly-report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["window_days"] == 7
    assert body["tasks_completed"] == 1
    assert body["tasks_corrected"] == 1
    assert body["time_saved_minutes"] is None
    assert body["money_saved_idr"] is None
    assert body["audit_trail_url"] == "/api/proof/audit-trail"
    assert len(body["linked_events"]) == 2
    assert all(
        item["receipt_url"].startswith("/api/proof/receipts/") for item in body["linked_events"]
    )


def test_weekly_report_rejects_bad_days(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn)) as client:
        assert client.get("/api/weekly-report?days=0").status_code == 400
        assert client.get("/api/weekly-report?days=91").status_code == 400
        assert client.get("/api/weekly-report?days=30").status_code == 200
