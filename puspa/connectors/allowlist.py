"""Advertised-tool allowlist (G10, flagship).

The engine only ever sees the tools this module advertises. Every
other connector layer (native, aggregator, MCP, token apps) hands its
full tool list to :class:`ToolAllowlist` and only the filtered result
reaches the engine, so a tool outside the allowlist can never leak.

The allowlist comes from two environment variables, mirroring the
DuskBot pattern (rewritten, not copied):

- ``CONNECTOR_ALLOWED_TOOLS``: comma-separated tool names.
- ``CONNECTOR_SERVICE_SLUGS``: comma-separated service slugs.

Each variable is byte-capped (``MAX_ENV_BYTES``): an over-long value
is rejected instead of silently truncated, because truncation could
quietly drop a tool the operator meant to allow (or keep one they
meant to drop). An unset variable means that axis is unrestricted;
an explicitly empty value (``""``) is treated the same as unset, so
an empty env var can never accidentally advertise nothing. To allow
nothing, pass an explicit empty set to the constructor instead.
"""

from __future__ import annotations

import os

__all__ = [
    "MAX_ENV_BYTES",
    "ENV_ALLOWED_TOOLS",
    "ENV_SERVICE_SLUGS",
    "AllowlistError",
    "ToolAllowlist",
    "parse_env_list",
]

#: Maximum size of one allowlist environment variable, in bytes.
MAX_ENV_BYTES = 4096

#: Env var listing the tool names the engine may see.
ENV_ALLOWED_TOOLS = "CONNECTOR_ALLOWED_TOOLS"

#: Env var listing the service slugs the engine may see.
ENV_SERVICE_SLUGS = "CONNECTOR_SERVICE_SLUGS"


class AllowlistError(ValueError):
    """An allowlist value is malformed or over the byte cap."""


def parse_env_list(raw: str | None, name: str) -> frozenset[str] | None:
    """Parse one comma-separated allowlist env value.

    Returns ``None`` when the variable is unset or blank (that axis
    is unrestricted). Raises :class:`AllowlistError` when the value
    exceeds ``MAX_ENV_BYTES`` or contains an empty entry. Names are
    stripped of surrounding whitespace and must be non-empty.
    """
    if raw is None:
        return None
    if len(raw.encode("utf-8")) > MAX_ENV_BYTES:
        raise AllowlistError(
            f"{name} is {len(raw.encode('utf-8'))} bytes, over the "
            f"{MAX_ENV_BYTES} byte cap; shorten the list instead of "
            "truncating it"
        )
    items = [item.strip() for item in raw.split(",")]
    if all(not item for item in items):
        return None
    empty = [item for item in items if not item]
    if empty:
        raise AllowlistError(f"{name} contains an empty entry")
    return frozenset(items)


class ToolAllowlist:
    """Which connector tools the engine is allowed to see.

    ``allowed_tools`` is a set of tool names (``None`` = any tool
    name passes this axis); ``allowed_services`` is a set of service
    slugs (``None`` = any service passes). A tool is advertised only
    when it passes both axes. Both axes set to empty sets advertises
    nothing at all.
    """

    def __init__(self, allowed_tools=None, allowed_services=None):
        self.allowed_tools = (
            None if allowed_tools is None else frozenset(allowed_tools)
        )
        self.allowed_services = (
            None if allowed_services is None else frozenset(allowed_services)
        )

    @classmethod
    def from_env(cls, environ=None):
        """Build an allowlist from the environment (or a mapping)."""
        env = os.environ if environ is None else environ
        return cls(
            allowed_tools=parse_env_list(
                env.get(ENV_ALLOWED_TOOLS), ENV_ALLOWED_TOOLS
            ),
            allowed_services=parse_env_list(
                env.get(ENV_SERVICE_SLUGS), ENV_SERVICE_SLUGS
            ),
        )

    def is_advertised(self, tool_name: str, service: str | None = None) -> bool:
        """True when this tool passes both allowlist axes."""
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            return False
        if (
            self.allowed_services is not None
            and service is not None
            and service not in self.allowed_services
        ):
            return False
        if self.allowed_services is not None and service is None:
            return False
        return True

    def advertise(self, tools):
        """Filter a tool list down to what the engine may see.

        ``tools`` is an iterable of dicts with at least ``"name"``
        and optionally ``"service"``. Returns a new list of shallow
        copies, in the original order, with nothing else added or
        removed. A tool dict without a ``"service"`` key is treated
        as service-less: it passes only when no service allowlist
        is set.
        """
        advertised = []
        for tool in tools:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            if self.is_advertised(tool["name"], tool.get("service")):
                advertised.append(dict(tool))
        return advertised

    def advertised_names(self, tools) -> list[str]:
        """Names of the tools :meth:`advertise` would return."""
        return [tool["name"] for tool in self.advertise(tools)]

    def assert_no_leak(self, tools, advertised) -> None:
        """Raise when ``advertised`` contains a tool outside the list.

        This is the flagship invariant: call it in tests (and in any
        layer that builds an engine-facing tool list) to prove that
        no tool outside the allowlist ever reaches the engine.
        """
        advertised_names = {tool["name"] for tool in advertised}
        allowed_names = set(self.advertised_names(tools))
        leaked = sorted(advertised_names - allowed_names)
        if leaked:
            raise AllowlistError(
                f"tools leaked past the allowlist: {', '.join(leaked)}"
            )
