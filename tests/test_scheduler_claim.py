"""Tests for multi-replica work claiming (claim.py).

The Postgres tests use the test database directly (self-contained
DSN, no conftest fixtures) so the file also runs with
--noconftest. Items use unique ids and are cleaned up per test.
"""

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from puspa.db import connect as _db_connect
from puspa.scheduler.claim import (
    DEFAULT_DSN,
    InMemoryWorkQueue,
    claim_work_items,
    complete_work_item,
    count_work_items,
    enqueue_work_item,
    ensure_work_items_schema,
    fail_work_item,
)

DSN = os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


# The claim queue lives in Postgres: puspa.scheduler.claim
# reads PUSPA_TEST_DSN when no DSN is passed, so every test
# that enqueues or claims needs the database (E-17).
pytestmark = pytest.mark.integration


def _cleanup(item_ids):
    if not item_ids:
        return
    with _db_connect(DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM work_items WHERE id = ANY(%s)", (list(item_ids),))


# --- postgres ----------------------------------------------------------------


def test_enqueue_and_claim_roundtrip():
    ensure_work_items_schema(DSN)
    item_id = enqueue_work_item({"kind": "bill_check"}, dsn=DSN)
    try:
        claimed = claim_work_items(owner="replica-a", limit=1, dsn=DSN)
        assert [item.id for item in claimed] == [item_id]
        assert claimed[0].payload == {"kind": "bill_check"}
        assert claimed[0].status == "claimed"
        assert claimed[0].lease_owner == "replica-a"
        assert claimed[0].lease_until is not None
        # A second claim finds nothing: the item is already held.
        assert claim_work_items(owner="replica-b", limit=1, dsn=DSN) == []
        assert complete_work_item(item_id, dsn=DSN) is True
        assert count_work_items("done", dsn=DSN) >= 1
    finally:
        _cleanup([item_id])


def test_claim_skips_locked_and_respects_limit():
    ensure_work_items_schema(DSN)
    ids = [enqueue_work_item({"n": n}, dsn=DSN) for n in range(3)]
    try:
        claimed = claim_work_items(owner="replica-a", limit=2, dsn=DSN)
        assert len(claimed) == 2
        claimed_ids = {item.id for item in claimed}
        # Only one item is still pending and claimable.
        remaining = claim_work_items(owner="replica-b", limit=5, dsn=DSN)
        assert len(remaining) == 1
        assert remaining[0].id not in claimed_ids
        assert remaining[0].id in set(ids)
    finally:
        _cleanup(ids)


def test_expired_lease_can_be_reclaimed():
    ensure_work_items_schema(DSN)
    item_id = enqueue_work_item({"kind": "stuck"}, dsn=DSN)
    try:
        claimed = claim_work_items(owner="replica-a", lease_seconds=1, dsn=DSN)
        assert len(claimed) == 1
        time.sleep(1.2)
        reclaimed = claim_work_items(owner="replica-b", dsn=DSN)
        assert [item.id for item in reclaimed] == [item_id]
        assert reclaimed[0].lease_owner == "replica-b"
    finally:
        _cleanup([item_id])


def test_attempt_cap_makes_failures_terminal():
    ensure_work_items_schema(DSN)
    item_id = enqueue_work_item({"kind": "flaky"}, max_attempts=2, dsn=DSN)
    try:
        claim_work_items(owner="replica-a", dsn=DSN)
        first = fail_work_item(item_id, dsn=DSN)
        assert first == {"status": "pending", "attempts": 1}
        claim_work_items(owner="replica-a", dsn=DSN)
        second = fail_work_item(item_id, dsn=DSN)
        assert second == {"status": "failed", "attempts": 2}
        # Terminal: never claimed again.
        assert claim_work_items(owner="replica-a", dsn=DSN) == []
        assert fail_work_item(item_id, dsn=DSN) is None
        assert complete_work_item(item_id, dsn=DSN) is False
    finally:
        _cleanup([item_id])


def test_complete_and_fail_on_unknown_item():
    ensure_work_items_schema(DSN)
    assert complete_work_item("work-does-not-exist", dsn=DSN) is False
    assert fail_work_item("work-does-not-exist", dsn=DSN) is None


# --- in-memory fallback -------------------------------------------------------


def test_in_memory_queue_same_surface():
    queue = InMemoryWorkQueue()
    item_id = queue.enqueue({"kind": "bill_check"}, max_attempts=2)
    claimed = queue.claim(owner="replica-a", now=NOW)
    assert [item.id for item in claimed] == [item_id]
    assert queue.claim(owner="replica-b", now=NOW) == []
    assert queue.complete(item_id) is True
    assert queue.count("done") == 1

    stuck = queue.enqueue({"kind": "stuck"})
    queue.claim(owner="replica-a", lease_seconds=1, now=NOW)
    assert queue.claim(owner="replica-b", now=NOW) == []
    reclaimed = queue.claim(owner="replica-b", now=NOW + timedelta(seconds=2))
    assert [item.id for item in reclaimed] == [stuck]

    flaky = queue.enqueue({"kind": "flaky"}, max_attempts=1)
    queue.claim(owner="replica-a", now=NOW)
    assert queue.fail(flaky) == {"status": "failed", "attempts": 1}
    assert queue.claim(owner="replica-a", now=NOW) == []
    assert queue.fail("work-nope") is None
    assert queue.complete("work-nope") is False
