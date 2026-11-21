"""Tests for the 9Router provider expansion (V2-29).

Every flow runs against a FAKE 9Router (a stub HTTP client
that answers with the same wire shapes the real providers
use, per the 9Router registry and provider files) and the
real encrypted vault in the test database: no live provider
is ever called, no real credential is ever used. Fake token
values are deliberately distinctive, so the leak tests can
search every emitted log line and repr for them.

Covered, per provider:
- Claude setup token: paste stores the token, wrong prefix
  and empty paste are refused;
- Cursor import: any pasted token stores, with no expiry;
- Codex PKCE: now wired (was not-wired in V2-28);
- Antigravity / Gemini CLI / GitLab / iFlow PKCE: authorize
  URL shape (extra params, redirect param, Basic auth) and
  token storage, plus refresh for the form grant;
- Kimi / Grok CLI / Nous / Muse / MiniMax device flow:
  user code shown, pending polls, token stored;
- Kiro device flow: OIDC client registration first, JSON
  device + token calls, camelCase tokens, refresh through
  the same registered client, creds kept in the vault;
- KiloCode custom device flow: empty-POST initiate, GET poll
  (202 pending, 200 approved);
- GLM polling flow: init + GET poll (pending, then ready);
- CodeBuddy CN + international polling flow: state + token
  poll (11217 pending, code 0 ready);
- Qoder + Qoder CN custom device flow: PKCE + nonce built
  locally, GET poll (202 pending, 200 token);
- Cline encoded token: base64 blob decodes and stores, and
  the exchange fallback runs when the blob does not decode;
- refresh: form grant rotates and persists, JSON grant
  (Kiro) rotates and keeps the OIDC client, no-grant
  providers fail fast with "log in again";
- boundary: on a hosted deployment every login start/finish
  is refused as local-only, while status still works;
- the terminal commands (``puspa provider login/status``)
  drive the same service for the new providers;
- no token value (and no Kiro client secret) ever appears
  in logs, reprs, or error messages.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from puspa.cli import main as cli_main
from puspa.router.oauth import TokenRefreshError, TokenSet
from puspa.router.provider_login import (
    ProviderLoginError,
    ProviderLoginService,
    VaultTokenStore,
    assert_valid_aws_region,
    known_provider_configs,
)

BASE_TIME = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)

OLD_ACCESS = "access-token-FAKE-OLD-7f3a"
OLD_REFRESH = "refresh-token-FAKE-OLD-9c1e"
NEW_ACCESS = "access-token-FAKE-NEW-4b2d"
NEW_REFRESH = "refresh-token-FAKE-NEW-8e5f"
DEVICE_CODE = "device-code-FAKE-1a2b"
USER_CODE = "USER-CODE-FAKE"
CLAUDE_TOKEN = "sk-ant-oat01-FAKE-SETUP-TOKEN-9d2c"
CURSOR_TOKEN = "cursor-import-FAKE-TOKEN-3f8a"
KIRO_CLIENT_SECRET = "kiro-client-secret-FAKE-6e1b"


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


class Fake9Router:
    """Fake 9Router: every provider's wire shape, no network.

    ``device_polls_before_success`` / ``polling_polls_before_success``
    count how many polls answer pending before the token is
    issued. ``pending_style`` is ``"status400"`` (the usual
    OAuth answer) or ``"body200"`` (Kimi answers pending as a
    200 with an error field). Every call is recorded on
    ``.posts`` / ``.gets`` (that is the endpoint's job, so leak
    tests must not search those).
    """

    def __init__(
        self,
        *,
        device_polls_before_success: int = 0,
        polling_polls_before_success: int = 0,
        pending_style: str = "status400",
    ) -> None:
        self.posts: list[tuple[str, dict | None, dict | None, dict | None]] = []
        self.gets: list[str] = []
        self.device_polls = 0
        self.polling_polls = 0
        self.device_polls_before_success = device_polls_before_success
        self.polling_polls_before_success = polling_polls_before_success
        self.pending_style = pending_style

    # -- helpers -----------------------------------------------------

    def _pending_device(self):
        if self.pending_style == "body200":
            return FakeResponse(200, {"error": "authorization_pending"})
        return FakeResponse(400, {"error": "authorization_pending"})

    def _device_tokens(self):
        return FakeResponse(
            200,
            {
                "access_token": OLD_ACCESS,
                "refresh_token": OLD_REFRESH,
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    # -- transport ---------------------------------------------------

    def post(self, url, data=None, json=None, headers=None):
        data = dict(data or {})
        self.posts.append((url, data, json, headers))
        # Kiro OIDC client registration (JSON).
        if url.endswith("/client/register"):
            return FakeResponse(
                200, {"clientId": "kiro-client-FAKE", "clientSecret": KIRO_CLIENT_SECRET}
            )
        # Kiro OIDC device authorization (JSON).
        if "/device_authorization" in url and json is not None:
            return FakeResponse(
                200,
                {
                    "deviceCode": DEVICE_CODE,
                    "userCode": USER_CODE,
                    "verificationUri": "https://view.awsapps.com/start/device",
                    "verificationUriComplete": (
                        "https://view.awsapps.com/start/device?user_code=" + USER_CODE
                    ),
                    "expiresIn": 900,
                    "interval": 1,
                },
            )
        # KiloCode initiate: empty POST to the codes URL.
        if url.endswith("/api/device-auth/codes"):
            return FakeResponse(
                200,
                {
                    "code": "KILO-CODE-FAKE",
                    "verificationUrl": "https://api.kilo.ai/device-auth",
                    "expiresIn": 300,
                },
            )
        # GLM ZCode CLI init (JSON body {"provider": "zai"}).
        if "/oauth/cli/init" in url:
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "data": {
                        "authorize_url": "https://zcode.z.ai/oauth/authorize",
                        "flow_id": "glm-flow-FAKE",
                        "poll_interval_sec": 1,
                        "expires_at": "2026-11-22T13:00:00+00:00",
                    },
                },
            )
        # CodeBuddy state (JSON body {}).
        if "/v2/plugin/auth/state" in url:
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "data": {
                        "state": "cb-state-FAKE",
                        "authUrl": "https://copilot.tencent.com/auth",
                    },
                },
            )
        # JSON token calls (Kiro device poll + refresh, Cline fallback).
        if json is not None:
            grant = json.get("grantType") or json.get("grant_type")
            if grant == "urn:ietf:params:oauth:grant-type:device_code":
                self.device_polls += 1
                if self.device_polls <= self.device_polls_before_success:
                    return self._pending_device()
                return FakeResponse(
                    200,
                    {
                        "accessToken": OLD_ACCESS,
                        "refreshToken": OLD_REFRESH,
                        "expiresIn": 3600,
                    },
                )
            if grant == "refresh_token":
                assert json.get("refreshToken") == OLD_REFRESH
                return FakeResponse(
                    200,
                    {
                        "accessToken": NEW_ACCESS,
                        "refreshToken": NEW_REFRESH,
                        "expiresIn": 3600,
                    },
                )
            if grant == "authorization_code" and "cline" in url:
                return FakeResponse(
                    200,
                    {
                        "access_token": OLD_ACCESS,
                        "refresh_token": OLD_REFRESH,
                        "expires_at": "2026-11-22T13:00:00+00:00",
                    },
                )
        # Standard device-code start (form POST to a device URL).
        if "device" in url and not data.get("grant_type"):
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
            return self._device_tokens()
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            self.device_polls += 1
            if self.device_polls <= self.device_polls_before_success:
                return self._pending_device()
            return self._device_tokens()
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

    def get(self, url, headers=None):
        self.gets.append(url)
        # GLM poll: GET {poll_url}/{flow_id}.
        if "/oauth/cli/poll/" in url:
            self.polling_polls += 1
            if self.polling_polls <= self.polling_polls_before_success:
                return FakeResponse(200, {"data": {"status": "pending"}})
            return FakeResponse(
                200,
                {
                    "data": {
                        "status": "ready",
                        "accessToken": OLD_ACCESS,
                        "refreshToken": OLD_REFRESH,
                    }
                },
            )
        # CodeBuddy poll: GET {token_url}?state=...
        if "/v2/plugin/auth/token" in url:
            self.polling_polls += 1
            if self.polling_polls <= self.polling_polls_before_success:
                return FakeResponse(200, {"code": 11217, "msg": "pending"})
            return FakeResponse(
                200,
                {
                    "code": 0,
                    "data": {
                        "accessToken": OLD_ACCESS,
                        "refreshToken": OLD_REFRESH,
                        "expiresIn": 86400,
                    },
                },
            )
        # KiloCode poll: GET {poll_base}/{code}.
        if "/api/device-auth/codes/" in url:
            self.polling_polls += 1
            if self.polling_polls <= self.polling_polls_before_success:
                return FakeResponse(202, {})
            return FakeResponse(
                200,
                {
                    "status": "approved",
                    "token": OLD_ACCESS,
                    "userEmail": "fake@example.test",
                },
            )
        # Qoder poll: GET {device_token_url}?nonce=...&verifier=...
        if "/deviceToken/poll" in url:
            self.polling_polls += 1
            if self.polling_polls <= self.polling_polls_before_success:
                return FakeResponse(202, {})
            return FakeResponse(
                200,
                {
                    "token": OLD_ACCESS,
                    "refresh_token": OLD_REFRESH,
                    "expires_in": 30 * 24 * 3600,
                },
            )
        return FakeResponse(404, {"error": "not_found"})


def _configs():
    """Real provider configs, with fake client ids where the real
    flow needs an env var (GitLab, and secrets for the Google /
    iFlow PKCE exchanges, which the fake ignores)."""
    configs = known_provider_configs()
    configs["gitlab"] = replace(
        configs["gitlab"], client_id="fake-gitlab-id", client_secret="fake-gitlab-secret"
    )
    configs["antigravity"] = replace(configs["antigravity"], client_secret="fake-secret")
    configs["gemini_cli"] = replace(configs["gemini_cli"], client_secret="fake-secret")
    configs["iflow"] = replace(configs["iflow"], client_secret="fake-secret")
    return configs


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
# Registry
# ---------------------------------------------------------------------------


def test_registry_lists_all_23_providers_and_all_are_local_only():
    configs = known_provider_configs()
    assert sorted(configs) == [
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
    ]
    assert all(config.local_only for config in configs.values())


def test_invalid_aws_region_is_refused_before_any_url(monkeypatch):
    assert_valid_aws_region("us-east-1")
    with pytest.raises(ValueError, match="invalid AWS region"):
        assert_valid_aws_region("evil/../x")
    monkeypatch.setenv("PUSPA_KIRO_REGION", "not a region")
    with pytest.raises(ValueError, match="invalid AWS region"):
        known_provider_configs()


# ---------------------------------------------------------------------------
# Pasted-token flows: Claude setup token, Cursor import
# ---------------------------------------------------------------------------


def test_claude_setup_token_stores_token_with_no_expiry(test_dsn, vault_master_key, user_id):
    service = _service(test_dsn, FakeClock(), Fake9Router())
    started = service.start_pasted_token("claude")
    assert started["flow"] == "setup_token"
    assert "claude setup-token" in started["help"]
    status = service.finish_pasted_token(user_id, "claude", CLAUDE_TOKEN)
    assert status["state"] == "logged_in"
    assert status["expires_at"] is None
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "claude")
    assert stored is not None
    assert stored.access_token == CLAUDE_TOKEN
    assert stored.refresh_token is None
    assert stored.expires_at is None


def test_claude_setup_token_rejects_wrong_prefix_and_empty(test_dsn, vault_master_key, user_id):
    service = _service(test_dsn, FakeClock(), Fake9Router())
    with pytest.raises(ProviderLoginError, match="must start with 'sk-ant-oat01-'"):
        service.finish_pasted_token(user_id, "claude", "sk-ant-api01-FAKE-WRONG")
    with pytest.raises(ProviderLoginError, match="no token pasted"):
        service.finish_pasted_token(user_id, "claude", "   ")
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "claude") is None


def test_cursor_import_stores_any_pasted_token(test_dsn, vault_master_key, user_id):
    service = _service(test_dsn, FakeClock(), Fake9Router())
    status = service.finish_pasted_token(user_id, "cursor", CURSOR_TOKEN)
    assert status["state"] == "logged_in"
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "cursor")
    assert stored is not None
    assert stored.access_token == CURSOR_TOKEN
    assert stored.refresh_token is None


# ---------------------------------------------------------------------------
# PKCE logins: Codex (newly wired), Antigravity, Gemini CLI, GitLab, iFlow
# ---------------------------------------------------------------------------


def _pkce_login(service, user_id, provider, clock):
    started = service.start_pkce(provider, "http://127.0.0.1:8765/callback")
    status = service.finish_pkce(user_id, started["state"], "AUTHCODE-FAKE")
    assert status["provider"] == provider
    assert status["state"] == "logged_in"
    return started, status


def test_codex_pkce_login_now_wired(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    service = _service(test_dsn, clock, Fake9Router())
    started, _ = _pkce_login(service, user_id, "openai_codex", clock)
    assert started["url"].startswith("https://auth.openai.com/oauth/authorize?")
    assert "client_id=app_EMoamEEZ73f0CkXaXp7hrann" in started["url"]
    assert "codex_cli_simplified_flow=true" in started["url"]
    assert "originator=codex_cli_rs" in started["url"]
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "openai_codex")
    assert stored is not None
    assert stored.access_token == OLD_ACCESS


def test_antigravity_pkce_login_requests_offline_access(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    service = _service(test_dsn, clock, Fake9Router())
    started, _ = _pkce_login(service, user_id, "antigravity", clock)
    assert started["url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in started["url"]
    assert "prompt=consent" in started["url"]
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "antigravity") is not None


def test_gemini_cli_pkce_login(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    service = _service(test_dsn, clock, Fake9Router())
    started, _ = _pkce_login(service, user_id, "gemini_cli", clock)
    assert started["url"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j" in started["url"]
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "gemini_cli") is not None


def test_gitlab_pkce_login(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    service = _service(test_dsn, clock, Fake9Router())
    started, _ = _pkce_login(service, user_id, "gitlab", clock)
    assert started["url"].startswith("https://gitlab.com/oauth/authorize?")
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "gitlab") is not None


def test_iflow_pkce_login_uses_redirect_param_and_basic_auth(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = Fake9Router()
    service = _service(test_dsn, clock, http_client)
    started, _ = _pkce_login(service, user_id, "iflow", clock)
    assert started["url"].startswith("https://iflow.cn/oauth?")
    assert "redirect=http%3A%2F%2F127.0.0.1%3A8765%2Fcallback" in started["url"]
    assert "loginMethod=phone" in started["url"]
    _, _, _, headers = http_client.posts[-1]
    assert headers is not None
    assert str(headers.get("Authorization", "")).startswith("Basic ")
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "iflow") is not None


# ---------------------------------------------------------------------------
# Device logins: Kimi, Grok CLI, Nous, Muse, MiniMax, Kiro, KiloCode, Qoder
# ---------------------------------------------------------------------------


def _device_login(service, user_id, provider):
    started = service.start_device(provider)
    assert started["user_code"]
    assert started["verification_uri"]
    result = service.poll_device(user_id, provider, started["poll_key"])
    assert result["status"] == "complete"
    assert result["state"] == "logged_in"
    return started, result


@pytest.mark.parametrize(
    "provider",
    ["kimi", "grok_cli", "nous", "muse", "minimax_code", "minimax_code_global"],
)
def test_standard_device_login_stores_token(test_dsn, vault_master_key, user_id, provider):
    clock = FakeClock()
    http_client = Fake9Router(device_polls_before_success=1)
    service = _service(test_dsn, clock, http_client)
    started = service.start_device(provider)
    assert service.poll_device(user_id, provider, started["poll_key"])["status"] == "pending"
    _device_login(service, user_id, provider)
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, provider)
    assert stored is not None
    assert stored.access_token == OLD_ACCESS
    assert stored.refresh_token == OLD_REFRESH


def test_kimi_device_login_accepts_200_pending_answer(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = Fake9Router(device_polls_before_success=1, pending_style="body200")
    service = _service(test_dsn, clock, http_client)
    started = service.start_device("kimi")
    assert service.poll_device(user_id, "kimi", started["poll_key"])["status"] == "pending"
    result = service.poll_device(user_id, "kimi", started["poll_key"])
    assert result["status"] == "complete"


def test_grok_cli_device_request_carries_referrer(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = Fake9Router()
    service = _service(test_dsn, clock, http_client)
    _device_login(service, user_id, "grok_cli")
    _, data, _, _ = http_client.posts[0]
    assert data is not None
    assert data.get("referrer") == "grok-build"
    assert data.get("client_id") == "b1a00492-073a-47ea-816f-4c329264a828"


def test_kiro_device_login_registers_oidc_client_first(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = Fake9Router()
    service = _service(test_dsn, clock, http_client)
    started, _ = _device_login(service, user_id, "kiro")
    assert started["verification_uri"].startswith("https://view.awsapps.com/start/device")
    register_url, _, register_json, _ = http_client.posts[0]
    assert register_url == "https://oidc.us-east-1.amazonaws.com/client/register"
    assert register_json is not None
    assert register_json.get("clientName") == "kiro-oauth-client"
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "kiro")
    assert stored is not None
    assert stored.access_token == OLD_ACCESS
    # The registered OIDC client rides in the vault entry, so a
    # later refresh can use the same client.
    assert stored.extra.get("client_id") == "kiro-client-FAKE"
    assert stored.extra.get("client_secret") == KIRO_CLIENT_SECRET
    assert stored.extra.get("region") == "us-east-1"


def test_kilocode_device_login_polls_with_get(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = Fake9Router(polling_polls_before_success=1)
    service = _service(test_dsn, clock, http_client)
    started = service.start_device("kilocode")
    assert started["verification_uri"] == "https://api.kilo.ai/device-auth"
    assert service.poll_device(user_id, "kilocode", started["poll_key"])["status"] == "pending"
    result = service.poll_device(user_id, "kilocode", started["poll_key"])
    assert result["status"] == "complete"
    assert result["state"] == "logged_in"
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "kilocode")
    assert stored is not None
    assert stored.access_token == OLD_ACCESS
    assert stored.refresh_token is None
    assert stored.expires_at is None


@pytest.mark.parametrize("provider", ["qoder", "qoder_cn"])
def test_qoder_device_login_builds_flow_locally(test_dsn, vault_master_key, user_id, provider):
    clock = FakeClock()
    http_client = Fake9Router(polling_polls_before_success=1)
    service = _service(test_dsn, clock, http_client)
    started = service.start_device(provider)
    assert started["verification_uri"].startswith(
        "https://qoder.com/device/selectAccounts?"
        if provider == "qoder"
        else "https://qoder.com.cn/device/selectAccounts?"
    )
    assert "challenge_method=S256" in started["verification_uri"]
    assert started["user_code"]
    assert service.poll_device(user_id, provider, started["poll_key"])["status"] == "pending"
    result = service.poll_device(user_id, provider, started["poll_key"])
    assert result["status"] == "complete"
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, provider)
    assert stored is not None
    assert stored.access_token == OLD_ACCESS
    assert http_client.posts == [], "Qoder start makes no server call"


# ---------------------------------------------------------------------------
# Polling logins: GLM, CodeBuddy
# ---------------------------------------------------------------------------


def test_glm_polling_login(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = Fake9Router(polling_polls_before_success=1)
    service = _service(test_dsn, clock, http_client)
    started = service.start_polling("glm")
    assert started["verification_uri"] == "https://zcode.z.ai/oauth/authorize"
    assert service.poll_polling(user_id, "glm", started["poll_key"])["status"] == "pending"
    result = service.poll_polling(user_id, "glm", started["poll_key"])
    assert result["status"] == "complete"
    assert result["state"] == "logged_in"
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "glm")
    assert stored is not None
    assert stored.access_token == OLD_ACCESS
    # The init POST carried the provider id and a Bearer token.
    _, _, init_json, init_headers = http_client.posts[0]
    assert init_json == {"provider": "zai"}
    assert str((init_headers or {}).get("Authorization", "")).startswith("Bearer ")


@pytest.mark.parametrize("provider", ["codebuddy_cn", "codebuddy_intl"])
def test_codebuddy_polling_login(test_dsn, vault_master_key, user_id, provider):
    clock = FakeClock()
    http_client = Fake9Router(polling_polls_before_success=1)
    service = _service(test_dsn, clock, http_client)
    started = service.start_polling(provider)
    assert started["verification_uri"] == "https://copilot.tencent.com/auth"
    assert service.poll_polling(user_id, provider, started["poll_key"])["status"] == "pending"
    result = service.poll_polling(user_id, provider, started["poll_key"])
    assert result["status"] == "complete"
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, provider)
    assert stored is not None
    assert stored.access_token == OLD_ACCESS
    assert stored.refresh_token == OLD_REFRESH


# ---------------------------------------------------------------------------
# Encoded-token login: Cline
# ---------------------------------------------------------------------------


def _cline_blob(access=OLD_ACCESS, refresh=OLD_REFRESH):
    raw = json.dumps(
        {
            "access_token": access,
            "refresh_token": refresh,
            "email": "fake@example.test",
            "expiresAt": "2026-11-22T13:00:00+00:00",
        }
    ).encode()
    return base64.b64encode(raw).decode()


def test_cline_encoded_token_blob_stores_tokens(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    service = _service(test_dsn, clock, Fake9Router())
    status = service.finish_encoded_token(user_id, "cline", _cline_blob())
    assert status["state"] == "logged_in"
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "cline")
    assert stored is not None
    assert stored.access_token == OLD_ACCESS
    assert stored.refresh_token == OLD_REFRESH
    assert stored.expires_at == datetime(2026, 11, 22, 13, 0, 0, tzinfo=UTC)


def test_cline_encoded_token_falls_back_to_exchange(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    service = _service(test_dsn, clock, Fake9Router())
    status = service.finish_encoded_token(user_id, "cline", "not-a-base64-blob")
    assert status["state"] == "logged_in"
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "cline")
    assert stored is not None
    assert stored.access_token == OLD_ACCESS


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def test_form_refresh_rotates_and_persists(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = Fake9Router()
    service = _service(test_dsn, clock, http_client)
    VaultTokenStore(dsn=test_dsn).save(user_id, "kimi", _token_set(clock, expires_in=120))
    assert service.get_valid_token(user_id, "kimi") == NEW_ACCESS
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "kimi")
    assert stored is not None
    assert stored.access_token == NEW_ACCESS
    assert stored.refresh_token == NEW_REFRESH


def test_kiro_json_refresh_keeps_registered_client(test_dsn, vault_master_key, user_id):
    clock = FakeClock()
    http_client = Fake9Router()
    service = _service(test_dsn, clock, http_client)
    _device_login(service, user_id, "kiro")
    clock.advance(seconds=3300)
    assert service.get_valid_token(user_id, "kiro") == NEW_ACCESS
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, "kiro")
    assert stored is not None
    assert stored.access_token == NEW_ACCESS
    assert stored.extra.get("client_id") == "kiro-client-FAKE"
    assert stored.extra.get("client_secret") == KIRO_CLIENT_SECRET
    # The refresh POST was JSON with the registered client.
    _, _, refresh_json, _ = http_client.posts[-1]
    assert refresh_json is not None
    assert refresh_json.get("grantType") == "refresh_token"
    assert refresh_json.get("clientId") == "kiro-client-FAKE"


@pytest.mark.parametrize("provider", ["glm", "muse", "qoder", "kilocode"])
def test_no_refresh_grant_fails_fast_with_relogin(test_dsn, vault_master_key, user_id, provider):
    # A forced refresh must fail fast with "log in again" for
    # providers that issue no refresh grant, instead of posting
    # a grant the provider would refuse.
    from puspa.router.oauth import TokenRefresher, TokenStore

    clock = FakeClock()
    http_client = Fake9Router()
    service = _service(test_dsn, clock, http_client)
    if provider == "glm":
        VaultTokenStore(dsn=test_dsn).save(user_id, provider, _token_set(clock, expires_in=120))
    else:
        _device_login(service, user_id, provider)
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, provider)
    assert stored is not None
    store = TokenStore()
    store.put(provider, stored)
    refresher = TokenRefresher(store=store, now_fn=clock.now)
    refresher.register(_configs()[provider].to_oauth_config())
    with pytest.raises(TokenRefreshError) as excinfo:
        refresher.refresh_now(provider, http_client)
    assert excinfo.value.reauth_required is True


@pytest.mark.parametrize("provider", ["claude", "cursor"])
def test_pasted_token_refresh_fails_fast_with_relogin(
    test_dsn, vault_master_key, user_id, provider
):
    # Pasted tokens carry no expiry, so normal use never tries a
    # refresh; a forced refresh must still fail fast with
    # "log in again" instead of posting a grant.
    from puspa.router.oauth import TokenRefresher, TokenStore

    clock = FakeClock()
    http_client = Fake9Router()
    service = _service(test_dsn, clock, http_client)
    token = CLAUDE_TOKEN if provider == "claude" else CURSOR_TOKEN
    service.finish_pasted_token(user_id, provider, token)
    stored = VaultTokenStore(dsn=test_dsn).load(user_id, provider)
    assert stored is not None
    store = TokenStore()
    store.put(provider, stored)
    refresher = TokenRefresher(store=store, now_fn=clock.now)
    refresher.register(_configs()[provider].to_oauth_config())
    with pytest.raises(TokenRefreshError) as excinfo:
        refresher.refresh_now(provider, http_client)
    assert excinfo.value.reauth_required is True


# ---------------------------------------------------------------------------
# Local-only boundary: hosted refuses every login
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    ["pkce", "device", "polling", "pasted", "finish_pasted"],
)
def test_hosted_deployment_refuses_plan_credential_logins(
    test_dsn, vault_master_key, user_id, call
):
    service = _service(test_dsn, FakeClock(), Fake9Router(), deployment="hosted")
    assert service.deployment == "hosted"
    with pytest.raises(ProviderLoginError, match="local-only"):
        if call == "pkce":
            service.start_pkce("xai", "http://127.0.0.1:8765/callback")
        elif call == "device":
            service.start_device("kimi")
        elif call == "polling":
            service.start_polling("glm")
        elif call == "pasted":
            service.start_pasted_token("claude")
        else:
            service.finish_pasted_token(user_id, "claude", CLAUDE_TOKEN)
    # Status still works on hosted: it only reads the vault.
    assert service.status(user_id, "xai")["state"] == "not_logged_in"


# ---------------------------------------------------------------------------
# No token value leaks into logs, reprs, or error messages
# ---------------------------------------------------------------------------


def test_no_token_value_in_logs_or_reprs(test_dsn, vault_master_key, caplog, user_id):
    clock = FakeClock()
    http_client = Fake9Router()
    service = _service(test_dsn, clock, http_client)
    with caplog.at_level(logging.INFO, logger="puspa.router.provider_login"):
        service.finish_pasted_token(user_id, "claude", CLAUDE_TOKEN)
        started = service.start_device("kiro")
        service.poll_device(user_id, "kiro", started["poll_key"])
        polling = service.start_polling("glm")
        service.poll_polling(user_id, "glm", polling["poll_key"])
        service.status(user_id, "kiro")
        service.logout(user_id, "kiro")
    emitted = caplog.text
    assert emitted, "expected the service to log something"
    for secret in (CLAUDE_TOKEN, OLD_ACCESS, OLD_REFRESH, KIRO_CLIENT_SECRET, DEVICE_CODE):
        assert secret not in emitted
    tokens = VaultTokenStore(dsn=test_dsn)
    assert CLAUDE_TOKEN not in repr(tokens)
    device = service.start_device("kimi")
    assert DEVICE_CODE not in repr(device)
    assert USER_CODE in repr(device)


# ---------------------------------------------------------------------------
# HTTP layer for the new routes (finish-token, polling start/poll)
# ---------------------------------------------------------------------------


def _build_app(test_user_id, test_dsn, http_client):
    from puspa.pwa.app import create_app

    app = create_app(test_user_id=test_user_id, dsn=test_dsn)
    app.state.provider_login_http_client = http_client
    return app


def test_api_routes_include_finish_token(make_user, test_dsn, vault_master_key):
    user = make_user()
    app = _build_app(user["user_id"], test_dsn, Fake9Router())
    paths = {route.path for route in app.routes}
    assert "/api/providers/{provider}/login/finish-token" in paths


def test_api_finish_token_login_claude(make_user, test_dsn, vault_master_key):
    from fastapi.testclient import TestClient

    user = make_user()
    app = _build_app(user["user_id"], test_dsn, Fake9Router())
    with TestClient(app) as client:
        started = client.post("/api/providers/claude/login/start", json={})
        assert started.status_code == 200
        assert started.json()["flow"] == "setup_token"
        assert "claude setup-token" in started.json()["help"]
        done = client.post("/api/providers/claude/login/finish-token", json={"token": CLAUDE_TOKEN})
        assert done.status_code == 200
        assert done.json()["state"] == "logged_in"
        assert CLAUDE_TOKEN not in done.text
        bad = client.post(
            "/api/providers/claude/login/finish-token",
            json={"token": "sk-ant-api01-FAKE-WRONG"},
        )
        assert bad.status_code == 400
        assert "sk-ant-oat01-" in bad.json()["error"]


def test_api_polling_login_glm(make_user, test_dsn, vault_master_key):
    from fastapi.testclient import TestClient

    user = make_user()
    http_client = Fake9Router(polling_polls_before_success=1)
    app = _build_app(user["user_id"], test_dsn, http_client)
    with TestClient(app) as client:
        started = client.post("/api/providers/glm/login/start", json={})
        assert started.status_code == 200
        body = started.json()
        assert body["flow"] == "polling"
        assert body["verification_uri"] == "https://zcode.z.ai/oauth/authorize"
        poll_key = body["poll_key"]
        pending = client.post("/api/providers/glm/device/poll", json={"poll_key": poll_key})
        assert pending.status_code == 200
        assert pending.json()["status"] == "pending"
        done = client.post("/api/providers/glm/device/poll", json={"poll_key": poll_key})
        assert done.status_code == 200
        assert done.json()["status"] == "complete"
        assert done.json()["state"] == "logged_in"
        assert OLD_ACCESS not in done.text


def test_api_unknown_provider_is_404(make_user, test_dsn, vault_master_key):
    from fastapi.testclient import TestClient

    user = make_user()
    app = _build_app(user["user_id"], test_dsn, Fake9Router())
    with TestClient(app) as client:
        assert client.post("/api/providers/nope/login/start", json={}).status_code == 404
        assert (
            client.post("/api/providers/nope/login/finish-token", json={"token": "x"}).status_code
            == 404
        )


# ---------------------------------------------------------------------------
# Terminal commands drive the same service for the new providers
# ---------------------------------------------------------------------------


def _cli_login(monkeypatch, test_dsn, user_id, provider, pasted):
    clock = FakeClock()
    http_client = Fake9Router()
    service = _service(test_dsn, clock, http_client)
    monkeypatch.setattr("puspa.cli._provider_service", lambda: service)
    monkeypatch.setattr("builtins.input", lambda _: pasted)
    return cli_main(["provider", "login", provider, "--user", user_id])


def test_cli_claude_login(monkeypatch, test_dsn, vault_master_key, capsys, user_id):
    rc = _cli_login(monkeypatch, test_dsn, user_id, "claude", CLAUDE_TOKEN)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Logged in to claude" in out
    assert CLAUDE_TOKEN not in out
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "claude") is not None


def test_cli_cursor_login(monkeypatch, test_dsn, vault_master_key, capsys, user_id):
    rc = _cli_login(monkeypatch, test_dsn, user_id, "cursor", CURSOR_TOKEN)
    assert rc == 0
    assert "Logged in to cursor" in capsys.readouterr().out
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "cursor") is not None


def test_cli_codex_login(monkeypatch, test_dsn, vault_master_key, capsys, user_id):
    rc = _cli_login(monkeypatch, test_dsn, user_id, "openai_codex", "AUTHCODE-FAKE")
    assert rc == 0
    assert "Logged in to openai_codex" in capsys.readouterr().out
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "openai_codex") is not None


def test_cli_glm_polling_login(monkeypatch, test_dsn, vault_master_key, capsys, user_id):
    clock = FakeClock()
    http_client = Fake9Router()
    service = _service(test_dsn, clock, http_client)
    monkeypatch.setattr("puspa.cli._provider_service", lambda: service)
    rc = cli_main(["provider", "login", "glm", "--user", user_id])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Logged in to glm" in out
    assert "https://zcode.z.ai/oauth/authorize" in out
    assert VaultTokenStore(dsn=test_dsn).load(user_id, "glm") is not None


def test_cli_status_lists_all_23_providers(
    monkeypatch, test_dsn, vault_master_key, capsys, user_id
):
    clock = FakeClock()
    service = _service(test_dsn, clock, Fake9Router())
    monkeypatch.setattr("puspa.cli._provider_service", lambda: service)
    VaultTokenStore(dsn=test_dsn).save(user_id, "kimi", _token_set(clock, expires_in=3600))
    assert cli_main(["provider", "status", "--user", user_id]) == 0
    out = capsys.readouterr().out
    for provider in known_provider_configs():
        assert f"{provider}:" in out
    assert "kimi: logged_in" in out
    assert "claude: not_logged_in" in out
    assert OLD_ACCESS not in out
