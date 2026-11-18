"""Tests: token apps, live check, sealed storage, refresh (G10, layer D).

In-memory only: no database, no real network. Live checks run
against httpx MockTransport, so the single validation call is
exercised without touching the network.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from puspa.connectors.token_apps import (
    CHECK_INTERVAL_SECONDS,
    REFRESH_BEFORE_SECONDS,
    InMemorySealStore,
    TokenApp,
    TokenAppError,
    TokenValidationError,
    attach_token_app,
    due_for_check,
    needs_refresh,
    refresh_token_app,
    token_app_to_mcp_definition,
    validate_token_live,
)
from puspa.plugins.mcp import validate_mcp_definition


def _client(status):
    def handler(request):
        assert request.headers["Authorization"] == "Bearer good-token"
        return httpx.Response(status, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_validate_token_live_accepts_2xx_rejects_401_and_fails_on_no_network():
    assert validate_token_live(
        "https://api.example.id/me", "good-token", http_client=_client(200)
    ) == {"valid": True, "status": 200}
    assert validate_token_live(
        "https://api.example.id/me", "good-token", http_client=_client(401)
    ) == {"valid": False, "status": 401}
    with pytest.raises(TokenValidationError, match="unexpectedly"):
        validate_token_live("https://api.example.id/me", "good-token", http_client=_client(500))

    def failing_handler(request):
        raise httpx.ConnectError("no network", request=request)

    failing = httpx.Client(transport=httpx.MockTransport(failing_handler))
    with pytest.raises(TokenValidationError, match="without an answer"):
        validate_token_live("https://api.example.id/me", "good-token", http_client=failing)
    with pytest.raises(TokenValidationError, match="https URL"):
        validate_token_live("http://api.example.id/me", "good-token", http_client=_client(200))


def test_attach_checks_live_first_then_seals_and_never_stores_bad_tokens():
    store = InMemorySealStore()
    app = attach_token_app(
        "notes",
        "Notes",
        "https://api.example.id/me",
        "good-token",
        seal_store=store,
        http_client=_client(200),
    )
    assert app.token_ref == "token_app:notes"
    assert store.get("token_app:notes") == "good-token"
    assert "good-token" not in repr(app)
    with pytest.raises(TokenValidationError, match="rejected live"):
        attach_token_app(
            "bad",
            "Bad",
            "https://api.example.id/me",
            "good-token",
            seal_store=store,
            http_client=_client(403),
        )
    assert "token_app:bad" not in store.names()


def test_refresh_ten_minutes_before_expiry_checked_every_five_minutes():
    assert CHECK_INTERVAL_SECONDS == 300
    assert REFRESH_BEFORE_SECONDS == 600
    now = datetime.now(UTC)
    far = TokenApp(
        "a", "A", "https://api.example.id/me", "token_app:a", expires_at=now + timedelta(hours=2)
    )
    soon = TokenApp(
        "b", "B", "https://api.example.id/me", "token_app:b", expires_at=now + timedelta(minutes=9)
    )
    past = TokenApp(
        "c", "C", "https://api.example.id/me", "token_app:c", expires_at=now - timedelta(minutes=1)
    )
    never = TokenApp("d", "D", "https://api.example.id/me", "token_app:d", expires_at=None)
    assert needs_refresh(far, now=now) is False
    assert needs_refresh(soon, now=now) is True
    assert needs_refresh(past, now=now) is True
    assert needs_refresh(never, now=now) is False
    # Due-for-check: never checked is due; checked 6 minutes ago is due.
    apps = [far, soon]
    last_checked = {
        "a": now - timedelta(minutes=6),
        "b": now - timedelta(minutes=1),
    }
    assert [app.app_id for app in due_for_check(apps, last_checked, now=now)] == ["a"]
    assert [app.app_id for app in due_for_check(apps, {}, now=now)] == ["a", "b"]


def test_refresh_token_app_replaces_sealed_token_only_when_due():
    store = InMemorySealStore()
    now = datetime.now(UTC)
    app = TokenApp(
        "notes",
        "Notes",
        "https://api.example.id/me",
        "token_app:notes",
        expires_at=now + timedelta(minutes=5),
    )
    store.put("token_app:notes", "old-token")

    def refresher(old_token):
        assert old_token == "old-token"
        return "new-token", now + timedelta(hours=1)

    result = refresh_token_app(app, refresher, store, now=now)
    assert result["refreshed"] is True
    assert store.get("token_app:notes") == "new-token"
    assert app.expires_at == now + timedelta(hours=1)
    # Not due anymore: the refresher is not called.
    calls = []
    result = refresh_token_app(app, lambda t: calls.append(t) or ("x", now), store, now=now)
    assert result["refreshed"] is False
    assert calls == []


def test_token_app_becomes_mcp_server_in_g27_format():
    store = InMemorySealStore()
    app = attach_token_app(
        "notes",
        "Notes",
        "https://api.example.id/me",
        "good-token",
        seal_store=store,
        http_client=_client(200),
    )
    definition = token_app_to_mcp_definition(
        app, [{"name": "read_note", "description": "Read a note."}]
    )
    assert validate_mcp_definition(definition) == []
    assert definition["id"] == "token-app-notes"
    assert "good-token" not in repr(definition)
    with pytest.raises(TokenAppError):
        token_app_to_mcp_definition(app, [])
