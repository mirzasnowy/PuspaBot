"""Token apps layer (G10, layer D): paste a token, verify, seal, serve.

A user pastes an API token for some app. The flow is:

1. **Live check**: exactly one validation call to the app's API
   (``validate_token_live``). A 2xx answer means the token works;
   401/403 means it does not. A network failure raises
   :class:`TokenValidationError`: it is a clear failure, never a
   false success. Nothing is stored before this check passes.
2. **Seal**: the token is stored sealed, never in plaintext. The
   default seal store is the Puspa vault (envelope encryption, see
   ``puspa.accounts.vault``); an in-memory seal store is provided
   for tests. Only the vault entry name (``token_ref``) is kept on
   the app record.
3. **Serve as MCP server**: the sealed token app becomes an MCP
   server definition (same format as ``puspa.plugins.mcp``), so the
   engine can use the app's tools without ever seeing the token.
4. **Refresh**: a token is refreshed 10 minutes before expiry
   (``REFRESH_BEFORE_SECONDS``) and checked every 5 minutes
   (``CHECK_INTERVAL_SECONDS``). This module defines the check and
   refresh functions; scheduling them is the scheduler's job and
   lives outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx

__all__ = [
    "CHECK_INTERVAL_SECONDS",
    "REFRESH_BEFORE_SECONDS",
    "InMemorySealStore",
    "TokenApp",
    "TokenAppError",
    "TokenValidationError",
    "VaultSealStore",
    "attach_token_app",
    "due_for_check",
    "needs_refresh",
    "refresh_token_app",
    "token_app_to_mcp_definition",
    "validate_token_live",
]

#: How often token expiry is checked, in seconds (5 minutes).
CHECK_INTERVAL_SECONDS = 300

#: Refresh a token this far before it expires, in seconds (10 minutes).
REFRESH_BEFORE_SECONDS = 600

#: Default timeout for the single live validation call, in seconds.
VALIDATION_TIMEOUT_SECONDS = 10.0


class TokenAppError(ValueError):
    """A token app operation is invalid."""


class TokenValidationError(TokenAppError):
    """The live token check failed (invalid token or no network)."""


@dataclass
class TokenApp:
    """One attached token app. The token itself is never stored here."""

    app_id: str
    display_name: str
    validate_url: str
    token_ref: str
    expires_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def is_expired(self, now=None) -> bool:
        """True when the token has passed its expiry."""
        if self.expires_at is None:
            return False
        now = now or datetime.now(UTC)
        return now >= self.expires_at


class InMemorySealStore:
    """Seal store that keeps values in memory (tests and demos).

    Values are kept as-is in a dict keyed by name. This is NOT
    encryption and must never be used for real user tokens; use
    :class:`VaultSealStore` for those.
    """

    def __init__(self):
        self._values: dict[str, str] = {}

    def put(self, name: str, value: str) -> None:
        self._values[name] = value

    def get(self, name: str) -> str:
        try:
            return self._values[name]
        except KeyError:
            raise KeyError(name) from None

    def delete(self, name: str) -> None:
        self._values.pop(name, None)

    def names(self) -> list[str]:
        """Stored entry names, sorted."""
        return sorted(self._values)


class VaultSealStore:
    """Seal store backed by the Puspa vault (envelope encryption).

    The vault encrypts per user with a key derived from the master
    key (``PUSPA_VAULT_MASTER_KEY``) and never stores plaintext.
    Entry names are ``token_app:<user_id>:<app_id>``-style names
    supplied by the caller; the vault itself is keyed by
    ``(user_id, name)``.
    """

    def __init__(self, user_id, *, dsn=None):
        self.user_id = str(user_id)
        self.dsn = dsn

    def put(self, name: str, value: str) -> None:
        from puspa.accounts.vault import put

        put(self.user_id, name, value, dsn=self.dsn)

    def get(self, name: str) -> str:
        from puspa.accounts.vault import get

        return get(self.user_id, name, dsn=self.dsn)

    def delete(self, name: str) -> None:
        from puspa.accounts.vault import delete

        delete(self.user_id, name, dsn=self.dsn)


def validate_token_live(validate_url: str, token: str, *, http_client=None) -> dict:
    """Check a token with exactly one live call to its API.

    Sends ``GET validate_url`` with ``Authorization: Bearer <token>``.
    Returns ``{"valid": bool, "status": int}``: 2xx means valid,
    401/403 means invalid. Any other status, and any transport
    error (no network, timeout, DNS failure), raises
    :class:`TokenValidationError` instead of returning a guess:
    without a network answer there is no basis to call the token
    valid, and a failed check must never look like a success.
    """
    if not isinstance(validate_url, str) or not validate_url.startswith("https://"):
        raise TokenValidationError("validate_url must be an https URL")
    if not isinstance(token, str) or not token:
        raise TokenValidationError("token must be a non-empty string")
    client = http_client or httpx.Client(timeout=VALIDATION_TIMEOUT_SECONDS)
    try:
        response = client.get(validate_url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise TokenValidationError(f"live token check failed without an answer: {exc}") from exc
    if 200 <= response.status_code < 300:
        return {"valid": True, "status": response.status_code}
    if response.status_code in (401, 403):
        return {"valid": False, "status": response.status_code}
    raise TokenValidationError(
        f"live token check answered unexpectedly: HTTP {response.status_code}"
    )


def attach_token_app(
    app_id, display_name, validate_url, token, *, seal_store, expires_at=None, http_client=None
) -> TokenApp:
    """Attach a token app: live-check first, seal second.

    The token is validated live (one call) and only then sealed
    into ``seal_store`` under ``token_app:<app_id>``. An invalid
    token raises :class:`TokenValidationError` and nothing is
    stored. Returns the :class:`TokenApp` record, which holds the
    seal reference, never the token.
    """
    if not isinstance(app_id, str) or not app_id:
        raise TokenAppError("app_id must be a non-empty string")
    result = validate_token_live(validate_url, token, http_client=http_client)
    if not result["valid"]:
        raise TokenValidationError(
            f"token for {app_id!r} was rejected live (HTTP {result['status']}); nothing was stored"
        )
    token_ref = f"token_app:{app_id}"
    seal_store.put(token_ref, token)
    return TokenApp(
        app_id=app_id,
        display_name=display_name,
        validate_url=validate_url,
        token_ref=token_ref,
        expires_at=expires_at,
    )


def needs_refresh(app: TokenApp, now=None) -> bool:
    """True when a token must be refreshed now.

    A token with no expiry never needs a refresh. Otherwise it
    needs one when expiry is within ``REFRESH_BEFORE_SECONDS``
    (10 minutes) or already passed.
    """
    if app.expires_at is None:
        return False
    now = now or datetime.now(UTC)
    return (app.expires_at - now) <= timedelta(seconds=REFRESH_BEFORE_SECONDS)


def due_for_check(apps, last_checked: dict, now=None) -> list[TokenApp]:
    """Apps whose expiry check is due (checked over 5 minutes ago).

    ``last_checked`` maps app ids to the datetime of their last
    check. An app never checked before is due immediately.
    """
    now = now or datetime.now(UTC)
    due = []
    for app in apps:
        checked_at = last_checked.get(app.app_id)
        if checked_at is None or (now - checked_at) >= timedelta(seconds=CHECK_INTERVAL_SECONDS):
            due.append(app)
    return due


def refresh_token_app(app: TokenApp, refresher, seal_store, *, now=None) -> dict:
    """Refresh one token app's token when it is due.

    ``refresher`` is a callable taking the current sealed token and
    returning ``(new_token, new_expires_at)``. The new token is
    sealed under the same reference and the app record is updated
    in place. Returns ``{"refreshed": bool, "app": app}``;
    ``refreshed`` is False (and nothing is called or stored) when
    the token does not need a refresh yet. The refresher itself
    (real OAuth refresh) is supplied by the caller: this module
    defines when a refresh happens, not how the provider refreshes.
    """
    if not needs_refresh(app, now=now):
        return {"refreshed": False, "app": app}
    current_token = seal_store.get(app.token_ref)
    new_token, new_expires_at = refresher(current_token)
    if not isinstance(new_token, str) or not new_token:
        raise TokenAppError("refresher must return a non-empty token")
    seal_store.put(app.token_ref, new_token)
    app.expires_at = new_expires_at
    return {"refreshed": True, "app": app}


def token_app_to_mcp_definition(app: TokenApp, tools: list[dict]) -> dict:
    """An MCP definition (G27 format) serving one token app.

    The definition carries the app's tools and a server command
    placeholder; the token itself is never in the definition (the
    transport injects the Authorization header from the sealed
    token at call time). ``tools`` must be a non-empty list of
    ``{"name", "description"}`` dicts.
    """
    if not tools:
        raise TokenAppError("a token app needs at least one tool")
    return {
        "id": f"token-app-{app.app_id}",
        "name": f"{app.display_name} (token app)",
        "version": "1.0.0",
        "description": f"MCP server for token app {app.display_name}.",
        "server": {
            "command": "puspa-token-app",
            "args": [app.app_id],
            "env": {},
            "transport": "stdio",
        },
        "tools": [{"name": tool["name"], "description": tool["description"]} for tool in tools],
        "dependencies": [],
    }
