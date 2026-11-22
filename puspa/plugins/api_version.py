"""Versioned plugin API (AST-01, the stable plugin SDK).

This module is the versioned half of the published plugin
interface. A community plugin says which plugin API it was
written against (``PLUGIN_API_VERSION`` on the module, or
``api_version`` in its catalog manifest), and PuspaBot decides
whether that plugin still runs on this release — so a plugin
written today keeps working when PuspaBot upgrades.

The compatibility promise (also written out in
``docs/plugin-sdk.md``):

- **Same major, older or equal minor: compatible.** A plugin
  written against API 1.0 runs on a host shipping API 1.3.
  Minor releases only *add* to the API; they never remove or
  change what an older plugin uses.
- **Different major: incompatible.** API 2.0 may remove or
  change things, so a 1.x plugin is refused with a clear
  error, and a 2.x plugin is refused by a 1.x host. The
  plugin author ships a new release for the new major.
- **Newer minor than the host: incompatible.** A plugin
  written against API 1.3 uses things a 1.0 host does not
  have yet, so the host refuses it and tells the operator
  to upgrade PuspaBot (or use an older plugin release).
- **Patch numbers are ignored.** ``1.0.3`` is API 1.0 as far
  as compatibility goes; patches are bug fixes only.

A plugin that declares no version at all is treated as
written against the current API (backwards compatible with
the pre-SDK plugins, which predate the declaration).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import ModuleType

from .errors import PluginApiVersionError, PluginStructureError

__all__ = [
    "API_VERSION",
    "API_VERSION_MAJOR",
    "API_VERSION_MINOR",
    "PLUGIN_API_VERSION_ATTR",
    "ApiVersion",
    "check_api_version",
    "declared_api_version",
    "is_compatible",
    "parse_api_version",
    "resolve_api_version",
]

#: Current published plugin API version (MAJOR.MINOR). This is
#: the single source of truth: ``PluginContext.api_version`` is
#: the major part, ``plugin_api.json`` carries the same major
#: and this string, and the catalog admission check uses the
#: compatibility rule below.
API_VERSION = "1.0"

#: Major part of :data:`API_VERSION`.
API_VERSION_MAJOR = 1

#: Minor part of :data:`API_VERSION`.
API_VERSION_MINOR = 0

#: Module attribute a plugin sets to declare its API version,
#: e.g. ``PLUGIN_API_VERSION = "1.0"``.
PLUGIN_API_VERSION_ATTR = "PLUGIN_API_VERSION"

_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


@dataclass(frozen=True)
class ApiVersion:
    """A plugin API version: major + minor (patch ignored)."""

    major: int
    minor: int = 0

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"


def parse_api_version(value) -> ApiVersion:
    """Parse an API version declaration.

    Accepts an :class:`ApiVersion` (returned unchanged), an int
    (a bare major, e.g. ``1``) or a string (``"1"``, ``"1.0"``,
    ``"1.0.3"`` — a patch part is accepted and ignored). Raises
    ``PluginStructureError`` naming the bad value for anything
    else.
    """
    if isinstance(value, ApiVersion):
        return value
    if isinstance(value, bool):
        raise PluginStructureError(f"api_version {value!r} is not a version")
    if isinstance(value, int):
        if value < 0:
            raise PluginStructureError(f"api_version {value!r} is not a version")
        return ApiVersion(major=value, minor=0)
    if isinstance(value, str):
        match = _VERSION_RE.match(value.strip())
        if match:
            return ApiVersion(
                major=int(match.group(1)),
                minor=int(match.group(2) or 0),
            )
    raise PluginStructureError(
        f"api_version {value!r} is not a version "
        "(expected a major number like 1, or 'MAJOR.MINOR' like '1.0')"
    )


def is_compatible(declared, current=API_VERSION) -> bool:
    """True when a plugin written against ``declared`` runs on a host shipping ``current``.

    Same major and a declared minor no newer than the host's:
    the host has everything the plugin needs. A different
    major, or a declared minor newer than the host, is not
    compatible. Both sides may be an :class:`ApiVersion`, an
    int, or a version string.
    """
    if not isinstance(declared, ApiVersion):
        declared = parse_api_version(declared)
    if not isinstance(current, ApiVersion):
        current = parse_api_version(current)
    return declared.major == current.major and declared.minor <= current.minor


def check_api_version(declared, *, plugin_id: str, current=API_VERSION) -> ApiVersion:
    """Check a declared API version against the host's, or refuse it.

    Returns the parsed declared version when compatible.
    Raises ``PluginApiVersionError`` with a message that names
    the plugin, both versions, and what to do (upgrade PuspaBot
    for a newer-minor plugin, or ship a plugin release for the
    host's major for a major mismatch).
    """
    declared_version = parse_api_version(declared)
    host_version = current if isinstance(current, ApiVersion) else parse_api_version(current)
    if is_compatible(declared_version, host_version):
        return declared_version
    if declared_version.major != host_version.major:
        raise PluginApiVersionError(
            f"plugin {plugin_id!r} needs plugin API {declared_version} "
            f"(major {declared_version.major}), but this PuspaBot ships "
            f"plugin API {host_version} (major {host_version.major}): "
            "major versions must match — use a plugin release written "
            f"for API {host_version.major}.x, or upgrade PuspaBot to a "
            f"release shipping API {declared_version.major}.x"
        )
    raise PluginApiVersionError(
        f"plugin {plugin_id!r} needs plugin API {declared_version}, but "
        f"this PuspaBot ships plugin API {host_version}: the plugin "
        "uses API features newer than this host — upgrade PuspaBot "
        f"to a release shipping API {declared_version} or newer, or "
        f"use a plugin release written for API {host_version} or older"
    )


def declared_api_version(module: ModuleType) -> ApiVersion | None:
    """A plugin module's declared API version, or None when absent.

    Reads ``PLUGIN_API_VERSION``; raises
    ``PluginStructureError`` when the attribute is present but
    unparsable, so a typo (``"v1"``, ``"one"``) fails at load
    time instead of silently meaning something else.
    """
    raw = getattr(module, PLUGIN_API_VERSION_ATTR, None)
    if raw is None:
        return None
    try:
        return parse_api_version(raw)
    except PluginStructureError as exc:
        plugin_id = getattr(module, "PLUGIN_ID", None) or module.__name__
        raise PluginStructureError(f"plugin {plugin_id!r}: {exc}") from exc


def resolve_api_version(module: ModuleType, *, plugin_id: str) -> ApiVersion:
    """Resolve a plugin module's API version, enforcing compatibility.

    A module with no ``PLUGIN_API_VERSION`` is treated as
    written against the current API. A declared version that
    is incompatible raises ``PluginApiVersionError``.
    """
    declared = declared_api_version(module)
    if declared is None:
        return ApiVersion(major=API_VERSION_MAJOR, minor=API_VERSION_MINOR)
    return check_api_version(declared, plugin_id=plugin_id)
