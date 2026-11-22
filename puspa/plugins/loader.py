"""Plugin discovery + loader (G27, hermes-agent pattern, rewritten).

Discovery consults three sources in a fixed order, and the first
registration of a plugin id wins at every step:

1. **user** plugins: ``~/.puspa/plugins`` (or ``PUSPA_PLUGIN_DIR``),
   one ``.py`` file or package directory per plugin;
2. **project** plugins: ``./plugins`` (or ``PUSPA_PROJECT_PLUGIN_DIR``);
3. **pip entry points**: group ``puspa.plugins``.

A plugin is a Python module with a callable ``register(ctx)``.
The loader calls it with a fresh ``PluginContext`` and records
exactly what the plugin registered (tools, hooks, commands,
single-select providers). A malformed plugin is rejected with a
``PluginStructureError`` that names the plugin and the problem;
``load_plugins`` collects failures per source instead of
aborting the whole discovery, and the caller decides whether to
raise (``LoadResult.raise_if_failed``).

The G3 marketplace seam: the marketplace installer calls
``load_plugin`` (this function, imported lazily) to load an
installed skill's plugin code. The installer passes the plugin
as a module object or a dotted path; the loader never installs
anything and never touches the network.

Versioned API (AST-01, the stable plugin SDK): before
``register(ctx)`` runs, the loader resolves the plugin's
declared ``PLUGIN_API_VERSION`` (``puspa.plugins.api_version``)
and refuses a plugin whose API major differs from the host's
or whose minor is newer than the host's, with a clear error.
A plugin that declares no version is treated as written
against the current API. After ``register`` runs, the loader
enforces declared permissions (``PLUGIN_PERMISSIONS`` on the
module, or the ``permissions`` argument, e.g. from a catalog
manifest): what the plugin registered must be a subset of
what it declared.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from .api_version import API_VERSION, resolve_api_version
from .context import PluginContext
from .errors import (
    PluginError,
    PluginShadowError,
    PluginStructureError,
    UnknownPluginError,
)
from .permissions import (
    PLUGIN_PERMISSIONS_ATTR,
    check_permissions,
    used_permissions,
    validate_permissions,
)
from .single_select import SingleSelectRegistry

__all__ = [
    "ENTRY_POINT_GROUP",
    "LoadResult",
    "PluginRecord",
    "PluginSource",
    "discover_plugins",
    "load_plugin",
    "load_plugins",
    "plugin_id_of",
    "run_command",
]

#: Pip entry-point group third-party plugins advertise in.
ENTRY_POINT_GROUP = "puspa.plugins"

#: Default user plugin directory (overridden by PUSPA_PLUGIN_DIR).
DEFAULT_USER_PLUGIN_DIR = Path.home() / ".puspa" / "plugins"

#: Default project plugin directory (overridden by
#: PUSPA_PROJECT_PLUGIN_DIR).
DEFAULT_PROJECT_PLUGIN_DIR = Path("plugins")


@dataclass(frozen=True)
class PluginSource:
    """One discovered plugin, before loading."""

    #: Origin: "user", "project", or "entry_point".
    origin: str
    #: Human-readable location (path or entry-point value).
    location: str
    #: Dotted module path (entry points), or None for file sources.
    module_name: str | None = None
    #: Filesystem path (file sources), or None for entry points.
    path: Path | None = None


@dataclass
class PluginRecord:
    """One loaded plugin: what it registered, and where it came from."""

    plugin_id: str
    origin: str
    location: str
    module_name: str
    context: PluginContext
    single_select: SingleSelectRegistry
    #: API version the plugin declared (or the host's, when it
    #: declared none), as "MAJOR.MINOR".
    api_version: str = API_VERSION
    #: Permissions the plugin declared, in declaration order
    #: (empty when it declared none).
    permissions: tuple = ()

    @property
    def tools(self):
        """Registered tools, by name."""
        return self.context.tools

    @property
    def hooks(self):
        """Registered hooks, in registration order."""
        return self.context.hooks

    @property
    def commands(self):
        """Registered commands, by name."""
        return self.context.commands

    @property
    def single_select_names(self):
        """Single-select registrations as (kind, name) pairs."""
        return self.context.single_select_names

    def summary(self) -> dict:
        """A plain summary of what this plugin registered."""
        return {
            "plugin_id": self.plugin_id,
            "origin": self.origin,
            "location": self.location,
            "api_version": self.api_version,
            "permissions": list(self.permissions),
            "tools": sorted(self.tools),
            "hooks": [hook.event for hook in self.hooks],
            "commands": sorted(self.commands),
            "single_select": [
                {"kind": kind, "name": name} for kind, name in self.single_select_names
            ],
        }


@dataclass
class LoadResult:
    """Outcome of ``load_plugins``: records plus per-source failures."""

    records: list[PluginRecord] = field(default_factory=list)
    failures: list[tuple[PluginSource, Exception]] = field(default_factory=list)
    single_select: SingleSelectRegistry = field(default_factory=SingleSelectRegistry)

    @property
    def loaded_ids(self) -> list[str]:
        """Plugin ids that loaded successfully, in load order."""
        return [record.plugin_id for record in self.records]

    def raise_if_failed(self):
        """Raise the first load failure, if any."""
        if self.failures:
            source, exc = self.failures[0]
            raise PluginError(
                f"failed to load plugin from {source.origin} ({source.location}): {exc}"
            ) from exc

    def get(self, plugin_id) -> PluginRecord:
        """Return the record for ``plugin_id`` (KeyError if absent)."""
        for record in self.records:
            if record.plugin_id == plugin_id:
                return record
        raise UnknownPluginError(f"plugin {plugin_id!r} is not loaded")


def plugin_id_of(module: ModuleType) -> str:
    """A module's plugin id: ``PLUGIN_ID`` when set, else its name."""
    plugin_id = getattr(module, "PLUGIN_ID", None)
    if isinstance(plugin_id, str) and plugin_id.strip():
        return plugin_id.strip()
    return module.__name__


