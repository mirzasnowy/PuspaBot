"""API + Jinja tests for the local admin dashboard (AST-10).

Covers the HTTP layer only (the readers live in
``puspa.pwa.admin_api``): a logged-in operator reads and edits
the instance configuration, lists and revokes sessions, reads
usage statistics counted from the ledger, and enables/disables
installed skills through the skill registry; an anonymous user
gets 401s (or the login redirect on the page), and in hosted
serve mode the whole surface answers 403, because the hosted
operator panel lives in the private cloud backend, not here.
The ``/admin`` page renders from its Jinja template (like
``/trial``: it must work with no React build).

These tests run against the real ``puspa.pwa.app.create_app``:
the routes are mounted there with the app's own ``need_user``
login resolver, so the paths, login gate, status codes, and
JSON shapes tested here are the contract the live PWA serves.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from puspa.accounts.ports import _connect, create_session
from puspa.marketplace.discovery import SKILL_INSTALL_ENV_VAR
from puspa.pwa import admin_settings
from puspa.pwa.app import create_app
from puspa.pwa.model_settings import get_selection

SKILL_TEXT = """---
name: "Weather Watcher"
description: "Watches the weather for you."
author: "Acme"
---

# Weather Watcher

Check the forecast before you go out.
"""

MIT_LICENSE_TEXT = """MIT License

