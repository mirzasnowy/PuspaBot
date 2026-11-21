"""Tests for Puspa-native subscription login (V2-28).

Every flow runs against a FAKE OAuth provider (a stub HTTP
client) and the real encrypted vault in the test database: no
live provider is ever called, no real credential is ever used.
Fake token values are deliberately distinctive, so the leak
tests can search every emitted log line and repr for them.

Covered:
- PKCE login end to end (terminal shape): build URL, exchange
  the pasted code, token stored in the vault, status logged in;
- state mismatch and stale sessions are refused;
- device login end to end: user code shown, pending polls, then
  the token is stored;
- an expiring token is refreshed BEFORE use through the shared
  refresher (``puspa/router/oauth.py``), exactly one refresh,
  and the fresh token is persisted in the vault so a new
  process sees it without another refresh;
- a safely valid token is returned untouched, with no HTTP call;
- logout clears the vault and the next use demands a new login;
- no token value ever appears in logs, reprs, or error messages;
- Codex login refuses with a clear not-wired error;
- the terminal commands (``puspa provider login/logout/status``)
  drive the same service.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from puspa.cli import main as cli_main
from puspa.router.oauth import TokenRefreshError, TokenSet
from puspa.router.provider_login import (
    ProviderLoginConfig,
    ProviderLoginError,
    ProviderLoginService,
    VaultTokenStore,
)

BASE_TIME = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)

OLD_ACCESS = "access-token-FAKE-OLD-7f3a"
OLD_REFRESH = "refresh-token-FAKE-OLD-9c1e"
NEW_ACCESS = "access-token-FAKE-NEW-4b2d"
NEW_REFRESH = "refresh-token-FAKE-NEW-8e5f"
DEVICE_CODE = "device-code-FAKE-1a2b"
USER_CODE = "USER-CODE-FAKE"

TOKEN_URL = "https://auth.example.test/oauth2/token"
AUTHORIZE_URL = "https://auth.example.test/oauth2/authorize"
DEVICE_URL = "https://auth.example.test/device/code"


class FakeClock:
    def __init__(self, start: datetime = BASE_TIME) -> None:
        self.now_value = start

    def now(self) -> datetime:
        return self.now_value

    def advance(self, **kwargs) -> None:
        self.now_value = self.now_value + timedelta(**kwargs)


class FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeOAuthProvider:
    """Fake OAuth provider: device code, PKCE, and refresh grants.

    ``device_polls_before_success`` counts how many device-code
    polls answer ``authorization_pending`` before the token is
    issued. Every post is recorded on ``.posts`` (that is the
    endpoint's job, so leak tests must not search ``.posts``).
    """

    def __init__(self, *, device_polls_before_success: int = 2) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.device_polls = 0
        self.device_polls_before_success = device_polls_before_success

    def post(self, url, data=None):
        data = dict(data or {})
        self.posts.append((url, data))
        if url == DEVICE_URL:
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
            if self.device_polls <= self.device_polls_before_success:
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
        if grant == "refresh_token":
            assert data["refresh_token"] == OLD_REFRESH
            return FakeResponse(
                200,
                {
                    "access_token": NEW_ACCESS,
                    "refresh_token": NEW_REFRESH,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        return FakeResponse(400, {"error": "unsupported_grant_type"})


def _configs() -> dict[str, ProviderLoginConfig]:
    return {
        "xai": ProviderLoginConfig(
            provider="xai",
            flow="pkce",
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            client_id="fake-client-id",
            scope=("openid", "profile"),
        ),
        "github_copilot": ProviderLoginConfig(
            provider="github_copilot",
            flow="device",
            device_code_url=DEVICE_URL,
            token_url=TOKEN_URL,
            client_id="fake-client-id",
        ),
        "openai_codex": ProviderLoginConfig(
            provider="openai_codex",
            flow="pkce",
            authorize_url=None,
            token_url=None,
            client_id=None,
            notes="login runs through oauth_cli_kit in nanobot; not verified",
        ),
    }


def _service(test_dsn, clock, http_client, **kwargs) -> ProviderLoginService:
    return ProviderLoginService(
        dsn=test_dsn,
        http_client=http_client,
        now_fn=clock.now,
        configs=_configs(),
        **kwargs,
    )


def _token_set(clock, *, access=OLD_ACCESS, refresh=OLD_REFRESH, expires_in=3600):
    return TokenSet(
        access_token=access,
        refresh_token=refresh,
        expires_at=clock.now() + timedelta(seconds=expires_in),
        obtained_at=clock.now(),
    )


@pytest.fixture
def user_id(make_user):
    """A real user id (the vault has a foreign key to users)."""
    return make_user()["user_id"]


# ---------------------------------------------------------------------------
# PKCE login
# ---------------------------------------------------------------------------


def test_pkce_login_stores_token_in_vault_and_reports_logged_in(
    test_dsn, vault_master_key, user_id
):
    clock = FakeClock()
    http_client = FakeOAuthProvider()
    service = _service(test_dsn, clock, http_client)
    started = service.start_pkce("xai", "http://127.0.0.1:8765/callback")
    assert started["url"].startswith(AUTHORIZE_URL + "?")
    assert "client_id=fake-client-id" in started["url"]
    assert "code_challenge=" in started["url"]
    assert started["state"]

    status = service.finish_pkce(user_id, started["state"], "AUTHCODE-FAKE")
    assert status["provider"] == "xai"
    assert status["state"] == "logged_in"
    assert status["expires_at"] is not None

    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "xai")
    assert stored is not None
    assert stored.access_token == OLD_ACCESS
    assert stored.refresh_token == OLD_REFRESH
    # The exchange posted the authorization-code grant.
    assert http_client.posts[-1][1]["grant_type"] == "authorization_code"


def test_pkce_login_rejects_unknown_state(test_dsn, vault_master_key, user_id):
    service = _service(test_dsn, FakeClock(), FakeOAuthProvider())
    with pytest.raises(ProviderLoginError, match="unknown or expired login session"):
        service.finish_pkce(user_id, "no-such-state", "AUTHCODE-FAKE")


def test_pkce_login_rejects_stale_session(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    service = _service(test_dsn, clock, FakeOAuthProvider())
    started = service.start_pkce("xai", "http://127.0.0.1:8765/callback")
    clock.advance(minutes=11)
    with pytest.raises(ProviderLoginError, match="login session expired"):
        service.finish_pkce(user_id, started["state"], "AUTHCODE-FAKE")


def test_codex_login_refuses_with_clear_not_wired_error(test_dsn, vault_master_key, user_id):
    service = _service(test_dsn, FakeClock(), FakeOAuthProvider())
    with pytest.raises(ProviderLoginError, match="not wired"):
        service.start_pkce("openai_codex", "http://127.0.0.1:8765/callback")


def test_unknown_provider_is_rejected(test_dsn, vault_master_key, user_id):
    service = _service(test_dsn, FakeClock(), FakeOAuthProvider())
    with pytest.raises(ProviderLoginError, match="unknown subscription provider"):
        service.status(user_id, "no-such-provider")


# ---------------------------------------------------------------------------
# Device login
# ---------------------------------------------------------------------------


def test_device_login_polls_then_stores_token(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = FakeOAuthProvider(device_polls_before_success=2)
    service = _service(test_dsn, clock, http_client)
    started = service.start_device("github_copilot")
    assert started["user_code"] == USER_CODE
    assert started["verification_uri"] == "https://auth.example.test/device"
    assert started["poll_key"]
    # The device code itself is never shown to the user.
    assert DEVICE_CODE not in str(started)

    assert service.poll_device(user_id, "github_copilot", started["poll_key"]) == {
        "status": "pending"
    }
    assert service.poll_device(user_id, "github_copilot", started["poll_key"]) == {
        "status": "pending"
    }
    done = service.poll_device(user_id, "github_copilot", started["poll_key"])
    assert done["status"] == "complete"
    assert done["state"] == "logged_in"
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "github_copilot")
    assert stored is not None
    assert stored.access_token == OLD_ACCESS


def test_device_poll_unknown_key_is_rejected(test_dsn, vault_master_key, user_id):
    service = _service(test_dsn, FakeClock(), FakeOAuthProvider())
    with pytest.raises(ProviderLoginError, match="unknown device login"):
        service.poll_device(user_id, "github_copilot", "no-such-poll-key")


# ---------------------------------------------------------------------------
# Refresh before expiry, persisted back to the vault
# ---------------------------------------------------------------------------


def test_expiring_token_is_refreshed_before_use_and_persisted(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = FakeOAuthProvider()
    service = _service(test_dsn, clock, http_client)
    VaultTokenStore(dsn=test_dsn).save(user_id, "xai", _token_set(clock, expires_in=120))

    token = service.get_valid_token(user_id, "xai")
    assert token == NEW_ACCESS
    refresh_posts = [
        post for post in http_client.posts if post[1].get("grant_type") == "refresh_token"
    ]
    assert len(refresh_posts) == 1

    # The fresh token is in the vault: a new service (a new
    # process) uses it without another refresh.
    fresh_client = FakeOAuthProvider()
    new_service = _service(test_dsn, clock, fresh_client)
    assert new_service.get_valid_token(user_id, "xai") == NEW_ACCESS
    assert fresh_client.posts == []


def test_valid_token_is_returned_untouched_with_no_http_call(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = FakeOAuthProvider()
    service = _service(test_dsn, clock, http_client)
    VaultTokenStore(dsn=test_dsn).save(user_id, "xai", _token_set(clock, expires_in=3600))
    assert service.get_valid_token(user_id, "xai") == OLD_ACCESS
    assert http_client.posts == []


def test_get_valid_token_without_login_demands_login(test_dsn, vault_master_key, user_id):
    service = _service(test_dsn, FakeClock(), FakeOAuthProvider())
    with pytest.raises(TokenRefreshError) as excinfo:
        service.get_valid_token(user_id, "xai")
    assert excinfo.value.reauth_required is True


# ---------------------------------------------------------------------------
# Logout + status
# ---------------------------------------------------------------------------


def test_logout_clears_vault_and_status_becomes_not_logged_in(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    service = _service(test_dsn, clock, FakeOAuthProvider())
    VaultTokenStore(dsn=test_dsn).save(user_id, "xai", _token_set(clock, expires_in=3600))
    assert service.status(user_id, "xai")["state"] == "logged_in"
    service.logout(user_id, "xai")
    assert service.status(user_id, "xai")["state"] == "not_logged_in"
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "xai") is None
    # Logging out twice is fine.
    service.logout(user_id, "xai")


def test_status_reports_expiring_and_expired(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    service = _service(test_dsn, clock, FakeOAuthProvider())
    store = VaultTokenStore(dsn=test_dsn)
    assert service.status(user_id, "xai")["state"] == "not_logged_in"
    store.save(user_id, "xai", _token_set(clock, expires_in=120))
    assert service.status(user_id, "xai")["state"] == "expiring"
    clock.advance(seconds=200)
    assert service.status(user_id, "xai")["state"] == "expired"


def test_list_status_covers_all_known_providers(test_dsn, vault_master_key, user_id):
    service = _service(test_dsn, FakeClock(), FakeOAuthProvider())
    listed = service.list_status(user_id)
    assert [entry["provider"] for entry in listed] == [
        "github_copilot",
        "openai_codex",
        "xai",
    ]
    codex = next(entry for entry in listed if entry["provider"] == "openai_codex")
    assert codex["login_ready"] is False


# ---------------------------------------------------------------------------
# Tokens never leak into logs, reprs, or error messages
# ---------------------------------------------------------------------------


def test_no_token_value_in_logs_or_reprs(test_dsn, vault_master_key, caplog, user_id):
    clock = FakeClock()
    http_client = FakeOAuthProvider()
    service = _service(test_dsn, clock, http_client)
    with caplog.at_level(logging.INFO, logger="puspa.router.provider_login"):
        started = service.start_pkce("xai", "http://127.0.0.1:8765/callback")
        service.finish_pkce(user_id, started["state"], "AUTHCODE-FAKE")
        VaultTokenStore(dsn=test_dsn).save(user_id, "xai", _token_set(clock, expires_in=120))
        service.get_valid_token(user_id, "xai")
        service.status(user_id, "xai")
        service.logout(user_id, "xai")
    emitted = caplog.text
    assert emitted, "expected the service to log something"
    for secret in (OLD_ACCESS, OLD_REFRESH, NEW_ACCESS, NEW_REFRESH):
        assert secret not in emitted
    tokens = VaultTokenStore(dsn=test_dsn)
    assert OLD_ACCESS not in repr(tokens)
    device = service.start_device("github_copilot")
    assert DEVICE_CODE not in repr(device)


def test_error_messages_never_carry_token_values(test_dsn, vault_master_key, user_id):
    service = _service(test_dsn, FakeClock(), FakeOAuthProvider())
    try:
        service.finish_pkce(user_id, "no-such-state", "AUTHCODE-FAKE")
    except ProviderLoginError as exc:
        assert OLD_ACCESS not in str(exc)
        assert OLD_REFRESH not in str(exc)


# ---------------------------------------------------------------------------
# Terminal commands drive the same service
# ---------------------------------------------------------------------------


def _cli_service(test_dsn, clock, http_client):
    return _service(test_dsn, clock, http_client)


def test_cli_pkce_login(monkeypatch, test_dsn, vault_master_key, capsys, user_id):
    clock = FakeClock()
    http_client = FakeOAuthProvider()
    service = _cli_service(test_dsn, clock, http_client)
    monkeypatch.setattr("puspa.cli._provider_service", lambda: service)
    monkeypatch.setattr("builtins.input", lambda _: "AUTHCODE-FAKE")
    rc = cli_main(["provider", "login", "xai", "--user", user_id])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Logged in to xai" in out
    assert OLD_ACCESS not in out
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "xai") is not None


def test_cli_device_login(monkeypatch, test_dsn, vault_master_key, capsys, user_id):
    clock = FakeClock()
    http_client = FakeOAuthProvider(device_polls_before_success=0)
    service = _cli_service(test_dsn, clock, http_client)
    monkeypatch.setattr("puspa.cli._provider_service", lambda: service)
    rc = cli_main(["provider", "login", "github_copilot", "--user", user_id])
    assert rc == 0
    out = capsys.readouterr().out
    assert USER_CODE in out
    assert "https://auth.example.test/device" in out
    assert DEVICE_CODE not in out
    assert OLD_ACCESS not in out
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "github_copilot") is not None


def test_cli_logout_and_status(monkeypatch, test_dsn, vault_master_key, capsys, user_id):
    clock = FakeClock()
    service = _cli_service(test_dsn, clock, FakeOAuthProvider())
    monkeypatch.setattr("puspa.cli._provider_service", lambda: service)
    VaultTokenStore(dsn=test_dsn).save(user_id, "xai", _token_set(clock, expires_in=3600))
    assert cli_main(["provider", "status", "--user", user_id]) == 0
    out = capsys.readouterr().out
    assert "xai: logged_in" in out
    assert "github_copilot: not_logged_in" in out
    assert cli_main(["provider", "logout", "xai", "--user", user_id]) == 0
    out = capsys.readouterr().out
    assert "Logged out of xai" in out
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "xai") is None
