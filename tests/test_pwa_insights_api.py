"""API tests for the G28 insights endpoints (``puspa/pwa/insights_api.py``).

Covers the HTTP layer only (the report maths live in
``tests/test_billing_insights.py``): a logged-in user gets their
report over N days, ``days`` outside 1..90 is a 400, and a user
with no recorded turns gets an honest all-zero report.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in G28): the routes are mounted there with the app's own
``need_user`` login resolver, so the paths, login gate, status
codes, and JSON shapes tested here are the contract the live
PWA serves.
"""

import psycopg
from fastapi.testclient import TestClient

from puspa.billing.ledger import PostgresUsageStore, UsageRow, create_schema
from puspa.pwa.app import create_app


def _build_app(test_user_id, test_dsn):
    return create_app(test_user_id=test_user_id, dsn=test_dsn)


def _row(user_id, **overrides):
    values = {
        "user_id": user_id,
        "bot_id": "bot-a",
        "task_id": "task-1",
        "driver": "openrouter",
        "model": "cheap-model",
        "input_tokens": 1000,
        "output_tokens": 200,
        "cached_tokens": 100,
        "cost_idr": 500,
        "trigger": "morning-briefing",
        "outcome": "done",
        "steps": 3,
        "tools": ["read"],
        "tool_errors": 0,
        "provider": "openrouter",
        "module_id": "briefing",
    }
    values.update(overrides)
    return UsageRow(**values)


def test_insights_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    assert "/api/insights" in {route.path for route in app.routes}
    with TestClient(app) as client:
        assert client.get("/api/insights").status_code == 200
    # Same route without login is a 401, not a 404.
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/insights").status_code == 401


def test_get_insights_reports_recorded_turns(make_user, test_dsn):
    user_id = make_user()["user_id"]
    create_schema(test_dsn)
    store = PostgresUsageStore(test_dsn)
    try:
        store.append(_row(user_id, cost_idr=800))
        store.append(_row(user_id, cost_idr=400, task_id="task-2"))
        with TestClient(_build_app(user_id, test_dsn)) as client:
            resp = client.get("/api/insights?days=7")
        assert resp.status_code == 200
        body = resp.json()
        assert body["window_days"] == 7
        assert body["turns"] == 2
        assert body["cost"]["total_idr"] == 1200
        assert body["cost"]["label"] == "Rp1.200"
        assert body["tokens"]["total"] == 2600
        assert body["per_model"][0]["model"] == "cheap-model"
        assert body["task_cost_ceiling_idr"] == 2000
        assert len(body["activity_per_day"]) == 7
    finally:
        with psycopg.connect(test_dsn) as conn:
            conn.execute("DELETE FROM billing_usage_rows WHERE user_id = %s", (user_id,))
            conn.commit()


def test_get_insights_empty_user_gets_honest_zeros(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn)) as client:
        resp = client.get("/api/insights")
    assert resp.status_code == 200
    body = resp.json()
    assert body["turns"] == 0
    assert body["cost"]["total_idr"] == 0
    assert body["cost"]["label"] == "Rp0"
    assert body["cost"]["per_1k_tokens_label"] is None
    assert "~" not in resp.text


def test_get_insights_rejects_bad_days(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn)) as client:
        assert client.get("/api/insights?days=0").status_code == 400
        assert client.get("/api/insights?days=91").status_code == 400
        assert client.get("/api/insights?days=30").status_code == 200


def test_insights_require_login(test_dsn):
    with TestClient(_build_app(None, test_dsn)) as client:
        resp = client.get("/api/insights")
    assert resp.status_code == 401
    assert resp.json()["error"] == "belum masuk"
