"""Builtin single-select plugin: in-memory memory provider (G27).

A ``MemoryProvider`` that stores shared-shape records in memory
only. It proves the ``memory_provider`` single-select kind end
to end and gives tests and local development a provider that
needs no Postgres. It is NOT the default provider: the bundled
``pgvector`` provider keeps that role, and this plugin is
active only when explicitly selected.

The provider reuses the shared record shape from
``puspa.memory.provider`` (published API): records carry ``id``,
``content``, ``status`` (``active`` / ``superseded`` /
``expired``), timestamps, and ``source``.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime
from typing import ClassVar

from puspa.memory.provider import (
    RECORD_STATUSES,
    MemoryProvider,
)

PLUGIN_ID = "memory_inmemory"
PLUGIN_VERSION = "1.0.0"


def _now():
    return datetime.now(UTC)


class InMemoryProvider(MemoryProvider):
    """Memory provider backed by a plain dict (no database)."""

    name = "inmemory"

    config_schema: ClassVar[dict] = {
        "max_records": {"type": "integer", "required": False, "default": 1000},
    }

    def __init__(self, config=None):
        super().__init__(config=config)
        self._records: dict[str, dict] = {}
        self._order = itertools.count(1)

    def store(self, user_id, record):
        """Store one record; return the stored record dict."""
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
        if len(self._records) >= (self.config.get("max_records") or 1000):
            raise ValueError("in-memory provider is full")
        record_id = record.get("id") or str(uuid.uuid4())
        now = _now()
        stored = {
            "id": str(record_id),
            "content": content,
            "status": status,
            "valid_from": record.get("valid_from") or now,
            "valid_until": record.get("valid_until"),
            "superseded_by": record.get("superseded_by"),
            "source": record.get("source") or "inmemory",
            "created_at": record.get("created_at") or now,
            "updated_at": now,
            "user_id": user_id,
            "seq": next(self._order),
        }
        self._records[stored["id"]] = stored
        return dict(stored)

    def query(self, user_id, query_text, *, limit=5, layer=None):
        """Substring search over the user's records, newest first."""
        if not isinstance(query_text, str) or not query_text:
            raise ValueError("query_text must be a non-empty string")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        needle = query_text.lower()
        matches = [
            record
            for record in self._records.values()
            if record["user_id"] == user_id and needle in record["content"].lower()
        ]
        matches.sort(key=lambda record: -record["seq"])
        return [dict(record) for record in matches[:limit]]


def register(ctx):
    """Register the in-memory provider (not the default)."""
    ctx.register_memory_provider("inmemory", InMemoryProvider)
