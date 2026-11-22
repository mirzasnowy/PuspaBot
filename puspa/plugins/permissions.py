"""Plugin permissions (AST-01, the stable plugin SDK).

A plugin declares what it needs in its catalog manifest
(``permissions``) or on the module (``PLUGIN_PERMISSIONS``).
The declaration is checked in two places:

- **Validation**: the names must be known (this module's
  :data:`KNOWN_PERMISSIONS`); an unknown name fails manifest
  admission or plugin load with a clear error.
- **Enforcement**: after ``register(ctx)`` runs, the loader
  compares what the plugin actually registered against what
  it declared. A plugin that registers a tool without
  declaring the ``tools`` permission is refused — the
  declaration is a promise the loader holds it to, not a
  comment.

Two permissions are declarations of intent in API v1, not
things the loader can block yet (a plugin runs in-process,
so it could open a socket or a file without asking):

- ``network`` — the plugin makes network requests.
- ``filesystem`` — the plugin reads or writes files outside
  its own directory.

They are recorded on the load record and shown to the
operator, so a plugin's network/file use is visible even
before the sandbox enforces it in a later API major. The
SDK docs (``docs/plugin-sdk.md``) say this plainly; a
permission that is only declared is never presented as
enforced.
"""

from __future__ import annotations

from .errors import PluginStructureError

__all__ = [
    "DECLARED_ONLY_PERMISSIONS",
    "ENFORCED_PERMISSIONS",
    "KNOWN_PERMISSIONS",
    "PLUGIN_PERMISSIONS_ATTR",
    "check_permissions",
    "used_permissions",
    "validate_permissions",
]

#: Permission names a plugin may declare.
KNOWN_PERMISSIONS = (
    "tools",
    "hooks",
    "commands",
    "memory_provider",
    "context_engine",
    "network",
    "filesystem",
)

#: Permissions the loader enforces at load time (the five
#: registration kinds: what a plugin registered must be a
#: subset of what it declared).
ENFORCED_PERMISSIONS = (
    "tools",
    "hooks",
    "commands",
    "memory_provider",
    "context_engine",
)

#: Permissions that are recorded and shown, not blocked, in
#: API v1 (see the module docstring).
DECLARED_ONLY_PERMISSIONS = ("network", "filesystem")

#: Module attribute a plugin sets to declare its permissions,
#: e.g. ``PLUGIN_PERMISSIONS = ["tools", "hooks"]``.
PLUGIN_PERMISSIONS_ATTR = "PLUGIN_PERMISSIONS"


def validate_permissions(value) -> list[str]:
    """Validate a declared permissions value; return problems.

    The value must be a list of known permission names (a
    duplicate-free list; an empty list means "needs nothing").
    """
    problems: list[str] = []
    if not isinstance(value, list):
        return [f"permissions must be a list of permission names, got {value!r}"]
    for item in value:
        if not isinstance(item, str):
            problems.append(f"permissions: {item!r} is not a permission name")
        elif item not in KNOWN_PERMISSIONS:
            problems.append(
                f"permissions: unknown permission {item!r} (known: {', '.join(KNOWN_PERMISSIONS)})"
            )
    if len(set(value)) != len(value):
        problems.append("permissions must not contain duplicates")
    return problems


def used_permissions(context) -> frozenset:
    """The enforced permissions a plugin actually used.

    Derived from what it registered on its ``PluginContext``:
    tools, hooks, commands, and single-select providers.
    """
    used: set[str] = set()
    if context.tools:
        used.add("tools")
    if context.hooks:
        used.add("hooks")
    if context.commands:
        used.add("commands")
    for kind, _name in context.single_select_names:
        used.add(kind)
    return frozenset(used)


def check_permissions(declared, used, *, plugin_id: str) -> None:
    """Refuse a plugin that used a permission it did not declare.

    ``declared`` is the plugin's declared permission list (or
    None when it declared none); ``used`` is the set from
    :func:`used_permissions`. Only enforced permissions are
    checked — ``network`` / ``filesystem`` are declarations
    of intent, not blockable at load time (see the module
    docstring). Raises ``PluginStructureError`` naming the
    plugin and the undeclared permissions.
    """
    if declared is None:
        return
    declared_set = set(declared)
    undeclared = sorted((set(used) & set(ENFORCED_PERMISSIONS)) - declared_set)
    if undeclared:
        raise PluginStructureError(
            f"plugin {plugin_id!r} used permission(s) it did not declare: "
            f"{', '.join(undeclared)} — add them to PLUGIN_PERMISSIONS "
            "(or to 'permissions' in the plugin manifest)"
        )