# ---------------------------------------------------------------- discovery


def discover_plugins(
    *, user_dir=None, project_dir=None, entry_points=True, extra_dirs=None
) -> list[PluginSource]:
    """Discover plugins from the three sources, in order.

    ``user_dir`` / ``project_dir`` are directories to scan (a
    ``.py`` file or a package directory with ``__init__.py``
    counts as one plugin). Defaults come from
    ``PUSPA_PLUGIN_DIR`` / ``PUSPA_PROJECT_PLUGIN_DIR`` or the
    built-in defaults. ``extra_dirs`` are additional directories
    scanned as project plugins (used by the chassis factory for
    explicitly configured plugin directories). Entry points are
    read from the ``puspa.plugins`` group. Missing directories
    are skipped silently; a directory that exists but is
    unreadable raises ``PluginError``.
    """
    sources: list[PluginSource] = []
    user = _resolve_dir(user_dir, "PUSPA_PLUGIN_DIR", DEFAULT_USER_PLUGIN_DIR)
    project = _resolve_dir(project_dir, "PUSPA_PROJECT_PLUGIN_DIR", DEFAULT_PROJECT_PLUGIN_DIR)
    sources.extend(_scan_dir(user, "user"))
    sources.extend(_scan_dir(project, "project"))
    for extra in extra_dirs or []:
        sources.extend(_scan_dir(Path(extra), "project"))
    if entry_points:
        sources.extend(_scan_entry_points())
    return sources


def _resolve_dir(explicit, env_name, default):
    if explicit is not None:
        return Path(explicit)
    env_value = os.environ.get(env_name)
    if env_value:
        return Path(env_value)
    return Path(default)


