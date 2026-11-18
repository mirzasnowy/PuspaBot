"""Tests for skill hooks, the learned-skill store and the MCP wrapper.

All tests are in-memory: the MCP server is the stub transport (no
network, no process), skills are signed in a temp directory, and the
learned-skill store is built from plain records or procedure rows.
No Postgres table is touched by the test bodies themselves (the
shared conftest autouse fixture still resets the test database, as
it does for every test file in this repo).
"""

import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from puspa.chassis.hooks import (
    HOOK_SKILL_BLOCKED,
    HOOK_SKILL_INSTALLED,
    HOOK_SKILL_USED,
    LearnedSkill,
    LearnedSkillStore,
    SkillHookRegistry,
    check_skill_use,
)
from puspa.chassis.tools import (
    McpClientWrapper,
    McpToolSpec,
    StubMcpTransport,
    mcp_sdk_info,
)
from puspa.policy.gate import Gate
from puspa.policy.skill_signing import load_skill, sign_skill

# ---------------------------------------------------------------------------
# Skill hook registry
# ---------------------------------------------------------------------------


def test_skill_hook_registry_emits_to_all_listeners():
    registry = SkillHookRegistry()
    seen = []
    registry.register(
        HOOK_SKILL_INSTALLED, lambda point, payload: seen.append((point, payload["skill"]))
    )
    registry.register(HOOK_SKILL_INSTALLED, lambda point, payload: "second")
    results = registry.emit(HOOK_SKILL_INSTALLED, {"skill": "price-compare"})
    assert seen == [(HOOK_SKILL_INSTALLED, "price-compare")]
    assert [r["ok"] for r in results] == [True, True]
    assert results[1]["result"] == "second"


def test_skill_hook_registry_isolates_a_broken_listener():
    registry = SkillHookRegistry()
    registry.register(HOOK_SKILL_USED, lambda point, payload: 1 / 0)
    seen = []
    registry.register(HOOK_SKILL_USED, lambda point, payload: seen.append(payload["skill"]))
    results = registry.emit(HOOK_SKILL_USED, {"skill": "price-compare"})
    assert results[0]["ok"] is False
    assert seen == ["price-compare"]


def test_skill_hook_registry_rejects_unknown_points_and_unregisters():
    registry = SkillHookRegistry()
    with pytest.raises(ValueError):
        registry.register("not_a_hook", lambda p, q: None)
    with pytest.raises(ValueError):
        registry.emit("not_a_hook", {})
    callback = lambda p, q: None  # noqa: E731
    registry.register(HOOK_SKILL_USED, callback)
    assert registry.unregister(HOOK_SKILL_USED, callback) is True
    assert registry.unregister(HOOK_SKILL_USED, callback) is False


# ---------------------------------------------------------------------------
# Learned skills stay inactive until approved
# ---------------------------------------------------------------------------


def test_learned_skill_is_inactive_by_default():
    skill = LearnedSkill(name="pln-bill-flow")
    assert skill.status == "pending_approval"
    assert skill.is_active is False


def test_learned_skill_store_blocks_unapproved_use_and_emits_blocked():
    store = LearnedSkillStore(
        [
            {"name": "pln-bill-flow", "status": "pending_approval", "tools": ["read_bill"]},
            {
                "name": "price-compare",
                "status": "approved",
                "tools": ["read_price"],
                "approved_by": "user-1",
            },
        ]
    )
    registry = SkillHookRegistry()
    events = []
    registry.register(HOOK_SKILL_BLOCKED, lambda p, q: events.append(("blocked", q["skill"])))
    registry.register(HOOK_SKILL_USED, lambda p, q: events.append(("used", q["skill"])))

    assert store.is_active("pln-bill-flow") is False
    assert store.is_active("price-compare") is True
    assert store.is_active("unknown-skill") is False
    assert [s.name for s in store.list_pending()] == ["pln-bill-flow"]
    assert [s.name for s in store.list_approved()] == ["price-compare"]

    assert check_skill_use(store, "pln-bill-flow", registry=registry) is False
    assert check_skill_use(store, "price-compare", registry=registry) is True
    assert check_skill_use(store, "unknown-skill", registry=registry) is False
    assert events == [
        ("blocked", "pln-bill-flow"),
        ("used", "price-compare"),
        ("blocked", "unknown-skill"),
    ]


def test_learned_skill_store_reads_procedure_rows_without_writing():
    rows = [
        {
            "service": "pln-bill",
            "step": [{"tool": "read_bill"}],
            "status": "trial",
            "source_event_id": None,
        },
        {
            "service": "price-compare",
            "step": [{"tool": "read_price"}],
            "status": "verified",
            "source_event_id": None,
        },
        {"service": "broken-flow", "step": [], "status": "broken", "source_event_id": None},
    ]
    store = LearnedSkillStore.from_procedures(rows)
    assert store.is_active("pln-bill") is False  # trial = pending approval
    assert store.is_active("price-compare") is True  # verified = approved
    assert store.is_active("broken-flow") is False
    assert store.get("pln-bill").tools == ("read_bill",)


