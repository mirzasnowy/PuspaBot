"""API tests for Puspa-native subscription login (V2-28).

Covers the HTTP layer only (the flows live in
``tests/test_provider_login.py``): a logged-in user lists
provider states, starts a PKCE login and finishes it at the
callback, starts a device login and polls it to completion,
logs out, and an anonymous user gets 401s. The OAuth provider
is fake (a stub HTTP client injected through
``app.state.provider_login_http_client``); no live provider is
ever called and no token value ever appears in a response.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.router.oauth import TokenSet
from puspa.router.provider_login import VaultTokenStore

BASE_TIME = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)

OLD_ACCESS = "access-token-FAKE-OLD-7f3a"
OLD_REFRESH = "refresh-token-FAKE-OLD-9c1e"
DEVICE_CODE = "device-code-FAKE-1a2b"
USER_CODE = "USER-CODE-FAKE"

TOKEN_URL = "https://auth.example.test/oauth2/token"


class FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeOAuthProvider:
    """Fake provider answering device, PKCE, and refresh grants."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.device_polls = 0

    def post(self, url, data=None):
        data = dict(data or {})
        self.posts.append((url, data))
        if "device/code" in url:
            return FakeResponse(
                200,
                {
                    "device_code": DEVICE_CODE,
                    "user_code": USER_CODE,
                    "verification_uri": "https://auth.example.test/device",
                    "expires_in": 900,
                    "interval": 1,
                },
            )
        grant = data.get("grant_type")
        if grant == "authorization_code":
            return FakeResponse(
                200,
                {
                    "access_token": OLD_ACCESS,
                    "refresh_token": OLD_REFRESH,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            self.device_polls += 1
            if self.device_polls == 1:
                return FakeResponse(400, {"error": "authorization_pending"})
            return FakeResponse(
                200,
                {
                    "access_token": OLD_ACCESS,
                    "refresh_token": OLD_REFRESH,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        return FakeResponse(400, {"error": "unsupported_grant_type"})


def _build_app(test_user_id, test_dsn, http_client):
    app = create_app(test_user_id=test_user_id, dsn=test_dsn)
    app.state.provider_login_http_client = http_client
    return app


def test_provider_routes_exist_on_create_app(make_user, test_dsn, vault_master_key):
    user = make_user()
    app = _build_app(user["user_id"], test_dsn, FakeOAuthProvider())
    paths = {route.path for route in app.routes}
    assert "/api/providers" in paths
    assert "/api/providers/{provider}/login/start" in paths
    assert "/api/providers/callback" in paths
    assert "/api/providers/{provider}/device/poll" in paths
    assert "/api/providers/{provider}/logout" in paths


def test_list_providers_requires_login(test_dsn):
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/providers").status_code == 401
        assert client.post("/api/providers/xai/login/start").status_code == 401
        assert client.post("/api/providers/xai/logout").status_code == 401


def test_list_providers_shows_all_three_states(make_user, test_dsn, vault_master_key):
    user = make_user()
    store = VaultTokenStore(dsn=test_dsn)
    store.save(
        user["user_id"],
        "xai",
        TokenSet(
            access_token=OLD_ACCESS,
            refresh_token=OLD_REFRESH,
            expires_at=BASE_TIME + timedelta(seconds=3600),
            obtained_at=BASE_TIME,
        ),
    )
    with TestClient(_build_app(user["user_id"], test_dsn, FakeOAuthProvider())) as client:
        resp = client.get("/api/providers")
    assert resp.status_code == 200
    providers = {entry["provider"]: entry for entry in resp.json()["providers"]}
    assert set(providers) == {
        "antigravity",
        "claude",
        "cline",
        "codebuddy_cn",
        "codebuddy_intl",
        "cursor",
        "gemini_cli",
        "github_copilot",
        "gitlab",
        "glm",
        "grok_cli",
        "iflow",
        "kilocode",
        "kimi",
        "kiro",
        "minimax_code",
        "minimax_code_global",
        "muse",
        "nous",
        "openai_codex",
        "qoder",
        "qoder_cn",
        "xai",
    }
    # The stored token expires far in the future relative to the
    # real clock only when BASE_TIME is near now; the state is
    # either logged_in or expired, never a token value.
    assert providers["xai"]["state"] in ("logged_in", "expiring", "expired")
    assert providers["github_copilot"]["state"] == "not_logged_in"
    # Codex is wired since V2-29 (endpoints verified from the
    # 9Router registry), and Claude/Cursor need no client id.
    assert providers["openai_codex"]["login_ready"] is True
    assert providers["claude"]["login_ready"] is True
    assert providers["cursor"]["login_ready"] is True
    assert providers["kiro"]["login_ready"] is True
    assert OLD_ACCESS not in resp.text
    assert OLD_REFRESH not in resp.text


def test_pkce_login_via_callback(make_user, test_dsn, vault_master_key):
    user = make_user()
    http_client = FakeOAuthProvider()
    app = _build_app(user["user_id"], test_dsn, http_client)
    with TestClient(app) as client:
        started = client.post(
            "/api/providers/xai/login/start",
            json={"redirect_uri": "http://127.0.0.1:8765/callback"},
        )
    assert started.status_code == 200
    body = started.json()
    assert body["flow"] == "pkce"
    assert body["url"].startswith("https://auth.x.ai/oauth2/authorize?")
    assert body["state"]
    assert OLD_ACCESS not in started.text

    with TestClient(app) as client:
        done = client.get(
            "/api/providers/callback",
            params={"code": "AUTHCODE-FAKE", "state": body["state"]},
        )
    assert done.status_code == 200
    assert "Login berhasil" in done.text
    assert OLD_ACCESS not in done.text
    stored = VaultTokenStore(dsn=test_dsn).load(user["user_id"], "xai")
    assert stored is not None
    assert stored.access_token == OLD_ACCESS


def test_callback_with_unknown_state_is_400(make_user, test_dsn, vault_master_key):
    user = make_user()
    app = _build_app(user["user_id"], test_dsn, FakeOAuthProvider())
    with TestClient(app) as client:
        resp = client.get(
            "/api/providers/callback",
            params={"code": "AUTHCODE-FAKE", "state": "no-such-state"},
        )
    assert resp.status_code == 400
    assert "Login gagal" in resp.text


def test_device_login_start_and_poll(make_user, test_dsn, vault_master_key, monkeypatch):
    # The real Copilot config reads its client id from the env.
    monkeypatch.setenv("PUSPA_GITHUB_OAUTH_CLIENT_ID", "fake-client-id")
    user = make_user()
    app = _build_app(user["user_id"], test_dsn, FakeOAuthProvider())
    with TestClient(app) as client:
        started = client.post("/api/providers/github_copilot/login/start", json={})
    assert started.status_code == 200
    body = started.json()
    assert body["flow"] == "device"
    assert body["user_code"] == USER_CODE
    assert body["verification_uri"] == "https://auth.example.test/device"
    assert body["poll_key"]
    assert DEVICE_CODE not in started.text

    with TestClient(app) as client:
        pending = client.post(
            "/api/providers/github_copilot/device/poll",
            json={"poll_key": body["poll_key"]},
        )
        assert pending.json() == {"status": "pending"}
        done = client.post(
            "/api/providers/github_copilot/device/poll",
            json={"poll_key": body["poll_key"]},
        )
    assert done.json()["status"] == "complete"
    assert done.json()["state"] == "logged_in"
    stored = VaultTokenStore(dsn=test_dsn).load(user["user_id"], "github_copilot")
    assert stored is not None
    assert stored.access_token == OLD_ACCESS


def test_logout_clears_vault(make_user, test_dsn, vault_master_key):
    user = make_user()
    store = VaultTokenStore(dsn=test_dsn)
    store.save(
        user["user_id"],
        "xai",
        TokenSet(
            access_token=OLD_ACCESS,
            refresh_token=OLD_REFRESH,
            expires_at=BASE_TIME + timedelta(seconds=3600),
            obtained_at=BASE_TIME,
        ),
    )
    app = _build_app(user["user_id"], test_dsn, FakeOAuthProvider())
    with TestClient(app) as client:
        resp = client.post("/api/providers/xai/logout")
    assert resp.status_code == 200
    assert resp.json()["state"] == "not_logged_in"
    assert store.load(user["user_id"], "xai") is None
