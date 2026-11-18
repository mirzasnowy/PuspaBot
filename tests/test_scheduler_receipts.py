"""Tests for event receipts / idempotency (receipts.py).

In-memory store tests need no database. The Postgres store tests
use the test database directly (self-contained DSN, no conftest
fixtures) so the file also runs with --noconftest.
"""

import os
import uuid

from puspa.scheduler.receipts import (
    DEFAULT_DSN,
    InMemoryReceiptStore,
    PostgresReceiptStore,
    ensure_receipts_schema,
    process_once,
)

DSN = os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


# --- in-memory --------------------------------------------------------------


def test_first_claim_wins_second_is_duplicate():
    store = InMemoryReceiptStore()
    assert store.claim("evt-1", {"kind": "webhook"}) is True
    assert store.claim("evt-1", {"kind": "webhook"}) is False
    assert store.seen("evt-1") is True
    assert store.seen("evt-2") is False
    assert store.receipt("evt-1") == {"kind": "webhook"}
    assert store.receipt("evt-2") is None


def test_process_once_runs_handler_only_once():
    store = InMemoryReceiptStore()
    calls = []
    first = process_once(
        store, "evt-1", lambda payload: calls.append(payload) or "done", {"amount": 50000}
    )
    assert first == {"ran": True, "result": "done"}
    second = process_once(
        store, "evt-1", lambda payload: calls.append(payload) or "done", {"amount": 50000}
    )
    assert second == {"ran": False, "result": None}
    assert calls == [{"amount": 50000}]


def test_different_keys_are_independent():
    store = InMemoryReceiptStore()
    assert process_once(store, "a", lambda p: "A")["ran"] is True
    assert process_once(store, "b", lambda p: "B")["ran"] is True
    assert process_once(store, "a", lambda p: "A")["ran"] is False


# --- postgres ---------------------------------------------------------------


def test_postgres_receipt_duplicate_is_rejected():
    ensure_receipts_schema(DSN)
    store = PostgresReceiptStore(DSN)
    key = f"evt-pg-test-{uuid.uuid4().hex[:8]}"
    assert store.claim(key, {"kind": "webhook"}) is True
    assert store.claim(key, {"kind": "webhook"}) is False
    assert store.seen(key) is True
    assert store.receipt(key) == {"kind": "webhook"}


def test_postgres_process_once_runs_handler_only_once():
    store = PostgresReceiptStore(DSN)
    calls = []
    key = f"evt-pg-test-{uuid.uuid4().hex[:8]}"
    first = process_once(store, key, lambda payload: calls.append(1) or "paid")
    assert first == {"ran": True, "result": "paid"}
    second = process_once(store, key, lambda payload: calls.append(1) or "paid")
    assert second == {"ran": False, "result": None}
    assert calls == [1]
