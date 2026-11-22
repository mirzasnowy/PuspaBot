"""Tests for the dependency-free tool registry (G23).

In-memory only: run with ``pytest tests/test_tool_registry.py --noconftest``.
"""

import asyncio
import json
import sys
from pathlib import Path

# --noconftest skips the repo conftest, which is what puts the local
# nanobot clone on sys.path; the discover tests below import the
# chassis adapter, so replicate that one path setup here.
_NANOBOT_LOCAL = Path(__file__).resolve().parent.parent / "nanobot"
if _NANOBOT_LOCAL.is_dir() and str(_NANOBOT_LOCAL) not in sys.path:
    sys.path.insert(0, str(_NANOBOT_LOCAL))

import pytest

from puspa.tools.registry import (
    MultimodalContent,
    ToolRegistry,
    discover,
    handle_function_call,
    register,
)


@pytest.fixture()
def registry():
    return ToolRegistry()


def _echo_handler(text):
    return json.dumps({"echo": text})


def test_register_and_schema(registry):
    registry.register(
        "echo",
        "Echo text.",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        _echo_handler,
        read_only=True,
    )
    assert "echo" in registry
    assert registry.names() == ["echo"]
    schema = registry.schemas()[0]
    assert schema["function"]["name"] == "echo"
    assert schema["function"]["parameters"]["required"] == ["text"]


def test_duplicate_registration_raises(registry):
    registry.register("echo", "Echo.", {}, _echo_handler)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("echo", "Echo again.", {}, _echo_handler)


def test_dispatch_returns_json_string(registry):
    registry.register(
        "echo",
        "Echo.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        _echo_handler,
    )
    raw = registry.handle_function_call("echo", {"text": "hi"})
    assert isinstance(raw, str)
    # A handler returns a JSON string by contract: it passes through
    # unchanged, the handler owns its envelope.
    assert json.loads(raw) == {"echo": "hi"}


def test_dispatch_accepts_json_string_arguments(registry):
    registry.register(
        "echo",
        "Echo.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        _echo_handler,
    )
    payload = json.loads(registry.handle_function_call("echo", '{"text": "yo"}'))
    assert payload == {"echo": "yo"}


def test_dispatch_plain_string_result_is_wrapped(registry):
    registry.register("plain", "Plain.", {}, lambda: "hello")
    payload = json.loads(registry.handle_function_call("plain", {}))
    assert payload == {"status": "ok", "tool": "plain", "result": "hello"}


def test_dispatch_unknown_tool_is_error_json(registry):
    payload = json.loads(registry.handle_function_call("nope", {}))
    assert payload["status"] == "error"
    assert "unknown tool" in payload["error"]


def test_dispatch_missing_required_argument(registry):
    registry.register(
        "echo",
        "Echo.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        _echo_handler,
    )
    payload = json.loads(registry.handle_function_call("echo", {}))
    assert payload["status"] == "error"
    assert "text" in payload["error"]


def test_dispatch_invalid_json_arguments(registry):
    registry.register("echo", "Echo.", {}, _echo_handler)
    payload = json.loads(registry.handle_function_call("echo", "{not json"))
    assert payload["status"] == "error"


def test_dispatch_handler_exception_is_wrapped(registry):
    def boom():
        raise RuntimeError("kaboom")

    registry.register("boom", "Boom.", {}, boom)
    payload = json.loads(registry.handle_function_call("boom", {}))
    assert payload["status"] == "error"
    assert "kaboom" in payload["error"]


def test_dispatch_dict_result_is_merged(registry):
    registry.register("info", "Info.", {}, lambda: {"answer": 42})
    payload = json.loads(registry.handle_function_call("info", {}))
    assert payload["status"] == "ok"
    assert payload["answer"] == 42


def test_dispatch_respects_check_fn(registry):
    registry.register(
        "gated", "Gated.", {}, lambda: "secret", check=lambda profile_id: profile_id == "owner"
    )
    assert registry.is_available("gated", "owner") is True
    assert registry.is_available("gated", "guest") is False
    payload = json.loads(registry.handle_function_call("gated", {}, profile_id="guest"))
    assert payload["status"] == "unavailable"
    payload = json.loads(registry.handle_function_call("gated", {}, profile_id="owner"))
    assert payload["status"] == "ok"


def test_check_fn_exception_is_unavailable_not_a_crash(registry):
    def broken(profile_id):
        raise RuntimeError("check exploded")

    registry.register("broken", "Broken.", {}, lambda: "x", check=broken)
    assert registry.is_available("broken", "anyone") is False
    payload = json.loads(registry.handle_function_call("broken", {}, profile_id="anyone"))
    assert payload["status"] == "unavailable"


