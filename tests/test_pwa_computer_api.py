"""API tests for the V2-35 computer endpoints (``puspa.pwa.computer_api``).

Covers the HTTP layer only (tiers, allowlist, stop, and audit
rules live in ``tests/test_computer_tiers.py``): a logged-in
user can list, add, and revoke allowlisted apps, request and
clear the emergency stop, and read the audit list; an
anonymous user gets 401s, not 404s.

These tests run against the real ``puspa.pwa.app.create_app``:
the routes are mounted there with the app's own ``need_user``
login resolver, so the paths, login gate, status codes, and
JSON shapes tested here are the contract the live PWA serves.
The allowlist and stop files live under
``PUSPA_COMPUTER_STATE_DIR`` (tmp_path here), never under the
real home dir.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from puspa.pwa.app import create_app


def _build_app(test_user_id, test_dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("PUSPA_COMPUTER_STATE_DIR", str(tmp_path))
    return create_app(test_user_id=test_user_id, dsn=test_dsn)


def test_computer_routes_exist_on_create_app(make_user, test_dsn, tmp_path, monkeypatch):
    user_id = make_user()["user_id"]
    app = _build_app(user_id, test_dsn, tmp_path, monkeypatch)
    paths = {route.path for route in app.routes}
    assert "/api/computer/allowlist" in paths
    assert "/api/computer/allowlist/{app}" in paths
    assert "/api/computer/stop" in paths
    assert "/api/computer/stop/clear" in paths
    assert "/api/computer/audit" in paths
    with TestClient(app) as client:
        assert client.get("/api/computer/allowlist").status_code == 200
        assert client.get("/api/computer/stop").status_code == 200
        assert client.get("/api/computer/audit").status_code == 200


def test_computer_routes_require_login(test_dsn, tmp_path, monkeypatch):
    anon = _build_app(None, test_dsn, tmp_path, monkeypatch)
    with TestClient(anon) as client:
        assert client.get("/api/computer/allowlist").status_code == 401
        assert client.post("/api/computer/allowlist", json={"app": "Notes"}).status_code == 401
        assert client.delete("/api/computer/allowlist/Notes").status_code == 401
        assert client.get("/api/computer/stop").status_code == 401
        assert client.post("/api/computer/stop", json={}).status_code == 401
        assert client.post("/api/computer/stop/clear", json={}).status_code == 401
        assert client.get("/api/computer/audit").status_code == 401


def test_allowlist_add_list_revoke(make_user, test_dsn, tmp_path, monkeypatch):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn, tmp_path, monkeypatch)) as client:
        assert client.get("/api/computer/allowlist").json() == {"apps": []}
        resp = client.post("/api/computer/allowlist", json={"app": "Notes"})
        assert resp.status_code == 200
        assert resp.json() == {"app": "Notes", "allowed": True, "changed": True}
        assert client.get("/api/computer/allowlist").json() == {"apps": ["Notes"]}
        resp = client.post("/api/computer/allowlist", json={"app": "  "})
        assert resp.status_code == 400
        resp = client.delete("/api/computer/allowlist/Notes")
        assert resp.status_code == 200
        assert resp.json() == {"app": "Notes", "allowed": False, "changed": True}
        assert client.get("/api/computer/allowlist").json() == {"apps": []}


def test_stop_request_status_clear(make_user, test_dsn, tmp_path, monkeypatch):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn, tmp_path, monkeypatch)) as client:
        assert client.get("/api/computer/stop").json()["active"] is False
        resp = client.post("/api/computer/stop", json={"reason": "halt"})
        assert resp.status_code == 200
        assert resp.json()["active"] is True
        assert resp.json()["reason"] == "halt"
        assert client.get("/api/computer/stop").json()["active"] is True
        resp = client.post("/api/computer/stop/clear", json={"reason": "go"})
        assert resp.status_code == 200
        assert resp.json()["active"] is False


def test_audit_lists_entries_and_reports_ledger(make_user, test_dsn, tmp_path, monkeypatch):
    user_id = make_user()["user_id"]
    with TestClient(_build_app(user_id, test_dsn, tmp_path, monkeypatch)) as client:
        resp = client.get("/api/computer/audit?limit=5")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ledger_available"] is True
        assert body["entries"] == []
        client.post("/api/computer/stop", json={"reason": "halt"})
        body = client.get("/api/computer/audit?limit=5").json()
        assert body["entries"][0]["computer_stop"] == "requested"
        assert client.get("/api/computer/audit?limit=500").status_code == 400
