"""Desktop login (COW-10) + chat message read.

Covers the Google callback wiring (state check, browser cookie vs
desktop one-time code), the one-time desktop session exchange, and
GET /api/chat/messages. The Google token exchange itself is covered
by puspa.auth's own tests; here the route wiring is tested with a
stubbed complete_google_login.
"""

import base64
import json
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from puspa.accounts.ports import validate_session
from puspa.auth.google_oauth import JWKS_URL, TOKEN_URL, find_user_by_email
from puspa.pwa.app import create_app


@pytest.fixture
def app_client(make_user):
    user = make_user()
    app = create_app(test_user_id=user["user_id"])
    with TestClient(app) as client:
        yield app, client, user["user_id"]


def test_chat_messages_requires_login():
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/api/chat/messages")
        assert resp.status_code == 401


def test_chat_messages_returns_thread(app_client):
    _app, client, _user_id = app_client
    resp = client.post("/api/chat", data={"text": "halo Puspa"})
    assert resp.status_code == 200
    listed = client.get("/api/chat/messages")
    assert listed.status_code == 200
    texts = [m["text"] for m in listed.json()["messages"]]
    assert "halo Puspa" in texts


def test_desktop_session_exchange_single_use(app_client, test_dsn):
    app, client, user_id = app_client
    app.state.desktop_codes["code-1"] = (user_id, time.time() + 300)
    resp = client.post("/api/desktop/session", json={"code": "code-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["user_id"] == user_id
    assert validate_session(body["session_token"], dsn=test_dsn) == user_id
    again = client.post("/api/desktop/session", json={"code": "code-1"})
    assert again.status_code == 401  # single-use


def test_desktop_session_unknown_and_expired(app_client):
    app, client, user_id = app_client
    unknown = client.post("/api/desktop/session", json={"code": "nope"})
    assert unknown.status_code == 401
    app.state.desktop_codes["code-old"] = (user_id, time.time() - 1)
    expired = client.post("/api/desktop/session", json={"code": "code-old"})
    assert expired.status_code == 401


def test_auth_google_redirect_sets_oauth_cookie(monkeypatch):
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv(
        "PUSPA_GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
    )
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/auth/google", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "accounts.google.com" in resp.headers["location"]
        assert "puspa_oauth" in resp.headers.get("set-cookie", "")


def test_auth_google_without_client_id_is_503(monkeypatch):
    monkeypatch.delenv("PUSPA_GOOGLE_CLIENT_ID", raising=False)
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/auth/google", follow_redirects=False)
        assert resp.status_code == 503


def test_auth_google_callback_state_mismatch():
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get(
            "/auth/google/callback?code=x&state=y", follow_redirects=False
        )
        assert resp.status_code == 400


def test_auth_google_callback_desktop_flow(app_client, monkeypatch, test_dsn):
    app, client, user_id = app_client
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "PUSPA_GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
    )

    def fake_login(*args, **kwargs):
        return {
            "user_id": user_id,
            "email": "user@contoh.id",
            "session_token": "browser-token",
            "new": False,
        }

    monkeypatch.setattr(
        "puspa.auth.google_oauth.complete_google_login", fake_login
    )
    monkeypatch.setattr(
        "puspa.auth.google_oauth.fetch_jwks", lambda http_client=None: {"keys": []}
    )
    saved = {
        "state": "state-abc",
        "code_verifier": "verifier-abc",
        "desktop_port": 54321,
    }
    client.cookies.set("puspa_oauth", json.dumps(saved))
    resp = client.get(
        "/auth/google/callback?code=auth-code&state=state-abc",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert location.startswith("http://127.0.0.1:54321/puspabot-callback?code=")
    code = location.split("code=", 1)[1]
    exchanged = client.post("/api/desktop/session", json={"code": code})
    assert exchanged.status_code == 200
    assert validate_session(
        exchanged.json()["session_token"], dsn=test_dsn
    ) == user_id


def test_auth_google_callback_browser_flow(monkeypatch):
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv(
        "PUSPA_GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
    )

    def fake_login(*args, **kwargs):
        return {
            "user_id": "00000000-0000-0000-0000-000000000000",
            "email": "user@contoh.id",
            "session_token": "browser-token",
            "new": False,
        }

    monkeypatch.setattr(
        "puspa.auth.google_oauth.complete_google_login", fake_login
    )
    monkeypatch.setattr(
        "puspa.auth.google_oauth.fetch_jwks", lambda http_client=None: {"keys": []}
    )
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        saved = {"state": "state-abc", "code_verifier": "verifier-abc"}
        client.cookies.set("puspa_oauth", json.dumps(saved))
        resp = client.get(
            "/auth/google/callback?code=auth-code&state=state-abc",
            follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/masuk"
        assert "puspa_sesi=browser-token" in resp.headers.get("set-cookie", "")


# --- COW-10 M2: the redirect URI must follow the backend's own base
# URL when PUSPA_GOOGLE_REDIRECT_URI is unset. A desktop backend
# runs on a random free port, so the old hardcoded
# http://localhost:8000/auth/google/callback sent Google to a port
# where nothing listens. ---


def test_auth_google_redirect_uses_request_base_url_when_env_unset(monkeypatch):
    """Env unset -> redirect_uri is the test client's actual base URL."""
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.delenv("PUSPA_GOOGLE_REDIRECT_URI", raising=False)
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/auth/google?desktop_port=9999", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "accounts.google.com" in resp.headers["location"]
        params = urllib.parse.parse_qs(
            urllib.parse.urlparse(resp.headers["location"]).query
        )
        # TestClient's actual base URL is http://testserver (no
        # port 8000 anywhere): the redirect must follow the request.
        assert params["redirect_uri"] == ["http://testserver/auth/google/callback"]


def test_auth_google_redirect_env_override_wins(monkeypatch):
    """An explicit PUSPA_GOOGLE_REDIRECT_URI still overrides the base URL."""
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setenv(
        "PUSPA_GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
    )
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/auth/google", follow_redirects=False)
        assert resp.status_code in (302, 307)
        params = urllib.parse.parse_qs(
            urllib.parse.urlparse(resp.headers["location"]).query
        )
        assert params["redirect_uri"] == ["http://localhost:8000/auth/google/callback"]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class _FakeGoogleResponse:
    """Minimal stand-in for an httpx response (same shape as the
    FakeResponse in test_router_oauth_translate.py)."""

    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeGoogleHttpClient:
    """Fake Google token + JWKS endpoints (no real Google needed).

    Serves a real RS256 id_token signed by a throwaway test key,
    so verify_id_token runs for real; every token POST is recorded
    so the test can check which redirect_uri the exchange used.
    """

    def __init__(self, client_id: str, email: str) -> None:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = key.public_key().public_numbers()

        def _int_b64(value: int) -> str:
            return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))

        self.jwks = {
            "keys": [
                {
                    "kid": "test-key-1",
                    "kty": "RSA",
                    "n": _int_b64(pub.n),
                    "e": _int_b64(pub.e),
                }
            ]
        }
        now = int(time.time())
        header = _b64url(
            json.dumps(
                {"alg": "RS256", "kid": "test-key-1", "typ": "JWT"},
                separators=(",", ":"),
            ).encode()
        )
        payload = _b64url(
            json.dumps(
                {
                    "iss": "https://accounts.google.com",
                    "aud": client_id,
                    "sub": "google-sub-1",
                    "email": email,
                    "iat": now,
                    "exp": now + 3600,
                },
                separators=(",", ":"),
            ).encode()
        )
        signing_input = (header + "." + payload).encode()
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        self.id_token = header + "." + payload + "." + _b64url(signature)
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, data=None):
        self.posts.append((url, dict(data or {})))
        return _FakeGoogleResponse(
            200,
            {
                "access_token": "fake-access",
                "refresh_token": "fake-refresh",
                "id_token": self.id_token,
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    def get(self, url, headers=None):
        if url == JWKS_URL:
            return _FakeGoogleResponse(200, self.jwks)
        return _FakeGoogleResponse(404, {"error": "not found"})


def test_auth_google_callback_desktop_flow_with_mocked_google(
    monkeypatch, test_dsn, vault_master_key
):
    """Full desktop flow against a fake Google (no monkeypatched login).

    /auth/google (env redirect unset) -> callback with the real
    state from the cookie -> one-time code on the desktop loopback
    listener -> POST /api/desktop/session exchanges it for a session
    token. The token exchange must carry the derived redirect URI,
    not port 8000.
    """
    client_id = "client-123"
    email = "desktop-flow@contoh.id"
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_ID", client_id)
    monkeypatch.setenv("PUSPA_GOOGLE_CLIENT_SECRET", "secret-123")
    monkeypatch.delenv("PUSPA_GOOGLE_REDIRECT_URI", raising=False)
    fake_google = _FakeGoogleHttpClient(client_id, email)
    app = create_app(
        test_user_id=None, google_http_client=fake_google, dsn=test_dsn
    )
    with TestClient(app) as client:
        start = client.get("/auth/google?desktop_port=54321", follow_redirects=False)
        assert start.status_code in (302, 307)
        start_params = urllib.parse.parse_qs(
            urllib.parse.urlparse(start.headers["location"]).query
        )
        assert start_params["redirect_uri"] == [
            "http://testserver/auth/google/callback"
        ]
        state = start_params["state"][0]
        resp = client.get(
            f"/auth/google/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        location = resp.headers["location"]
        assert location.startswith("http://127.0.0.1:54321/puspabot-callback?code=")
        token_posts = [data for url, data in fake_google.posts if url == TOKEN_URL]
        assert len(token_posts) == 1
        assert token_posts[0]["redirect_uri"] == (
            "http://testserver/auth/google/callback"
        )
        code = location.split("code=", 1)[1]
        exchanged = client.post("/api/desktop/session", json={"code": code})
        assert exchanged.status_code == 200
        body = exchanged.json()
        assert body["ok"] is True
        assert validate_session(body["session_token"], dsn=test_dsn) == body["user_id"]
        assert find_user_by_email(email, dsn=test_dsn) == body["user_id"]
