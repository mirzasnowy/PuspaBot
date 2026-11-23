"""Tests for the nightly amnesia check (Kosmos V2, issue 116).

Unit tests inject every store (fact lists, in-memory fact and
durable stores, a recording ledger), so they run without
Postgres. Database tests seed Postgres with exactly 20 pinned
facts, then run the check cold through a fresh store, and run
the job through the scheduler caller.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from puspa.memory.amnesia import (
    AMNESIA_SAMPLE_SIZE,
    check_pinned_recall,
    expected_pinned_items,
    repair_index,
    run_amnesia_check,
    sample_pinned_items,
)
from puspa.memory.durable import MemoryDurableStore
from puspa.memory.facts import MemoryFactStore
from puspa.memory.recall_plan import recall_plan

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
USER = "user-amnesia"


def _fact(fid, key, content, source="user_stated"):
    return {
        "id": fid,
        "user_id": USER,
        "key": key,
        "content": content,
        "status": "active",
        "valid_from": NOW,
        "valid_until": None,
        "superseded_by": None,
        "source": source,
        "confidence": None,
        "source_detail": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _twenty_facts():
    """20 pinned facts: 7 corrections, 7 commitments, 6 goals."""
    facts = [
        _fact(f"c{i}", f"correction-{i}", f"Correction number {i}", "user_confirmed")
        for i in range(1, 8)
    ]
    facts += [_fact(f"m{i}", f"commitment:item-{i}", f"Commitment number {i}") for i in range(1, 8)]
    facts += [_fact(f"g{i}", f"goal:item-{i}", f"Standing goal number {i}") for i in range(1, 7)]
    return facts


class FakeLedger:
    """Ledger stub: reads nothing, records every appended event."""

    def __init__(self):
        self.appended: list[dict] = []

    def events(self, task_id):
        return []

    def append_event(self, *, task_id, agent_id, event_type, content, **kwargs):
        event = {
            "id": f"evt-{uuid.uuid4().hex[:12]}",
            "task_id": task_id,
            "agent_id": agent_id,
            "type": event_type,
            "content": content,
        }
        self.appended.append(event)
        return event


@pytest.mark.unit
def test_sample_is_first_twenty_in_pinned_order():
    pinned = expected_pinned_items(_twenty_facts() * 2)
    assert len(pinned) == 40
    sample = sample_pinned_items(pinned)
    assert len(sample) == AMNESIA_SAMPLE_SIZE
    assert sample == pinned[:20]
    assert [item["section"] for item in sample[:7]] == ["Corrections"] * 7


@pytest.mark.unit
def test_check_pinned_recall_counts_a_text_change_as_a_miss():
    expected = [{"section": "Corrections", "key": "city", "text": "Lives in Karawang"}]
    recalled = [{"section": "Corrections", "key": "city", "text": "Lives in Tangerang"}]
    result = check_pinned_recall(expected, recalled)
    assert result == {"checked": 1, "recalled": 0, "missed": expected}


@pytest.mark.unit
def test_cold_check_passes_twenty_of_twenty():
    ledger = FakeLedger()
    result = run_amnesia_check(USER, facts=_twenty_facts(), ledger=ledger)
    assert result["status"] == "ok"
    assert result["checked"] == 20
    assert result["recalled"] == 20
    assert result["missed"] == []
    assert result["repair"] is None
    assert result["recheck"] is None
    assert result["ledger_event_id"] is not None
    assert len(ledger.appended) == 1
    event = ledger.appended[0]
    assert event["type"] == "task_completed"
    assert event["content"]["job"] == "amnesia_check"
    assert event["content"]["status"] == "ok"
    assert event["content"]["checked"] == 20
    assert "20 of 20" in event["content"]["report"]


@pytest.mark.unit
def test_fewer_than_twenty_pinned_is_insufficient_not_an_incident():
    ledger = FakeLedger()
    result = run_amnesia_check(USER, facts=_twenty_facts()[:5], ledger=ledger)
    assert result["status"] == "insufficient_pinned"
    assert result["checked"] == 5
    assert result["required"] == 20
    assert result["missed"] == []
    assert result["repair"] is None
    assert len(ledger.appended) == 1
    assert ledger.appended[0]["content"]["status"] == "insufficient_pinned"


@pytest.mark.unit
def test_planted_miss_is_incident_triggers_repair_and_recheck_passes():
    facts = _twenty_facts()
    fact_store = MemoryFactStore()
    for fact in facts:
        fact_store.insert(
            {
                "id": fact["id"],
                "user_id": USER,
                "key": fact["key"],
                "value": fact["content"],
                "source": fact["source"],
                "status": "active",
                "valid_from": NOW,
                "valid_until": None,
                "superseded_by": None,
                "confidence": None,
                "source_detail": None,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
    durable_store = MemoryDurableStore()
    ledger = FakeLedger()
    dropped_key = "goal:item-1"
    index = {fact["key"] for fact in facts}

    def recall_fn():
        # A stale recall index: one pinned item is unreachable
        # until the repair rebuilds the durable memory.
        visible = [fact for fact in facts if fact["key"] in index]
        return recall_plan(USER, "", facts=visible, ledger=ledger)

    def repair_fn(user_id, **kwargs):
        rebuilt = repair_index(
            user_id,
            fact_store=fact_store,
            store=durable_store,
            summaries=[],
            episodes=[],
            journal_entries=[],
        )
        index.add(dropped_key)
        return rebuilt

    index.discard(dropped_key)
    result = run_amnesia_check(
        USER,
        facts=facts,
        ledger=ledger,
        recall_fn=recall_fn,
        repair_fn=repair_fn,
        durable_store=durable_store,
    )
    assert result["status"] == "incident"
    assert result["checked"] == 20
    assert result["recalled"] == 19
    assert [item["key"] for item in result["missed"]] == [dropped_key]
    assert result["repair"] is not None
    assert "error" not in result["repair"]
    assert result["repair"]["changed"] is True
    assert result["recheck"] is not None
    assert result["recheck"]["status"] == "ok"
    assert result["recheck"]["recalled"] == 20
    assert result["recheck"]["missed"] == []
    assert durable_store.get_document(USER) is not None
    assert len(ledger.appended) == 1
    assert ledger.appended[0]["content"]["status"] == "incident"
    assert ledger.appended[0]["content"]["repair"]["version"] == 1


@pytest.mark.unit
def test_repair_turned_off_reports_incident_without_recheck():
    facts = _twenty_facts()
    ledger = FakeLedger()

    def recall_fn():
        return recall_plan(USER, "", facts=facts[:-1], ledger=ledger)

    result = run_amnesia_check(USER, facts=facts, ledger=ledger, recall_fn=recall_fn, repair=False)
    assert result["status"] == "incident"
    assert result["recalled"] == 19
    assert result["repair"] is None
    assert result["recheck"] is None


def test_db_cold_check_passes_twenty_of_twenty(test_dsn, make_user):
    from puspa.memory.facts import PostgresFactStore, record_fact

    user = make_user()
    user_id = user["user_id"]
    store = PostgresFactStore(dsn=test_dsn)
    for fact in _twenty_facts():
        assert record_fact(
            user_id, fact["key"], fact["content"], source=fact["source"], store=store
        )["stored"]
    result = run_amnesia_check(user_id, dsn=test_dsn)
    assert result["status"] == "ok"
    assert result["checked"] == 20
    assert result["recalled"] == 20
    assert result["missed"] == []
    assert result["ledger_event_id"] is not None
    found = _find_amnesia_event(test_dsn, user_id)
    assert found is not None
    assert found["content"]["job"] == "amnesia_check"
    assert found["content"]["status"] == "ok"


def _find_amnesia_event(test_dsn, user_id):
    from puspa.db import connect as _db_connect, dict_row as _db_dict_row

    with _db_connect(test_dsn) as conn, conn.cursor(row_factory=_db_dict_row) as cur:
        cur.execute(
            "SELECT * FROM events WHERE type = 'task_completed' "
            "AND content ->> 'job' = 'amnesia_check' "
            "AND content ->> 'user_id' = %s ORDER BY created_at DESC LIMIT 1",
            (str(user_id),),
        )
        return cur.fetchone()


def test_db_amnesia_check_runs_as_scheduled_job(test_dsn, make_user):
    from puspa.ledger.ledger import Ledger
    from puspa.memory.facts import PostgresFactStore, record_fact
    from puspa.scheduler import scheduler

    user = make_user()
    user_id = user["user_id"]
    store = PostgresFactStore(dsn=test_dsn)
    for fact in _twenty_facts():
        assert record_fact(
            user_id, fact["key"], fact["content"], source=fact["source"], store=store
        )["stored"]
    trigger = scheduler.schedule_amnesia_check(user_id, dsn=test_dsn)
    assert trigger.event_type == "amnesia_check"
    run = scheduler.run_trigger(trigger, dsn=test_dsn, now=trigger.next_trigger_at)
    assert run is not None
    result = scheduler.run_scheduled_job(run, dsn=test_dsn)
    assert result["job"] == "amnesia_check"
    assert result["result"]["status"] == "ok"
    assert result["result"]["checked"] == 20
    assert result["result"]["recalled"] == 20
    events = Ledger(test_dsn).events(run["task_id"], event_type="task_completed")
    assert len(events) == 1
    assert events[0]["content"]["job"] == "amnesia_check"
