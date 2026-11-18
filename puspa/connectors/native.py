"""Native one-click connector layer (G10, layer A).

A curated directory of approved connectors plus their operation
contracts. This is the native layer: each connector is a definition
(id, display name, auth kind, scopes, operations with request and
response contracts), not a live client. A definition says what a
connector *can* do; execution lives behind the live-execution gate
in :mod:`puspa.connectors.wedges` and is OFF by default.

The directory is curated: only definitions registered here are
offered to the user. Registration rejects duplicate ids, bad slugs,
and operations with duplicate names or empty contracts, so a broken
definition can never reach the product surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "AUTH_KINDS",
    "ConnectorDefinition",
    "Operation",
    "CuratedDirectory",
    "DirectoryError",
]

#: Auth kinds a connector definition may declare.
AUTH_KINDS = ("oauth2", "api_key", "credentials", "none")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class DirectoryError(ValueError):
    """A connector definition or directory operation is invalid."""


@dataclass(frozen=True)
class Operation:
    """One operation a connector supports, with its contract.

    ``request_schema`` and ``response_schema`` map field names to a
    short type label (``"string"``, ``"integer"``, ``"number"``,
    ``"boolean"``, ``"object"``, ``"array"``). They are contracts
    for validation and documentation, not a runtime validator.
    ``requires_approval`` marks operations with a side effect
    (sending, paying, buying) that must pass the approval gate
    before they run. ``live`` marks operations that need live
    execution (network to the vendor); non-live operations may be
    served by a fake provider for tests and demos.
    """

    name: str
    description: str
    request_schema: dict
    response_schema: dict
    requires_approval: bool = False
    live: bool = True


@dataclass(frozen=True)
class ConnectorDefinition:
    """One curated connector: identity, auth, scopes, operations."""

    connector_id: str
    display_name: str
    auth_kind: str
    scopes: tuple = ()
    operations: tuple = ()
    description: str = ""
    layer: str = "native"

    def operation(self, name: str) -> Operation:
        """Return the operation named ``name`` (KeyError if absent)."""
        for op in self.operations:
            if op.name == name:
                return op
        raise KeyError(f"connector {self.connector_id!r} has no operation {name!r}")

    def operation_names(self) -> list[str]:
        """Operation names, in definition order."""
        return [op.name for op in self.operations]


def _validate_definition(definition: ConnectorDefinition) -> list[str]:
    problems = []
    if not _ID_RE.match(definition.connector_id or ""):
        problems.append(
            f"connector_id {definition.connector_id!r} must be a lowercase slug"
        )
    if not isinstance(definition.display_name, str) or not definition.display_name.strip():
        problems.append("display_name must be a non-empty string")
    if definition.auth_kind not in AUTH_KINDS:
        problems.append(
            f"auth_kind {definition.auth_kind!r} must be one of "
            f"{', '.join(AUTH_KINDS)}"
        )
    if not isinstance(definition.scopes, (list, tuple)) or not all(
        isinstance(scope, str) and scope for scope in definition.scopes
    ):
        problems.append("scopes must be a list of non-empty strings")
    if not definition.operations:
        problems.append("a connector needs at least one operation")
    seen: set[str] = set()
    for op in definition.operations or ():
        if not _NAME_RE.match(op.name or ""):
            problems.append(f"operation name {op.name!r} must be snake_case")
            continue
        if op.name in seen:
            problems.append(f"duplicate operation name: {op.name!r}")
        seen.add(op.name)
        if not isinstance(op.description, str) or not op.description.strip():
            problems.append(f"operation {op.name!r} needs a description")
        if not isinstance(op.request_schema, dict) or not op.request_schema:
            problems.append(f"operation {op.name!r} needs a request_schema")
        if not isinstance(op.response_schema, dict) or not op.response_schema:
            problems.append(f"operation {op.name!r} needs a response_schema")
    return problems


class CuratedDirectory:
    """The curated connector directory: approved definitions only.

    Definitions are registered explicitly; nothing is discovered
    implicitly, so a connector reaches users only after a human
    curates it in. The first registration of an id wins; a second
    one raises instead of silently replacing it.
    """

    def __init__(self):
        self._definitions: dict[str, ConnectorDefinition] = {}

    def register(self, definition: ConnectorDefinition) -> ConnectorDefinition:
        """Register one definition after validation."""
        problems = _validate_definition(definition)
        if problems:
            raise DirectoryError("; ".join(problems))
        if definition.connector_id in self._definitions:
            raise DirectoryError(
                f"connector {definition.connector_id!r} is already registered; "
                "shadowing is rejected"
            )
        self._definitions[definition.connector_id] = definition
        return definition

    def get(self, connector_id: str) -> ConnectorDefinition:
        """Return the definition for ``connector_id`` (KeyError if absent)."""
        return self._definitions[connector_id]

    def __contains__(self, connector_id: str) -> bool:
        return connector_id in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)

    def list(self, *, layer: str | None = None) -> list[ConnectorDefinition]:
        """Definitions in registration order, optionally by layer."""
        definitions = list(self._definitions.values())
        if layer is not None:
            definitions = [d for d in definitions if d.layer == layer]
        return definitions

    def list_ids(self, *, layer: str | None = None) -> list[str]:
        """Connector ids in registration order, optionally by layer."""
        return [d.connector_id for d in self.list(layer=layer)]

    def tools(self, connector_id: str) -> list[dict]:
        """Engine-facing tool descriptors for one connector.

        Each tool is ``{"name", "description", "service"}`` where
        ``service`` is the connector id, so the allowlist can filter
        by service slug. No credentials, tokens, or endpoints are
        included: the engine never sees them.
        """
        definition = self.get(connector_id)
        return [
            {
                "name": op.name,
                "description": op.description,
                "service": definition.connector_id,
            }
            for op in definition.operations
        ]

    def all_tools(self) -> list[dict]:
        """Engine-facing tool descriptors for every connector."""
        tools: list[dict] = []
        for definition in self.list():
            tools.extend(self.tools(definition.connector_id))
        return tools
