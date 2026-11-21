"""Tests for Puspa's own MCP server (``puspa mcp``).

An outside MCP client can list Puspa's tools, call memory search
and price check through them, and a disallowed call is refused
by the gateway. The fake client below drives the server exactly
the way a real client would — JSON-RPC requests in, replies out
— including a full stdio round-trip over byte streams and one
real subprocess run of ``python -m puspa mcp``.

Markers: ``integration`` (every test here touches the Postgres
test database through the shared fixtures, or spawns the real
CLI); the pure-protocol tests use injected fakes and no network.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pytest

from puspa.computer.backend import make_backend
from puspa.computer.hands import DesktopHands
from puspa.mcp_server.handlers import HandlerContext, build_handlers
from puspa.mcp_server.server import McpServer
from puspa.mcp_server.tools import (
    TOOL_EFFECTS,
    all_tool_definitions,
    core_tool_definitions,
    mcp_server_definition,
)
from puspa.plugins.mcp import validate_mcp_definition
from puspa.policy.gateway import Gateway

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fake outside client
# ---------------------------------------------------------------------------


class FakeClient:
    """Drive an :class:`McpServer` the way a real MCP client would."""

    def __init__(self, server: McpServer):
        self.server = server
        self.next_id = 1

    def request(self, method: str, params: dict | None = None) -> dict:
        message = {
            "jsonrpc": "2.0",
            "id": self.next_id,
            "method": method,
            "params": params or {},
        }
        self.next_id += 1
        reply = self.server.handle_message(message)
        assert reply is not None, f"no reply for {method}"
        return reply

    def notify(self, method: str, params: dict | None = None):
        reply = self.server.handle_message(
            {"jsonrpc": "2.0", "method": method, "params": params or {}}
        )
        assert reply is None, f"notification {method} got a reply: {reply}"

    def initialize(self, version: str = "2025-06-18") -> dict:
        return self.request(
            "initialize",
            {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "fake-client", "version": "0"},
            },
        )

    def call(self, name: str, arguments: dict | None = None):
        """Call a tool; return (is_error, payload).

        The payload is the parsed JSON text when the tool returned
        JSON (success results and gateway denials), else the raw
        error text."""
        reply = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        assert "result" in reply, f"tools/call {name} errored at protocol level: {reply}"
        result = reply["result"]
        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = text
        return result.get("isError", False), payload


def _server(ctx: HandlerContext | None = None, gateway: Gateway | None = None) -> McpServer:
    ctx = ctx if ctx is not None else HandlerContext()
    return McpServer(build_handlers(ctx), gateway=gateway, context=ctx)


def _fake_hands() -> DesktopHands:
    return DesktopHands(make_backend("fake"))


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------


def test_server_definition_validates():
    problems = validate_mcp_definition(mcp_server_definition())
    assert problems == []


def test_all_tools_listed_once_with_effects():
    definitions = all_tool_definitions()
    names = [tool["name"] for tool in definitions]
    assert len(names) == 29  # 6 core + 23 desktop hands
    assert len(set(names)) == len(names)
    assert set(names) == set(TOOL_EFFECTS)
    assert "memory_search" in names and "price_check" in names
    assert "computer_snapshot" in names and "computer_close_window" in names
    assert TOOL_EFFECTS["memory_search"] == "read"
    assert TOOL_EFFECTS["schedule_add"] == "act"
    assert TOOL_EFFECTS["computer_snapshot"] == "read"
    assert TOOL_EFFECTS["computer_click"] == "act"


def test_core_definitions_validate_standalone():
    definition = dict(mcp_server_definition())
    definition["tools"] = core_tool_definitions()
    assert validate_mcp_definition(definition) == []


# ---------------------------------------------------------------------------
# Protocol: initialize, ping, list, unknown methods, notifications
# ---------------------------------------------------------------------------


def test_initialize_handshake():
    client = FakeClient(_server())
    reply = client.initialize()
    result = reply["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"] == {"name": "puspa", "version": "0.1.0"}
    assert client.server.initialized


def test_initialize_unknown_version_falls_back_to_latest():
    client = FakeClient(_server())
    reply = client.initialize(version="2099-01-01")
    assert reply["result"]["protocolVersion"] == "2025-06-18"


def test_tools_list_before_initialize_is_refused():
    server = _server()
    reply = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert reply["error"]["code"] == -32002


def test_ping_before_initialize_is_allowed():
    client = FakeClient(_server())
    assert client.request("ping")["result"] == {}


def test_unknown_method_is_protocol_error():
    client = FakeClient(_server())
    client.initialize()
    reply = client.request("resources/list")
    assert reply["error"]["code"] == -32601


def test_notification_gets_no_reply():
    server = _server()
    client = FakeClient(server)
    client.initialize()
    client.notify("notifications/initialized")
    client.notify("notifications/cancelled", {"requestId": 1})


def test_tools_call_with_non_object_arguments_is_protocol_error():
    client = FakeClient(_server())
    client.initialize()
    reply = client.request("tools/call", {"name": "memory_search", "arguments": "nope"})
    assert reply["error"]["code"] == -32602


def test_unknown_tool_is_tool_error_not_protocol_error():
    client = FakeClient(_server())
    client.initialize()
    is_error, payload = client.call("no_such_tool", {})
    assert is_error is True
    assert "no_such_tool" in payload


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------


def test_tools_list_returns_all_29():
    client = FakeClient(_server())
    client.initialize()
    reply = client.request("tools/list")
    tools = reply["result"]["tools"]
    assert len(tools) == 29
    assert [tool["name"] for tool in tools] == [tool["name"] for tool in all_tool_definitions()]
    for tool in tools:
        assert tool["description"].strip()
        assert tool["inputSchema"]["type"] == "object"


# ---------------------------------------------------------------------------
# memory_search through the server (real recall over the test DB)
# ---------------------------------------------------------------------------


def test_memory_search_roundtrip(make_user, test_dsn):
    from puspa.memory.profile import add_fact

    user = make_user()
    add_fact(
        user["user_id"],
        "food",
        "user likes spicy food",
        "user_stated",
        agent_id=user["agent_id"],
        dsn=test_dsn,
    )
    ctx = HandlerContext(user_id=user["user_id"], agent_id=user["agent_id"], dsn=test_dsn)
    client = FakeClient(_server(ctx))
    client.initialize()
    is_error, payload = client.call("memory_search", {"query": "spicy food"})
    assert is_error is False
    assert payload["query"] == "spicy food"
    assert any(fact["content"] == "user likes spicy food" for fact in payload["facts"])


def test_memory_search_empty_query_is_tool_error(make_user, test_dsn):
    user = make_user()
    ctx = HandlerContext(user_id=user["user_id"], dsn=test_dsn)
    client = FakeClient(_server(ctx))
    client.initialize()
    is_error, payload = client.call("memory_search", {"query": "   "})
    assert is_error is True
    assert "query" in payload


# ---------------------------------------------------------------------------
# price_check through the server
# ---------------------------------------------------------------------------


def test_price_check_roundtrip_via_fake_compare():
    calls = []

    def fake_compare(ctx, arguments):
        calls.append(arguments)
        return {"success": True, "cheapest": "Tokopedia", "difference": 50000}

    ctx = HandlerContext(price_compare_fn=fake_compare)
    client = FakeClient(_server(ctx))
    client.initialize()
    is_error, payload = client.call(
        "price_check",
        {
            "model_number": "SM-A556E",
            "brand": "Samsung",
            "category": "phone",
            "listing_urls": {"Tokopedia": "https://tokopedia.example/x"},
        },
    )
    assert is_error is False
    assert payload["cheapest"] == "Tokopedia"
    assert calls and calls[0]["model_number"] == "SM-A556E"


def test_price_check_real_compare_runs_and_asks_when_no_match(make_user, test_dsn):
    """The real wedge runs: an unknown model asks instead of guessing."""
    user = make_user()
    ctx = HandlerContext(user_id=user["user_id"], agent_id=user["agent_id"], dsn=test_dsn)
    client = FakeClient(_server(ctx))
    client.initialize()
    is_error, payload = client.call(
        "price_check",
        {"model_number": "NO-SUCH-MODEL-123", "category": "phone", "listing_urls": {}},
    )
    assert is_error is False
    assert payload["reason"] == "perlu_ask"


# ---------------------------------------------------------------------------
# Gateway refusal: denied calls never reach the handler
# ---------------------------------------------------------------------------


def test_denied_price_check_is_refused_and_reader_never_runs():
    calls = []

    def fake_compare(ctx, arguments):
        calls.append(arguments)
        return {"success": True}

    gateway = Gateway(policy={"deny": ['mcp.tool == "price_check"'], "allow": ["true"]})
    ctx = HandlerContext(price_compare_fn=fake_compare)
    client = FakeClient(_server(ctx, gateway=gateway))
    client.initialize()
    is_error, payload = client.call(
        "price_check", {"model_number": "SM-A556E", "category": "phone"}
    )
    assert is_error is True
    assert payload["error"] == "denied"
    assert payload["tool"] == "price_check"
    assert "deny rule matched" in payload["reason"]
    assert calls == []


def test_denied_computer_tool_never_touches_hands():
    class _NeverTouch:
        def __getattr__(self, name):
            raise AssertionError(f"hands touched: {name}")

    gateway = Gateway(policy={"deny": ['mcp.tool == "computer_close_window"'], "allow": ["true"]})
    ctx = HandlerContext(hands=_NeverTouch())
    client = FakeClient(_server(ctx, gateway=gateway))
    client.initialize()
    is_error, payload = client.call("computer_close_window", {"window_id": "0.1"})
    assert is_error is True
    assert payload["error"] == "denied"


def test_gateway_with_no_policy_refuses_everything():
    gateway = Gateway(policy=None)
    ctx = HandlerContext(hands=_fake_hands())
    client = FakeClient(_server(ctx, gateway=gateway))
    client.initialize()
    is_error, payload = client.call("computer_snapshot", {})
    assert is_error is True
    assert payload["error"] == "denied"
    assert "no policy loaded" in payload["reason"]


# ---------------------------------------------------------------------------
# Desktop hands through the server (fake backend)
# ---------------------------------------------------------------------------


def test_computer_snapshot_via_fake_hands():
    ctx = HandlerContext(hands=_fake_hands())
    client = FakeClient(_server(ctx))
    client.initialize()
    is_error, payload = client.call("computer_snapshot", {})
    assert is_error is False
    assert payload["total_nodes"] > 0
    assert payload["nodes"][0]["name"] == "Demo Notes"


def test_computer_tool_without_hands_is_tool_error():
    client = FakeClient(_server(HandlerContext()))
    client.initialize()
    is_error, payload = client.call("computer_snapshot", {})
    assert is_error is True
    assert "no hands wired up" in payload


# ---------------------------------------------------------------------------
# ledger, bills, scheduler through the server (real DB)
# ---------------------------------------------------------------------------


def test_ledger_recent_roundtrip(make_user, test_dsn, ledger):
    user = make_user()
    ledger.append_event(
        task_id="tugas-mcp-1",
        agent_id=user["agent_id"],
        event_type="tool_called",
        content={"tool": "memory_search"},
    )
    ctx = HandlerContext(user_id=user["user_id"], agent_id=user["agent_id"], dsn=test_dsn)
    client = FakeClient(_server(ctx))
    client.initialize()
    is_error, payload = client.call("ledger_recent", {"task_id": "tugas-mcp-1"})
    assert is_error is False
    assert payload["count"] == 1
    assert payload["events"][0]["content"]["tool"] == "memory_search"


def test_bill_status_roundtrip(make_user, test_dsn):
    from puspa.modules.bills import create_module_bill
    from puspa.modules.tables import add_rows, list_tables

    user = make_user()
    module = create_module_bill(user_id=user["user_id"], dsn=test_dsn)
    tables = list_tables(str(module["id"]), user_id=user["user_id"], dsn=test_dsn)
    bill_table = next(table for table in tables if table["name"] == "bill")
    add_rows(
        str(bill_table["id"]),
        user_id=user["user_id"],
        data={
            "provider": "PLN",
            "amount": 132500,
            "due_date": "2026-11-20",
            "status": "not_yet_pay",
            "notes": "November 2026",
        },
        dsn=test_dsn,
    )
    ctx = HandlerContext(user_id=user["user_id"], dsn=test_dsn)
    client = FakeClient(_server(ctx))
    client.initialize()
    is_error, payload = client.call("bill_status", {})
    assert is_error is False
    assert payload["module_found"] is True and payload["table_found"] is True
    assert len(payload["bills"]) == 1
    assert payload["bills"][0]["provider"] == "PLN"
    assert payload["total_rupiah"] == 132500
    is_error, payload = client.call("bill_status", {"provider": "PDAM"})
    assert is_error is False
    assert payload["bills"] == [] and payload["total_rupiah"] == 0


def test_bill_status_without_module_is_empty_not_error(make_user, test_dsn):
    user = make_user()
    ctx = HandlerContext(user_id=user["user_id"], dsn=test_dsn)
    client = FakeClient(_server(ctx))
    client.initialize()
    is_error, payload = client.call("bill_status", {})
    assert is_error is False
    assert payload == {
        "bills": [],
        "total_rupiah": 0,
        "module_found": False,
        "table_found": False,
    }


def test_schedule_add_then_list(make_user, test_dsn):
    user = make_user()
    ctx = HandlerContext(user_id=user["user_id"], dsn=test_dsn)
    client = FakeClient(_server(ctx))
    client.initialize()
    is_error, payload = client.call(
        "schedule_add",
        {"event_type": "manual", "next_trigger_at": "2026-11-23T03:00:00+00:00"},
    )
    assert is_error is False
    assert payload["event_type"] == "manual"
    assert payload["active"] is True
    created_id = payload["id"]
    is_error, payload = client.call("schedule_list", {})
    assert is_error is False
    assert payload["count"] == 1
    assert payload["triggers"][0]["id"] == created_id
    assert payload["triggers"][0]["event_type"] == "manual"


def test_schedule_add_bad_event_type_is_tool_error(make_user, test_dsn):
    user = make_user()
    ctx = HandlerContext(user_id=user["user_id"], dsn=test_dsn)
    client = FakeClient(_server(ctx))
    client.initialize()
    is_error, payload = client.call(
        "schedule_add",
        {"event_type": "nope", "next_trigger_at": "2026-11-23T03:00:00+00:00"},
    )
    assert is_error is True
    assert "event_type" in payload


# ---------------------------------------------------------------------------
# Full stdio loop over byte streams (the transport a real client sees)
# ---------------------------------------------------------------------------


def test_stdio_serve_roundtrip(make_user, test_dsn):
    from puspa.memory.profile import add_fact

    user = make_user()
    add_fact(
        user["user_id"],
        "food",
        "user likes spicy food",
        "user_stated",
        agent_id=user["agent_id"],
        dsn=test_dsn,
    )
    ctx = HandlerContext(
        user_id=user["user_id"], agent_id=user["agent_id"], dsn=test_dsn, hands=_fake_hands()
    )
    server = _server(ctx)
    lines = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "fake", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        "this is not json",
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "memory_search", "arguments": {"query": "spicy food"}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "computer_snapshot", "arguments": {}},
        },
    ]
    raw_in = "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n"
    out = io.BytesIO()
    server.serve(io.BytesIO(raw_in.encode()), out)
    replies = [json.loads(line) for line in out.getvalue().decode().splitlines()]
    # Notification gets no reply; the bad-JSON line gets a -32700 reply.
    assert [reply.get("id") for reply in replies] == [1, None, 2, 3, 4]
    assert replies[1]["error"]["code"] == -32700
    assert len(replies[2]["result"]["tools"]) == 29
    memory_text = replies[3]["result"]["content"][0]["text"]
    assert "spicy food" in memory_text
    snapshot = json.loads(replies[4]["result"]["content"][0]["text"])
    assert snapshot["total_nodes"] > 0


# ---------------------------------------------------------------------------
# The real CLI, as a subprocess (an outside client spawning `puspa mcp`)
# ---------------------------------------------------------------------------


def test_cli_subprocess_lists_tools(test_dsn):
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "subprocess-client", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    env = dict(os.environ)
    env["PUSPA_TEST_DSN"] = test_dsn
    env["NO_PROXY"] = "localhost,127.0.0.1"
    env["no_proxy"] = "localhost,127.0.0.1"
    proc = subprocess.run(
        [sys.executable, "-m", "puspa", "mcp", "--backend", "fake"],
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert len(replies) == 2
    assert replies[0]["result"]["serverInfo"]["name"] == "puspa"
    tools = replies[1]["result"]["tools"]
    assert len(tools) == 29
    assert "memory_search" in [tool["name"] for tool in tools]


def test_cli_bad_policy_refuses_to_start(tmp_path):
    from puspa.cli import build_parser
    from puspa.mcp_server.cli import run_mcp

    bad_policy = tmp_path / "policy.json"
    bad_policy.write_text(json.dumps({"deny": ["not a rule ((("], "allow": ["true"]}))
    args = build_parser().parse_args(["mcp", "--policy", str(bad_policy), "--backend", "fake"])
    rc = run_mcp(args, in_stream=io.BytesIO(b""), out_stream=io.BytesIO())
    assert rc == 1


def test_cli_registers_mcp_command():
    from puspa.cli import _KNOWN_COMMANDS, build_parser

    assert "mcp" in _KNOWN_COMMANDS
    parser = build_parser()
    args = parser.parse_args(["mcp", "--backend", "fake"])
    assert args.command == "mcp"
    assert args.backend == "fake"
