"""Plugin system for Puspa (G27, hermes-agent pattern, rewritten).

A plugin is a Python module with ``register(ctx)`` that registers
tools, hooks, and CLI commands through the published ``ctx``
API, plus two single-select kinds (memory provider, context
engine) of which only one is active at a time. The published
API surface is documented in ``plugin_api.json``; the boundary
lint (``puspa.plugins.boundary``) enforces it.

- ``loader`` — discovery from user / project / pip entry points
  plus ``load_plugin`` (the G3 marketplace installer seam).
- ``context`` — ``PluginContext`` (the ``ctx`` API).
- ``single_select`` — the single-select registry + the G24
  memory-provider bridge.
- ``context_engine`` — the ``ContextEngine`` ABC.
- ``adapters`` — nanobot ``Tool`` / ``AgentHook`` adapters
  (the only module that imports nanobot).
- ``catalog`` — third-party plugin manifest validation + loading.
- ``mcp`` — optional MCP definitions + install-free readiness.
- ``boundary`` — the import lint (``python -m puspa.plugins.boundary``).
- ``builtin`` — the bundled proof plugins (``echo``,
  ``simple_context``, ``memory_inmemory``).
"""

from __future__ import annotations

from .boundary import Violation, lint_tree
from .catalog import (
    check_repository_alive,
    load_catalog,
    load_manifest,
    validate_manifest,
)
from .context import (
    HOOK_EVENTS,
    CommandDefinition,
    HookDefinition,
    PluginContext,
    ToolDefinition,
)
from .context_engine import ContextEngine
from .errors import (
    PluginConfigError,
    PluginError,
    PluginShadowError,
    PluginStructureError,
    UnknownPluginError,
    UnknownSingleSelectKindError,
)
from .loader import (
    ENTRY_POINT_GROUP,
    LoadResult,
    PluginRecord,
    PluginSource,
    discover_plugins,
    load_plugin,
    load_plugins,
    run_command,
)
from .mcp import check_mcp_readiness, check_readiness, load_mcp_definition
from .single_select import (
    SINGLE_SELECT_KINDS,
    SingleSelectRegistry,
    install_into_provider_registry,
    select_single,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "HOOK_EVENTS",
    "SINGLE_SELECT_KINDS",
    "CommandDefinition",
    "ContextEngine",
    "HookDefinition",
    "LoadResult",
    "PluginContext",
    "PluginError",
    "PluginConfigError",
    "PluginRecord",
    "PluginShadowError",
    "PluginSource",
    "PluginStructureError",
    "SingleSelectRegistry",
    "ToolDefinition",
    "UnknownPluginError",
    "UnknownSingleSelectKindError",
    "Violation",
    "check_mcp_readiness",
    "check_readiness",
    "check_repository_alive",
    "discover_plugins",
    "install_into_provider_registry",
    "lint_tree",
    "load_catalog",
    "load_manifest",
    "load_mcp_definition",
    "load_plugin",
    "load_plugins",
    "run_command",
    "select_single",
    "validate_manifest",
]
