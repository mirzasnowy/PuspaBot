"""Puspa-native subscription-provider login (V2-28).

Before this module, logging in to a subscription provider (xAI
Grok, GitHub Copilot, Codex) meant running nanobot's login
(``nanobot provider login xai`` and friends): Puspa reused
nanobot's provider machinery and had no login of its own. This
module is Puspa's own login, built on the two pieces that already
existed:

- the OAuth refresh side in ``puspa/router/oauth.py``
  (``TokenRefresher`` / ``TokenSet``), reused as-is: this module
  never implements a second refresh, it only hands the refresher
  a token set loaded from the vault and saves back whatever the
  refresher renews;
- the per-user vault in ``puspa/accounts/vault.py``, where tokens
  live encrypted under ``byok:oauth:<provider>`` (the same
  ``byok:`` prefix the BYOK factory uses for API keys).

Two flows, one per provider shape (the same shape as
``claude setup-token`` / ``codex login``):

- Authorization code + PKCE (xAI, Codex): build an authorize URL,
  the user opens it and approves, the returned code is exchanged
  for tokens. The terminal prints the URL and asks for the pasted
  code; the PWA opens the URL in a tab and receives the code at
  its callback endpoint. The pending PKCE session (state +
  verifier) lives in memory only, for ten minutes.
- Device flow (GitHub Copilot): the provider returns a user code
  and a verification URL, the user approves in a browser, Puspa
  polls until the token arrives. The terminal prints the code
  and URL; the PWA shows them and polls its own endpoint.

Tokens are never logged, never printed, and never leave the
vault except as a Bearer <redacted> the router sends. The only values
this module prints or logs are provider names, URLs, user codes
(which the user must type in by hand), and expiry times.

Honest limits
-------------
- Codex is registered but not wired: its login in nanobot runs
  through the external ``oauth_cli_kit`` library, whose authorize
  and token endpoints were not verified from nanobot's source, so
  starting a Codex login raises a clear error naming exactly
  that instead of pretending to work.
- GitHub Copilot's device flow needs a GitHub OAuth client id,
  which lives in the ``PUSPA_GITHUB_OAUTH_CLIENT_ID`` env var
  (or a ``client_id`` passed to the config); without one the
  login refuses with a clear error.
- xAI's authorize URL comes from its issuer
  (``https://auth.x.ai``) plus ``/oauth2/authorize``; the token
  endpoint and public client id are the ones read from nanobot's
  source (``nanobot/nanobot/providers/xai_oauth.py``). If xAI
  ever moves its authorize endpoint, set
  ``PUSPA_XAI_AUTHORIZE_URL``.
- No live provider is contacted by the tests in this repo: every
  flow is tested against a fake OAuth provider (a stub HTTP
  client), never real credentials or real network. A real login
  against a live provider is still an open follow-up.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from .oauth import (
    DEFAULT_REFRESH_LEEWAY_SECONDS,
    OAuthProviderConfig,
    TokenRefresher,
    TokenRefreshError,
    TokenSet,
    TokenStore,
)

__all__ = [
    "DEFAULT_PKCE_SESSION_TTL_SECONDS",
    "DeviceCode",
    "LoginSession",
    "ProviderLoginConfig",
    "ProviderLoginError",
    "ProviderLoginService",
    "VaultTokenStore",
    "build_pkce_auth_url",
    "exchange_pkce_code",
    "known_provider_configs",
    "new_code_challenge",
    "new_code_verifier",
    "poll_device_code",
    "start_device_flow",
    "token_vault_name",
]

logger = logging.getLogger(__name__)

#: Providers Puspa knows how to log in to (same three the BYOK
#: factory and the OAuth refresher already know).
PROVIDERS = ("xai", "github_copilot", "openai_codex")

#: Vault entry name prefix for subscription tokens.
VAULT_NAME_PREFIX = "byok:oauth:"

#: How long a pending PKCE login session (state + verifier) lives
#: in memory before it is refused as stale.
DEFAULT_PKCE_SESSION_TTL_SECONDS = 600

#: xAI public client id, read from nanobot's own source
#: (``nanobot/nanobot/providers/xai_oauth.py``). A public client
#: id is not a secret.
XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_ISSUER = "https://auth.x.ai"
XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"
XAI_AUTHORIZE_URL = "https://auth.x.ai/oauth2/authorize"
XAI_SCOPES = ("openid", "profile", "email", "offline_access")

GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"


def token_vault_name(provider: str) -> str:
    """Vault entry name for one provider's token set."""
    return f"{VAULT_NAME_PREFIX}{provider}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ProviderLoginError(Exception):
    """A subscription login failed.

    ``provider`` names the provider, ``reauth_required`` is True
    when only a fresh interactive login can fix it. The message
    never contains a token value: only the provider, the reason,
    and the provider's error code.
    """

    def __init__(
        self,
        provider: str,
        reason: str,
        *,
        error_code: str | None = None,
        reauth_required: bool = False,
    ) -> None:
        self.provider = provider
        self.reason = reason
        self.error_code = error_code
        self.reauth_required = reauth_required
        message = f"provider login failed for {provider!r}: {reason}"
        if error_code:
            message += f" (error={error_code})"
        if reauth_required:
            message += " (interactive login required)"
        super().__init__(message)


