"""Tests for the pluggable memory provider (G24).

Run with ``--noconftest``: the registry / selection / validation
tests are pure in-memory. The one Postgres round-trip test at the
bottom manages its own schema and cleans up its own rows, so it
needs no conftest fixture either (it needs a live ``puspa_test``
database, like every other DB test in this repo).
"""

import uuid
from typing import ClassVar

import pytest

from puspa.memory.provider import (
    DEFAULT_PROVIDER_NAME,
    DefaultPgVectorProvider,
    MemoryProvider,
    ProviderConfigError,
    ProviderError,
    ProviderRegistry,
    ProviderShadowError,
    UnknownProviderError,
    default_registry,
    select_provider,
    validate_config,
)


class _InMemoryProvider(MemoryProvider):
    """Tiny test provider: records live in a dict, no database."""

    name = "mem"
    config_schema: ClassVar[dict] = {
        "capacity": {"type": "integer", "required": False, "default": 10},
    }

    def __init__(self, config=None):
        super().__init__(config=config)
        self.records = {}

    def store(self, user_id, record):
        stored = dict(record)
        stored.setdefault("id", str(uuid.uuid4()))
        self.records.setdefault(str(user_id), []).append(stored)
        return stored

    def query(self, user_id, query_text, *, limit=5, layer=None):
        needle = query_text.lower()
        found = [
            record
            for record in self.records.get(str(user_id), [])
            if needle in str(record.get("content", "")).lower()
        ]
        return found[:limit]


class _OtherProvider(_InMemoryProvider):
    name = "other"


# --- registry ----------------------------------------------------------


def test_registry_registers_and_lists_in_order():
    registry = ProviderRegistry()
    registry.register(_InMemoryProvider)
    registry.register(_OtherProvider)
    assert registry.names() == ["mem", "other"]
    assert "mem" in registry
    assert len(registry) == 2
    assert registry.get("mem") is _InMemoryProvider


def test_registry_rejects_shadowing():
    registry = ProviderRegistry()
    registry.register(_InMemoryProvider)
    with pytest.raises(ProviderShadowError):
        registry.register(_InMemoryProvider)


def test_registry_rejects_non_provider():
    registry = ProviderRegistry()
    with pytest.raises(ProviderError):
        registry.register(object)


def test_default_registry_bundles_pgvector():
    registry = default_registry()
    assert registry.names() == [DEFAULT_PROVIDER_NAME]
    assert registry.get("pgvector") is DefaultPgVectorProvider


# --- selection ---------------------------------------------------------


def test_select_provider_by_name():
    registry = ProviderRegistry()
    registry.register(_InMemoryProvider)
    provider = select_provider("mem", registry=registry)
    assert isinstance(provider, _InMemoryProvider)
    assert provider.config == {"capacity": 10}


def test_select_provider_unknown_name():
    registry = ProviderRegistry()
    with pytest.raises(UnknownProviderError):
        select_provider("nope", registry=registry)


def test_select_provider_reads_env(monkeypatch):
    registry = ProviderRegistry()
    registry.register(_InMemoryProvider)
    registry.register(_OtherProvider)
    monkeypatch.setenv("PUSPA_MEMORY_PROVIDER", "other")
    assert isinstance(select_provider(registry=registry), _OtherProvider)


def test_select_provider_explicit_name_beats_env(monkeypatch):
    registry = ProviderRegistry()
    registry.register(_InMemoryProvider)
    registry.register(_OtherProvider)
    monkeypatch.setenv("PUSPA_MEMORY_PROVIDER", "other")
    assert isinstance(select_provider("mem", registry=registry), _InMemoryProvider)


def test_select_provider_defaults_to_pgvector():
    provider = select_provider()
    assert isinstance(provider, DefaultPgVectorProvider)


# --- config validation -------------------------------------------------


def test_validate_config_fills_defaults_and_rejects_unknown():
    schema = {
        "dsn": {"type": "string", "required": False, "default": None},
        "retries": {"type": "integer", "required": True},
    }
    assert validate_config(schema, {"retries": 2}) == {
        "dsn": None,
        "retries": 2,
    }
    with pytest.raises(ProviderConfigError):
        validate_config(schema, {"retries": 2, "bogus": 1})


def test_validate_config_rejects_missing_required_and_wrong_type():
    schema = {"retries": {"type": "integer", "required": True}}
    with pytest.raises(ProviderConfigError):
        validate_config(schema, {})
    with pytest.raises(ProviderConfigError):
        validate_config(schema, {"retries": "two"})


def test_provider_rejects_bad_default_layer():
    with pytest.raises(ProviderConfigError):
        DefaultPgVectorProvider(config={"default_layer": "nope"})


def test_provider_rejects_unknown_config_key():
    with pytest.raises(ProviderConfigError):
        DefaultPgVectorProvider(config={"surprise": True})


# --- in-memory provider round trip -------------------------------------


def test_in_memory_provider_round_trip():
    registry = ProviderRegistry()
    registry.register(_InMemoryProvider)
    provider = select_provider("mem", registry=registry)
    record = {
        "id": "r1",
        "content": "user prefers dark mode for the dashboard",
        "status": "active",
        "source": "user_stated",
    }
    stored = provider.store("user-1", record)
    assert stored["id"] == "r1"
    found = provider.query("user-1", "dark mode")
    assert [item["id"] for item in found] == ["r1"]
    assert provider.query("user-1", "light mode") == []
    assert provider.query("someone-else", "dark mode") == []


# --- pgvector provider round trip (Postgres) ---------------------------


def test_pgvector_provider_round_trip():
    """Default provider stores + queries a real episode on Postgres.

    Own schema + own rows: creates the memory schema (idempotent),
    uses a fresh user id, and deletes its rows afterwards.
    """
    import psycopg

    from puspa.memory import create_schema
    from puspa.memory.provider import _shared_record  # noqa: F401  (shape check)

    create_schema()
    user_id = str(uuid.uuid4())
    provider = DefaultPgVectorProvider()
    try:
        stored = provider.store(
            user_id,
            {
                "content": "user compared prices for a rice cooker across shops",
                "status": "active",
                "source": "episodic",
            },
        )
        assert stored["layer"] == "episodic"
        assert stored["status"] == "active"
        assert stored["id"]
        assert set(stored) >= {
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
        found = provider.query(user_id, "rice cooker prices")
        assert any(item["id"] == stored["id"] for item in found)
        assert provider.query(user_id, "rice cooker prices", layer="episodic")
    finally:
        with psycopg.connect(
            "dbname=puspa_test user=root host=/var/run/postgresql",
            autocommit=True,
        ) as conn:
            conn.execute("DELETE FROM episodic WHERE user_id = %s", (user_id,))


def test_pgvector_provider_rejects_bad_records():
    provider = DefaultPgVectorProvider()
    user_id = str(uuid.uuid4())
    with pytest.raises(ValueError):
        provider.store(user_id, {"content": ""})
    with pytest.raises(ValueError):
        provider.store(user_id, {"content": "x", "status": "weird"})
    with pytest.raises(ValueError):
        provider.store(user_id, {"content": "x", "layer": "profile"})
    with pytest.raises(ValueError):
        provider.store(
            user_id,
            {"content": "x", "layer": "profile", "key": "k", "status": "expired"},
        )
    with pytest.raises(ValueError):
        provider.store(
            user_id,
            {"content": "x", "layer": "procedural", "service": "svc"},
        )
    with pytest.raises(ValueError):
        provider.query(user_id, "")
    with pytest.raises(ValueError):
        provider.query(user_id, "x", limit=0)
