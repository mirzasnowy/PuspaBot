"""Event receipts: idempotency keys for triggers and webhooks (G8).

Every application event and every webhook delivery carries an
idempotency key. The first time a key is seen its handler runs;
a second delivery of the same key is acknowledged but never runs
the handler again — a webhook that arrives twice must never pay
a bill twice or run an action twice.
"""

from __future__ import annotations

import json
import os
import threading

from puspa.db import connect as _db_connect

from puspa.migrations import apply_migrations

__all__ = [
    "DEFAULT_DSN",
    "InMemoryReceiptStore",
    "PostgresReceiptStore",
    "ensure_receipts_schema",
    "process_once",
]

DEFAULT_DSN = None


def ensure_receipts_schema(dsn: str | None = None) -> None:
    """Apply the ``event_receipts`` table (migration 0018_scheduler)."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    apply_migrations(dsn, target="0018")


class InMemoryReceiptStore:
    """Receipt store for tests and single-process use."""

    def __init__(self):
        self._seen: dict[str, dict] = {}
        self._lock = threading.Lock()

    def claim(self, key: str, payload: dict | None = None) -> bool:
        """Record ``key``; True only the first time it is seen."""
        with self._lock:
            if key in self._seen:
                return False
            self._seen[key] = dict(payload or {})
            return True

    def seen(self, key: str) -> bool:
        """True when ``key`` was already claimed."""
        with self._lock:
            return key in self._seen

    def receipt(self, key: str) -> dict | None:
        """The payload recorded for ``key`` (None when unseen)."""
        with self._lock:
            stored = self._seen.get(key)
            return dict(stored) if stored is not None else None


class PostgresReceiptStore:
    """Receipt store backed by Postgres (survives restarts).

    The claim is one atomic statement: ``INSERT ... ON CONFLICT
    DO NOTHING`` returns a row only for the first delivery, so two
    concurrent deliveries of the same key cannot both run.
    """

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
        ensure_receipts_schema(self.dsn)

    def claim(self, key: str, payload: dict | None = None) -> bool:
        with _db_connect(self.dsn, autocommit=True) as conn:
            row = conn.execute(
                "INSERT INTO event_receipts (idempotency_key, payload)"
                " VALUES (%s, %s::jsonb) ON CONFLICT (idempotency_key)"
                " DO NOTHING RETURNING idempotency_key",
                (key, json.dumps(payload or {})),
            ).fetchone()
        return row is not None

    def seen(self, key: str) -> bool:
        with _db_connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT 1 FROM event_receipts WHERE idempotency_key = %s",
                (key,),
            ).fetchone()
        return row is not None

    def receipt(self, key: str) -> dict | None:
        with _db_connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT payload FROM event_receipts WHERE idempotency_key = %s",
                (key,),
            ).fetchone()
        if row is None:
            return None
        payload = row[0]
        return dict(payload) if isinstance(payload, dict) else payload


def process_once(store, key: str, handler, payload: dict | None = None):
    """Run ``handler(payload)`` once per key; duplicates are skipped.

    Returns ``{"ran": True, "result": ...}`` on the first delivery
    and ``{"ran": False, "result": None}`` on a duplicate.
    ``handler`` receives the payload dict (or ``{}``).
    """
    if not store.claim(key, payload):
        return {"ran": False, "result": None}
    return {"ran": True, "result": handler(dict(payload or {}))}