@dataclass
class ProviderLoginConfig:
    """How to log in to one subscription provider.

    ``flow`` is ``"pkce"`` (authorization code + PKCE) or
    ``"device"`` (device flow). ``authorize_url`` is used only by
    the PKCE flow, ``device_code_url`` only by the device flow.
    ``token_url`` and ``client_id`` come from the matching
    ``OAuthProviderConfig`` in the refresher; a None value means
    the login is not wired yet and starting it raises a clear
    error naming the gap. ``client_secret`` is only for
    confidential clients and must come from env or the vault,
    never from code; it is never logged.
    """

    provider: str
    flow: str  # "pkce" | "device"
    authorize_url: str | None = None
    device_code_url: str | None = None
    token_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scope: tuple[str, ...] = ()
    notes: str = ""

    def __repr__(self) -> str:  # never leak the client secret
        return (
            f"ProviderLoginConfig(provider={self.provider!r}, flow={self.flow!r}, "
            f"authorize_url={self.authorize_url!r}, "
            f"device_code_url={self.device_code_url!r}, "
            f"token_url={self.token_url!r}, client_id={self.client_id!r}, "
            f"client_secret=<redacted> "
            f"scope={self.scope!r})"
        )

    def to_oauth_config(self) -> OAuthProviderConfig:
        """The matching refresh config for ``puspa/router/oauth.py``."""
        return OAuthProviderConfig(
            provider=self.provider,
            token_url=self.token_url,
            client_id=self.client_id,
            client_secret=self.client_secret,
            scope=self.scope,
            simulated=True,
            notes=self.notes,
        )


def known_provider_configs() -> dict[str, ProviderLoginConfig]:
    """Login configs for the three known subscription providers.

    xAI logs in with authorization code + PKCE (its token endpoint
    and public client id are verified from nanobot's source).
    GitHub Copilot logs in with the device flow (its device-code
    and token URLs are the public GitHub OAuth endpoints; the
    client id comes from ``PUSPA_GITHUB_OAUTH_CLIENT_ID``).
    Codex is registered but not wired: its login runs through the
    external ``oauth_cli_kit`` library in nanobot, whose endpoints
    were not verified, so its config carries no URLs.
    """
    github_client_id = os.environ.get("PUSPA_GITHUB_OAUTH_CLIENT_ID") or None
    xai_authorize_url = os.environ.get("PUSPA_XAI_AUTHORIZE_URL") or XAI_AUTHORIZE_URL
    return {
        "xai": ProviderLoginConfig(
            provider="xai",
            flow="pkce",
            authorize_url=xai_authorize_url,
            token_url=XAI_TOKEN_URL,
            client_id=XAI_CLIENT_ID,
            scope=XAI_SCOPES,
            notes="public client id from nanobot source; no client secret",
        ),
        "github_copilot": ProviderLoginConfig(
            provider="github_copilot",
            flow="device",
            device_code_url=GITHUB_DEVICE_CODE_URL,
            token_url=GITHUB_TOKEN_URL,
            client_id=github_client_id,
            scope=(),
            notes=("client id from PUSPA_GITHUB_OAUTH_CLIENT_ID; device flow, no client secret"),
        ),
        "openai_codex": ProviderLoginConfig(
            provider="openai_codex",
            flow="pkce",
            authorize_url=None,
            token_url=None,
            client_id=None,
            notes=(
                "login runs through the oauth_cli_kit library in "
                "nanobot; its authorize and token endpoints are not "
                "verified yet, so Puspa login is not wired for Codex"
            ),
        ),
    }


