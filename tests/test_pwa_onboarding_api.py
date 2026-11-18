"""API tests for the G28 onboarding + hints endpoints
(``puspa/pwa/onboarding_api.py``).

Covers the HTTP layer only (the 4-step flow lives in
``tests/test_onboarding.py``, the hint rules in
``tests/test_onboarding_hints.py``): start + 4 steps end to end,
due hints for the branch the client reports, marking a hint
seen, validation failures as 400, and the login gate.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in G28): the routes are mounted there with the app's own
``need_user`` login resolver, so the paths, login gate, status
codes, and JSON shapes tested here are the contract the live
PWA serves.
"""

from fastapi.testclient import TestClient

from puspa.onboarding.hints import create_schema as create_hint_schema
from puspa.pwa.app import create_app
from puspa.pwa.onboarding_api import parse_hint_context


def _build_app(test_user_id, test_dsn):
    return create_app(test_user_id=test_user_id, dsn=test_dsn)


def test_onboarding_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    for path in (
        "/api/onboarding",
        "/api/onboarding/start",
        "/api/onboarding/step1",
        "/api/onboarding/step2",
        "/api/onboarding/step3",
        "/api/onboarding/first-task",
        "/api/onboarding/hints",
        "/api/onboarding/hints/{flag}/seen",
    ):
        assert path in paths, path
    with TestClient(app) as client:
        assert client.get("/api/onboarding").status_code == 200
        assert client.get("/api/onboarding/hints").status_code == 200
        # Unknown flag seen is a 400 on the real app too.
        assert client.post("/api/onboarding/hints/not_a_hint/seen").status_code == 400
    # Same routes without login are 401, not 404.
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/onboarding").status_code == 401
        assert client.get("/api/onboarding/hints").status_code == 401
        assert client.post("/api/onboarding/hints/first_approval/seen").status_code == 401


def test_parse_hint_context_typed():
    assert parse_hint_context({}) == {}
    assert parse_hint_context({"screen": "modules", "first_visit": "true"}) == {
        "screen": "modules",
        "first_visit": True,
    }
    assert parse_hint_context({"approval_requested": "1", "memory_used": "yes"}) == {
        "approval_requested": True,
        "memory_used": True,
    }
    assert parse_hint_context({"task_cost_idr": "2500", "billing_rows": "3"}) == {
        "task_cost_idr": 2500,
        "billing_rows": 3,
    }
    assert parse_hint_context({"unknown_key": "x"}) == {}
    import pytest

    with pytest.raises(ValueError, match="must be an integer"):
        parse_hint_context({"task_cost_idr": "banyak"})


def test_onboarding_status_before_start(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn)) as client:
        resp = client.get("/api/onboarding")
    assert resp.status_code == 200
    assert resp.json() == {"started": False, "current_step": 0}


def test_onboarding_four_steps_end_to_end(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn)) as client:
        started = client.post("/api/onboarding/start")
        assert started.status_code == 200
        assert started.json()["started"] is True
        assert started.json()["current_step"] == 0

        step1 = client.post("/api/onboarding/step1", json={"name": "Nero", "style": "santai"})
        assert step1.status_code == 200
        assert step1.json()["current_step"] == 1
        assert step1.json()["name"] == "Nero"

        # Step 2 honestly reports the notification test as NOT sent:
        # Web Push is not built yet, so nothing is faked as delivered.
        step2 = client.post("/api/onboarding/step2")
        assert step2.status_code == 200
        assert step2.json()["current_step"] == 2
        assert step2.json()["notification_sent"] is False

        step3 = client.post("/api/onboarding/step3", json={"connector": "nanti"})
        assert step3.status_code == 200
        assert step3.json()["current_step"] == 3

        first_task = client.post(
            "/api/onboarding/first-task",
            json={"event_type": "compare_prices", "target": "Asus Vivobook 14"},
        )
        assert first_task.status_code == 200
        body = first_task.json()
        assert body["verified"] is False  # honest stub: wedges not built yet
        assert body["status"]["current_step"] == 4
        assert body["offered_workflow_templates"] == [
            "Tagihan bulanan",
            "Pantau harga barang",
            "Rekap tagihan minggu ini",
        ]

        status = client.get("/api/onboarding")
        assert status.json()["started"] is True
        assert status.json()["current_step"] == 4


def test_onboarding_step_validation_is_400(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn)) as client:
        # Step before start: onboarding not started yet.
        assert (
            client.post(
                "/api/onboarding/step1", json={"name": "Nero", "style": "santai"}
            ).status_code
            == 400
        )
        assert client.post("/api/onboarding/start").status_code == 200
        # Unknown style, unknown connector, unknown first-task type.
        assert (
            client.post(
                "/api/onboarding/step1", json={"name": "Nero", "style": "galak"}
            ).status_code
            == 400
        )
        assert (
            client.post("/api/onboarding/step3", json={"connector": "whatsapp"}).status_code == 400
        )
        assert (
            client.post(
                "/api/onboarding/first-task",
                json={"event_type": "main_game", "target": "x"},
            ).status_code
            == 400
        )


def test_hints_due_then_seen(make_user, test_dsn):
    user_id = make_user()["user_id"]
    create_hint_schema(test_dsn)
    with TestClient(_build_app(user_id, test_dsn)) as client:
        resp = client.get("/api/onboarding/hints?screen=modules&first_visit=true")
        assert resp.status_code == 200
        hints = resp.json()["hints"]
        assert [hint["flag"] for hint in hints] == ["first_modules_visit"]
        assert hints[0]["screen"] == "modules"
        assert "http" not in hints[0]["body"]

        # No branch reached: no hints due.
        assert client.get("/api/onboarding/hints").json() == {"hints": []}

        seen = client.post("/api/onboarding/hints/first_modules_visit/seen")
        assert seen.status_code == 200
        assert seen.json() == {
            "user_id": user_id,
            "flag": "first_modules_visit",
            "seen": True,
        }
        # Seen once, never due again.
        assert client.get("/api/onboarding/hints?screen=modules&first_visit=true").json() == {
            "hints": []
        }
        # Unknown flag is a 400, not a silent no-op.
        assert client.post("/api/onboarding/hints/not_a_hint/seen").status_code == 400


def test_onboarding_routes_require_login(test_dsn):
    with TestClient(_build_app(None, test_dsn)) as client:
        assert client.get("/api/onboarding").status_code == 401
        assert client.post("/api/onboarding/start").status_code == 401
        assert client.get("/api/onboarding/hints").status_code == 401
        assert client.post("/api/onboarding/hints/first_approval/seen").status_code == 401
