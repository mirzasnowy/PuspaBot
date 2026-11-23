"""Tests for background review (G24).

Run with ``--noconftest``: everything here is in-memory with a
stub ``record_fact``. No live LLM, no database, no G7 facts
module (the fail-soft default path is tested explicitly).
"""

import pytest

from puspa.accounts.ports import resolve_dsn
from puspa.memory.background_review import (
    TURN_TIMEOUT_SECONDS,
    ReviewHandle,
    TurnSnapshot,
    review_turn,
)


def _snapshot(*contents, user_id="user-1"):
    return TurnSnapshot(
        user_id=user_id,
        turn_id="turn-1",
        messages=[{"role": "user", "content": content} for content in contents],
    )


class _StubStore:
    """Fake fact store: records what the review writes."""

    def __init__(self):
        self.records = []

    def record_fact(self, user_id, record):
        stored = dict(record)
        stored["user_id"] = user_id
        self.records.append(stored)
        return {"id": stored["id"]}


def test_review_records_facts_from_user_messages_sync():
    store = _StubStore()
    snapshot = _snapshot("User prefers dark mode for the dashboard and pays bills on the 5th.")
    result = review_turn(snapshot, record_fact=store.record_fact, sync=True)
    assert result.status == "completed"
    assert result.facts_considered >= 1
    assert len(store.records) == result.facts_considered
    assert all(item["source"] == "inferred" for item in store.records)
    assert all(item["status"] == "active" for item in store.records)
    assert all(item["user_id"] == "user-1" for item in store.records)
    assert result.facts_recorded == [item["id"] for item in store.records]


def test_review_ignores_assistant_messages_and_questions():
    store = _StubStore()
    snapshot = TurnSnapshot(
        user_id="user-1",
        turn_id="turn-2",
        messages=[
            {"role": "assistant", "content": "User likes dark mode a lot."},
            {"role": "user", "content": "What time is it?"},
        ],
    )
    result = review_turn(snapshot, record_fact=store.record_fact, sync=True)
    assert result.status == "completed"
    assert store.records == []


def test_review_skips_turn_with_no_user_messages():
    store = _StubStore()
    snapshot = TurnSnapshot(user_id="user-1", messages=[])
    result = review_turn(snapshot, record_fact=store.record_fact, sync=True)
    assert result.status == "skipped"
    assert store.records == []


def test_review_dedupes_inside_the_turn():
    store = _StubStore()
    text = "User prefers dark mode for the dashboard and pays bills on the 5th."
    result = review_turn(_snapshot(text, text), record_fact=store.record_fact, sync=True)
    assert result.status == "completed"
    assert len(store.records) == len({item["content"] for item in store.records})


def test_review_dedupes_against_recall_seam():
    store = _StubStore()
    known = [
        {
            "id": "old",
            "content": "User prefers dark mode for the dashboard and pays bills on the 5th.",
            "status": "active",
        }
    ]

    def recall_facts(query_text):
        return known

    text = "User prefers dark mode for the dashboard and pays bills on the 5th."
    result = review_turn(
        _snapshot(text),
        record_fact=store.record_fact,
        recall_facts=recall_facts,
        sync=True,
    )
    assert result.status == "completed"
    assert result.facts_considered == 1
    assert store.records == []


def test_review_survives_a_failing_recall_seam():
    store = _StubStore()

    def recall_facts(query_text):
        raise RuntimeError("fts index down")

    result = review_turn(
        _snapshot("User prefers dark mode and pays bills on the 5th."),
        record_fact=store.record_fact,
        recall_facts=recall_facts,
        sync=True,
    )
    assert result.status == "completed"
    assert len(store.records) == 1
    assert len(result.errors) == 1


def test_review_survives_a_failing_record_fact():
    def record_fact(user_id, record):
        raise RuntimeError("store down")

    result = review_turn(
        _snapshot("User prefers dark mode and pays bills on the 5th."),
        record_fact=record_fact,
        sync=True,
    )
    assert result.status == "completed"
    assert result.facts_recorded == []
    assert len(result.errors) == 1


def test_review_default_record_fact_is_fail_soft_without_g7_module():
    # Simulate the G7 module being absent: the lazy import must
    # skip, not crash.
    import sys

    with __import__("unittest.mock", fromlist=["mock"]).patch.dict(
        sys.modules, {"puspa.memory.facts": None}
    ):
        result = review_turn(
            _snapshot("User prefers dark mode and pays bills on the 5th."),
            sync=True,
        )
    assert result.status == "skipped"
    assert result.errors


@pytest.mark.integration
def test_review_default_record_fact_writes_to_g7_store():
    """Default path end to end: review -> G7 record_fact -> Postgres.

    Own rows: a fresh user id, rows deleted afterwards. The
    inferred fact lands as a G7 proposal (never active).
    """
    import uuid

    from puspa.db import connect as _db_connect
    from puspa.memory import create_schema
    from puspa.memory.profile import list_facts

    create_schema()
    user_id = str(uuid.uuid4())
    try:
        result = review_turn(
            TurnSnapshot(
                user_id=user_id,
                turn_id="turn-default",
                messages=[
                    {
                        "role": "user",
                        "content": "User prefers dark mode for the dashboard "
                        "and pays bills on the 5th.",
                    }
                ],
            ),
            sync=True,
        )
        assert result.status == "completed"
        assert result.errors == []
        assert len(result.facts_recorded) == 1
        facts = list_facts(user_id)
        assert len(facts) == 1
        assert facts[0]["key"].startswith("fact:")
        assert facts[0]["status"] == "proposed"
        assert facts[0]["source"] == "inferred"
        assert "dark mode" in facts[0]["value"]
    finally:
        with _db_connect(
            resolve_dsn(),
            autocommit=True,
        ) as conn:
            conn.execute("DELETE FROM profile_facts WHERE user_id = %s", (user_id,))


def test_review_async_handle_returns_result():
    store = _StubStore()
    handle = review_turn(
        _snapshot("User prefers dark mode and pays bills on the 5th."),
        record_fact=store.record_fact,
    )
    assert isinstance(handle, ReviewHandle)
    result = handle.wait(timeout=10)
    assert result.status == "completed"
    assert len(store.records) == 1
    assert handle.done


def test_review_async_sees_frozen_snapshot_not_later_edits():
    store = _StubStore()
    snapshot = _snapshot("User prefers dark mode and pays bills on the 5th.")
    handle = review_turn(snapshot, record_fact=store.record_fact)
    # Mutate the live conversation after submitting the review:
    # the fork replays its frozen copy, not this.
    snapshot.messages[0]["content"] = "User loves bright pink themes instead."
    result = handle.wait(timeout=10)
    assert result.status == "completed"
    assert len(store.records) == 1
    assert "dark mode" in store.records[0]["content"]


def test_review_async_timeout_reports_timeout_not_hang():
    import threading

    started = threading.Event()

    def record_fact(user_id, record):
        started.set()
        threading.Event().wait(timeout=30)  # block far past the limit
        return {"id": record["id"]}

    handle = review_turn(
        _snapshot("User prefers dark mode and pays bills on the 5th."),
        record_fact=record_fact,
    )
    assert started.wait(timeout=10)
    result = handle.wait(timeout=1)
    assert result.status == "timeout"
    assert TURN_TIMEOUT_SECONDS == 120
