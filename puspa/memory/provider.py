"""Pluggable memory providers (G24).

The three Puspa memory layers (profile facts, episodic vector
recall, procedures) already exist on Postgres + pgvector. This
module puts a single-select provider plug-in on top of them,
rewritten from the hermes-agent pattern (MIT, adapted, not
copied):

- ``MemoryProvider`` is the ABC every provider implements:
  ``store`` / ``query`` over one shared record shape.
- The existing three layers become ONE provider,
  ``DefaultPgVectorProvider``. Nothing about the layers changes;
  the provider only routes a record to the layer it belongs to.
- Selection is single-select with an explicit registry. Sources
  are consulted in a fixed order (bundled -> user -> project ->
  entry point, simplified below); the first registration of a
  name wins and any later registration of the same name is
  rejected (``ProviderShadowError``), so a memory provider can
  never be silently swapped underneath the agent.
- Every provider carries its own config schema (a plain dict,
  not a full ``plugin.yaml`` — see the simplification note in
  ``select_provider``).

Shared record shape (the interface shared with the G7 facts
worker): a dict with ``id``, ``content``, ``status`` (``"active"``
| ``"superseded"`` | ``"expired"``), ``valid_from``,
``valid_until``, ``superseded_by``, ``source``, ``created_at``,
``updated_at``. Providers may add extra keys (``layer``,
``key``, ``service``, ``step``, ``event_type``, ``layer_status``);
callers must not depend on them.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import ClassVar

__all__ = [
    "DEFAULT_PROVIDER_NAME",
    "ENV_PROVIDER_NAME",
    "SHARED_RECORD_KEYS",
    "DefaultPgVectorProvider",
    "MemoryProvider",
    "ProviderConfigError",
    "ProviderError",
    "ProviderRegistry",
    "ProviderShadowError",
    "UnknownProviderError",
    "default_registry",
    "get_memory_provider",
    "select_provider",
    "validate_config",
]

#: Name of the bundled provider.
DEFAULT_PROVIDER_NAME = "pgvector"

#: Environment variable that names the provider to select.
ENV_PROVIDER_NAME = "PUSPA_MEMORY_PROVIDER"

#: Keys of the shared record shape (see the module docstring).
SHARED_RECORD_KEYS = frozenset(
    {
        "id",
        "content",
        "status",
        "valid_from",
        "valid_until",
        "superseded_by",
        "source",
        "created_at",
        "updated_at",
    }
)

#: Record statuses in the shared shape.
RECORD_STATUSES = ("active", "superseded", "expired")

#: Layers a record can be routed to.
LAYERS = ("profile", "episodic", "procedural")


class ProviderError(Exception):
    """Base error for provider registration, selection, and use."""


class ProviderShadowError(ProviderError):
    """A provider name was registered twice.

    Shadowing is rejected: the first registration wins, so a
    later source can never silently replace the memory provider
    an earlier source chose.
    """


class UnknownProviderError(ProviderError):
    """The requested provider name is not in the registry."""


class ProviderConfigError(ProviderError):
    """A provider config fails its own config schema."""


def validate_config(schema, config):
    """Validate ``config`` against a provider's ``config_schema``.

    ``schema`` maps a key to ``{"type": ..., "required": bool,
    "default": ...}``. ``"type"`` is one of ``"string"``,
    ``"integer"``, ``"boolean"``, ``"object"``, or ``"any"``.
    Returns a new dict with defaults filled in. Raises
    ``ProviderConfigError`` on an unknown key, a missing required
    key, or a wrong type. ``config`` may be None (all defaults).
    A ``None`` value always passes the type check: it means
    "not set", and the provider falls back to its own default.
    """
    schema = schema or {}
    config = dict(config or {})
    unknown = sorted(set(config) - set(schema))
    if unknown:
        raise ProviderConfigError(f"unknown config keys: {', '.join(unknown)}")
    out = {}
    for key, spec in schema.items():
        if key in config:
            value = config[key]
        elif spec.get("required"):
            raise ProviderConfigError(f"missing required config key: {key}")
        elif "default" in spec:
            value = spec["default"]
        else:
            continue
        expected = spec.get("type", "any")
        if value is not None and expected != "any" and not _matches_type(value, expected):
            raise ProviderConfigError(
                f"config key {key!r} must be {expected}, got {type(value).__name__}"
            )
        out[key] = value
    return out


def _matches_type(value, expected):
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    return True


class MemoryProvider(ABC):
    """Abstract memory provider: one store, one query.

    ``store`` takes a shared-shape record dict and returns the
    stored record (same shape, with the store-assigned ``id``
    when the record had none). ``query`` takes free text and
    returns at most ``limit`` shared-shape records, best first.
    Both take ``user_id`` first so providers never mix tenants.
    """

    #: Registry name of this provider.
    name = "base"

    #: Config schema for this provider (see ``validate_config``).
    config_schema: ClassVar[dict] = {}

    def __init__(self, config=None):
        self.config = validate_config(self.config_schema, config)

    @abstractmethod
    def store(self, user_id, record):
        """Store one record; return the stored record dict."""

    @abstractmethod
    def query(self, user_id, query_text, *, limit=5, layer=None):
        """Find records matching free text; return a list of dicts."""

    def close(self):
        """Release provider resources (no-op by default)."""
        return


class ProviderRegistry:
    """Explicit registry of memory providers.

    Registration order is the discovery order: bundled providers
    first, then user, then project, then entry-point providers.
    The first registration of a name wins; a second one raises
    ``ProviderShadowError`` instead of silently replacing it.
    """

    def __init__(self):
        self._providers = {}

    def register(self, provider_cls):
        """Register a provider class under its ``name``.

        Raises ``ProviderShadowError`` when the name is already
        registered, and ``ProviderError`` when the class is not a
        ``MemoryProvider`` subclass or has no usable name.
        """
        if not (isinstance(provider_cls, type) and issubclass(provider_cls, MemoryProvider)):
            raise ProviderError("provider must be a MemoryProvider subclass")
        name = getattr(provider_cls, "name", None)
        if not isinstance(name, str) or not name:
            raise ProviderError("provider must define a non-empty name")
        if name in self._providers:
            raise ProviderShadowError(
                f"provider {name!r} is already registered; shadowing is rejected"
            )
        self._providers[name] = provider_cls
        return provider_cls

    def get(self, name):
        """Return the provider class for ``name`` (KeyError if absent)."""
        return self._providers[name]

    def names(self):
        """Registered provider names, in registration order."""
        return list(self._providers)

    def __contains__(self, name):
        return name in self._providers

    def __len__(self):
        return len(self._providers)


def default_registry():
    """Registry with the bundled providers only (``pgvector``)."""
    registry = ProviderRegistry()
    registry.register(DefaultPgVectorProvider)
    return registry


_DEFAULT_REGISTRY = None


def _global_registry():
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = default_registry()
    return _DEFAULT_REGISTRY


def select_provider(name=None, config=None, registry=None):
    """Select exactly one provider and instantiate it.

    ``name`` wins; otherwise the ``PUSPA_MEMORY_PROVIDER``
    environment variable; otherwise ``"pgvector"``. ``config`` is
    validated against the provider's own ``config_schema``.
    Raises ``UnknownProviderError`` for an unregistered name.

    Simplification (documented, versus hermes-agent): there is no
    ``plugin.yaml``, no ``config_schema.py`` file, and no pip
    entry-point scan. Discovery is this registry dict plus the
    env var above; a user or project provider is added by
    calling ``registry.register(...)`` before selection, in the
    bundled -> user -> project -> entry-point order. Entry-point
    scanning is a follow-up if a third-party provider ever
    needs to self-register.
    """
    registry = registry if registry is not None else _global_registry()
    wanted = name or os.environ.get(ENV_PROVIDER_NAME) or DEFAULT_PROVIDER_NAME
    if wanted not in registry:
        raise UnknownProviderError(
            f"unknown memory provider {wanted!r}; "
            f"registered: {', '.join(registry.names()) or '(none)'}"
        )
    provider_cls = registry.get(wanted)
    return provider_cls(config=config)


def get_memory_provider(name=None, config=None):
    """Convenience wrapper over ``select_provider`` (global registry)."""
    return select_provider(name=name, config=config)


def _now():
    return datetime.now(UTC)


def _shared_record(
    *,
    record_id,
    content,
    status="active",
    valid_from=None,
    valid_until=None,
    superseded_by=None,
    source="episodic",
    created_at=None,
    updated_at=None,
    **extra,
):
    """Build a shared-shape record dict (extra keys kept as-is)."""
    now = _now()
    record = {
        "id": str(record_id),
        "content": content,
        "status": status,
        "valid_from": valid_from or now,
        "valid_until": valid_until,
        "superseded_by": superseded_by,
        "source": source,
        "created_at": created_at or now,
        "updated_at": updated_at or now,
    }
    record.update(extra)
    return record


def _as_uuid(value):
    """A value as a UUID string, or None when it is not one."""
    if value is None:
        return None
    import uuid

    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


class DefaultPgVectorProvider(MemoryProvider):
    """The existing three Puspa memory layers as one provider.

    Routing by ``record["layer"]`` (default ``"episodic"``):

    - ``"episodic"``: the record becomes one episode
      (``puspa.memory.episodic.record_episode``) and queries run
      pgvector search (``search_episodes``).
    - ``"profile"``: the record needs a ``"key"`` and becomes a
      profile fact (``puspa.memory.profile.add_fact``). Only
      live facts belong here: status ``"active"`` (or
      ``"proposed"`` for an inferred fact, which the profile
      layer keeps as a proposal); ``"superseded"`` / ``"expired"``
      raise ``ValueError`` — history lives in the episodic layer,
      never as a live profile fact. Source maps to the profile
      layer's own values (``user_stated`` / ``user_confirmed`` /
      ``inferred``; anything else becomes ``"inferred"``).
    - ``"procedural"``: the record needs ``"service"`` and a
      ``"step"`` list and becomes a trial procedure
      (``puspa.memory.procedural.record_successful_task``).

    Config schema: ``dsn`` (optional Postgres DSN string, passed
    through to the layer functions) and ``default_layer``
    (optional, one of ``LAYERS``).
    """

    name = "pgvector"

    config_schema: ClassVar[dict] = {
        "dsn": {"type": "string", "required": False, "default": None},
        "default_layer": {
            "type": "string",
            "required": False,
            "default": "episodic",
        },
    }

    def __init__(self, config=None, guest_mode: bool = False):
        super().__init__(config=config)
        layer = self.config.get("default_layer") or "episodic"
        if layer not in LAYERS:
            raise ProviderConfigError(
                f"default_layer must be one of {', '.join(LAYERS)}, got {layer!r}"
            )
        self._default_layer = layer
        self._dsn = self.config.get("dsn")
        # Guest mode (P-01): a turn that saves no memory. While on,
        # ``store`` refuses every write and reports it, so the turn
        # leaves no episode, fact, or procedure behind. Reads
        # (``query``) are unaffected: a guest turn may still use
        # what the user already stored, it just adds nothing.
        self._guest_mode = bool(guest_mode)

    @property
    def guest_mode(self) -> bool:
        """Whether this provider currently refuses memory writes."""
        return self._guest_mode

    def set_guest_mode(self, guest_mode: bool) -> None:
        """Turn guest mode on or off for this provider instance."""
        self._guest_mode = bool(guest_mode)

    # -- store ---------------------------------------------------------

    def store(self, user_id, record):
        """Route one record to its layer; return the stored record.

        In guest mode nothing is written: the return value carries
        ``"stored": False`` and ``"guest_mode": True`` instead of a
        stored record, so a caller can tell the turn was not saved.
        """
        from . import episodic, procedural, profile

        if self._guest_mode:
            return {
                "stored": False,
                "guest_mode": True,
                "user_id": str(user_id),
                "content": record.get("content") if isinstance(record, dict) else None,
                "layer": record.get("layer") if isinstance(record, dict) else None,
            }
        if not isinstance(record, dict):
            raise ValueError("record must be a dict")
        content = record.get("content")
        if not isinstance(content, str) or not content:
            raise ValueError("record needs a non-empty string content")
        status = record.get("status", "active")
        if status not in RECORD_STATUSES:
            raise ValueError(
                f"record status must be one of {', '.join(RECORD_STATUSES)}, got {status!r}"
            )
        layer = record.get("layer") or self._default_layer
        if layer not in LAYERS:
            raise ValueError(f"layer must be one of {', '.join(LAYERS)}, got {layer!r}")
        if layer == "profile":
            return self._store_profile(user_id, record, profile)
        if layer == "procedural":
            return self._store_procedural(user_id, record, procedural)
        return self._store_episodic(user_id, record, episodic)

    def _store_episodic(self, user_id, record, episodic):
        episode = episodic.record_episode(
            user_id,
            record["content"],
            event_type=record.get("event_type") or "memory",
            agent_id=record.get("agent_id"),
            dsn=self._dsn,
        )
        return _shared_record(
            record_id=episode["id"],
            content=episode["content"],
            status=record.get("status", "active"),
            valid_from=record.get("valid_from"),
            valid_until=record.get("valid_until"),
            superseded_by=record.get("superseded_by"),
            source=record.get("source") or "episodic",
            created_at=episode.get("created_at"),
            updated_at=episode.get("created_at"),
            user_id=episode["user_id"],
            layer="episodic",
            event_type=episode["type"],
        )

    def _store_profile(self, user_id, record, profile):
        status = record.get("status", "active")
        if status in ("superseded", "expired"):
            raise ValueError(
                f"profile layer stores live facts only; cannot store status {status!r}"
            )
        key = record.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("profile records need a non-empty 'key'")
        source = record.get("source") or "inferred"
        if source not in ("user_stated", "user_confirmed", "inferred"):
            source = "inferred"
        fact = profile.add_fact(
            user_id,
            key,
            record["content"],
            source,
            agent_id=record.get("agent_id"),
            event_id=_as_uuid(record.get("id")),
            dsn=self._dsn,
        )
        stored = dict(fact)
        stored.pop("key", None)
        return _shared_record(
            record_id=stored["id"],
            content=stored["value"],
            status="active",
            valid_from=stored.get("valid_from"),
            valid_until=stored.get("valid_until"),
            superseded_by=None,
            source=stored["source"],
            created_at=stored.get("created_at"),
            updated_at=stored.get("created_at"),
            user_id=stored["user_id"],
            layer="profile",
            key=key,
            layer_status=stored["status"],
            conflicts_with=stored.get("conflicts_with"),
        )

    def _store_procedural(self, user_id, record, procedural):
        service = record.get("service")
        step = record.get("step")
        if not isinstance(service, str) or not service:
            raise ValueError("procedural records need a 'service'")
        if not isinstance(step, list) or not step:
            raise ValueError("procedural records need a non-empty 'step' list")
        proc = procedural.record_successful_task(
            user_id,
            service,
            step,
            agent_id=record.get("agent_id"),
            source_event_id=_as_uuid(record.get("id")),
            dsn=self._dsn,
        )
        return _shared_record(
            record_id=proc["id"],
            content=record["content"],
            status=record.get("status", "active"),
            valid_from=record.get("valid_from"),
            valid_until=record.get("valid_until"),
            superseded_by=record.get("superseded_by"),
            source=record.get("source") or "procedural",
            created_at=proc.get("created_at"),
            updated_at=proc.get("updated_at"),
            user_id=proc["user_id"],
            layer="procedural",
            service=proc["service"],
            layer_status=proc["status"],
        )

    # -- query ---------------------------------------------------------

    def query(self, user_id, query_text, *, limit=5, layer=None):
        """Search records by free text (best first, at most ``limit``).

        ``layer`` restricts the search (``"episodic"`` /
        ``"profile"`` / ``"procedural"``); with no layer the
        episodic vector search runs plus a substring match over
        profile facts, merged in that order.
        """
        from . import episodic, procedural, profile

        if not isinstance(query_text, str) or not query_text:
            raise ValueError("query_text must be a non-empty string")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if layer is not None and layer not in LAYERS:
            raise ValueError(f"layer must be one of {', '.join(LAYERS)}, got {layer!r}")
        results = []
        if layer in (None, "episodic"):
            results.extend(
                self._episode_to_record(ep)
                for ep in episodic.search_episodes(
                    user_id,
                    query_text,
                    limit=limit,
                    embedder=None,
                    dsn=self._dsn,
                )
            )
        if layer in (None, "profile"):
            needle = query_text.lower()
            for fact in profile.list_facts(user_id, dsn=self._dsn):
                if needle in fact["key"].lower() or needle in fact["value"].lower():
                    results.append(self._fact_to_record(fact))
        if layer == "procedural":
            needle = query_text.lower()
            for proc in procedural.list_procedures(user_id, dsn=self._dsn):
                if needle in proc["service"].lower():
                    results.append(self._procedure_to_record(proc))
        return results[:limit]

    @staticmethod
    def _episode_to_record(episode):
        return _shared_record(
            record_id=episode["id"],
            content=episode["content"],
            status="active",
            valid_from=episode.get("created_at"),
            valid_until=None,
            superseded_by=None,
            source="episodic",
            created_at=episode.get("created_at"),
            updated_at=episode.get("created_at"),
            user_id=episode["user_id"],
            layer="episodic",
            event_type=episode.get("type"),
            similarity=episode.get("similarity"),
        )

    @staticmethod
    def _fact_to_record(fact):
        status = fact["status"]
        shared_status = "superseded" if status in ("replaced", "rejected") else "active"
        return _shared_record(
            record_id=fact["id"],
            content=fact["value"],
            status=shared_status,
            valid_from=fact.get("valid_from"),
            valid_until=fact.get("valid_until"),
            superseded_by=None,
            source=fact["source"],
            created_at=fact.get("created_at"),
            updated_at=fact.get("created_at"),
            user_id=fact["user_id"],
            layer="profile",
            key=fact["key"],
            layer_status=status,
        )

    @staticmethod
    def _procedure_to_record(proc):
        return _shared_record(
            record_id=proc["id"],
            content="; ".join(str(s) for s in (proc.get("step") or [])),
            status="active",
            valid_from=proc.get("created_at"),
            valid_until=None,
            superseded_by=None,
            source="procedural",
            created_at=proc.get("created_at"),
            updated_at=proc.get("updated_at"),
            user_id=proc["user_id"],
            layer="procedural",
            service=proc["service"],
            layer_status=proc["status"],
        )
