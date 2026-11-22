"""Tool registry, toolsets, availability checks, approval, footprint (G23).

- :mod:`puspa.tools.registry` -- dependency-free registry: import-time
  ``register()``, automatic discovery, schema collection, dispatch
  (``handle_function_call()``), error wrapping, multimodal envelope.
- :mod:`puspa.tools.toolsets` -- registration vs exposure: the
  default core bundle plus platform presets and per-tenant custom
  toolsets, honouring the connector allowlist.
- :mod:`puspa.tools.check` -- per-profile availability (``check_fn``)
  with a TTL cache; credential checks go through the G22 seam
  ``puspa.credentials.scope.check_credential`` (lazy import,
  fail-soft).
- :mod:`puspa.tools.approval` -- dangerous-command approval gate:
  classify, approval callback, yolo mode, approval floor, per-OS
  patterns.
- :mod:`puspa.tools.footprint` -- footprint ladder: decide whether a
  new capability should be a CLI command, a skill, a plugin, or a
  core tool.
- :mod:`puspa.tools.builtin` -- adapters registering Puspa's existing
  tools (``run_code``, ``plugin_echo``) on the registry; imported by
  :func:`puspa.tools.registry.discover`, not by this package, so
  importing ``puspa.tools`` stays free of the chassis/plugins stack.
  It also registers the browser + web tools from
  :mod:`puspa.tools.browser`.
- :mod:`puspa.tools.browser` -- the browser tools (``browser_open``,
  ``browser_click``, ``browser_fill``) over the computer web bridge,
  plus ``web_search`` (DuckDuckGo HTML, no key) and ``web_fetch``
  (plain HTTP, clear error on JavaScript pages); exposed through the
  ``web`` toolset preset in :mod:`puspa.tools.toolsets`.
- :mod:`puspa.tools.narrowing` -- per-run skill narrowing + the
  ``search_tools`` overflow tool (G17): pick the skills that fit the
  user message, cap the offered set small, hide the rest behind
  ``search_tools``, refuse the run past 128 tools with the
  ``agent.tool_limit_refused`` audit event.
"""

from .approval import ApprovalDenied, ApprovalGate, DangerLevel, classify_command
from .browser import BROWSER_TOOL_NAMES, BrowserTools, register_browser_tools
from .check import AvailabilityChecker, CheckResult, default_checker
from .footprint import Capability, LayerRecommendation, recommend_layer
from .narrowing import (
    DEFAULT_MAX_DIRECT_TOOLS,
    DEFAULT_MAX_OFFERED_TOOLS,
    DEFAULT_MAX_TOOLS,
    DEFAULT_SEARCH_RESULT_LIMIT,
    SEARCH_TOOLS_NAME,
    TOOL_LIMIT_REFUSED_EVENT,
    KeywordSelector,
    NarrowingConfig,
    NarrowingResult,
    Skill,
    ToolLimitError,
    ToolSelector,
    current_hidden_tools,
    hidden_tools_context,
    prepare_run_tools,
    register_search_tools,
    search_tools,
    search_tools_definition,
)
from .registry import (
    ToolDefinition,
    ToolRegistry,
    default_registry,
    discover,
    handle_function_call,
    handle_function_call_async,
    register,
    schemas,
)
from .toolsets import (
    _PUSPA_CORE_TOOLS,
    CODING_TOOLSET,
    DEFAULT_TOOLSET,
    WEB_TOOLSET,
    Toolset,
    ToolsetRegistry,
    default_toolsets,
    exposed_names,
    exposed_schemas,
)

__all__ = [
    "BROWSER_TOOL_NAMES",
    "CODING_TOOLSET",
    "DEFAULT_MAX_DIRECT_TOOLS",
    "DEFAULT_MAX_OFFERED_TOOLS",
    "DEFAULT_MAX_TOOLS",
    "DEFAULT_SEARCH_RESULT_LIMIT",
    "DEFAULT_TOOLSET",
    "SEARCH_TOOLS_NAME",
    "TOOL_LIMIT_REFUSED_EVENT",
    "WEB_TOOLSET",
    "_PUSPA_CORE_TOOLS",
    "ApprovalDenied",
    "ApprovalGate",
    "AvailabilityChecker",
    "BrowserTools",
    "Capability",
    "CheckResult",
    "DangerLevel",
    "KeywordSelector",
    "LayerRecommendation",
    "NarrowingConfig",
    "NarrowingResult",
    "Skill",
    "ToolDefinition",
    "ToolLimitError",
    "ToolRegistry",
    "ToolSelector",
    "Toolset",
    "ToolsetRegistry",
    "classify_command",
    "current_hidden_tools",
    "default_checker",
    "default_registry",
    "default_toolsets",
    "discover",
    "exposed_names",
    "exposed_schemas",
    "handle_function_call",
    "handle_function_call_async",
    "hidden_tools_context",
    "prepare_run_tools",
    "recommend_layer",
    "register",
    "register_browser_tools",
    "register_search_tools",
    "schemas",
    "search_tools",
    "search_tools_definition",
]
