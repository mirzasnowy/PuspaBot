"""Plugin system errors (G27, hermes-agent pattern, rewritten).

Every failure a plugin author or an operator can hit has its own
error type with a message that names the plugin and the exact
problem, so a broken plugin is reported, never silently skipped.
"""

from __future__ import annotations

__all__ = [
    "PluginError",
    "PluginStructureError",
    "PluginShadowError",
    "PluginConfigError",
    "UnknownPluginError",
    "UnknownSingleSelectKindError",
]


class PluginError(Exception):
    """Base error for plugin loading, registration, and selection."""


class PluginStructureError(PluginError):
    """A plugin module is malformed.

    Raised when the module has no callable ``register(ctx)``, when
    ``register`` raises, when a registration is invalid (bad name,
    duplicate tool, unknown hook event), or when a manifest or
    definition file fails validation.
    """


class PluginShadowError(PluginError):
    """A plugin id (or a single-select name) was registered twice.

    Shadowing is rejected: the first registration wins, so a later
    source can never silently replace a plugin an earlier source
    chose. This mirrors the G24 memory-provider rule.
    """


class PluginConfigError(PluginError):
    """A plugin config fails its own config schema."""


class UnknownPluginError(PluginError):
    """A requested plugin id is not loaded."""


class UnknownSingleSelectKindError(PluginError):
    """A single-select kind is not one of the known kinds."""