Copyright (c) 2026 Acme

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files.
"""

RAW_BASE = "https://raw.githubusercontent.com/acme/weather-watcher/main"


def _mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(RAW_BASE):
            rest = url[len(RAW_BASE) + 1 :]
            if rest == "LICENSE":
                return httpx.Response(200, text=MIT_LICENSE_TEXT)
            if rest in (
                "skills/weather-watcher/SKILL.md",
                "weather-watcher/SKILL.md",
                "SKILL.md",
            ):
                return httpx.Response(200, text=SKILL_TEXT)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _clean_instance_settings(test_dsn):
    """The instance name is global, not per-user, so the table is
    emptied for every test (conftest only truncates its own list)."""
    admin_settings.create_schema(test_dsn)
    with _connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM instance_settings")
        conn.commit()
    yield
    with _connect(test_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM instance_settings")
        conn.commit()


@pytest.fixture(autouse=True)
def _skill_install_enabled(monkeypatch):
    monkeypatch.setenv(SKILL_INSTALL_ENV_VAR, "1")


@pytest.fixture
def client(make_user, test_dsn, tmp_path):
    user = make_user()
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    app.state.hub_http_client = httpx.Client(transport=_mock_transport())
    app.state.hub_skills_root = tmp_path / "skills"
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.user_id = user["user_id"]
        test_client.agent_id = user["agent_id"]
        test_client.app = app
        yield test_client


@pytest.fixture
def anon_client(test_dsn):
    app = create_app(dsn=test_dsn)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _install_weather_watcher(client):
    preview = client.get("/api/hub/preview", params={"source": "acme/weather-watcher"})
    assert preview.status_code == 200
    installed = client.post(
        "/api/hub/install",
        json={"source": "acme/weather-watcher", "expected_sha256": preview.json()["sha256"]},
    )
    assert installed.status_code == 200
    return installed.json()


# ---------------------------------------------------------------------------
# Routes exist + login gate
# ---------------------------------------------------------------------------


def test_admin_routes_exist_on_create_app(client):
    paths = {route.path for route in client.app.routes}
    assert "/api/admin/config" in paths
    assert "/api/admin/sessions" in paths
    assert "/api/admin/sessions/{session_id}/revoke" in paths
    assert "/api/admin/sessions/revoke-all" in paths
    assert "/api/admin/usage" in paths
    assert "/api/admin/skills" in paths
    assert "/api/admin/skills/{skill_id}/enable" in paths
    assert "/api/admin/skills/{skill_id}/disable" in paths
    assert "/admin" in paths


def test_admin_requires_login(anon_client):
    for path in (
        "/api/admin/config",
        "/api/admin/sessions",
        "/api/admin/usage",
        "/api/admin/skills",
    ):
        assert anon_client.get(path).status_code == 401
    assert anon_client.post("/api/admin/config", json={"instance_name": "X"}).status_code == 401
    assert anon_client.post("/api/admin/sessions/revoke-all").status_code == 401
    resp = anon_client.get("/admin", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert "/masuk" in resp.headers["location"]


def test_admin_page_renders(client):
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "Admin" in resp.text
    assert "Konfigurasi instansi" in resp.text
    assert "Sesi" in resp.text
    assert "Statistik pemakaian" in resp.text
    assert "Skill" in resp.text
    assert "/api/admin/config" in resp.text


def test_admin_page_jinja_template_without_dist(client, monkeypatch, tmp_path):
    """The Jinja template must exist: the page is Jinja-only (like
    /trial), so a missing template would 500 here while the local
    dist/ build hides nothing — there is no SPA fallback."""
    import puspa.pwa.app as app_module

    monkeypatch.setattr(app_module, "DIST_DIR", tmp_path / "no-dist-here")
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "Admin" in resp.text
    assert "/api/admin/config" in resp.text


def test_admin_blocked_in_hosted_mode(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "serve_mode", "hosted")
    assert client.get("/admin").status_code == 403
    assert "lokal" in client.get("/admin").text
    assert client.get("/api/admin/config").status_code == 403
    assert client.get("/api/admin/sessions").status_code == 403
    assert client.get("/api/admin/usage").status_code == 403
    assert client.get("/api/admin/skills").status_code == 403
    assert client.post("/api/admin/config", json={"instance_name": "X"}).status_code == 403


# ---------------------------------------------------------------------------
# Instance configuration
# ---------------------------------------------------------------------------


def test_config_get_defaults(client):
    body = client.get("/api/admin/config").json()
    assert body["instance_name"] == "Puspa"
    assert body["serve_mode"] == "local"
    assert "PUSPA_SERVE_MODE" in body["serve_mode_change"]
    assert body["model"]["source"] == "hosted"
    assert body["telemetry"]["enabled"] is False
    assert body["telemetry"]["effective_enabled"] is False


def test_config_save_instance_name(client):
    resp = client.post("/api/admin/config", json={"instance_name": "  Puspa Rumah  "})
    assert resp.status_code == 200
    assert resp.json()["instance_name"] == "Puspa Rumah"
    assert client.get("/api/admin/config").json()["instance_name"] == "Puspa Rumah"
    assert admin_settings.get_instance_name(dsn=client.app.state.dsn) == "Puspa Rumah"
    assert "Puspa Rumah" in client.get("/admin").text


def test_config_save_rejects_bad_values(client):
    for body in (
        {"instance_name": ""},
        {"instance_name": "   "},
        {"instance_name": "x" * 81},
        {"instance_name": 42},
        {"telemetry_enabled": "yes"},
        {"telemetry_enabled": 1},
        {"model": "local"},
        {"model": {"source": "local", "api_key": "secret-123"}},
        {"model": {"source": "nope"}},
        {"unknown_key": True},
        {},
    ):
        resp = client.post("/api/admin/config", json=body)
        assert resp.status_code == 400, body
    resp = client.post("/api/admin/config", json=[True])
    assert resp.status_code == 400
    assert client.get("/api/admin/config").json()["instance_name"] == "Puspa"


def test_config_save_model_and_telemetry(client, test_dsn):
    resp = client.post(
        "/api/admin/config",
        json={
            "model": {"source": "local", "base_url": "http://localhost:11434/v1"},
            "telemetry_enabled": True,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["model"]["source"] == "local"
    assert resp.json()["model"]["base_url"] == "http://localhost:11434/v1"
    assert resp.json()["telemetry"]["enabled"] is True
    assert resp.json()["telemetry"]["effective_enabled"] is True
    stored = get_selection(client.user_id, dsn=test_dsn)
    assert stored.source == "local"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_sessions_list_and_revoke_one(client, test_dsn):
    dsn = test_dsn
    token_a = create_session(client.user_id, "laptop", dsn=dsn)
    token_b = create_session(client.user_id, "ponsel", dsn=dsn)
    assert token_a and token_b
    body = client.get("/api/admin/sessions").json()
    assert body["active"] == 2
    assert body["revoked"] == 0
    labels = {s["device_label"] for s in body["sessions"]}
    assert labels == {"laptop", "ponsel"}
    assert body["pairing"]["active_sessions"] == 0
    target = next(s for s in body["sessions"] if s["device_label"] == "ponsel")
    resp = client.post(f"/api/admin/sessions/{target['id']}/revoke")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "session_id": target["id"], "revoked": True}
    # Idempotent: revoking again still succeeds.
    assert client.post(f"/api/admin/sessions/{target['id']}/revoke").status_code == 200
    body = client.get("/api/admin/sessions").json()
    assert body["active"] == 1
    assert body["revoked"] == 1
    revoked = next(s for s in body["sessions"] if s["id"] == target["id"])
    assert revoked["revoked"] is True
    assert (
        client.post("/api/admin/sessions/00000000-0000-0000-0000-000000000000/revoke").status_code
        == 404
    )
    assert client.post("/api/admin/sessions/bukan-uuid/revoke").status_code == 404


def test_revoke_other_users_session_is_404(client, make_user, test_dsn):
    other = make_user()
    create_session(other["user_id"], "laptop-orang-lain", dsn=test_dsn)
    from puspa.accounts.ports import list_sessions

    other_id = list_sessions(other["user_id"], dsn=test_dsn)[0]["id"]
    assert client.post(f"/api/admin/sessions/{other_id}/revoke").status_code == 404


def test_revoke_all_sessions(client, test_dsn):
    create_session(client.user_id, "laptop", dsn=test_dsn)
    create_session(client.user_id, "ponsel", dsn=test_dsn)
    assert client.get("/api/admin/sessions").json()["active"] == 2
    resp = client.post("/api/admin/sessions/revoke-all")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "revoked_all": True}
    body = client.get("/api/admin/sessions").json()
    assert body["active"] == 0
    assert body["revoked"] == 2


# ---------------------------------------------------------------------------
# Usage statistics (counted from the ledger, no new telemetry)
# ---------------------------------------------------------------------------


def test_usage_counts_ledger_events(client, ledger):
    ledger.append_event(
        task_id="tugas-admin-1",
        agent_id=client.agent_id,
        event_type="user_message",
        content={},
    )
    ledger.append_event(
        task_id="tugas-admin-1",
        agent_id=client.agent_id,
        event_type="user_message",
        content={},
    )
    ledger.append_event(
        task_id="tugas-admin-1",
        agent_id=client.agent_id,
        event_type="heartbeat",
        content={},
    )
    body = client.get("/api/admin/usage").json()
    by_type = {row["type"]: row["count"] for row in body["events_by_type"]}
    assert by_type["user_message"] == 2
    assert by_type.get("heartbeat", 0) >= 1
    assert body["events_total"] >= 3
    assert body["events_last_24h"] == body["events_total"]
    assert body["events_last_7d"] == body["events_total"]
    assert body["users"] == 1
    assert body["sessions_active"] == 0
    assert body["skills"]["total"] >= 1
    assert body["skills"]["builtin"] >= 1
    assert body["messages_in_memory"] == 0


def test_usage_empty_ledger_still_reports_consistent_counts(client):
    body = client.get("/api/admin/usage").json()
    assert body["events_last_24h"] == body["events_total"]
    assert body["events_last_7d"] == body["events_total"]
    assert body["users"] == 1
    assert sum(row["count"] for row in body["events_by_type"]) == body["events_total"]


# ---------------------------------------------------------------------------
# Skills (the existing registry: install is enable, uninstall is disable)
# ---------------------------------------------------------------------------


def test_skills_list_builtin(client):
    body = client.get("/api/admin/skills").json()
    ids = {s["id"] for s in body["skills"]}
    assert "find-skills" in ids
    builtin = next(s for s in body["skills"] if s["id"] == "find-skills")
    assert builtin["origin"] == "builtin"
    assert builtin["installed"] is True


def test_skills_enable_disable_installed(client):
    _install_weather_watcher(client)
    listed = client.get("/api/admin/skills", params={}).json()["skills"]
    watcher = next(s for s in listed if s["id"] == "weather-watcher")
    assert watcher["installed"] is True
    assert watcher["origin"] != "builtin"

    resp = client.post("/api/admin/skills/weather-watcher/disable")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"
    listed = client.get("/api/admin/skills").json()["skills"]
    assert next(s for s in listed if s["id"] == "weather-watcher")["installed"] is False

    resp = client.post("/api/admin/skills/weather-watcher/enable")
    assert resp.status_code == 200
    assert resp.json()["status"] == "enabled"
    listed = client.get("/api/admin/skills").json()["skills"]
    assert next(s for s in listed if s["id"] == "weather-watcher")["installed"] is True


def test_skills_builtin_toggle_refused_and_unknown_is_404(client):
    assert client.post("/api/admin/skills/find-skills/enable").status_code == 409
    assert client.post("/api/admin/skills/find-skills/disable").status_code == 409
    assert client.post("/api/admin/skills/skill-tidak-ada/enable").status_code == 404
    assert client.post("/api/admin/skills/skill-tidak-ada/disable").status_code == 404


def test_skills_disable_is_idempotent(client):
    _install_weather_watcher(client)
    assert client.post("/api/admin/skills/weather-watcher/disable").status_code == 200
    resp = client.post("/api/admin/skills/weather-watcher/disable")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"
    listed = client.get("/api/admin/skills").json()["skills"]
    assert next(s for s in listed if s["id"] == "weather-watcher")["installed"] is False
