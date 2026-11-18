"""Child toolset validation for the subagent lifecycle (G25).

A child runs a toolset, never the parent's raw registry. The child
toolset must be a known toolset whose resolved tools are all
registered and all inside the parent toolset; anything else is
refused before spawn. The check runs on resolved tool names only —
no availability (``check_fn``) call happens here, because
availability is a per-profile live check and the lifecycle must not
turn a launch into a credential probe.
"""

from __future__ import annotations

from puspa.tools.registry import ToolRegistry
from puspa.tools.toolsets import ToolsetRegistry

from .errors import ToolsetRejectedError

__all__ = [
    "resolved_tools",
    "validate_child_toolset",
]


def resolved_tools(
    name: str,
    *,
    toolsets: ToolsetRegistry,
) -> frozenset[str]:
    """Resolved tool names of one named toolset (inheritance applied).

    Raises ``ToolsetRejectedError`` for an unknown toolset name.
    """
    toolset = toolsets.get(name)
    if toolset is None:
        raise ToolsetRejectedError(f"unknown toolset {name!r}")
    return toolset.all_tools(toolsets)


def validate_child_toolset(
    *,
    child_name: str,
    parent_name: str,
    toolsets: ToolsetRegistry,
    registry: ToolRegistry,
) -> frozenset[str]:
    """Validate a child toolset against its parent, fail-closed.

    Returns the child's resolved tool names — the frozen view the
    child receives. Raises ``ToolsetRejectedError`` when the child
    toolset is unknown, when it names a tool no registry knows
    (an unregistered tool must never reach a child), or when it
    names any tool outside the parent toolset (a child may never
    widen what its parent can do).
    """
    child_tools = resolved_tools(child_name, toolsets=toolsets)
    parent_tools = resolved_tools(parent_name, toolsets=toolsets)
    unknown = sorted(name for name in child_tools if registry.get(name) is None)
    if unknown:
        raise ToolsetRejectedError(
            f"child toolset {child_name!r} names unregistered tools: {', '.join(unknown)}"
        )
    widening = sorted(child_tools - parent_tools)
    if widening:
        raise ToolsetRejectedError(
            f"child toolset {child_name!r} widens the parent toolset "
            f"{parent_name!r} with: {', '.join(widening)}"
        )
    return frozenset(child_tools)
