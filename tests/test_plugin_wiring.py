"""Chassis wiring tests for plugins (G27).

Proves the loader is really wired into the composition root:
plugin tools land in the Puspa registry (with the same
enforcement as any tool), plugin hooks join the assembled loop,
and a broken plugin is recorded on the chassis instead of
aborting startup. In-memory only: the agent loop is stubbed
(the same pattern as tests/test_factory_rails.py).
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

# Belt + braces (same guard as tests/conftest.py): the local
# nanobot clone is the import source when it exists.
_NANOBOT_LOCAL = Path(__file__).resolve().parent.parent / "nanobot"
if _NANOBOT_LOCAL.exists():
    sys.path.insert(0, str(_NANOBOT_LOCAL))

from puspa.chassis.factory import (
    PuspaChassis,
    assemble,
    build_registry,
    load_chassis_plugins,
)
from puspa.plugins.adapters import (
    PluginHook,
    PluginTool,
    attach_plugin_tools,
    build_plugin_hook,
)
from puspa.plugins.loader import load_plugin

ECHO_MODULE = "puspa.plugins.builtin.echo"


def test_plugin_tool_executes_through_adapter():
    record = load_plugin(ECHO_MODULE)
    tool = PluginTool(record.tools["plugin_echo"])
    assert tool.name == "plugin_echo"
    assert tool.read_only is True
    result = asyncio.run(tool.execute(text="hello"))
    assert result == "echo: hello"


def test_plugin_tool_error_becomes_tool_result():
    record = load_plugin(ECHO_MODULE)
    definition = record.tools["plugin_echo"]

    def failing_handler(**kwargs):
        raise ValueError("boom")

    broken = type(definition)(
        name=definition.name,
        description=definition.description,
        parameters=definition.parameters,
        handler=failing_handler,
        read_only=True,
        plugin_id=definition.plugin_id,
    )
    tool = PluginTool(broken)
    result = asyncio.run(tool.execute(text="hi"))
    assert result.is_error is True
    assert "boom" in str(result)


def test_plugin_hook_dispatches_and_isolates_failures():
    record = load_plugin(ECHO_MODULE)
    hook = build_plugin_hook([record])
    assert isinstance(hook, PluginHook)
    asyncio.run(hook.after_run(object()))
    assert len(record.context.shared["events"]) == 1
    assert record.context.shared["events"][0]["event"] == "after_run"
    assert build_plugin_hook([]) is None


def test_attach_plugin_tools_into_registry():
    record = load_plugin(ECHO_MODULE)
    chassis = PuspaChassis(plugins=[record])
    registry = build_registry(chassis)
    assert "run_code" in registry._tools
    assert "plugin_echo" in registry._tools
    # PuspaRegistry wraps every tool in PuspaTool exactly once.
    from puspa.chassis.tools import PuspaTool

    assert isinstance(registry._tools["plugin_echo"], PuspaTool)


def test_attach_plugin_tools_skips_name_collisions():
    record = load_plugin(ECHO_MODULE)
    chassis = PuspaChassis(plugins=[record])
    registry = build_registry(chassis)
    outcome = attach_plugin_tools(registry, [record])
    assert outcome["attached"] == []
    assert outcome["skipped"] == ["plugin_echo"]


def test_load_chassis_plugins_from_dir(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "greeter.py").write_text(
        textwrap.dedent(
            """\
            def register(ctx):
                ctx.register_tool(
                    "greet",
                    "Say hello.",
                    {"type": "object", "properties": {}, "required": []},
                    lambda: "hello",
                )
            """
        ),
        encoding="utf-8",
    )
    chassis = PuspaChassis(plugin_dirs=(str(plugin_dir),))
    result = load_chassis_plugins(chassis)
    assert result.failures == []
    assert chassis.plugins[0].plugin_id == "_puspa_plugin_greeter"
    assert "greet" in chassis.plugins[0].tools
    registry = build_registry(chassis)
    assert "greet" in registry._tools


def test_load_chassis_plugins_records_failures_without_raising(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "broken.py").write_text(
        "def register(ctx):\n    raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    chassis = PuspaChassis(plugin_dirs=(str(plugin_dir),))
    result = load_chassis_plugins(chassis)
    assert chassis.plugins == []
    assert len(chassis.plugin_failures) == 1
    assert "boom" in str(result.failures[0][1])


def test_assemble_adds_plugin_hook_only_when_plugins_exist(monkeypatch):
    import nanobot.agent.loop as loop_module

    captured = {}

    def from_config(cls, config, bus=None, **extra):
        captured.update(extra)
        return object()

    monkeypatch.setattr(loop_module.AgentLoop, "from_config", classmethod(from_config))
    # No plugins configured: exactly one hook, as before.
    assemble(PuspaChassis(), config="fake-config")
    assert len(captured["hooks"]) == 1

    # Echo plugin pre-loaded: PuspaHook + PluginHook, and its tool
    # is in the registry.
    record = load_plugin(ECHO_MODULE)
    chassis = PuspaChassis(plugins=[record])
    assemble(chassis, config="fake-config")
    assert len(captured["hooks"]) == 2
    assert isinstance(captured["hooks"][1], PluginHook)
    assert "plugin_echo" in captured["tool_registry"]._tools
