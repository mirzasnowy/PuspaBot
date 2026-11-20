"""Automatic OAuth2 token refresh for subscription providers.

Pattern studied, then rewritten as Puspa's own code (see docs/reuse.md):
9Router (decolua/9router, MIT) sits in front of OAuth subscription
providers (Claude Code, Codex, Cursor, GitHub Copilot, Antigravity)
and keeps their sessions alive by refreshing access tokens, so a
coding tool never stalls mid-task on an expired token. Only the
PATTERN is taken: no 9Router code is copied, and no 9Router
sub-project (RTK, Headroom, Ponytail) is used here. The refresh
request itself is the standard OAuth2 refresh-token grant
(RFC 6749 section 6), the same grant Puspa already uses for Google
in ``puspa/auth/google_oauth.py`` (``refresh_tokens``), this module
generalises that one-provider function into a provider registry
with proactive refresh, so any OAuth provider can be registered
once and then just works.

Where it sits in the router path
--------------------------------
Puspa's router catalog (``puspa/router/router.py``) is API-key
providers only: OpenRouter and the OpenAI-compatible family behind
the BYOK factory (``demo/byok_provider.py``). The three OAuth
providers the factory already knows, ``xai`` (Grok subscription),
``github_copilot``, ``openai_codex``, are built by nanobot with an
interactive login (``nanobot provider login ...``) and no proactive
refresh on Puspa's side: nanobot refreshes xAI tokens only inside
its own login flow, and Copilot / Codex tokens are fetched on
demand. This module is the refresh side of that gap: register an
OAuth provider once (``register_known_oauth_providers`` in
``router.py``), store its tokens, and call ``get_valid_token``
before any provider call. The caller then sends the returned access
token as the Bearer token; the router never sees a dead token.

What this module does
---------------------
- Proactive refresh: a token whose expiry falls inside the leeway
  (default 300 seconds, the same 5-minute margin nanobot uses for
  xAI) is refreshed BEFORE it is handed out, never after a 401.
- Single-flight refresh: concurrent callers for the same provider
  share one refresh. A per-provider lock plus a re-check after
  acquiring it means the second caller sees the fresh token and
  makes no second HTTP call.
- Clear failure: when a refresh fails, ``TokenRefreshError`` is
  raised with the provider, the HTTP status, and the provider's
  error code (``invalid_grant`` means the refresh token is dead
  and an interactive login is required again). The expired token
  is NEVER returned silently.
- Refresh-token rotation: when the token endpoint returns a new
  refresh token it replaces the old one; when it does not, the old
  one is kept.
- Separation: the access token and the refresh token are stored as
  two separate fields on ``TokenSet``. Nothing in this module ever
  writes, prints, or logs either value: ``__repr__`` methods show
  only provider names and expiry times, error messages carry only
  the provider, the status, and the error code, and the audit
  trail (``TokenRefresher.events``) records outcomes, never values.

What this module does NOT do (honest limits)
--------------------------------------------
- Storage is IN-MEMORY. Tokens do not survive a process restart
  and are not written to the Puspa vault (``puspa/accounts/vault.py``)
  yet. The vault is the follow-up: store the token set per user
  under ``byok:oauth:<provider>`` through ``vault.put`` / ``vault.get``
  (envelope encryption, per-user key), which needs a user id and a
  Postgres DSN this module deliberately does not take. Until then,
  a restart means an interactive login again.
- No interactive login flow lives here. The authorization-code /
  device-code step that first obtains a refresh token still runs
  through nanobot's login commands (or the provider's own flow);
  this module only keeps a token set alive once one exists.
- The three named providers are registered as SIMULATED entries by
  ``register_known_oauth_providers``: no real client secret and no
  live call is made here. xAI carries its token URL and public
  client id as read from nanobot's own source
  (``nanobot/nanobot/providers/xai_oauth.py``: issuer
  ``https://auth.x.ai``, token endpoint
  ``https://auth.x.ai/oauth2/token``); GitHub Copilot carries the
  public GitHub OAuth token endpoint but no client id; Codex
  carries neither, because its token endpoint was not verified
  from nanobot's source. A refresh against any of them raises a
  clear error naming exactly what is missing until a real
  registration (client id, secret from env or vault, token URL)
  is supplied.
- Only the refresh-token grant is implemented. Providers whose
  sessions cannot be refreshed with a refresh token (for example
  a device-flow-only session with no refresh token) raise
  ``TokenRefreshError`` with ``reauth_required=True`` instead of
  pretending a refresh happened.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

__all__ = [
    "DEFAULT_REFRESH_LEEWAY_SECONDS",
    "DEFAULT_TOKEN_TTL_SECONDS",
    "OAuthProviderConfig",
    "RefreshEvent",
    "TokenRefreshError",
    "TokenRefresher",
    "TokenSet",
    "TokenStore",
]

#: Refresh a token this many seconds before it expires, by default.
#: The same 5-minute margin nanobot uses for xAI
#: (``_TOKEN_REFRESH_MARGIN_MS = 5 * 60 * 1000``).
DEFAULT_REFRESH_LEEWAY_SECONDS = 300

#: Assumed access-token lifetime when the token endpoint answers
#: without ``expires_in``. Same 1-hour default nanobot uses for xAI
#: (``_DEFAULT_TOKEN_TTL_S = 60 * 60``).
DEFAULT_TOKEN_TTL_SECONDS = 3600

#: How many audit events are kept per refresher (newest kept).
_MAX_EVENTS = 200


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TokenRefreshError(Exception):
    """A token could not be refreshed (or no usable token exists).

    ``provider`` names the provider, ``status`` is the token
    endpoint's HTTP status when one answered (None for a transport
    failure or a pre-flight problem), and ``reauth_required`` is
    True when the refresh token itself is dead or missing, so only
    an interactive login can fix it. The message never contains a
    token value: only the provider, the reason, the status, and the
    provider's error code.
    """

    def __init__(
        self,
        provider: str,
        reason: str,
        *,
        status: int | None = None,
        error_code: str | None = None,
        reauth_required: bool = False,
    ) -> None:
        self.provider = provider
        self.reason = reason
        self.status = status
        self.error_code = error_code
        self.reauth_required = reauth_required
        message = f"oauth refresh failed for provider {provider!r}: {reason}"
        details = []
        if status is not None:
            details.append(f"HTTP {status}")
        if error_code:
            details.append(f"error={error_code}")
        if reauth_required:
            details.append("interactive login required")
        if details:
            message += " (" + ", ".join(details) + ")"
        super().__init__(message)


@dataclass
class TokenSet:
    """One provider's token pair, stored as two separate fields.

    ``access_token`` is the short-lived Bearer token; ``refresh_token``
    is the long-lived token that renews it (None when the provider
    issued no refresh token). ``expires_at`` is when the access token
    stops being valid (None when the provider never said). ``scope``
    records the granted scopes for audit, never a secret.
    ``__repr__`` deliberately shows no token value.
    """

    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    token_type: str = "Bearer"
    scope: str = ""
    obtained_at: datetime = field(default_factory=_utcnow)

    def __repr__(self) -> str:  # never leak a token value
        return (
            f"TokenSet(access_token=<redacted>, "
            f"refresh_token={'present' if self.refresh_token else 'absent'}, "
            f"expires_at={self.expires_at!r}, token_type={self.token_type!r})"
        )

    def seconds_until_expiry(self, now: datetime) -> int | None:
        """Seconds until the access token expires; None when unknown."""
        if self.expires_at is None:
            return None
        return int((self.expires_at - now).total_seconds())

    def is_expired(self, now: datetime) -> bool:
        """True when the access token is already past its expiry."""
        remaining = self.seconds_until_expiry(now)
        return remaining is not None and remaining <= 0

    def needs_refresh(self, now: datetime, leeway_seconds: int) -> bool:
        """True when the token is expired or expires inside the leeway.

        A token with unknown expiry (``expires_at`` None) cannot be
        judged, so it is treated as usable: refreshing blindly on
        every call would be worse than using it.
        """
        remaining = self.seconds_until_expiry(now)
        if remaining is None:
            return False
        return remaining <= leeway_seconds


@dataclass
class OAuthProviderConfig:
    """How to refresh one OAuth provider.

    ``token_url`` is the provider's token endpoint (None when not
    verified yet: a refresh then fails with a clear error naming
    the gap). ``client_id`` is the OAuth client id (a public value,
    not a secret). ``client_secret`` is only for confidential
    clients; it must come from env or the vault, never from code,
    and is never logged. ``simulated`` marks a registration that
    has no live credentials behind it: refreshes against it are
    expected to run only against a fake endpoint in tests.
    """

    provider: str
    token_url: str | None
    client_id: str | None
    client_secret: str | None = None
    scope: tuple[str, ...] = ()
    refresh_leeway_seconds: int = DEFAULT_REFRESH_LEEWAY_SECONDS
    simulated: bool = False
    notes: str = ""

    def __repr__(self) -> str:  # never leak the client secret
        return (
            f"OAuthProviderConfig(provider={self.provider!r}, "
            f"token_url={self.token_url!r}, client_id={self.client_id!r}, "
            f"client_secret={'present' if self.client_secret else 'absent'}, "
            f"simulated={self.simulated!r})"
        )


@dataclass
class RefreshEvent:
    """One audit record for a refresh decision. No token values."""

    provider: str
    at: datetime
    outcome: str  # "refreshed" | "failed" | "skipped"
    reason: str = ""

    def __repr__(self) -> str:
        return (
            f"RefreshEvent(provider={self.provider!r}, at={self.at!r}, "
            f"outcome={self.outcome!r}, reason={self.reason!r})"
        )


class TokenStore:
    """In-memory store for token sets, one entry per provider.

    Deliberately in-memory: persisting tokens safely means the
    encrypted vault (``puspa/accounts/vault.py``) with a user id
    and a DSN, which is a follow-up, not something this store
    pretends to do. ``__repr__`` lists provider names only.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, TokenSet] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        with self._lock:
            names = sorted(self._tokens)
        return f"TokenStore(providers={names!r})"

    def put(self, provider: str, tokens: TokenSet) -> None:
        """Store (or replace) a provider's token set."""
        if not tokens.access_token:
            raise ValueError("cannot store an empty access token")
        with self._lock:
            self._tokens[provider] = tokens

    def get(self, provider: str) -> TokenSet | None:
        with self._lock:
            return self._tokens.get(provider)

    def remove(self, provider: str) -> None:
        """Drop a provider's tokens (logout / revocation). Idempotent."""
        with self._lock:
            self._tokens.pop(provider, None)

    def providers(self) -> list[str]:
        with self._lock:
            return sorted(self._tokens)