def _scan_dir(directory: Path, origin: str) -> list[PluginSource]:
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise PluginError(f"plugin directory {directory} is not a directory")
    found: list[PluginSource] = []
    for child in sorted(directory.iterdir()):
        if child.name.startswith((".", "_")):
            continue
        if (child.is_file() and child.suffix == ".py") or (
            child.is_dir() and (child / "__init__.py").is_file()
        ):
            found.append(
                PluginSource(
                    origin=origin,
                    location=str(child),
                    path=child,
                )
            )
    return found


def _scan_entry_points() -> list[PluginSource]:
    try:
        points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        return []
    return [
        PluginSource(
            origin="entry_point",
            location=f"{point.name} ({point.value})",
            module_name=point.value,
        )
        for point in sorted(points, key=lambda p: p.name)
    ]


# ---------------------------------------------------------------- loading


def _import_file(path: Path) -> ModuleType:
    """Import a plugin from a ``.py`` file or a package directory."""
    if path.is_dir():
        init = path / "__init__.py"
        module_name = f"_puspa_plugin_{path.name}"
        spec = importlib.util.spec_from_file_location(module_name, init)
    else:
        module_name = f"_puspa_plugin_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PluginStructureError(f"cannot import plugin file {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise PluginStructureError(f"plugin file {path} failed to import: {exc}") from exc
    return module


def _import_module(module_name: str) -> ModuleType:
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        raise PluginStructureError(
            f"plugin module {module_name!r} failed to import: {exc}"
        ) from exc


def _call_register(module: ModuleType, context: PluginContext):
    register = getattr(module, "register", None)
    if register is None:
        raise PluginStructureError(f"plugin {plugin_id_of(module)!r} has no register(ctx) function")
    if not callable(register):
        raise PluginStructureError(f"plugin {plugin_id_of(module)!r}: register is not callable")
    try:
        sig = inspect.signature(register)
    except (TypeError, ValueError):
        sig = None
    if sig is not None:
        params = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        has_var_positional = any(
            p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values()
        )
        if not has_var_positional and len(params) != 1:
            raise PluginStructureError(
                f"plugin {plugin_id_of(module)!r}: register(ctx) must take "
                f"exactly one argument, got {len(params)}"
            )
    try:
        register(context)
    except PluginError:
        raise
    except Exception as exc:
        raise PluginStructureError(
            f"plugin {plugin_id_of(module)!r}: register(ctx) raised: {exc}"
        ) from exc
    problems = context.validate_handler_signatures()
    if problems:
        raise PluginStructureError(f"plugin {plugin_id_of(module)!r}: " + "; ".join(problems))


def load_plugin(source, *, single_select=None, permissions=None) -> PluginRecord:
    """Load one plugin and return its record.

    ``source`` is a module object, a dotted module path, a
    ``PluginSource``, or a filesystem path to a ``.py`` file or a
    package directory. ``single_select`` is the shared
    ``SingleSelectRegistry`` (a fresh one is created when omitted,
    so single-select registrations never leak between loads).
    ``permissions`` is the plugin's declared permission list
    (e.g. from its catalog manifest); when omitted, the module's
    ``PLUGIN_PERMISSIONS`` attribute is used instead, and when
    neither is present no permission check runs.

    Before ``register(ctx)`` runs, the plugin's declared
    ``PLUGIN_API_VERSION`` is checked against the host's API:
    a different major, or a minor newer than the host's, raises
    ``PluginApiVersionError`` and the plugin is refused. After
    ``register`` runs, what the plugin registered is checked
    against its declared permissions (``PluginStructureError``
    when it used a permission it did not declare).
    Raises ``PluginStructureError`` for malformed plugins and
    ``PluginShadowError`` when the plugin id is already loaded
    in this call (callers that load several plugins must pass
    one shared ``single_select`` and check ids via
    ``load_plugins`` instead).
    """
    registry = single_select if single_select is not None else SingleSelectRegistry()
    if isinstance(source, ModuleType):
        module = source
        origin, location = "module", module.__name__
    elif isinstance(source, PluginSource):
        origin, location = source.origin, source.location
        if source.path is not None:
            module = _import_file(source.path)
        elif source.module_name is not None:
            module = _import_module(source.module_name)
        else:
            raise PluginStructureError(
                f"plugin source {location!r} has neither a path nor a module"
            )
    elif isinstance(source, (str, Path)) and Path(source).exists():
        module = _import_file(Path(source))
        origin, location = "file", str(source)
    elif isinstance(source, str):
        module = _import_module(source)
        origin, location = "module", source
    else:
        raise PluginStructureError(f"cannot load plugin from {source!r}")
    plugin_id = plugin_id_of(module)
    api_version = resolve_api_version(module, plugin_id=plugin_id)
    declared_permissions = permissions
    if declared_permissions is None:
        declared_permissions = getattr(module, PLUGIN_PERMISSIONS_ATTR, None)
    if declared_permissions is not None:
        problems = validate_permissions(declared_permissions)
        if problems:
            raise PluginStructureError(f"plugin {plugin_id!r}: " + "; ".join(problems))
    context = PluginContext(plugin_id, single_select=registry)
    _call_register(module, context)
    check_permissions(declared_permissions, used_permissions(context), plugin_id=plugin_id)
    return PluginRecord(
        plugin_id=plugin_id,
        origin=origin,
        location=location,
        module_name=module.__name__,
        context=context,
        single_select=registry,
        api_version=str(api_version),
        permissions=tuple(declared_permissions or ()),
    )


def load_plugins(
    sources=None, *, user_dir=None, project_dir=None, entry_points=True, extra_dirs=None
) -> LoadResult:
    """Discover (when ``sources`` is None) and load plugins.

    Returns a ``LoadResult`` with the loaded records and one
    failure entry per source that failed. A plugin id loaded
    twice (same id from a later source) is a failure entry with
    ``PluginShadowError``: the first registration wins and the
    later source never silently replaces it.
    """
    registry = SingleSelectRegistry()
    result = LoadResult(single_select=registry)
    if sources is None:
        sources = discover_plugins(
            user_dir=user_dir,
            project_dir=project_dir,
            entry_points=entry_points,
            extra_dirs=extra_dirs,
        )
    seen_ids: set[str] = set()
    for source in sources:
        try:
            record = load_plugin(source, single_select=registry)
        except Exception as exc:
            result.failures.append((_as_source(source), exc))
            continue
        if record.plugin_id in seen_ids:
            result.failures.append(
                (
                    _as_source(source),
                    PluginShadowError(
                        f"plugin {record.plugin_id!r} is already loaded; shadowing is rejected"
                    ),
                )
            )
            continue
        seen_ids.add(record.plugin_id)
        result.records.append(record)
    return result


def _as_source(source) -> PluginSource:
    """Normalize any load input to a ``PluginSource`` for reports."""
    if isinstance(source, PluginSource):
        return source
    if isinstance(source, ModuleType):
        return PluginSource(origin="module", location=source.__name__, module_name=source.__name__)
    if isinstance(source, (str, Path)):
        return PluginSource(origin="module", location=str(source))
    return PluginSource(origin="unknown", location=str(source))


def run_command(records, name, argv=None) -> int:
    """Run a plugin CLI command by name across loaded records.

    Returns the command handler's int exit code. Raises
    ``UnknownPluginError`` when no loaded plugin registered
    ``name``. When two plugins registered the same command name,
    the first loaded wins (load order is the discovery order).
    """
    for record in records:
        command = record.commands.get(name)
        if command is not None:
            result = command.handler(list(argv or []))
            if not isinstance(result, int):
                raise PluginStructureError(
                    f"command {name!r} from plugin {record.plugin_id!r} "
                    f"must return an int exit code, got {type(result).__name__}"
                )
            return result
    raise UnknownPluginError(f"no loaded plugin registered command {name!r}")