# ---------------------------------------------------------------------------
# PKCE helpers (same construction as puspa/auth/google_oauth.py)
# ---------------------------------------------------------------------------


def new_code_verifier() -> str:
    """A random PKCE code verifier (43-128 characters)."""
    return secrets.token_urlsafe(48)


def new_code_challenge(verifier: str) -> str:
    """The S256 code challenge for a verifier."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def new_state() -> str:
    """A random login state value (CSRF protection)."""
    return secrets.token_urlsafe(24)


def _http_post(http_client, url: str, data: dict):
    """POST form data; ``http_client`` is the injectable transport.

    The transport has ``post(url, data=...)`` returning an object
    with ``status_code`` and ``json()``, the same seam
    ``puspa/router/oauth.py`` uses. With no client, a short-lived
    httpx client is used (only for a real login).
    """
    if http_client is not None:
        return http_client.post(url, data=data)
    import httpx

    with httpx.Client(timeout=30) as client:
        return client.post(url, data=data)


def _token_set_from_payload(provider: str, payload: dict, now: datetime) -> TokenSet:
    """Build a TokenSet from a token endpoint response.

    Raises ProviderLoginError when the response carries no access
    token. The payload itself is never logged.
    """
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ProviderLoginError(provider, "token endpoint returned no access token")
    try:
        lifetime = int(payload.get("expires_in") or 3600)
    except (TypeError, ValueError):
        lifetime = 3600
    if lifetime <= 0:
        lifetime = 3600
    return TokenSet(
        access_token=str(payload["access_token"]),
        refresh_token=(str(payload["refresh_token"]) if payload.get("refresh_token") else None),
        expires_at=now + timedelta(seconds=lifetime),
        token_type=str(payload.get("token_type") or "Bearer"),
        scope=str(payload.get("scope") or ""),
        obtained_at=now,
    )


def _error_code(response) -> str | None:
    """The provider's error code from a failed response, if any."""
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("error")
    return str(code) if code else None


# ---------------------------------------------------------------------------
# Authorization code + PKCE flow
# ---------------------------------------------------------------------------


