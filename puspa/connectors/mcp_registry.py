"""Generic MCP layer (G10, layer C): registry + OAuth sign-in.

This layer reuses the MCP definition format from
:mod:`puspa.plugins.mcp` (G27): an MCP server is a validated JSON
definition (``id``, ``name``, ``version``, ``description``,
``server``, ``tools``). No second format is introduced here.

OAuth sign-in follows the DuskBot pattern (rewritten, not copied):

- At most one pending sign-in per server. Starting a second one
  while one is active raises instead of silently replacing it.
- The pending ``state`` is compared with ``hmac.compare_digest``
  (constant time), never ``==``.
- A listener (callback) lives only until the sign-in completes:
  it is invoked exactly once on success and then removed.
- The engine never sees a token. :func:`engine_view` returns only
  ids, names, and tool names/descriptions: no ``server.env``, no
  token, no ``Authorization`` header. The header is built by
  :func:`authorization_header` for the transport layer only, from
  a caller-supplied token loader.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from puspa.plugins.mcp import validate_mcp_definition

__all__ = [
    "McpRegistry",
    "McpRegistryError",
    "PendingSignIn",
    "OAuthFlow",
    "OAuthError",
    "engine_view",
    "authorization_header",
    "PENDING_TTL_SECONDS",
]

#: How long a pending OAuth sign-in stays valid (10 minutes).
PENDING_TTL_SECONDS = 600


class McpRegistryError(ValueError):
    """An MCP server registration is invalid or unknown."""


class OAuthError(ValueError):
    """An OAuth sign-in step is invalid, expired, or mismatched."""


@dataclass
class _ServerRecord:
    definition: dict
    status: str = "registered"
    token_ref: str | None = None


class McpRegistry:
    """Registry of MCP server definitions.

    Definitions are validated with ``validate_mcp_definition`` from
    ``puspa.plugins.mcp`` before they are stored. The registry holds
    definitions and a token *reference* (vault entry name) per
    server, never a token value.
    """

    def __init__(self):
        self._servers: dict[str, _ServerRecord] = {}

    def register(self, definition: dict) -> dict:
        """Validate and register one MCP definition."""
        problems = validate_mcp_definition(definition)
        if problems:
            raise McpRegistryError("; ".join(problems))
        server_id = definition["id"]
        if server_id in self._servers:
            raise McpRegistryError(
                f"MCP server {server_id!r} is already registered; "
                "shadowing is rejected"
            )
        self._servers[server_id] = _ServerRecord(definition=dict(definition))
        return dict(definition)

    def get(self, server_id: str) -> dict:
        """Return the definition for ``server_id`` (KeyError if absent)."""
        return dict(self._servers[server_id].definition)

    def __contains__(self, server_id: str) -> bool:
        return server_id in self._servers

    def __len__(self) -> int:
        return len(self._servers)

    def list_ids(self) -> list[str]:
        """Registered server ids, in registration order."""
        return list(self._servers)

    def status(self, server_id: str) -> str:
        """Connection status of one server."""
        return self._servers[server_id].status

    def set_connected(self, server_id: str, token_ref: str) -> None:
        """Mark a server connected, storing only a token reference."""
        record = self._servers[server_id]
        record.status = "connected"
        record.token_ref = token_ref

    def token_ref(self, server_id: str) -> str | None:
        """The stored token reference (vault name), or None."""
        return self._servers[server_id].token_ref

    def remove(self, server_id: str) -> None:
        """Remove a server (KeyError if absent)."""
        del self._servers[server_id]

    def tools(self, server_id: str) -> list[dict]:
        """Tool descriptors for one server: name, description, service."""
        definition = self.get(server_id)
        return [
            {
                "name": tool["name"],
                "description": tool["description"],
                "service": server_id,
            }
            for tool in definition["tools"]
        ]

    def all_tools(self) -> list[dict]:
        """Tool descriptors for every registered server."""
        tools: list[dict] = []
        for server_id in self.list_ids():
            tools.extend(self.tools(server_id))
        return tools


@dataclass
class PendingSignIn:
    """One pending OAuth sign-in for one MCP server."""

    server_id: str
    state: str
    auth_url: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
        + timedelta(seconds=PENDING_TTL_SECONDS)
    )

    def is_expired(self, now=None) -> bool:
        """True when the pending sign-in has passed its TTL."""
        now = now or datetime.now(timezone.utc)
        return now >= self.expires_at


class OAuthFlow:
    """OAuth sign-in state machine for MCP servers.

    One pending sign-in per server at most. The ``state`` value is
    compared constant-time on completion. A listener registered with
    :meth:`start_sign_in` is called exactly once when that sign-in
    completes and is then dropped: the listener lives only until
    the sign-in finishes. Tokens are never held here: completion
    takes a ``token_ref`` (a vault entry name) supplied by the
    caller, which is what gets stored on the server record.
    """

    def __init__(self, registry: McpRegistry):
        self.registry = registry
        self._pending: dict[str, PendingSignIn] = {}
        self._listeners: dict[str, list] = {}

    def start_sign_in(self, server_id: str, auth_url: str, *, listener=None) -> PendingSignIn:
        """Begin a sign-in for one server; return the pending record.

        Raises :class:`OAuthError` when the server is unknown or
        already has an active (unexpired) pending sign-in. An
        expired pending sign-in is replaced.
        """
        if server_id not in self.registry:
            raise OAuthError(f"unknown MCP server {server_id!r}")
        existing = self._pending.get(server_id)
        if existing is not None and not existing.is_expired():
            raise OAuthError(
                f"server {server_id!r} already has a pending sign-in; "
                "complete or cancel it first"
            )
        pending = PendingSignIn(
            server_id=server_id,
            state=secrets.token_urlsafe(32),
            auth_url=auth_url,
        )
        self._pending[server_id] = pending
        if listener is not None:
            self._listeners.setdefault(server_id, []).append(listener)
        return pending

    def cancel_sign_in(self, server_id: str) -> bool:
        """Cancel a pending sign-in (also drops its listeners)."""
        self._listeners.pop(server_id, None)
        return self._pending.pop(server_id, None) is not None

    def pending(self, server_id: str):
        """The pending sign-in for a server, or None."""
        record = self._pending.get(server_id)
        if record is not None and record.is_expired():
            self._pending.pop(server_id, None)
            self._listeners.pop(server_id, None)
            return None
        return record

    def complete_sign_in(self, server_id: str, state: str, *, token_ref: str) -> dict:
        """Complete a sign-in after the provider redirects back.

        ``state`` is compared constant-time against the stored
        value. On success the server is marked connected with the
        given token reference, the pending record and its listeners
        are dropped, and each listener is invoked once with the
        server id. Returns ``{"server_id", "status"}``.
        """
        record = self._pending.get(server_id)
        if record is None or record.is_expired():
            self._pending.pop(server_id, None)
            self._listeners.pop(server_id, None)
            raise OAuthError("no active pending sign-in for this server")
        if not isinstance(state, str) or not hmac.compare_digest(
            state, record.state
        ):
            raise OAuthError("OAuth state mismatch; sign-in refused")
        self._pending.pop(server_id, None)
        listeners = self._listeners.pop(server_id, [])
        self.registry.set_connected(server_id, token_ref)
        for listener in listeners:
            listener(server_id)
        return {"server_id": server_id, "status": "connected"}

    def state_digest(self, server_id: str) -> str | None:
        """SHA-256 hex of the pending state (for logs, never the state)."""
        record = self._pending.get(server_id)
        if record is None:
            return None
        return hashlib.sha256(record.state.encode()).hexdigest()


def engine_view(registry: McpRegistry) -> list[dict]:
    """What the engine may see of the MCP servers.

    Only ids, names, and tool names/descriptions. Server commands,
    environment, tokens, and token references are excluded: the
    engine never sees a token or anything that could carry one.
    """
    view = []
    for server_id in registry.list_ids():
        definition = registry.get(server_id)
        view.append(
            {
                "id": server_id,
                "name": definition["name"],
                "tools": [
                    {"name": tool["name"], "description": tool["description"]}
                    for tool in definition["tools"]
                ],
            }
        )
    return view


def authorization_header(token: str) -> dict:
    """Build the Authorization header for the transport layer only.

    This header is for the HTTP transport that talks to the MCP
    server. It must never be included in anything handed to the
    engine (see :func:`engine_view`).
    """
    if not isinstance(token, str) or not token:
        raise ValueError("token must be a non-empty string")
    return {"Authorization": f"Bearer {token}"}
