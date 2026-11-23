"""Desktop Google OAuth config (M8-fix).

Covers: client ID/secret from the user-owned config file (env
wins), the human HTML pages on the browser-facing OAuth routes
(never raw JSON), and GET /api/desktop/config, which the native
app checks before opening the browser.
"""

import json

import pytest
from fastapi.testclient import TestClient

from puspa.auth.google_oauth import (
    OAUTH_CONFIG_FILE_ENV,
    google_client_id,
    google_oauth_configured,
)
from puspa.pwa.app import create_app


@pytest.fixture
def no_oauth_env(monkeypatch, tmp_path):
    """No client in env and no config file anywhere."""
    monkeypatch.delenv("PUSPA_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("PUSPA_GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv(OAUTH_CONFIG_FILE_ENV, str(tmp_path / "nope.json"))
    return tmp_path


def _write_config(path, client_id="client-123", secret="secret-abc"):
    path.write_text(
        json.dumps({"client_id": client_id, "client_secret": secret}),
        encoding="utf-8",
    )


def test_config_file_provides_client_id_and_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("PUSPA_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("PUSPA_GOOGLE_CLIENT_SECRET", raising=False)
    config = tmp_path / "google-oauth.json"
    _write_config(config)
    monkeypatch.setenv(OAUTH_CONFIG_FILE_ENV, str(config))
    assert google_client_id() == "client-123"
    assert google_oauth_configured() is True


def test_env_wins_over_config_file(monkeypatch, tmp_path):
    config = tmp_path / "google-oauth.json"
    _write_config(config, client_id="from-file", secret="file-secret")
    monkeypatch.setenv(OAUTH_CONFIG_FILE_ENV, str(config))
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_ID", "from-env")
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_SECRET", "env-secret")
    assert google_client_id() == "from-env"
    assert google_oauth_configured() is True


def test_corrupt_config_file_is_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("PUSPA_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("PUSPA_GOOGLE_CLIENT_SECRET", raising=False)
    config = tmp_path / "google-oauth.json"
    config.write_text("not json at all", encoding="utf-8")
    monkeypatch.setenv(OAUTH_CONFIG_FILE_ENV, str(config))
    assert google_client_id() == ""
    assert google_oauth_configured() is False


def test_auth_google_without_config_returns_human_page(no_oauth_env):
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/auth/google", follow_redirects=False)
        assert resp.status_code == 503
        assert "text/html" in resp.headers["content-type"]
        assert "Login Google belum disiapkan" in resp.text
        assert '{"error"' not in resp.text


def test_auth_google_with_file_config_redirects(monkeypatch, tmp_path):
    monkeypatch.delenv("PUSPA_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("PUSPA_GOOGLE_CLIENT_SECRET", raising=False)
    config = tmp_path / "google-oauth.json"
    _write_config(config)
    monkeypatch.setenv(OAUTH_CONFIG_FILE_ENV, str(config))
    monkeypatch.setenv("PUSPA_GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/auth/google", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "accounts.google.com" in resp.headers["location"]


def test_desktop_config_endpoint_reports_status(no_oauth_env, monkeypatch, tmp_path):
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/api/desktop/config")
        assert resp.status_code == 200
        assert resp.json() == {"google_configured": False}
    config = tmp_path / "google-oauth.json"
    _write_config(config)
    monkeypatch.setenv(OAUTH_CONFIG_FILE_ENV, str(config))
    with TestClient(app) as client:
        resp = client.get("/api/desktop/config")
        assert resp.status_code == 200
        assert resp.json() == {"google_configured": True}


def test_callback_state_mismatch_returns_human_page(no_oauth_env):
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/auth/google/callback?code=abc&state=xyz", follow_redirects=False)
        assert resp.status_code == 400
        assert "text/html" in resp.headers["content-type"]
        assert "Login tidak cocok" in resp.text
        assert '{"error"' not in resp.text
