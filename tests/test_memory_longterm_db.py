"""Database tests for long-term consolidation (K16).

Against the real ``puspa_test_w1`` database: the Postgres tier
store, the full pass over real episodic rows and facts, and the
scheduler wiring. No live model and no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from puspa.db import connect as _db_connect
from puspa.memory import episodic, longterm
from puspa.memory import facts as facts_module
from puspa.memory.longterm import (
    PostgresLongTermStore,
    consolidate_longterm,
    rank_items,
    to_item,
)
from puspa.scheduler.scheduler import run_scheduled_job, schedule_longterm_consolidation

NOW = datetime(2026, 11, 22, 5, 0, tzinfo=UTC)


def _backdate_episodes(episode_ids, days, test_dsn):
    # The timestamp is computed in Python and bound as a value:
    # Postgres-only make_interval() has no SQLite equivalent and
    # no product code uses it, so the test does not rely on it.
    backdated = datetime.now(UTC) - timedelta(days=days)
    with _db_connect(test_dsn) as conn:
        conn.execute(
            "UPDATE episodic SET created_at = %s WHERE id = ANY(%s)",
            (backdated, episode_ids),
        )
        conn.commit()


def test_postgres_store_roundtrip(make_user, test_dsn):
    user = make_user()
    store = PostgresLongTermStore(dsn=test_dsn)
    item = longterm.MemoryItem(
        item_id="12345678-1234-5678-1234-567812345678",
        kind="fact",
        user_id=str(user["user_id"]),
        key="fact:kopi",
        text="suka kopi",
        importance=0.8,
        repetitions=1,
        created_at=NOW,
    )
    saved = store.upsert(item)
    assert saved["tier"] == "warm"
    assert saved["text"] == ""
    assert store.get(str(user["user_id"]), "fact", "12345678-1234-5678-1234-567812345678")[
        "importance"
    ] == pytest.approx(0.8)
    assert store.get(str(user["user_id"]), "fact", "12345678-1234-5678-1234-567812345679") is None
    assert (
        store.touch(str(user["user_id"]), "fact", "12345678-1234-5678-1234-567812345679", NOW)
        is None
    )
    assert [row["item_id"] for row in store.list_user(str(user["user_id"]))] == [
        "12345678-1234-5678-1234-567812345678"
    ]
    assert store.list_user(str(user["user_id"]), tiers=("archived",)) == []
    # Upsert keeps one row per item (no duplicates).
    store.upsert(item)
    assert len(store.list_user(str(user["user_id"]))) == 1


def test_consolidate_longterm_over_real_rows(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    episodes = [
        episodic.record_episode(
            user_id, "check_pln: bill checked successfully", "task_completed", dsn=test_dsn
        )
        for _ in range(3)
    ]
    episodic.record_episode(user_id, "suka kopi tubruk", "user_message", dsn=test_dsn)
    fact_store = facts_module.PostgresFactStore(dsn=test_dsn)
    facts_module.record_fact(
        user_id,
        "correction:jam",
        "jam yang benar 07.00",
        source="user_confirmed",
        store=fact_store,
    )
    facts_module.record_fact(user_id, "fact:kopi", "suka kopi", store=fact_store)

    result = consolidate_longterm(user_id, now=NOW, dsn=test_dsn)
    assert result["promoted"] == ["check_pln"] * 3
    assert result["pinned"] == ["correction:jam"]
    assert result["stayed"] == 2
    assert result["tiers"] == {"hot": 3, "pinned": 1}

    store = PostgresLongTermStore(dsn=test_dsn)
    hot = store.list_user(user_id, tiers=("hot",))
    assert len(hot) == 3
    assert all(row["repetitions"] == 3 for row in hot)
    pinned = store.list_user(user_id, tiers=("pinned",))
    assert len(pinned) == 1
    assert pinned[0]["is_correction"] is True
    assert pinned[0]["importance"] == pytest.approx(0.8)

    # 100 days later the repeated pattern is cold enough to archive;
    # the correction stays pinned, and nothing is deleted.
    _backdate_episodes([ep["id"] for ep in episodes], 100, test_dsn)
    later = consolidate_longterm(user_id, dsn=test_dsn)
    assert sorted(later["archived"]) == ["check_pln"] * 3
    assert later["tiers"]["archived"] == 3
    assert later["tiers"]["pinned"] == 1
    archived = store.list_user(user_id, tiers=("archived",))
    assert len(archived) == 3
    assert all(row["archived_at"] is not None for row in archived)

    # Archived items are still there and still retrievable when the
    # search is explicit; a plain search skips them.
    items = [to_item(row) for row in store.list_user(user_id)]
    relevance = {item.item_id: 1.0 for item in items}
    plain = rank_items(items, relevance)
    assert [entry["item"].key for entry in plain] == ["correction:jam"]
    explicit = rank_items(items, relevance, include_archived=True)
    assert len(explicit) == 4
    assert explicit[-1]["item"].tier == "archived"
    assert explicit[-1]["score"] > 0

    # Access brings an archived item back to warm.
    touched = store.touch(user_id, "episode", episodes[0]["id"])
    assert touched is not None and touched["tier"] == "warm"
    assert touched["archived_at"] is None
    assert len(store.list_user(user_id, tiers=("archived",))) == 2


def test_scheduler_runs_longterm_consolidation(make_user, test_dsn):
    user = make_user()
    user_id = str(user["user_id"])
    episodic.record_episode(
        user_id, "check_pln: bill checked successfully", "task_completed", dsn=test_dsn
    )
    trigger = schedule_longterm_consolidation(user_id, dsn=test_dsn)
    assert trigger.event_type == "longterm_consolidation"
    result = run_scheduled_job(
        {"trigger_id": trigger.id, "task_id": "task-longterm-1"}, dsn=test_dsn
    )
    assert result["job"] == "longterm_consolidation"
    assert result["result"]["stayed"] == 1
    store = PostgresLongTermStore(dsn=test_dsn)
    assert store.list_user(user_id) == []