def build_pkce_auth_url(
    config: ProviderLoginConfig,
    redirect_uri: str,
    *,
    state: str | None = None,
    code_verifier: str | None = None,
) -> dict:
    """Build a PKCE authorize URL for ``config``.

    Returns ``{"url", "state", "code_verifier", "code_challenge"}``.
    The caller keeps ``state`` + ``code_verifier`` (in the pending
    session) and checks the returned state on callback.
    """
    if config.flow != "pkce":
        raise ProviderLoginError(config.provider, f"provider uses the {config.flow} flow, not PKCE")
    if not config.authorize_url:
        raise ProviderLoginError(
            config.provider,
            "no authorize URL registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    if not config.client_id:
        raise ProviderLoginError(
            config.provider,
            "no client id registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    state = state or new_state()
    code_verifier = code_verifier or new_code_verifier()
    if not 43 <= len(code_verifier) <= 128:
        raise ValueError("code_verifier must be 43-128 characters")
    params = {
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.scope),
        "state": state,
        "code_challenge": new_code_challenge(code_verifier),
        "code_challenge_method": "S256",
    }
    return {
        "url": config.authorize_url + "?" + urlencode(params),
        "state": state,
        "code_verifier": code_verifier,
        "code_challenge": params["code_challenge"],
    }


def exchange_pkce_code(
    config: ProviderLoginConfig,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    *,
    http_client=None,
    now: datetime | None = None,
) -> TokenSet:
    """Exchange an authorization code for a token set (PKCE)."""
    if config.flow != "pkce":
        raise ProviderLoginError(config.provider, f"provider uses the {config.flow} flow, not PKCE")
    if not config.token_url:
        raise ProviderLoginError(
            config.provider,
            "no token URL registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    if not code:
        raise ProviderLoginError(config.provider, "no authorization code given")
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": config.client_id or "",
        "code_verifier": code_verifier,
    }
    if config.client_secret:
        data["client_secret"] = config.client_secret
    response = _http_post(http_client, config.token_url, data)
    if response.status_code != 200:
        code_out = _error_code(response)
        raise ProviderLoginError(
            config.provider,
            "token endpoint refused the authorization code",
            error_code=code_out,
            reauth_required=(code_out == "invalid_grant"),
        )
    return _token_set_from_payload(config.provider, response.json(), now or _utcnow())


# ---------------------------------------------------------------------------
# Device flow
# ---------------------------------------------------------------------------


@dataclass
class DeviceCode:
    """One device-flow authorization, as returned by the provider.

    ``user_code`` and ``verification_uri`` are what the user types
    and opens; they are safe to print. ``device_code`` is a
    credential: it is kept in memory only and never printed or
    logged.
    """

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int = 900
    interval: int = 5

    def __repr__(self) -> str:  # never leak the device code
        return (
            f"DeviceCode(device_code=<redacted> "
            f"user_code={self.user_code!r}, "
            f"verification_uri={self.verification_uri!r}, "
            f"expires_in={self.expires_in!r}, interval={self.interval!r})"
        )

    def public_dict(self) -> dict:
        """What the terminal and the PWA may show the user."""
        return {
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "expires_in": self.expires_in,
            "interval": self.interval,
        }


def start_device_flow(config: ProviderLoginConfig, *, http_client=None) -> DeviceCode:
    """Start a device flow: ask the provider for a user code."""
    if config.flow != "device":
        raise ProviderLoginError(
            config.provider, f"provider uses the {config.flow} flow, not device flow"
        )
    if not config.device_code_url:
        raise ProviderLoginError(
            config.provider,
            "no device-code URL registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    if not config.client_id:
        raise ProviderLoginError(
            config.provider,
            "no client id registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    data: dict[str, str] = {
        "client_id": config.client_id,
        "scope": " ".join(config.scope),
    }
    response = _http_post(http_client, config.device_code_url, data)
    if response.status_code != 200:
        raise ProviderLoginError(
            config.provider,
            "device-code endpoint refused the request",
            error_code=_error_code(response),
        )
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("device_code"):
        raise ProviderLoginError(config.provider, "device-code endpoint returned no device code")
    return DeviceCode(
        device_code=str(payload["device_code"]),
        user_code=str(payload.get("user_code") or ""),
        verification_uri=str(
            payload.get("verification_uri") or payload.get("verification_url") or ""
        ),
        expires_in=int(payload.get("expires_in") or 900),
        interval=int(payload.get("interval") or 5),
    )


def poll_device_code(
    config: ProviderLoginConfig,
    device_code: str,
    *,
    http_client=None,
    now: datetime | None = None,
) -> TokenSet | None:
    """Poll once for a device-flow token.

    Returns a TokenSet when the user has approved, None when the
    provider says ``authorization_pending`` (keep polling), and
    raises ProviderLoginError on ``expired_token`` or any other
    refusal. ``slow_down`` is reported as pending as well: the
    caller waits longer before the next poll.
    """
    if not config.token_url:
        raise ProviderLoginError(
            config.provider,
            "no token URL registered for this provider; "
            "login is not wired yet" + (f" ({config.notes})" if config.notes else ""),
        )
    data: dict[str, str] = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": config.client_id or "",
    }
    if config.client_secret:
        data["client_secret"] = config.client_secret
    response = _http_post(http_client, config.token_url, data)
    if response.status_code == 200:
        return _token_set_from_payload(config.provider, response.json(), now or _utcnow())
    error_code = _error_code(response)
    if error_code in ("authorization_pending", "slow_down"):
        return None
    raise ProviderLoginError(
        config.provider,
        "device flow failed",
        error_code=error_code,
        reauth_required=(error_code == "expired_token"),
    )


# ---------------------------------------------------------------------------
# Vault-backed token storage (tokens live ONLY here, encrypted)
# ---------------------------------------------------------------------------


def _serialize_token_set(tokens: TokenSet) -> str:
    return json.dumps(
        {
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None,
            "token_type": tokens.token_type,
            "scope": tokens.scope,
            "obtained_at": tokens.obtained_at.isoformat(),
        }
    )


def _deserialize_token_set(raw: str) -> TokenSet:
    data = json.loads(raw)
    expires_at = data.get("expires_at")
    obtained_at = data.get("obtained_at")
    return TokenSet(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
        token_type=data.get("token_type") or "Bearer",
        scope=data.get("scope") or "",
        obtained_at=datetime.fromisoformat(obtained_at) if obtained_at else _utcnow(),
    )


class VaultTokenStore:
    """One provider's token set in the per-user vault, encrypted.

    The vault entry is ``byok:oauth:<provider>``. Nothing here
    logs or prints a token value; only the provider name and the
    expiry time are ever visible outside the vault.
    """

    def __init__(self, *, dsn=None) -> None:
        self._dsn = dsn

    def save(self, user_id: str, provider: str, tokens: TokenSet) -> None:
        from puspa.accounts.vault import put as vault_put

        vault_put(user_id, token_vault_name(provider), _serialize_token_set(tokens), dsn=self._dsn)

    def load(self, user_id: str, provider: str) -> TokenSet | None:
        from puspa.accounts.vault import get as vault_get

        try:
            raw = vault_get(user_id, token_vault_name(provider), dsn=self._dsn)
        except KeyError:
            return None
        return _deserialize_token_set(raw)

    def delete(self, user_id: str, provider: str) -> None:
        from puspa.accounts.vault import delete as vault_delete

        vault_delete(user_id, token_vault_name(provider), dsn=self._dsn)

    def status(self, user_id: str, provider: str, *, now: datetime | None = None) -> dict:
        """Login state for one provider, safe to show in a UI.

        Returns ``{"state", "expires_at"}`` where state is one of
        ``logged_in`` / ``expiring`` / ``expired`` /
        ``not_logged_in``. No token value is ever in the result.
        """
        moment = now or _utcnow()
        tokens = self.load(user_id, provider)
        if tokens is None:
            return {"state": "not_logged_in", "expires_at": None}
        expires_at = tokens.expires_at.isoformat() if tokens.expires_at else None
        if tokens.is_expired(moment):
            return {"state": "expired", "expires_at": expires_at}
        if tokens.needs_refresh(moment, DEFAULT_REFRESH_LEEWAY_SECONDS):
            return {"state": "expiring", "expires_at": expires_at}
        return {"state": "logged_in", "expires_at": expires_at}


# ---------------------------------------------------------------------------
# Pending PKCE sessions (in-memory, ten minutes)
# ---------------------------------------------------------------------------


@dataclass
class LoginSession:
    """One pending PKCE login: state + verifier + provider."""

    provider: str
    state: str
    code_verifier: str
    redirect_uri: str
    created_at: datetime = field(default_factory=_utcnow)
    ttl_seconds: int = DEFAULT_PKCE_SESSION_TTL_SECONDS

    def is_expired(self, now: datetime) -> bool:
        return (now - self.created_at).total_seconds() > self.ttl_seconds


# ---------------------------------------------------------------------------
# The service: one login, two surfaces (terminal + PWA)
# ---------------------------------------------------------------------------


class ProviderLoginService:
    """Subscription login for one user base, backed by the vault.

    The terminal and the PWA call the same methods here, so there
    is one flow with two surfaces. Refresh reuses
    ``TokenRefresher`` from ``puspa/router/oauth.py``: the stored
    token set is loaded from the vault into the refresher's store,
    ``get_valid_token`` refreshes it before expiry when needed,
    and a renewed set is written back to the vault.
    """

    def __init__(
        self,
        *,
        dsn=None,
        http_client=None,
        now_fn=None,
        refresher: TokenRefresher | None = None,
        configs: dict[str, ProviderLoginConfig] | None = None,
    ) -> None:
        self._dsn = dsn
        self._http_client = http_client
        self._now_fn = now_fn or _utcnow
        self._store = VaultTokenStore(dsn=dsn)
        self._refresher = (
            refresher if refresher is not None else TokenRefresher(now_fn=self._now_fn)
        )
        self._configs = dict(configs) if configs is not None else known_provider_configs()
        self._sessions: dict[str, LoginSession] = {}
        self._sessions_lock = threading.Lock()
        self._device_codes: dict[str, DeviceCode] = {}
        self._device_lock = threading.Lock()

    def now(self) -> datetime:
        return self._now_fn()

    # -- registry -------------------------------------------------------

    def providers(self) -> list[str]:
        return sorted(self._configs)

    def config(self, provider: str) -> ProviderLoginConfig:
        config = self._configs.get(provider)
        if config is None:
            raise ProviderLoginError(provider, "unknown subscription provider")
        return config

    # -- status / logout -------------------------------------------------

    def status(self, user_id: str, provider: str) -> dict:
        """Login state for one provider (see VaultTokenStore.status)."""
        self.config(provider)  # unknown provider raises first
        result = self._store.status(user_id, provider, now=self.now())
        result["provider"] = provider
        result["flow"] = self._configs[provider].flow
        result["login_ready"] = bool(
            self._configs[provider].authorize_url or self._configs[provider].device_code_url
        ) and bool(self._configs[provider].client_id)
        return result

    def list_status(self, user_id: str) -> list[dict]:
        return [self.status(user_id, provider) for provider in self.providers()]

    def logout(self, user_id: str, provider: str) -> None:
        """Drop one provider's tokens from the vault (idempotent)."""
        self.config(provider)
        self._store.delete(user_id, provider)
        logger.info("provider %s logged out for one user", provider)

    # -- PKCE flow (terminal paste-code + PWA callback share it) --------

    def start_pkce(self, provider: str, redirect_uri: str) -> dict:
        """Begin a PKCE login: returns the URL + state to track it.

        The returned dict carries the authorize URL, the state, and
        the code challenge; the verifier stays server-side in the
        pending session and is never returned.
        """
        config = self.config(provider)
        built = build_pkce_auth_url(config, redirect_uri)
        session = LoginSession(
            provider=provider,
            state=built["state"],
            code_verifier=built["code_verifier"],
            redirect_uri=redirect_uri,
            created_at=self.now(),
        )
        with self._sessions_lock:
            self._sessions[built["state"]] = session
        logger.info("pkce login started for provider %s", provider)
        return {
            "url": built["url"],
            "state": built["state"],
            "code_challenge": built["code_challenge"],
        }

    def finish_pkce(self, user_id: str, state: str, code: str, *, http_client=None) -> dict:
        """Finish a PKCE login: check state, exchange code, save.

        Returns the provider status dict. Raises
        ProviderLoginError when the state is unknown, stale, or
        for another provider, or when the exchange fails.
        """
        with self._sessions_lock:
            session = self._sessions.pop(state, None)
        if session is None:
            raise ProviderLoginError(
                "unknown", "unknown or expired login session; start the login again"
            )
        if session.is_expired(self.now()):
            raise ProviderLoginError(
                session.provider, "login session expired; start the login again"
            )
        config = self.config(session.provider)
        tokens = exchange_pkce_code(
            config,
            code,
            session.code_verifier,
            session.redirect_uri,
            http_client=http_client if http_client is not None else self._http_client,
            now=self.now(),
        )
        self._store.save(user_id, session.provider, tokens)
        logger.info("pkce login completed for provider %s", session.provider)
        return self.status(user_id, session.provider)

    # -- device flow ------------------------------------------------------

    def start_device(self, provider: str) -> dict:
        """Begin a device login: returns what the user must open/type.

        The device code itself stays server-side, keyed by a
        one-time poll key that the caller passes back to
        :meth:`poll_device`. The user code and verification URL
        are safe to show.
        """
        config = self.config(provider)
        device = start_device_flow(config, http_client=self._http_client)
        poll_key = secrets.token_urlsafe(24)
        with self._device_lock:
            self._device_codes[poll_key] = device
        result = device.public_dict()
        result["poll_key"] = poll_key
        logger.info("device login started for provider %s", provider)
        return result

    def poll_device(self, user_id: str, provider: str, poll_key: str, *, http_client=None) -> dict:
        """Poll once for a device login started by :meth:`start_device`.

        Returns ``{"status": "pending"}`` while the user has not
        approved yet, ``{"status": "complete", ...}`` with the
        provider status once the token is stored, and raises
        ProviderLoginError when the flow expires or fails.
        """
        config = self.config(provider)
        with self._device_lock:
            device = self._device_codes.get(poll_key)
        if device is None:
            raise ProviderLoginError(provider, "unknown device login; start the login again")
        tokens = poll_device_code(
            config,
            device.device_code,
            http_client=http_client if http_client is not None else self._http_client,
            now=self.now(),
        )
        if tokens is None:
            return {"status": "pending"}
        with self._device_lock:
            self._device_codes.pop(poll_key, None)
        self._store.save(user_id, provider, tokens)
        logger.info("device login completed for provider %s", provider)
        result = self.status(user_id, provider)
        result["status"] = "complete"
        return result

    # -- token use: refresh before expiry, persisted back -----------------

    def get_valid_token(self, user_id: str, provider: str, *, http_client=None) -> str:
        """Return a usable access token, refreshing before expiry.

        Loads the vault token set into the shared refresher, lets
        the refresher renew it when it expires inside the leeway,
        and writes a renewed set back to the vault, so the next
        call (even in a new process) sees the fresh token. Raises
        ``TokenRefreshError`` (from ``puspa/router/oauth.py``)
        when no usable token exists or the refresh fails; the
        expired token is never returned.
        """
        config = self.config(provider)
        stored = self._store.load(user_id, provider)
        if stored is None:
            raise TokenRefreshError(
                provider, "no token stored; interactive login required", reauth_required=True
            )
        store = TokenStore()
        store.put(provider, stored)
        refresher = TokenRefresher(store=store, now_fn=self._now_fn)
        refresher.register(config.to_oauth_config())
        token = refresher.get_valid_token(
            provider, http_client if http_client is not None else self._http_client
        )
        fresh = store.get(provider)
        if fresh is not None and fresh.access_token != stored.access_token:
            self._store.save(user_id, provider, fresh)
        return token

    # -- helpers for the terminal -----------------------------------------

    def wait_for_device_login(
        self,
        user_id: str,
        provider: str,
        poll_key: str,
        *,
        timeout_seconds: int = 900,
        sleep_fn=time.sleep,
    ) -> dict:
        """Poll a device login until it completes or times out.

        ``sleep_fn`` is injectable so tests never really sleep.
        Raises ProviderLoginError on timeout.
        """
        config = self.config(provider)
        started = self.now()
        interval = 5
        with self._device_lock:
            device = self._device_codes.get(poll_key)
        if device is not None:
            interval = max(1, device.interval)
        while True:
            result = self.poll_device(user_id, provider, poll_key)
            if result.get("status") == "complete":
                return result
            elapsed = (self.now() - started).total_seconds()
            if elapsed >= timeout_seconds:
                raise ProviderLoginError(
                    provider,
                    "device login timed out; start the login again",
                    reauth_required=True,
                )
            sleep_fn(interval)
            _ = config
