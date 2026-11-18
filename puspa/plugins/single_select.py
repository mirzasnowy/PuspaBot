"""Single-select plugin registry (G27).

Two plugin kinds are single-select: ``memory_provider`` and
``context_engine``. Only one implementation of each kind is
active at a time, following the exact G24 memory-provider
pattern (rewritten here as a generic registry so both kinds
share the rule):

- Registration order is the discovery order (bundled -> user ->
  project -> entry point). The first registration of a
  (kind, name) pair wins; a second one raises
  ``PluginShadowError`` instead of silently replacing it, so a
  provider/engine can never be swapped underneath the agent.
- Selection is explicit: an explicit name wins, else the
  ``PUSPA_<KIND>_PLUGIN`` environment variable, else the
  registered default (the registration flagged ``default=True``,
  or the first registration of that kind when none is flagged).
- The losers are never silently active: ``select`` returns
  exactly one instance, and ``active()`` reports which name is
  selected.

The memory-provider bridge (``install_into_provider_registry``)
hands the selected plugin provider class to a G24
``ProviderRegistry``, so a plugin memory provider can serve the
existing three memory layers without the G24 registry learning
about plugins.
"""

from __future__ import annotations

import os

from puspa.memory.provider import validate_config

from .context_engine import ContextEngine
from .errors import (
    PluginConfigError,
    PluginShadowError,
    UnknownPluginError,
    UnknownSingleSelectKindError,
)

__all__ = [
    "SINGLE_SELECT_KINDS",
    "SingleSelectRegistry",
    "default_registry",
    "install_into_provider_registry",
    "select_single",
]

#: The two single-select kinds.
SINGLE_SELECT_KINDS = ("memory_provider", "context_engine")

#: Environment variable per kind, e.g. PUSPA_MEMORY_PROVIDER_PLUGIN.
ENV_BY_KIND = {
    "memory_provider": "PUSPA_MEMORY_PROVIDER_PLUGIN",
    "context_engine": "PUSPA_CONTEXT_ENGINE_PLUGIN",
}


def _check_kind(kind):
    if kind not in SINGLE_SELECT_KINDS:
        raise UnknownSingleSelectKindError(
            f"unknown single-select kind {kind!r} (allowed: {', '.join(SINGLE_SELECT_KINDS)})"
        )


class SingleSelectRegistry:
    """Explicit registry for the single-select plugin kinds."""

    def __init__(self):
        # kind -> {name: factory}; factory takes config=None and
        # returns an instance (provider class or engine factory).
        self._entries: dict[str, dict[str, object]] = {kind: {} for kind in SINGLE_SELECT_KINDS}
        # kind -> default name (first registration wins when none
        # is flagged default).
        self._defaults: dict[str, str | None] = {kind: None for kind in SINGLE_SELECT_KINDS}
        # kind -> currently selected name (None until select() runs).
        self._active: dict[str, str | None] = {kind: None for kind in SINGLE_SELECT_KINDS}

    def register(self, kind, name, factory, *, default=False):
        """Register one implementation under (kind, name).

        Raises ``PluginShadowError`` when the name is already
        registered for that kind, and ``PluginConfigError`` when
        the factory is not callable or the name is unusable.
        """
        _check_kind(kind)
        if not isinstance(name, str) or not name:
            raise PluginConfigError(f"single-select {kind}: name must be a non-empty string")
        if not callable(factory):
            raise PluginConfigError(f"single-select {kind} {name!r}: factory is not callable")
        if name in self._entries[kind]:
            raise PluginShadowError(
                f"single-select {kind} {name!r} is already registered; shadowing is rejected"
            )
        self._entries[kind][name] = factory
        if default or self._defaults[kind] is None:
            self._defaults[kind] = name
        return factory

    def names(self, kind):
        """Registered names for ``kind``, in registration order."""
        _check_kind(kind)
        return list(self._entries[kind])

    def get(self, kind, name):
        """Return the factory registered under (kind, name)."""
        _check_kind(kind)
        return self._entries[kind][name]

    def default(self, kind):
        """Default name for ``kind`` (None when nothing registered)."""
        _check_kind(kind)
        return self._defaults[kind]

    def active(self, kind):
        """Currently selected name for ``kind`` (None until select())."""
        _check_kind(kind)
        return self._active[kind]

    def select(self, kind, name=None, config=None):
        """Select exactly one implementation and instantiate it.

        ``name`` wins; otherwise the ``PUSPA_<KIND>_PLUGIN``
        environment variable; otherwise the registered default.
        Raises ``UnknownPluginError`` for an unregistered name and
        ``PluginConfigError`` when the config fails the
        implementation's own schema (validated inside its
        constructor for classes, here for plain factories).
        """
        _check_kind(kind)
        wanted = name or os.environ.get(ENV_BY_KIND[kind]) or self._defaults[kind]
        if wanted is None or wanted not in self._entries[kind]:
            raise UnknownPluginError(
                f"unknown {kind} {wanted!r}; registered: {', '.join(self.names(kind)) or '(none)'}"
            )
        factory = self._entries[kind][wanted]
        try:
            instance = factory(config=config)
        except TypeError:
            # Factories that take no config argument (plain
            # zero-arg callables) still work.
            instance = factory()
        self._active[kind] = wanted
        return instance

    def __contains__(self, item):
        kind, name = item
        _check_kind(kind)
        return name in self._entries[kind]

    def __len__(self):
        return sum(len(names) for names in self._entries.values())


def default_registry():
    """An empty single-select registry (no bundled implementations).

    Bundled implementations arrive as builtin plugins loaded
    through the loader, never hard-coded here, so core never
    imports a specific builtin plugin.
    """
    return SingleSelectRegistry()


def select_single(kind, name=None, config=None, registry=None):
    """Convenience wrapper over ``SingleSelectRegistry.select``."""
    registry = registry if registry is not None else default_registry()
    return registry.select(kind, name=name, config=config)


def install_into_provider_registry(plugin_registry, name, provider_registry):
    """Bridge a selected plugin memory provider into a G24 registry.

    Registers the plugin provider class under ``name`` in the
    given G24 ``ProviderRegistry`` (``puspa.memory.provider``),
    so ``select_provider`` can then select it like any other
    provider. The plugin class is returned. Raises
    ``UnknownPluginError`` when the plugin registry has no such
    memory provider.
    """
    from puspa.memory.provider import MemoryProvider

    _check_kind("memory_provider")
    if name not in plugin_registry.names("memory_provider"):
        raise UnknownPluginError(
            f"unknown memory provider plugin {name!r}; registered: "
            f"{', '.join(plugin_registry.names('memory_provider')) or '(none)'}"
        )
    provider_cls = plugin_registry.get("memory_provider", name)
    if not (isinstance(provider_cls, type) and issubclass(provider_cls, MemoryProvider)):
        raise PluginConfigError(f"memory provider plugin {name!r} is not a MemoryProvider subclass")
    provider_registry.register(provider_cls)
    return provider_cls


# Re-exported for plugin authors that validate configs by hand.
__all__ += ["ContextEngine", "validate_config"]
