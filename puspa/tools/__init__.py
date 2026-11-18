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
"""

from .approval import ApprovalDenied, ApprovalGate, DangerLevel, classify_command
from .check import AvailabilityChecker, CheckResult, default_checker
from .footprint import Capability, LayerRecommendation, recommend_layer
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
    Toolset,
    ToolsetRegistry,
    default_toolsets,
    exposed_names,
    exposed_schemas,
)

__all__ = [
    "CODING_TOOLSET",
    "DEFAULT_TOOLSET",
    "_PUSPA_CORE_TOOLS",
    "ApprovalDenied",
    "ApprovalGate",
    "AvailabilityChecker",
    "Capability",
    "CheckResult",
    "DangerLevel",
    "LayerRecommendation",
    "ToolDefinition",
    "ToolRegistry",
    "Toolset",
    "ToolsetRegistry",
    "classify_command",
    "default_checker",
    "default_registry",
    "default_toolsets",
    "discover",
    "exposed_names",
    "exposed_schemas",
    "handle_function_call",
    "handle_function_call_async",
    "recommend_layer",
    "register",
    "schemas",
]