class TokenRefresher:
    """Proactive, single-flight OAuth2 refresh for registered providers.

    ``now_fn`` is an injectable clock (tests pass a fake clock; the
    default is real UTC time). ``http_client`` is an injectable
    transport with ``post(url, data=...)`` returning an object with
    ``status_code`` and ``json()``, the same seam
    ``puspa/auth/google_oauth.py`` uses. When no client is passed,
    a short-lived httpx client is used (only for a real refresh).
    """

    def __init__(self, *, store: TokenStore | None = None, now_fn=None) -> None:
        self.store = store if store is not None else TokenStore()
        self._now_fn = now_fn or _utcnow
        self._configs: dict[str, OAuthProviderConfig] = {}
        self._config_lock = threading.Lock()
        # One lock per provider: the single-flight mechanism. Two
        # callers for the same provider serialize on it; the second
        # re-reads the store and sees the fresh token.
        self._provider_locks: dict[str, threading.Lock] = {}
        self._events: deque[RefreshEvent] = deque(maxlen=_MAX_EVENTS)

    def now(self) -> datetime:
        return self._now_fn()

    # -- registration -------------------------------------------------

    def register(self, config: OAuthProviderConfig) -> OAuthProviderConfig:
        """Register (or replace) one provider's refresh configuration."""
        if not config.provider:
            raise ValueError("provider name must not be empty")
        if config.token_url is not None and not config.token_url.startswith("https://"):
            raise ValueError(
                f"token_url for {config.provider!r} must be https, got {config.token_url!r}"
            )
        if config.refresh_leeway_seconds < 0:
            raise ValueError("refresh_leeway_seconds must not be negative")
        with self._config_lock:
            self._configs[config.provider] = config
            self._provider_locks.setdefault(config.provider, threading.Lock())
        return config

    def register_simulated(
        self,
        provider: str,
        *,
        token_url: str | None,
        client_id: str | None,
        scope=(),
        notes: str = "",
    ) -> OAuthProviderConfig:
        """Register a provider with no live credentials behind it.

        For tests and for named providers whose real registration
        (client id / secret / token URL) is not wired yet. Refreshes
        against it only make sense with a fake token endpoint.
        """
        return self.register(
            OAuthProviderConfig(
                provider=provider,
                token_url=token_url,
                client_id=client_id,
                scope=tuple(scope),
                simulated=True,
                notes=notes,
            )
        )

    def config(self, provider: str) -> OAuthProviderConfig | None:
        with self._config_lock:
            return self._configs.get(provider)

    def providers(self) -> list[str]:
        with self._config_lock:
            return sorted(self._configs)

    @property
    def events(self) -> list[RefreshEvent]:
        """Audit trail of refresh decisions (newest last). No tokens."""
        return list(self._events)

    # -- the live path ------------------------------------------------

    def get_valid_token(self, provider: str, http_client=None) -> str:
        """Return a usable access token for ``provider``.

        Refreshes proactively when the stored token expires inside
        the provider's leeway, and returns the fresh token. Returns
        the stored token untouched when it is still safely valid.
        Raises ``TokenRefreshError`` when there is no stored token,
        no refresh token, no registration, or the refresh itself
        fails, the expired token is never returned.
        """
        config = self.config(provider)
        if config is None:
            raise TokenRefreshError(provider, "provider is not registered for OAuth refresh")
        stored = self.store.get(provider)
        if stored is None:
            raise TokenRefreshError(
                provider, "no token stored; interactive login required", reauth_required=True
            )
        lock = self._provider_lock(provider)
        with lock:
            # Re-read inside the lock: another thread may have
            # refreshed while this one waited. That re-check is
            # what makes the refresh single-flight.
            stored = self.store.get(provider)
            if stored is None:  # removed while waiting
                raise TokenRefreshError(
                    provider, "no token stored; interactive login required", reauth_required=True
                )
            now = self.now()
            if not stored.needs_refresh(now, config.refresh_leeway_seconds):
                self._record(provider, "skipped", "token still valid; no refresh needed")
                return stored.access_token
            fresh = self._refresh(config, stored, http_client, now)
            self.store.put(provider, fresh)
            self._record(provider, "refreshed", "access token renewed before expiry")
            return fresh.access_token

    def refresh_now(self, provider: str, http_client=None) -> TokenSet:
        """Force a refresh now, regardless of expiry. For ops/tests."""
        config = self.config(provider)
        if config is None:
            raise TokenRefreshError(provider, "provider is not registered for OAuth refresh")
        stored = self.store.get(provider)
        if stored is None:
            raise TokenRefreshError(
                provider, "no token stored; interactive login required", reauth_required=True
            )
        lock = self._provider_lock(provider)
        with lock:
            stored = self.store.get(provider)
            if stored is None:
                raise TokenRefreshError(
                    provider, "no token stored; interactive login required", reauth_required=True
                )
            fresh = self._refresh(config, stored, http_client, self.now())
            self.store.put(provider, fresh)
            self._record(provider, "refreshed", "forced refresh")
            return fresh

    # -- internals ----------------------------------------------------

    def _provider_lock(self, provider: str) -> threading.Lock:
        with self._config_lock:
            lock = self._provider_locks.get(provider)
            if lock is None:
                lock = threading.Lock()
                self._provider_locks[provider] = lock
            return lock

    def _record(self, provider: str, outcome: str, reason: str) -> None:
        self._events.append(
            RefreshEvent(provider=provider, at=self.now(), outcome=outcome, reason=reason)
        )

    def _refresh(
        self, config: OAuthProviderConfig, old: TokenSet, http_client, now: datetime
    ) -> TokenSet:
        """One refresh-token grant against the provider's token endpoint.

        Raises ``TokenRefreshError`` on every failure path. The
        request body and the response are never logged: they carry
        token values.
        """
        provider = config.provider
        if config.token_url is None:
            self._record(provider, "failed", "no token URL registered")
            raise TokenRefreshError(
                provider,
                "no token URL registered for this provider; "
                "register its token endpoint first"
                + (f" ({config.notes})" if config.notes else ""),
            )
        if not config.client_id:
            self._record(provider, "failed", "no client id registered")
            raise TokenRefreshError(
                provider,
                "no client id registered for this provider; "
                "register its OAuth client first" + (f" ({config.notes})" if config.notes else ""),
            )
        if not old.refresh_token:
            self._record(provider, "failed", "no refresh token stored")
            raise TokenRefreshError(
                provider,
                "no refresh token stored; interactive login required",
                reauth_required=True,
            )
        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": old.refresh_token,
            "client_id": config.client_id,
        }
        if config.client_secret:
            data["client_secret"] = config.client_secret
        if config.scope:
            data["scope"] = " ".join(config.scope)
        try:
            response = self._post(config.token_url, data, http_client)
        except TokenRefreshError:
            raise
        except Exception as exc:
            self._record(provider, "failed", f"token endpoint unreachable: {type(exc).__name__}")
            raise TokenRefreshError(
                provider,
                f"token endpoint unreachable: {type(exc).__name__}",
            ) from exc
        if response.status_code != 200:
            error_code = self._error_code(response)
            reason = "token endpoint refused the refresh" + (
                f" ({error_code})" if error_code else ""
            )
            self._record(provider, "failed", reason)
            raise TokenRefreshError(
                provider,
                reason,
                status=response.status_code,
                error_code=error_code,
                reauth_required=(error_code == "invalid_grant"),
            )
        try:
            payload = response.json()
        except Exception as exc:
            self._record(provider, "failed", "token endpoint returned bad JSON")
            raise TokenRefreshError(
                provider,
                "token endpoint returned a response that is not JSON",
            ) from exc
        if not isinstance(payload, dict) or not payload.get("access_token"):
            self._record(provider, "failed", "token endpoint returned no access_token")
            raise TokenRefreshError(
                provider, "token endpoint answered 200 but returned no access_token"
            )
        expires_in = payload.get("expires_in")
        try:
            lifetime = int(expires_in) if expires_in is not None else DEFAULT_TOKEN_TTL_SECONDS
        except (TypeError, ValueError):
            lifetime = DEFAULT_TOKEN_TTL_SECONDS
        if lifetime <= 0:
            lifetime = DEFAULT_TOKEN_TTL_SECONDS
        new_refresh = payload.get("refresh_token") or old.refresh_token
        scope = payload.get("scope") or old.scope
        return TokenSet(
            access_token=str(payload["access_token"]),
            refresh_token=(str(new_refresh) if new_refresh else None),
            expires_at=now + timedelta(seconds=lifetime),
            token_type=str(payload.get("token_type") or old.token_type),
            scope=str(scope),
            obtained_at=now,
        )

    @staticmethod
    def _post(url: str, data: dict[str, str], http_client):
        """POST form data to a token endpoint (injectable transport)."""
        if http_client is not None:
            return http_client.post(url, data=data)
        import httpx

        with httpx.Client(timeout=30) as client:
            return client.post(url, data=data)

    @staticmethod
    def _error_code(response) -> str | None:
        """The provider's error code from a failed refresh, if any.

        Only the code is read (``error`` / ``error_description`` are
        provider diagnostics, never token values). Unparsable bodies
        yield None instead of raising.
        """
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        code = payload.get("error")
        return str(code) if code else None