# ---------------------------------------------------------------------------
# MCP wrapper: signed-skill gate + outbound gate, stub transport
# ---------------------------------------------------------------------------


def _signed_manifest(tmp_path, signing_key, public_key, tools):
    skill_dir = tmp_path / "skill-mcp"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Skill MCP\n\nTest.\n", encoding="utf-8")
    manifest = {
        "name": "mcp-test",
        "version": "0.1.0",
        "tools": tools,
        "domains": [],
        "connectors": {},
        "author": "puspa",
    }
    (skill_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    sign_skill(skill_dir, signing_key)
    return load_skill(skill_dir, public_key)


def _stub():
    transport = StubMcpTransport(server_name="stub-test")
    transport.add_tool(
        "read_price",
        {"type": "object", "properties": {"query": {"type": "string"}}},
        lambda args: {"value": 11999000, "store": "Shopee"},
    )
    transport.add_tool("send", {"type": "object", "properties": {}}, lambda args: {"sent": True})
    return transport


def test_mcp_sdk_is_installed_mit_and_available():
    info = mcp_sdk_info()
    assert info["name"] == "mcp"
    assert info["version"] == "1.30.0"
    assert info["license"] == "MIT"
    assert info["available"] is True


def test_mcp_tool_spec_validates_typed_tools():
    spec = McpToolSpec.from_sdk_tool(
        {
            "name": "read_price",
            "description": "read a price",
            "inputSchema": {"type": "object", "properties": {"query": {}}},
        },
        server_name="stub",
    )
    assert spec.name == "read_price"
    assert spec.input_schema["type"] == "object"
    with pytest.raises(ValidationError):
        McpToolSpec(description="no name")


def test_mcp_wrapper_calls_signed_tool_and_lists_only_allowed(tmp_path, ephemeral_keys):
    signing_key, public_key = ephemeral_keys
    manifest = _signed_manifest(tmp_path, signing_key, public_key, ["read_price"])
    transport = _stub()
    wrapper = McpClientWrapper(transport, skill_manifest=manifest, public_key=public_key)
    result = wrapper.call_tool_sync("read_price", {"query": "laptop"})
    assert result["status"] == "ok"
    assert result["result"]["value"] == 11999000
    assert len(transport.calls) == 1

    listed = wrapper.list_tools_sync()
    assert [spec.name for spec in listed] == ["read_price"]


def test_mcp_wrapper_rejects_unsigned_skill_without_calling(tmp_path):
    transport = _stub()
    unsigned = {
        "name": "mcp-test",
        "version": "0.1.0",
        "tools": ["read_price"],
        "domains": [],
        "connectors": {},
        "author": "puspa",
    }  # no signature field at all
    signing_key = Ed25519PrivateKey.generate()
    wrapper = McpClientWrapper(
        transport, skill_manifest=unsigned, public_key=signing_key.public_key()
    )
    result = wrapper.call_tool_sync("read_price", {"query": "laptop"})
    assert result["status"] == "rejected"
    assert "unsigned" in result["reason"]
    assert transport.calls == []


def test_mcp_wrapper_rejects_tool_outside_signed_manifest(tmp_path, ephemeral_keys):
    signing_key, public_key = ephemeral_keys
    manifest = _signed_manifest(tmp_path, signing_key, public_key, ["read_price"])
    transport = _stub()
    wrapper = McpClientWrapper(transport, skill_manifest=manifest, public_key=public_key)
    result = wrapper.call_tool_sync("send", {})
    assert result["status"] == "rejected"
    assert transport.calls == []


def test_mcp_wrapper_routes_outbound_tool_through_approval_gate(tmp_path, ephemeral_keys):
    signing_key, public_key = ephemeral_keys
    manifest = _signed_manifest(tmp_path, signing_key, public_key, ["send", "read_price"])
    transport = _stub()
    now = int(time.time())
    payload = {
        "user_id": "user-1",
        "agent_id": "agent-1",
        "tools": ["send"],
        "domains": [],
        "connectors": {},
        "budget": {"rupiah_max": 2000},
        "exp": now + 3600,
        "iat": now,
    }
    wrapper = McpClientWrapper(
        transport, gate=Gate(), skill_manifest=manifest, public_key=public_key
    )
    result = wrapper.call_tool_sync("send", {}, token_payload=payload)
    assert result["status"] == "pending_approval"
    assert result["request_id"]
    assert transport.calls == []  # the tool was NOT executed
