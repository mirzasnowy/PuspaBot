"""Toolsets: the exposure layer on top of the registry (G23).

Registration and exposure are two different layers:

- **Registration** (``puspa.tools.registry``) makes a tool *known*.
  Any tool module can register itself at import time.
- **Exposure** (this module) decides which registered tools a given
  platform or tenant actually *sees*. A tool is exposed only when
  some toolset names it -- registering alone never exposes anything.

Every platform toolset inherits the default bundle
:data:`_PUSPA_CORE_TOOLS`, mirroring the hermes-agent pattern
(``_HERMES_CORE_TOOLS``). A tenant can define a custom toolset that
inherits the default and adds or removes tools.

Exposure also honours the connector allowlist
(:class:`puspa.connectors.allowlist.ToolAllowlist`): a tool outside
the allowlist is never exposed, even when a toolset names it. The
allowlist is consulted here, never duplicated -- this module calls
``ToolAllowlist.is_advertised`` and nothing else. Availability
(``check_fn``) is the last filter: a tool whose check fails for the
profile is not exposed to that profile.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from puspa.connectors.allowlist import ToolAllowlist
from puspa.tools.registry import ToolDefinition, ToolRegistry, default_registry

__all__ = [
    "CODING_TOOLSET",
    "DEFAULT_TOOLSET",
    "WEB_TOOLSET",
    "_PUSPA_CORE_TOOLS",
    "Toolset",
    "ToolsetRegistry",
    "default_toolsets",
    "exposed_names",
    "exposed_schemas",
]

#: The default bundle every platform toolset inherits. Small on
#: purpose: a tool joins the core bundle only when it is safe and
#: useful on every platform. ``run_code`` is deliberately NOT here --
#: code execution is exposed only through the ``coding`` toolset (or
#: a tenant toolset that names it).
_PUSPA_CORE_TOOLS: frozenset[str] = frozenset({"plugin_echo"})

#: Name of the default toolset (core bundle only).
DEFAULT_TOOLSET = "default"

#: Name of the coding-platform preset (core + code execution).
CODING_TOOLSET = "coding"

#: Name of the web preset (core + browser and web tools). The
#: browser tools are not in the core bundle: ``browser_click``
#: and ``browser_fill`` act on live pages (permission-gated, but
#: still actions), so a caller opts into them by picking this
#: preset, the same way it opts into code execution with
#: ``coding``. The read-only three (``browser_open``,
#: ``web_search``, ``web_fetch``) ride along because a web run
#: needs all five together.
WEB_TOOLSET = "web"


class Toolset:
    """A named set of tools, optionally inheriting other toolsets."""

    def __init__(
        self,
        name: str,
        tools: Any = (),
        *,
        inherits: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.tools: frozenset[str] = frozenset(tools)
        self.inherits: tuple[str, ...] = tuple(inherits)

    def all_tools(self, registry: ToolsetRegistry) -> frozenset[str]:
        """This toolset's tools, with inherited toolsets resolved."""
        collected: set[str] = set(self.tools)
        seen: set[str] = {self.name}
        stack = list(self.inherits)
        while stack:
            parent_name = stack.pop()
            if parent_name in seen:
                continue
            seen.add(parent_name)
            parent = registry.get(parent_name)
            if parent is None:
                continue
            collected.update(parent.tools)
            stack.extend(parent.inherits)
        return frozenset(collected)


