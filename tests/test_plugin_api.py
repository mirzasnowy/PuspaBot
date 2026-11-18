"""Published plugin API tests (G27).

The ``ctx`` API a plugin sees must match ``plugin_api.json``
exactly: every method and attribute the JSON promises must
exist on ``PluginContext``, every hook event must be known, and
the single-select kinds must match the registry. Registration
validation (names, duplicates, unknown events, bad schemas)
fails at load time with a clear error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from puspa.plugins.context import (
    HOOK_EVENTS,
    PluginContext,
    is_valid_command_name,
    is_valid_tool_name,
)
from puspa.plugins.errors import PluginStructureError
from puspa.plugins.single_select import SINGLE_SELECT_KINDS

REPO_ROOT = Path(__file__).resolve().parent.parent
API_SPEC = json.loads(
    (REPO_ROOT / "puspa" / "plugins" / "plugin_api.json").read_text(
        encoding="utf-8"
    )
)


def _ctx():
    return PluginContext("test-plugin")


def _params():
    return {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }


# ---------------------------------------------------------------- spec sync


def test_api_spec_version_is_current():
    assert API_SPEC["api_version"] == 1


def test_context_methods_match_spec():
    for method in API_SPEC["context_methods"]:
        assert hasattr(PluginContext, method), f"missing ctx method: {method}"
        assert callable(getattr(PluginContext, method))
    public_methods = {
        name
        for name in dir(PluginContext)
        if not name.startswith("_") and callable(getattr(PluginContext, name))
    }
    # Every public method must be in the spec (no undocumented API).
    assert public_methods == set(API_SPEC["context_methods"])


def test_context_attributes_match_spec():
    ctx = _ctx()
    for attribute in API_SPEC["context_attributes"]:
        assert hasattr(ctx, attribute), f"missing ctx attribute: {attribute}"


def test_hook_events_match_spec():
    assert list(HOOK_EVENTS) == API_SPEC["hook_events"]


def test_single_select_kinds_match_spec():
    assert list(SINGLE_SELECT_KINDS) == API_SPEC["single_select_kinds"]
    assert API_SPEC["single_select_env_vars"] == {
        "context_engine": "PUSPA_CONTEXT_ENGINE_PLUGIN",
        "memory_provider": "PUSPA_MEMORY_PROVIDER_PLUGIN",
    }


def test_name_patterns_match_spec():
    import re

    tool_re = re.compile(API_SPEC["tool_name_pattern"])
    command_re = re.compile(API_SPEC["command_name_pattern"])
    assert tool_re.match("plugin_echo") and not tool_re.match("Bad Name")
    assert command_re.match("my-command") and not command_re.match("Bad Name")
    assert is_valid_tool_name("plugin_echo")
    assert not is_valid_tool_name("PluginEcho")
    assert is_valid_command_name("my-command")
    assert not is_valid_command_name("my command")


# ---------------------------------------------------------------- registration


def test_register_tool_hook_command_happy_path():
    ctx = _ctx()
    ctx.register_tool(
        "my_tool", "Does a thing.", _params(), lambda text: text,
        read_only=True,
    )
    ctx.register_hook("after_run", lambda event, payload: None)
    ctx.register_command("my-cmd", "Runs a thing.", lambda argv: 0)
    assert "my_tool" in ctx.tools
    assert ctx.tools["my_tool"].read_only is True
    assert [hook.event for hook in ctx.hooks] == ["after_run"]
    assert "my-cmd" in ctx.commands


def test_register_tool_rejects_bad_names():
    ctx = _ctx()
    for bad in ("BadName", "has space", "1abc", "", None):
        with pytest.raises(PluginStructureError):
            ctx.register_tool(bad, "desc", _params(), lambda text: text)


def test_register_tool_rejects_duplicates_and_bad_handlers():
    ctx = _ctx()
    ctx.register_tool("tool_a", "desc", _params(), lambda text: text)
    with pytest.raises(PluginStructureError, match="registered twice"):
        ctx.register_tool("tool_a", "desc", _params(), lambda text: text)
    with pytest.raises(PluginStructureError, match="not callable"):
        ctx.register_tool("tool_b", "desc", _params(), "not-a-function")
    with pytest.raises(PluginStructureError, match="non-empty description"):
        ctx.register_tool("tool_c", "  ", _params(), lambda text: text)


def test_register_tool_rejects_bad_parameter_schema():
    ctx = _ctx()
    with pytest.raises(PluginStructureError, match="parameters must be a dict"):
        ctx.register_tool("tool_a", "desc", ["not", "a", "dict"], lambda: None)
    with pytest.raises(PluginStructureError, match="must be 'object'"):
        ctx.register_tool(
            "tool_b", "desc", {"type": "string"}, lambda: None
        )
    with pytest.raises(PluginStructureError, match="not in properties"):
        ctx.register_tool(
            "tool_c",
            "desc",
            {"type": "object", "properties": {}, "required": ["missing"]},
            lambda: None,
        )


def test_register_hook_rejects_unknown_events_and_bad_handlers():
    ctx = _ctx()
    with pytest.raises(PluginStructureError, match="unknown hook event"):
        ctx.register_hook("on_monday", lambda event, payload: None)
    with pytest.raises(PluginStructureError, match="not callable"):
        ctx.register_hook("after_run", "not-a-function")


def test_register_command_rejects_bad_names_and_duplicates():
    ctx = _ctx()
    with pytest.raises(PluginStructureError, match="invalid command name"):
        ctx.register_command("Bad Name", "desc", lambda argv: 0)
    ctx.register_command("my-cmd", "desc", lambda argv: 0)
    with pytest.raises(PluginStructureError, match="registered twice"):
        ctx.register_command("my-cmd", "desc", lambda argv: 0)


def test_validate_handler_signatures_catches_wrong_arity():
    ctx = _ctx()
    ctx.register_hook("after_run", lambda event: None)
    problems = ctx.validate_handler_signatures()
    assert len(problems) == 1
    assert "after_run" in problems[0]

    ctx2 = _ctx()
    ctx2.register_hook("after_run", lambda event, payload: None)
    ctx2.register_command("cmd", "Runs a thing.", lambda argv: 0)
    assert ctx2.validate_handler_signatures() == []


def test_ctx_log_lines_are_kept_in_shared_state():
    ctx = _ctx()
    ctx.log("hello from plugin")
    assert ctx.shared["log_lines"] == ["hello from plugin"]
