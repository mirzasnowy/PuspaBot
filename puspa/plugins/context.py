"""The published plugin context (G27, hermes-agent ``ctx`` pattern).

A plugin is a Python module with one entry point::

    def register(ctx):
        ctx.register_tool(...)
        ctx.register_hook(...)
        ctx.register_command(...)

``PluginContext`` is the ONLY surface a plugin may touch. It is the
runtime half of the published API documented in
``plugin_api.json``; ``tests/test_plugin_api.py`` checks the two
stay in sync. A plugin must never import Puspa core modules
directly: the boundary lint (``puspa.plugins.boundary``) rejects
any import outside the published API list.

What a plugin can register:

- a **tool**: a named callable the agent can call. The handler
  receives keyword arguments matching its JSON Schema parameters
  and returns a string (or a dict, which is JSON-encoded).
- a **hook**: a callable run on one agent lifecycle event
  (``before_run``, ``after_run``, ``on_error``,
  ``before_execute_tool``, ``after_execute_tool``,
  ``finalize_content``). Hooks are observation-only except
  ``finalize_content``, whose handler may return a replacement
  string for the turn answer.
- a **CLI command**: a named command run from the Puspa CLI. The
  handler receives the argument list and returns an int exit code.
- a **single-select** provider: a memory provider or a context
  engine. Only one is active at a time per kind (see
  ``puspa.plugins.single_select``).

``ctx.shared`` is a per-plugin dict for state shared between a
plugin's own registrations (for example a tool that writes notes
and a context engine that reads them). It is never shared with
other plugins.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .context_engine import ContextEngine
from .errors import PluginConfigError, PluginStructureError

__all__ = [
    "HOOK_EVENTS",
    "CommandDefinition",
    "HookDefinition",
    "PluginContext",
    "ToolDefinition",
    "is_valid_command_name",
    "is_valid_tool_name",
]

#: Lifecycle events a plugin hook may subscribe to. These are the
#: nanobot ``AgentHook`` events the adapter maps them to.
HOOK_EVENTS = (
    "before_run",
    "after_run",
    "on_error",
    "before_execute_tool",
    "after_execute_tool",
    "finalize_content",
)

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_COMMAND_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def is_valid_tool_name(name) -> bool:
    """A tool name is lowercase snake_case starting with a letter."""
    return isinstance(name, str) and bool(_TOOL_NAME_RE.match(name))


def is_valid_command_name(name) -> bool:
    """A command name is lowercase kebab/snake-case starting with a letter."""
    return isinstance(name, str) and bool(_COMMAND_NAME_RE.match(name))


def _validate_parameters(parameters, *, what: str) -> dict:
    """A tool parameter schema must be a JSON Schema object schema."""
    if not isinstance(parameters, dict):
        raise PluginStructureError(f"{what}: parameters must be a dict")
    if parameters.get("type", "object") != "object":
        raise PluginStructureError(
            f"{what}: parameters type must be 'object', got {parameters.get('type')!r}"
        )
    properties = parameters.get("properties", {})
    if not isinstance(properties, dict):
        raise PluginStructureError(f"{what}: properties must be a dict")
    required = parameters.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise PluginStructureError(f"{what}: required must be a list of strings")
    unknown_required = sorted(set(required) - set(properties))
    if unknown_required:
        raise PluginStructureError(
            f"{what}: required names not in properties: {', '.join(unknown_required)}"
        )
    return parameters


@dataclass(frozen=True)
class ToolDefinition:
    """One tool registered by a plugin."""

    name: str
    description: str
    parameters: dict
    handler: Callable[..., Any]
    read_only: bool = False
    plugin_id: str = ""


@dataclass(frozen=True)
class HookDefinition:
    """One lifecycle hook registered by a plugin."""

    event: str
    handler: Callable[..., Any]
    plugin_id: str = ""


@dataclass(frozen=True)
class CommandDefinition:
    """One CLI command registered by a plugin."""

    name: str
    description: str
    handler: Callable[..., Any]
    plugin_id: str = ""


class PluginContext:
    """The registration surface handed to a plugin's ``register(ctx)``.

    One context per plugin load. Registrations are validated
    eagerly so a broken plugin fails at load time with a clear
    message, never halfway through a turn.
    """

    def __init__(self, plugin_id: str, *, single_select=None):
        self.plugin_id = plugin_id
        self.api_version = 1
        #: Per-plugin shared state (see the module docstring).
        self.shared: dict = {}
        self._tools: dict[str, ToolDefinition] = {}
        self._hooks: list[HookDefinition] = []
        self._commands: dict[str, CommandDefinition] = {}
        self._single_select = single_select
        self._single_select_names: list[tuple[str, str]] = []

    # -- tools ---------------------------------------------------------

    def register_tool(self, name, description, parameters, handler, *, read_only=False):
        """Register a tool the agent can call.

        ``handler`` takes keyword arguments matching ``parameters``
        and returns a string (or a dict, JSON-encoded by the
        adapter). ``read_only`` marks side-effect-free tools.
        """
        if not is_valid_tool_name(name):
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: invalid tool name {name!r} "
                "(lowercase snake_case starting with a letter)"
            )
        if name in self._tools:
            raise PluginStructureError(f"plugin {self.plugin_id!r}: tool {name!r} registered twice")
        if not isinstance(description, str) or not description.strip():
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: tool {name!r} needs a non-empty description"
            )
        if not callable(handler):
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: tool {name!r} handler is not callable"
            )
        _validate_parameters(parameters, what=f"tool {name!r}")
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            read_only=bool(read_only),
            plugin_id=self.plugin_id,
        )
        return self._tools[name]

    # -- hooks ---------------------------------------------------------

    def register_hook(self, event, handler):
        """Register a lifecycle hook for one event in ``HOOK_EVENTS``."""
        if event not in HOOK_EVENTS:
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: unknown hook event {event!r} "
                f"(allowed: {', '.join(HOOK_EVENTS)})"
            )
        if not callable(handler):
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: hook handler for {event!r} is not callable"
            )
        definition = HookDefinition(event=event, handler=handler, plugin_id=self.plugin_id)
        self._hooks.append(definition)
        return definition

    # -- CLI commands --------------------------------------------------

    def register_command(self, name, description, handler):
        """Register a CLI command.

        ``handler`` takes the argument list (``list[str]``) and
        returns an int exit code.
        """
        if not is_valid_command_name(name):
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: invalid command name {name!r} "
                "(lowercase, letters/digits/'-'/'_')"
            )
        if name in self._commands:
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: command {name!r} registered twice"
            )
        if not isinstance(description, str) or not description.strip():
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: command {name!r} needs a non-empty description"
            )
        if not callable(handler):
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: command {name!r} handler is not callable"
            )
        self._commands[name] = CommandDefinition(
            name=name,
            description=description,
            handler=handler,
            plugin_id=self.plugin_id,
        )
        return self._commands[name]

    # -- single-select providers ---------------------------------------

    def register_memory_provider(self, name, provider_cls, *, default=False):
        """Register a memory provider (single-select kind).

        ``provider_cls`` must be a ``MemoryProvider`` subclass (the
        published provider ABC from ``puspa.memory.provider``).
        Only one memory provider is active at a time; see
        ``puspa.plugins.single_select``.
        """
        from puspa.memory.provider import MemoryProvider

        if self._single_select is None:
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: no single-select registry attached to this context"
            )
        if not (isinstance(provider_cls, type) and issubclass(provider_cls, MemoryProvider)):
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: memory provider {name!r} "
                "must be a MemoryProvider subclass"
            )
        self._single_select.register("memory_provider", name, provider_cls, default=default)
        self._single_select_names.append(("memory_provider", name))

    def register_context_engine(self, name, engine_factory, *, default=False):
        """Register a context engine (single-select kind).

        ``engine_factory`` is a callable taking an optional config
        dict and returning a ``ContextEngine`` instance.
        """
        if self._single_select is None:
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: no single-select registry attached to this context"
            )
        if not callable(engine_factory):
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: context engine {name!r} factory is not callable"
            )
        probe = None
        try:
            probe = engine_factory(config=None)
        except Exception as exc:
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: context engine {name!r} "
                f"factory failed to build an engine: {exc}"
            ) from exc
        if not isinstance(probe, ContextEngine):
            raise PluginStructureError(
                f"plugin {self.plugin_id!r}: context engine {name!r} "
                "factory must return a ContextEngine"
            )
        self._single_select.register("context_engine", name, engine_factory, default=default)
        self._single_select_names.append(("context_engine", name))

    # -- logging -------------------------------------------------------

    def log(self, message):
        """Best-effort log line from a plugin (kept in ``shared``).

        Plugins have no direct access to the Puspa logger; the
        loader surfaces these lines on the load report instead.
        """
        self.shared.setdefault("log_lines", []).append(str(message))

    # -- read-only views (used by the loader and the tests) -----------

    @property
    def tools(self) -> dict[str, ToolDefinition]:
        """Registered tools, by name (copy)."""
        return dict(self._tools)

    @property
    def hooks(self) -> list[HookDefinition]:
        """Registered hooks, in registration order (copy)."""
        return list(self._hooks)

    @property
    def commands(self) -> dict[str, CommandDefinition]:
        """Registered commands, by name (copy)."""
        return dict(self._commands)

    @property
    def single_select_names(self) -> list[tuple[str, str]]:
        """Single-select registrations as (kind, name) pairs."""
        return list(self._single_select_names)

    def validate_handler_signatures(self) -> list[str]:
        """Check every registered handler accepts its call shape.

        Returns a list of problems (empty when all handlers fit).
        Tool handlers must accept keyword arguments; command
        handlers must accept one positional argument; hook handlers
        must accept two positional arguments (event, payload) or
        use ``*args``.
        """
        problems: list[str] = []

        def _accepts(handler, *, min_positional, accepts_kwargs):
            try:
                sig = inspect.signature(handler)
            except (TypeError, ValueError):
                return True  # builtins/callables without a signature pass
            params = list(sig.parameters.values())
            has_var_positional = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
            has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
            positional = [
                p
                for p in params
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            if has_var_positional:
                return True
            if len(positional) < min_positional:
                return False
            if accepts_kwargs and not has_var_keyword:
                # A tool handler takes its parameters as keywords;
                # a fixed parameter list is fine too (the adapter
                # only passes declared parameter names).
                return True
            return True

        for tool in self._tools.values():
            if not _accepts(tool.handler, min_positional=0, accepts_kwargs=True):
                problems.append(f"tool {tool.name!r}: handler takes no arguments")
        for hook in self._hooks:
            if not _accepts(hook.handler, min_positional=2, accepts_kwargs=False):
                problems.append(f"hook {hook.event!r}: handler must accept (event, payload)")
        for command in self._commands.values():
            if not _accepts(command.handler, min_positional=1, accepts_kwargs=False):
                problems.append(f"command {command.name!r}: handler must accept (argv)")
        return problems


def validate_plugin_config(schema, config):
    """Validate a plugin config against its schema.

    Same schema shape as the G24 provider config (``{"type": ...,
    "required": bool, "default": ...}``). Kept as a thin alias so
    plugin authors use one name; raises ``PluginConfigError``.
    """
    from puspa.memory.provider import ProviderConfigError, validate_config

    try:
        return validate_config(schema, config)
    except ProviderConfigError as exc:
        raise PluginConfigError(str(exc)) from exc
