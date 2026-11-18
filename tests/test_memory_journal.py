"""Tests for the append-only journal + undo (G7). No database."""

import uuid
from datetime import datetime, timezone

from puspa.memory import facts
from puspa.memory.journal import Journal

UTC = timezone.utc


def _user():
    return str(uuid.uuid4())


def test_log_appends_in_order_with_sequence():
    journal = Journal()
    first = journal.log("record", fact_id="f1", after={"id": "f1"})
    second = journal.log("expire", fact_id="f1")
    assert first["seq"] == 1
    assert second["seq"] == 2
    assert [e["seq"] for e in journal.entries()] == [1, 2]
    assert journal.entries()[0]["action"] == "record"


def test_undo_expire_restores_before_snapshot():
    store = facts.MemoryFactStore()
    journal = Journal()
    user = _user()
    fact = facts.record_fact(user, "city", "Jakarta", store=store,
                             trust_check=False, journal=journal)
    facts.expire_fact(user, fact["id"], store=store, journal=journal)
    assert store.get(fact["id"], user)["status"] == "expired"
    undo = journal.undo_last(store)
    assert undo is not None
    assert undo["action"] == "undo"
    assert store.get(fact["id"], user)["status"] == "active"
    # Undo appends; the log only grows.
    assert len(journal.entries()) == 3


def test_undo_record_expires_the_created_fact():
    store = facts.MemoryFactStore()
    journal = Journal()
    user = _user()
    fact = facts.record_fact(user, "city", "Jakarta", store=store,
                             trust_check=False, journal=journal)
    journal.undo_last(store)
    # History is never deleted: the created fact is expired, not removed.
    assert store.get(fact["id"], user)["status"] == "expired"


def test_undo_empty_journal_returns_none():
    assert Journal().undo_last(facts.MemoryFactStore()) is None


def test_quarantine_queue_lists_blocked_lines():
    journal = Journal()
    journal.log("record", fact_id="f1", kind="change")
    journal.log("blocked", kind="blocked",
                note="[blocked: instruction pattern] ignore me")
    journal.log("quarantine", kind="quarantined",
                note="[quarantined: external origin: web] shop opens at nine")
    queued = journal.list_quarantined()
    assert len(queued) == 2
    assert all(e["kind"] in ("blocked", "quarantined") for e in queued)


def test_journal_persists_to_jsonl_and_reloads(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = Journal(path=path)
    journal.log("record", fact_id="f1", after={"id": "f1"})
    journal.log("expire", fact_id="f1")
    reloaded = Journal(path=path)
    assert [e["seq"] for e in reloaded.entries()] == [1, 2]
    assert reloaded.entries()[1]["action"] == "expire"
    # Sequence continues after reload, never reused.
    assert reloaded.log("record", fact_id="f2")["seq"] == 3
