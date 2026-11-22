"""API tests for the telemetry toggle in Settings (AST-09).

``GET /api/telemetry/settings`` reports the stored toggle, the
environment override (if any), and the effective switch;
``POST /api/telemetry/settings`` saves the toggle. Saving the
toggle never sends anything by itself — it only records the
choice. No test touches the network.
"""

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.telemetry.settings import create_schema


@pytest.fixture
def client(make_user, test_dsn):
    user = make_user()
    create_schema(test_dsn)
    app = create_app(test_user_id=user["user_id"])
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.user_id = user["user_id"]
        yield test_client


def test_get_settings_defaults_to_off(client):
    resp = client.get("/api/telemetry/settings")
    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": False,
        "env_override": None,
        "effective_enabled": False,
    }


def test_save_toggle_on_then_off(client):
    resp = client.post("/api/telemetry/settings", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": True,
        "env_override": None,
        "effective_enabled": True,
    }
    resp = client.get("/api/telemetry/settings")
    assert resp.json()["enabled"] is True
    assert resp.json()["effective_enabled"] is True
    resp = client.post("/api/telemetry/settings", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["effective_enabled"] is False


def test_save_refuses_non_boolean_and_bad_bodies(client):
    for body in (
        {"enabled": "yes"},
        {"enabled": 1},
        {"enabled": None},
        {},
        {"other": True},
    ):
        resp = client.post("/api/telemetry/settings", json=body)
        assert resp.status_code == 400, body
    resp = client.post("/api/telemetry/settings", json=[True])
    assert resp.status_code == 400
    assert client.get("/api/telemetry/settings").json()["enabled"] is False


def test_env_flag_overrides_the_toggle_in_state(client, monkeypatch):
    client.post("/api/telemetry/settings", json={"enabled": True})
    monkeypatch.setenv("PUSPA_TELEMETRY", "0")
    resp = client.get("/api/telemetry/settings")
    assert resp.json() == {
        "enabled": True,
        "env_override": False,
        "effective_enabled": False,
    }
    monkeypatch.setenv("PUSPA_TELEMETRY", "1")
    resp = client.get("/api/telemetry/settings")
    assert resp.json()["env_override"] is True
    assert resp.json()["effective_enabled"] is True


def test_env_flag_on_with_toggle_off_still_reports_effective_on(client, monkeypatch):
    monkeypatch.setenv("PUSPA_TELEMETRY", "1")
    resp = client.get("/api/telemetry/settings")
    assert resp.json() == {
        "enabled": False,
        "env_override": True,
        "effective_enabled": True,
    }


def test_settings_require_login(make_user, test_dsn):
    user = make_user()
    create_schema(test_dsn)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as anon:
        assert anon.get("/api/telemetry/settings").status_code == 401
        assert anon.post("/api/telemetry/settings", json={"enabled": True}).status_code == 401
    assert user["user_id"] is not None
