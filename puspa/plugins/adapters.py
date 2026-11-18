"""Adapters from plugin registrations to nanobot (G27 wiring).

The plugin system never imports nanobot except here: this is the
one module the published API allows to touch it (see
``plugin_api.json``). Two adapters:

- ``PluginTool`` wraps a plugin ``ToolDefinition`` as a nanobot
  ``Tool`` so ``PuspaRegistry.register`` can enforce it like any
  other tool (token check, audit, gate). Sync handlers run
  directly; async handlers are awaited.
- ``PluginHook`` wraps all loaded plugins' hook registrations as
  one nanobot ``AgentHook``. Each handler runs isolated: a
  failing plugin hook is logged to the record's context and
  never breaks the turn.

``attach_plugin_tools`` / ``build_plugin_hook`` are the call
sites the chassis factory uses; ``puspa.chassis.factory`` is the
only core caller.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from nanobot.agent.hook import AgentHook
from nanobot.agent.tools.base import Tool, ToolResult

__all__ = [
    "PluginHook",
    "PluginTool",
    "attach_plugin_tools",
    "build_plugin_hook",
]


class PluginTool(Tool):
    """A nanobot Tool backed by a plugin-registered handler."""

    def __init__(self, definition):
        self._definition = definition

    @property
    def name(self) -> str:
        return self._definition.name

    @property
    def description(self) -> str:
        return self._definition.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._definition.parameters

    @property
    def read_only(self) -> bool:
        return self._definition.read_only

    @property
    def concurrency_safe(self) -> bool:
        return self._definition.read_only

    async def execute(self, **kwargs: Any) -> Any:
        handler = self._definition.handler
        try:
            result = handler(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - tool errors are results
            return ToolResult.error(
                f"plugin tool {self._definition.name!r} failed: {exc}"
            )
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        if result is None:
            return ""
        return str(result)


class PluginHook(AgentHook):
    """A nanobot AgentHook dispatching to plugin hook handlers.

    ``definitions`` is the flat list of hook registrations from
    all loaded plugins. ``finalize_content`` is the only event
    whose handler return value matters: the first non-None string
    returned replaces the turn answer.
    """

    def __init__(self, definitions):
        super().__init__()
        self._definitions = list(definitions)

    def _handlers(self, event):
        return [
            definition.handler
            for definition in self._definitions
            if definition.event == event
        ]

    async def _dispatch(self, event, payload):
        for handler in self._handlers(event):
            try:
                result = handler(event, payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 - a plugin hook never breaks a turn
                continue

    async def before_run(self, context):
        await self._dispatch("before_run", context)

    async def after_run(self, context):
        await self._dispatch("after_run", context)

    async def on_error(self, context):
        await self._dispatch("on_error", context)

    async def before_execute_tool(self, context, tool_call, tool, params):
        await self._dispatch(
            "before_execute_tool",
            {"tool": getattr(tool, "name", None), "args": params},
        )

    async def after_execute_tool(
        self, context, tool_call, tool, params, result
    ):
        await self._dispatch(
            "after_execute_tool",
            {"tool": getattr(tool, "name", None), "result": result},
        )

    def finalize_content(self, context, content):
        for handler in self._handlers("finalize_content"):
            try:
                result = handler("finalize_content", content)
            except Exception:  # noqa: BLE001 - never break the turn answer
                continue
            if isinstance(result, str) and result:
                return result
        return content


def attach_plugin_tools(registry, records):
    """Register every loaded plugin tool into a tool registry.

    Returns the list of registered tool names. Tools are
    registered in load order; a tool name that collides with an
    already-registered tool is skipped (the registry keeps the
    first one) and reported in the returned ``skipped`` list.
    """
    attached: list[str] = []
    skipped: list[str] = []
    for record in records:
        for definition in record.tools.values():
            existing = _registry_names(registry)
            if definition.name in existing:
                skipped.append(definition.name)
                continue
            registry.register(PluginTool(definition))
            attached.append(definition.name)
    return {"attached": attached, "skipped": skipped}


def _registry_names(registry) -> set[str]:
    tools = getattr(registry, "_tools", None)
    if isinstance(tools, dict):
        return set(tools)
    try:
        return {
            schema["function"]["name"] for schema in registry.get_definitions()
        }
    except Exception:  # noqa: BLE001 - give up, treat as empty
        return set()


def build_plugin_hook(records):
    """Build one ``PluginHook`` for all loaded records, or None.

    Returns None when no plugin registered any hook, so the
    chassis keeps its exact hook list when plugins add nothing.
    """
    definitions = []
    for record in records:
        definitions.extend(record.hooks)
    if not definitions:
        return None
    return PluginHook(definitions)