class ToolsetRegistry:
    """Named toolsets: platform presets plus per-tenant custom sets."""

    def __init__(self) -> None:
        self._toolsets: dict[str, Toolset] = {}

    def register_toolset(
        self,
        name: str,
        tools: Any = (),
        *,
        inherits: tuple[str, ...] = (),
    ) -> Toolset:
        """Create (or replace) a toolset."""
        toolset = Toolset(name, tools, inherits=inherits)
        self._toolsets[name] = toolset
        return toolset

    def register_custom(
        self,
        name: str,
        tools: Any = (),
        *,
        inherits: tuple[str, ...] = (DEFAULT_TOOLSET,),
    ) -> Toolset:
        """Create a per-tenant custom toolset (inherits default)."""
        return self.register_toolset(name, tools, inherits=inherits)

    def get(self, name: str) -> Toolset | None:
        return self._toolsets.get(name)

    def names(self) -> list[str]:
        return sorted(self._toolsets)

    def __contains__(self, name: str) -> bool:
        return name in self._toolsets

    # -- exposure -------------------------------------------------------

    def exposed_names(
        self,
        toolset_name: str,
        *,
        registry: ToolRegistry | None = None,
        allowlist: ToolAllowlist | None = None,
        profile_id: str | None = None,
        checker: Callable[[ToolDefinition, str | None], bool] | None = None,
    ) -> list[str]:
        """Names of the tools this toolset exposes, in sorted order.

        A tool is exposed only when it passes all four filters, in
        order: (1) it is registered, (2) the toolset (with its
        parents) names it, (3) the connector allowlist advertises it
        (when an allowlist is given), (4) its availability check
        passes for the profile (when a ``checker`` is given, it
        decides; otherwise the tool's own ``check`` is used).
        """
        toolset = self._toolsets.get(toolset_name)
        if toolset is None:
            return []
        tools = toolset.all_tools(self)
        reg = registry if registry is not None else default_registry()
        exposed: list[str] = []
        for name in sorted(tools):
            definition = reg.get(name)
            if definition is None:
                continue
            if allowlist is not None and not allowlist.is_advertised(name, definition.service):
                continue
            if checker is not None:
                try:
                    if not checker(definition, profile_id):
                        continue
                except Exception:
                    continue
            elif not reg.is_available(name, profile_id):
                continue
            exposed.append(name)
        return exposed

    def exposed_schemas(
        self,
        toolset_name: str,
        *,
        registry: ToolRegistry | None = None,
        allowlist: ToolAllowlist | None = None,
        profile_id: str | None = None,
        checker: Callable[[ToolDefinition, str | None], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Function schemas for the tools this toolset exposes."""
        reg = registry if registry is not None else default_registry()
        schemas: list[dict[str, Any]] = []
        for name in self.exposed_names(
            toolset_name,
            registry=reg,
            allowlist=allowlist,
            profile_id=profile_id,
            checker=checker,
        ):
            definition = reg.get(name)
            if definition is None:
                continue
            schemas.append(definition.to_schema())
        return schemas


def default_toolsets() -> ToolsetRegistry:
    """The built-in toolsets: ``default`` + the ``coding`` and
    ``web`` presets."""
    registry = ToolsetRegistry()
    registry.register_toolset(DEFAULT_TOOLSET, _PUSPA_CORE_TOOLS)
    registry.register_toolset(CODING_TOOLSET, {"run_code"}, inherits=(DEFAULT_TOOLSET,))
    registry.register_toolset(
        WEB_TOOLSET,
        {"browser_open", "browser_click", "browser_fill", "web_search", "web_fetch"},
        inherits=(DEFAULT_TOOLSET,),
    )
    return registry


def exposed_names(
    toolset_name: str,
    *,
    toolsets: ToolsetRegistry | None = None,
    registry: ToolRegistry | None = None,
    allowlist: ToolAllowlist | None = None,
    profile_id: str | None = None,
    checker: Callable[[ToolDefinition, str | None], bool] | None = None,
) -> list[str]:
    """Shortcut: expose from the built-in toolsets."""
    return (toolsets if toolsets is not None else default_toolsets()).exposed_names(
        toolset_name,
        registry=registry,
        allowlist=allowlist,
        profile_id=profile_id,
        checker=checker,
    )


def exposed_schemas(
    toolset_name: str,
    *,
    toolsets: ToolsetRegistry | None = None,
    registry: ToolRegistry | None = None,
    allowlist: ToolAllowlist | None = None,
    profile_id: str | None = None,
    checker: Callable[[ToolDefinition, str | None], bool] | None = None,
) -> list[dict[str, Any]]:
    """Shortcut: schemas exposed from the built-in toolsets."""
    return (toolsets if toolsets is not None else default_toolsets()).exposed_schemas(
        toolset_name,
        registry=registry,
        allowlist=allowlist,
        profile_id=profile_id,
        checker=checker,
    )
