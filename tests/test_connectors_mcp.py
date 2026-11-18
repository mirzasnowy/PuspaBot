"""Tests: MCP registry + OAuth sign-in (G10, layer C).

In-memory only: no database, no network. Uses the G27 MCP
definition format from puspa.plugins.mcp; no second format.
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.connectors.mcp_registry import (
    McpRegistry,
    McpRegistryError,
    OAuthError,
    OAuthFlow,
    authorization_header,
    engine_view,
)
from puspa.plugins.mcp import validate_mcp_definition


def _definition(server_id="notes-mcp"):
    return {
        "id": server_id,
        "name": "Notes MCP",
        "version": "1.0.0",
        "description": "Read notes.",
        "server": {
            "command": "python3",
            "args": ["-m", "notes_mcp"],
            "env": {},
            "transport": "stdio",
        },
        "tools": [{"name": "read_note", "description": "Read a note."}],
        "dependencies": [],
    }


def test_registry_validates_g27_format_and_rejects_duplicates():
    registry = McpRegistry()
    assert validate_mcp_definition(_definition()) == []
    registry.register(_definition())
    assert registry.list_ids() == ["notes-mcp"]
    assert registry.status("notes-mcp") == "registered"
    with pytest.raises(McpRegistryError, match="already registered"):
        registry.register(_definition())
    bad = _definition("bad-mcp")
    bad["tools"] = []
    with pytest.raises(McpRegistryError, match="non-empty list"):
        registry.register(bad)
    with pytest.raises(KeyError):
        registry.get("no-such-server")


def test_engine_view_never_shows_tokens_or_env():
    registry = McpRegistry()
    definition = _definition()
    definition["server"]["env"] = {"SECRET": "should-never-leak"}
    registry.register(definition)
    registry.set_connected("notes-mcp", "vault:token:notes")
    view = engine_view(registry)
    assert view == [
        {
            "id": "notes-mcp",
            "name": "Notes MCP",
            "tools": [{"name": "read_note", "description": "Read a note."}],
        }
    ]
    rendered = repr(view)
    assert "SECRET" not in rendered
    assert "should-never-leak" not in rendered
    assert "vault:token:notes" not in rendered
    assert "Authorization" not in rendered


def test_oauth_flow_one_pending_constant_time_state_and_listener_once():
    registry = McpRegistry()
    registry.register(_definition())
    flow = OAuthFlow(registry)
    calls = []
    pending = flow.start_sign_in(
        "notes-mcp",
        "https://example.id/oauth/authorize?client_id=x",
        listener=calls.append,
    )
    assert pending.server_id == "notes-mcp"
    assert pending.state
    assert flow.state_digest("notes-mcp") is not None
    assert flow.state_digest("notes-mcp") != pending.state
    # A second pending sign-in for the same server is refused.
    with pytest.raises(OAuthError, match="already has a pending"):
        flow.start_sign_in("notes-mcp", "https://example.id/oauth/authorize")
    # Wrong state is refused (constant-time compare inside).
    with pytest.raises(OAuthError, match="state mismatch"):
        flow.complete_sign_in("notes-mcp", "wrong-state", token_ref="vault:t")
    assert registry.status("notes-mcp") == "registered"
    # Correct state completes: listener runs exactly once, then is gone.
    result = flow.complete_sign_in("notes-mcp", pending.state, token_ref="vault:token:notes")
    assert result == {"server_id": "notes-mcp", "status": "connected"}
    assert registry.status("notes-mcp") == "connected"
    assert registry.token_ref("notes-mcp") == "vault:token:notes"
    assert calls == ["notes-mcp"]
    assert flow.pending("notes-mcp") is None
    # Completing twice is refused: the pending record is gone.
    with pytest.raises(OAuthError, match="no active pending"):
        flow.complete_sign_in("notes-mcp", pending.state, token_ref="vault:t")


def test_oauth_pending_expires_and_can_be_cancelled():
    registry = McpRegistry()
    registry.register(_definition())
    flow = OAuthFlow(registry)
    pending = flow.start_sign_in("notes-mcp", "https://example.id/oauth/authorize")
    future = datetime.now(UTC) + timedelta(seconds=3600)
    assert pending.is_expired(now=future) is True
    assert flow.pending("notes-mcp") is not None  # not expired yet (real clock)
    assert flow.cancel_sign_in("notes-mcp") is True
    assert flow.cancel_sign_in("notes-mcp") is False
    assert flow.pending("notes-mcp") is None
    with pytest.raises(OAuthError, match="unknown MCP server"):
        flow.start_sign_in("ghost-mcp", "https://example.id/oauth/authorize")


def test_authorization_header_is_for_transport_only():
    header = authorization_header("token-abc")
    assert header == {"Authorization": "Bearer token-abc"}
    with pytest.raises(ValueError):
        authorization_header("")
