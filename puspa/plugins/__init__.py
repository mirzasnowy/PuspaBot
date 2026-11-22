"""Plugin system for Puspa (G27, hermes-agent pattern, rewritten).

A plugin is a Python module with ``register(ctx)`` that registers
tools, hooks, and CLI commands through the published ``ctx``
API, plus two single-select kinds (memory provider, context
engine) of which only one is active at a time. The published
API surface is documented in ``plugin_api.json``; the boundary
lint (``puspa.plugins.boundary``) enforces it.

- ``loader`` — discovery from user / project / pip entry points
  plus ``load_plugin`` (the G3 marketplace installer seam).
  The loader enforces the declared plugin API version and
  declared permissions before/after ``register(ctx)`` runs.
- ``api_version`` — the versioned SDK core: the current API
  version, parsing, and the compatibility rule (same major,
  minor no newer than the host).
- ``permissions`` — the permission names a plugin may declare
  and the load-time enforcement of the registration kinds.
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

from .api_version import (
    API_VERSION,
    API_VERSION_MAJOR,
    API_VERSION_MINOR,
    ApiVersion,
    check_api_version,
    is_compatible,
    parse_api_version,
    resolve_api_version,
)
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
    PluginApiVersionError,
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
from .permissions import (
    KNOWN_PERMISSIONS,
    check_permissions,
    used_permissions,
    validate_permissions,
)
from .single_select import (
    SINGLE_SELECT_KINDS,
    SingleSelectRegistry,
    install_into_provider_registry,
    select_single,
)

__all__ = [
    "API_VERSION",
    "API_VERSION_MAJOR",
    "API_VERSION_MINOR",
    "ENTRY_POINT_GROUP",
    "HOOK_EVENTS",
    "KNOWN_PERMISSIONS",
    "SINGLE_SELECT_KINDS",
    "ApiVersion",
    "CommandDefinition",
    "ContextEngine",
    "HookDefinition",
    "LoadResult",
    "PluginApiVersionError",
    "PluginConfigError",
    "PluginContext",
    "PluginError",
    "PluginRecord",
    "PluginShadowError",
    "PluginSource",
    "PluginStructureError",
    "SingleSelectRegistry",
    "ToolDefinition",
    "UnknownPluginError",
    "UnknownSingleSelectKindError",
    "Violation",
    "check_api_version",
    "check_mcp_readiness",
    "check_permissions",
    "check_readiness",
    "check_repository_alive",
    "discover_plugins",
    "install_into_provider_registry",
    "is_compatible",
    "lint_tree",
    "load_catalog",
    "load_manifest",
    "load_mcp_definition",
    "load_plugin",
    "load_plugins",
    "parse_api_version",
    "resolve_api_version",
    "run_command",
    "select_single",
    "used_permissions",
    "validate_manifest",
    "validate_permissions",
]
