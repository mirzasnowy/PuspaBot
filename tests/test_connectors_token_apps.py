"""Tests: token apps, live check, sealed storage, refresh (G10, layer D).

In-memory only, except the end-to-end server test: no database, no
real network for the rest. Live checks run against httpx
MockTransport, so the single validation call is exercised without
touching the network. The end-to-end test seals a token in the
Postgres test database (PUSPA_TEST_DSN) and drives the real
installed ``puspa-token-app`` script through the real MCP client.
"""

import importlib.metadata
import io
import json
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from puspa.connectors.token_app_server import main as token_app_server_main
from puspa.connectors.token_app_server import serve_stdio
from puspa.connectors.token_apps import (
    CHECK_INTERVAL_SECONDS,
    REFRESH_BEFORE_SECONDS,
    InMemorySealStore,
    TokenApp,
    TokenAppError,
    TokenValidationError,
    VaultSealStore,
    attach_token_app,
    due_for_check,
    needs_refresh,
    refresh_token_app,
    token_app_to_mcp_definition,
    validate_token_live,
)
from puspa.plugins.mcp import check_mcp_readiness, validate_mcp_definition


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


def _attached_definition(tools=None, **kwargs):
    """An attached token app + its emitted MCP definition (in-memory seal)."""
    store = InMemorySealStore()
    app = attach_token_app(
        "notes",
        "Notes",
        "https://api.example.id/me",
        "good-token",
        seal_store=store,
        http_client=_client(200),
    )
    return token_app_to_mcp_definition(
        app, tools or [{"name": "read_note", "description": "Read a note."}], **kwargs
    )


def test_token_app_definition_command_resolves_to_shipped_entry_point(monkeypatch):
    """The emitted command is a console script this package ships (V2-39)."""
    definition = _attached_definition()
    assert validate_mcp_definition(definition) == []
    assert definition["server"]["command"] == "puspa-token-app"
    # The entry point is installed: its value names the server module.
    scripts = importlib.metadata.entry_points(group="console_scripts")
    entry = next((ep for ep in scripts if ep.name == "puspa-token-app"), None)
    assert entry is not None
    assert entry.value == "puspa.connectors.token_app_server:main"
    assert entry.load() is token_app_server_main
    # And the script resolves on PATH, so check_readiness passes: a
    # fresh install puts the script dir on PATH, which this test
    # mirrors by prepending the venv's bin dir.
    venv_bin = str(Path(sys.executable).parent)
    monkeypatch.setenv("PATH", os.pathsep.join([venv_bin, os.environ.get("PATH", "")]))
    assert shutil.which("puspa-token-app") is not None
    report = check_mcp_readiness(definition)
    assert report["ready"] is True
    assert report["missing"] == []


def test_token_app_definition_server_args_carry_the_tool_list():
    tools = [
        {"name": "read_note", "description": "Read a note."},
        {"name": "list_notes", "description": "List notes."},
    ]
    definition = _attached_definition(tools)
    args = definition["server"]["args"]
    assert args[0] == "notes"
    assert json.loads(args[1]) == tools
    assert "good-token" not in json.dumps(definition)


def test_token_app_definition_server_args_carry_launch_flags_when_given(test_dsn):
    # The MCP transport scrubs the environment, so a definition meant
    # to be spawned by the engine carries user, DSN and key file on
    # argv. The DSN comes from the test fixture, never hardcoded.
    user_id = "00000000-0000-0000-0000-000000000000"
    definition = _attached_definition(
        [{"name": "read_note", "description": "Read a note."}],
        user_id=user_id,
        dsn=test_dsn,
        key_file="/tmp/vault.key",
    )
    assert validate_mcp_definition(definition) == []
    args = definition["server"]["args"]
    assert args[0] == "notes"
    assert args[2:] == [
        "--user",
        user_id,
        "--dsn",
        test_dsn,
        "--key-file",
        "/tmp/vault.key",
    ]
    assert "good-token" not in json.dumps(definition)


