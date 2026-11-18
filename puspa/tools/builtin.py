"""Adapters: register Puspa's existing tools on the new registry (G23).

This module is the bridge between the tools Puspa already has and
the dependency-free registry in :mod:`puspa.tools.registry`. It does
not define new tools; it adapts two existing ones, with no edits to
their home packages:

- ``run_code`` -- :class:`puspa.chassis.sandbox_tool.SandboxTool`
  (code execution in the Docker sandbox). Its ``execute`` is async,
  so the adapter handler is async too and dispatch goes through
  ``handle_function_call_async`` (or the sync path, which drives it
  with ``asyncio.run`` outside a running loop).
- ``plugin_echo`` -- the builtin echo plugin
  (``puspa.plugins.builtin.echo``). The plugin is loaded by module
  path through :func:`puspa.plugins.loader.load_plugin` (the boundary
  lint forbids core code from importing a builtin plugin module
  directly); the adapter reads the resulting tool definitions off
  the returned record's public ``tools`` accessor.

Importing this module registers both tools on the default registry.
``puspa.tools.discover`` imports it automatically when it scans the
``puspa.tools`` package.
"""

from __future__ import annotations

from typing import Any

from puspa.chassis.sandbox_tool import SandboxTool
from puspa.plugins.loader import load_plugin
from puspa.tools.narrowing import register_search_tools
from puspa.tools.registry import ToolRegistry, default_registry, register

__all__ = ["register_builtin_tools", "register_search_tools"]


def _register_run_code(registry: ToolRegistry) -> None:
    """Adapt ``SandboxTool`` (chassis) into a registry tool."""
    tool = SandboxTool()

    async def run_code_handler(code: str, timeout_s: float = 30) -> str:
        result = await tool.execute(code=code, timeout_s=timeout_s)
        if isinstance(result, str):
            return result
        # ToolResult: surface its text, errors included.
        return getattr(result, "text", None) or str(result)

    if "run_code" not in registry:
        register(
            "run_code",
            tool.description,
            tool.parameters,
            run_code_handler,
            read_only=False,
            registry=registry,
        )


def _register_plugin_tools(registry: ToolRegistry) -> None:
    """Adapt builtin plugin tools (loaded by module path) into registry tools."""
    # Load by module path through the loader: core code must never
    # import a builtin plugin module directly (boundary rule B1).
    record = load_plugin("puspa.plugins.builtin.echo")
    for definition in record.tools.values():
        if definition.name in registry:
            continue
        handler = definition.handler

        def _make_handler(fn):
            def _handler(**kwargs: Any) -> str:
                result = fn(**kwargs)
                if isinstance(result, dict):
                    import json

                    return json.dumps(result)
                return str(result)

            return _handler

        register(
            definition.name,
            definition.description,
            definition.parameters,
            _make_handler(handler),
            read_only=bool(definition.read_only),
            registry=registry,
        )


def register_builtin_tools(registry: ToolRegistry | None = None) -> ToolRegistry:
    """Register all adapted Puspa tools. Safe to call twice."""
    target = registry if registry is not None else default_registry()
    _register_run_code(target)
    _register_plugin_tools(target)
    # G17: the overflow search tool, so a run whose catalog
    # overflowed can search the hidden tools through the registry.
    register_search_tools(target)
    return target


# Import-time registration: importing this module (e.g. through
# ``puspa.tools.discover``) registers the adapted tools.
register_builtin_tools()