def test_multimodal_envelope(registry):
    def pixels():
        return MultimodalContent(mime_type="image/png", data="aGVsbG8=", text="a chart")

    registry.register("chart", "Chart.", {}, pixels, multimodal=True)
    result = registry.handle_function_call("chart", {})
    assert isinstance(result, dict)
    assert result["kind"] == "multimodal"
    assert result["content"][0]["mime_type"] == "image/png"
    assert result["content"][0]["data"] == "aGVsbG8="
    assert result["text"] == "a chart"


def test_multimodal_dict_return_is_coerced(registry):
    registry.register(
        "shot",
        "Shot.",
        {},
        lambda: {"mime_type": "image/jpeg", "data": "xyz", "text": "cap"},
        multimodal=True,
    )
    result = registry.handle_function_call("shot", {})
    assert result["kind"] == "multimodal"
    assert result["content"][0]["mime_type"] == "image/jpeg"


def test_non_multimodal_tool_dict_with_pixels_is_plain_json(registry):
    # A multimodal-looking dict from a normal tool is JSON, not an envelope.
    registry.register("data", "Data.", {}, lambda: {"mime_type": "image/png", "data": "xyz"})
    result = registry.handle_function_call("data", {})
    assert isinstance(result, str)
    assert json.loads(result)["mime_type"] == "image/png"


def test_async_handler_via_async_dispatch(registry):
    async def slow(text):
        await asyncio.sleep(0)
        return json.dumps({"slow": text})

    registry.register(
        "slow",
        "Slow.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        slow,
    )
    payload = json.loads(asyncio.run(registry.handle_function_call_async("slow", {"text": "x"})))
    assert payload["slow"] == "x"


def test_async_handler_via_sync_dispatch_outside_loop(registry):
    async def slow():
        return json.dumps({"slow": True})

    registry.register("slow", "Slow.", {}, slow)
    payload = json.loads(registry.handle_function_call("slow", {}))
    assert payload["slow"] is True


def test_async_handler_inside_running_loop_errors_cleanly(registry):
    async def slow():
        return "{}"

    registry.register("slow", "Slow.", {}, slow)

    async def _inside_loop():
        return registry.handle_function_call("slow", {})

    payload = json.loads(asyncio.run(_inside_loop()))
    assert payload["status"] == "error"
    assert "handle_function_call_async" in payload["error"]


def test_module_level_register_decorator():
    from puspa.tools import registry as reg_module

    before = set(reg_module.default_registry().names())
    try:

        @register(
            "decorated_echo",
            "Echo.",
            {"type": "object", "properties": {"text": {"type": "string"}}},
        )
        def _decorated(text):
            return json.dumps({"echo": text})

        assert "decorated_echo" in reg_module.default_registry()
        payload = json.loads(handle_function_call("decorated_echo", {"text": "d"}))
        assert payload["echo"] == "d"
    finally:
        reg_module.default_registry().unregister("decorated_echo")
    assert set(reg_module.default_registry().names()) == before


def test_discover_imports_tool_modules():
    fresh = ToolRegistry()
    discover("puspa.tools", registry=fresh)
    # builtin.py adapts the existing Puspa tools at import time,
    # registers the COW-07 browser + web tools, and registers the
    # G17 search_tools overflow tool.
    assert "run_code" in fresh
    assert "plugin_echo" in fresh
    assert "browser_open" in fresh
    assert "browser_click" in fresh
    assert "browser_fill" in fresh
    assert "web_search" in fresh
    assert "web_fetch" in fresh
    assert "search_tools" in fresh
    assert len(fresh.schemas()) == 8


def test_discover_is_idempotent_for_fresh_registry():
    first = ToolRegistry()
    discover("puspa.tools", registry=first)
    names = first.names()
    second = ToolRegistry()
    discover("puspa.tools", registry=second)
    assert second.names() == names


def test_discovered_echo_tool_dispatches():
    fresh = ToolRegistry()
    discover("puspa.tools", registry=fresh)
    raw = fresh.handle_function_call("plugin_echo", {"text": "ping"})
    assert isinstance(raw, str)
    assert json.loads(raw) == {"status": "ok", "tool": "plugin_echo", "result": "echo: ping"}


def test_unregister():
    registry = ToolRegistry()
    registry.register("temp", "Temp.", {}, lambda: "x")
    assert registry.unregister("temp") is True
    assert registry.unregister("temp") is False
    assert "temp" not in registry


def test_empty_registry_is_not_swapped_for_default():
    # Regression: ToolRegistry defines __len__, so an empty registry
    # is falsy -- helpers must test ``is not None``, never ``or``.
    from puspa.tools import registry as reg_module

    empty = ToolRegistry()
    assert len(empty) == 0
    assert reg_module.schemas(registry=empty) == []
    assert reg_module.is_available("plugin_echo", registry=empty) is False
    assert empty.names() == []
    discover("puspa.tools", registry=empty)
    assert "plugin_echo" in empty
    assert "run_code" in empty
