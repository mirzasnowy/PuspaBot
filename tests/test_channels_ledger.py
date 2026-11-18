"""Delivery ledger tests (G14).

The in-memory ledger runs anywhere. The Postgres ledger tests
use the shared ``test_dsn`` fixture and create the channel schema
themselves (``conftest`` does not apply it — same pattern as the
teams schema).
"""

from __future__ import annotations

import pytest

from puspa.channels.ledger import InMemoryDeliveryLedger, PostgresDeliveryLedger
from puspa.channels.schema import create_schema, reset_tables


def _record_kwargs(**overrides):
    kwargs = {
        "platform": "telegram",
        "chat_id": "111",
        "direction": "inbound",
        "status": "sent",
        "dedup_key": "telegram:111:5",
    }
    kwargs.update(overrides)
    return kwargs


# -- in-memory ------------------------------------------------------------


def test_in_memory_record_and_seen():
    ledger = InMemoryDeliveryLedger()
    assert ledger.seen("telegram:111:5") is False
    record = ledger.record(**_record_kwargs())
    assert record.status == "sent"
    assert record.attempts == 1
    assert ledger.seen("telegram:111:5") is True
    assert ledger.by_dedup("telegram:111:5").id == record.id


def test_in_memory_first_write_wins_for_dedup():
    ledger = InMemoryDeliveryLedger()
    first = ledger.record(**_record_kwargs())
    ledger.record(**_record_kwargs(status="failed"))
    assert ledger.by_dedup("telegram:111:5").id == first.id


def test_in_memory_update():
    ledger = InMemoryDeliveryLedger()
    record = ledger.record(**_record_kwargs(status="pending"))
    updated = ledger.update(record.id, status="sent", attempts=2)
    assert updated.status == "sent"
    assert updated.attempts == 2
    assert ledger.update("delivery-missing", status="sent") is None


def test_in_memory_list_for_chat():
    ledger = InMemoryDeliveryLedger()
    ledger.record(**_record_kwargs())
    ledger.record(**_record_kwargs(dedup_key="telegram:111:6"))
    ledger.record(**_record_kwargs(chat_id="222", dedup_key="telegram:222:5"))
    rows = ledger.list_for_chat("telegram", "111")
    assert len(rows) == 2
    assert all(row.chat_id == "111" for row in rows)


def test_in_memory_rejects_bad_values():
    ledger = InMemoryDeliveryLedger()
    with pytest.raises(ValueError):
        ledger.record(**_record_kwargs(status="nope"))
    with pytest.raises(ValueError):
        ledger.record(**_record_kwargs(direction="sideways"))


# -- postgres ---------------------------------------------------------------


@pytest.fixture
def pg_ledger(test_dsn):
    create_schema(test_dsn)
    reset_tables(test_dsn)
    yield PostgresDeliveryLedger(test_dsn)
    reset_tables(test_dsn)


def test_postgres_record_and_seen(pg_ledger):
    assert pg_ledger.seen("telegram:111:5") is False
    record = pg_ledger.record(**_record_kwargs())
    assert record.id.startswith("delivery-")
    assert pg_ledger.seen("telegram:111:5") is True
    assert pg_ledger.get(record.id).dedup_key == "telegram:111:5"


def test_postgres_duplicate_dedup_returns_existing(pg_ledger):
    first = pg_ledger.record(**_record_kwargs())
    second = pg_ledger.record(**_record_kwargs(status="failed"))
    assert second.id == first.id
    assert second.status == "sent"  # first write wins


def test_postgres_update(pg_ledger):
    record = pg_ledger.record(**_record_kwargs(status="pending"))
    updated = pg_ledger.update(record.id, status="sent", attempts=2, error=None)
    assert updated.status == "sent"
    assert updated.attempts == 2
    assert pg_ledger.update("delivery-missing", status="sent") is None


def test_postgres_list_for_chat(pg_ledger):
    pg_ledger.record(**_record_kwargs())
    pg_ledger.record(**_record_kwargs(dedup_key="telegram:111:6"))
    pg_ledger.record(**_record_kwargs(chat_id="222", dedup_key="telegram:222:5"))
    rows = pg_ledger.list_for_chat("telegram", "111")
    assert len(rows) == 2