def test_token_app_server_main_rejects_bad_arguments(monkeypatch):
    tools = json.dumps([{"name": "read_note", "description": "Read a note."}])
    for argv in (
        [],
        ["notes"],
        ["notes", "not-json"],
        ["notes", json.dumps([])],
        ["notes", json.dumps([{"name": "read_note"}])],
        ["", tools],
    ):
        with pytest.raises(SystemExit) as excinfo:
            token_app_server_main(argv)
        assert excinfo.value.code == 2
    # Valid arguments but no user: the vault is keyed by UUID user
    # id, so serving without one is a usage error too.
    monkeypatch.delenv("PUSPA_USER_ID", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        token_app_server_main(["notes", tools])
    assert excinfo.value.code == 2


def test_token_app_server_stdio_lists_tools_and_explains_tool_calls():
    tools = [{"name": "read_note", "description": "Read a note."}]
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "read_note", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 4, "method": "ping"},
    ]
    stdin = io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n")
    stdout = io.StringIO()
    assert serve_stdio("notes", tools, stdin=stdin, stdout=stdout) == 0
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    # The notification gets no reply; every request does.
    assert [reply["id"] for reply in replies] == [1, 2, 3, 4]
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    assert replies[0]["result"]["serverInfo"]["name"] == "puspa-token-app"
    assert replies[1]["result"]["tools"] == [
        {
            "name": "read_note",
            "description": "Read a note.",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]
    call = replies[2]["result"]
    assert call["isError"] is True
    assert "cannot run yet" in call["content"][0]["text"]
    assert "no mapping" in call["content"][0]["text"]
    assert replies[3]["result"] == {}


def test_token_app_server_stdio_rejects_bad_requests():
    tools = [{"name": "read_note", "description": "Read a note."}]
    stdin = io.StringIO(
        "not json\n"
        + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "nope"},
            }
        )
        + "\n"
    )
    stdout = io.StringIO()
    assert serve_stdio("notes", tools, stdin=stdin, stdout=stdout) == 0
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "request is not valid JSON"},
    }
    assert replies[1]["id"] == 9
    assert replies[1]["error"]["code"] == -32601
    assert replies[2]["id"] == 10
    assert replies[2]["error"]["code"] == -32602


@pytest.mark.integration
def test_token_app_server_end_to_end_over_real_mcp_client(
    test_dsn, vault_master_key, monkeypatch, make_user, tmp_path
):
    """The installed script serves the declared tools to the real MCP client."""
    import asyncio

    from puspa.chassis.tools import StdioMcpTransport

    # The vault keys entries by UUID user id with a foreign key to
    # users: seal under a real user. The MCP transport scrubs the
    # environment, so the user, DSN and key file travel on argv; the
    # master key itself only ever lives in the 0600 key file.
    user_id = make_user()["user_id"]
    store = VaultSealStore(user_id, dsn=test_dsn)
    app = attach_token_app(
        "notes",
        "Notes",
        "https://api.example.id/me",
        "good-token",
        seal_store=store,
        http_client=_client(200),
    )
    key_file = tmp_path / "vault.key"
    key_file.write_text(vault_master_key, encoding="utf-8")
    os.chmod(key_file, 0o600)
    tools = [{"name": "read_note", "description": "Read a note."}]
    definition = token_app_to_mcp_definition(
        app, tools, user_id=user_id, dsn=test_dsn, key_file=key_file
    )
    script = Path(sys.executable).parent / "puspa-token-app"
    assert script.exists()
    venv_bin = str(Path(sys.executable).parent)
    monkeypatch.setenv("PATH", os.pathsep.join([venv_bin, os.environ.get("PATH", "")]))
    assert check_mcp_readiness(definition)["ready"] is True

    async def _list_tools():
        transport = StdioMcpTransport(
            str(script), definition["server"]["args"], server_name="token-app-notes"
        )
        async with transport.session() as session:
            result = await session.list_tools()
            return [(tool.name, tool.description) for tool in result.tools]

    listed = asyncio.run(asyncio.wait_for(_list_tools(), timeout=30))
    assert listed == [("read_note", "Read a note.")]
