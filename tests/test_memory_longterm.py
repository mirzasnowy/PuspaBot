"""Tests for long-term consolidation (K16).

Pure, deterministic, no database and no live model: every test
injects its own clock (``NOW``), so nothing depends on wall time.
The database path (Postgres store + real episodic/fact rows) is
covered by ``tests/test_memory_longterm_db.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from puspa.memory import longterm
from puspa.memory.longterm import (
    MemoryItem,
    MemoryLongTermStore,
    archive_item,
    consolidate_longterm,
    forgetting_priority,
    item_from_fact,
    promote_from_working,
    promotion_decision,
    rank_items,
    restore_item,
    tier_for,
    touch_item,
)

NOW = datetime(2026, 11, 22, 5, 0, tzinfo=UTC)


def _item(**overrides):
    base = {
        "item_id": "item-1",
        "kind": "fact",
        "user_id": "user-1",
        "key": "fact:contoh",
        "text": "contoh",
        "importance": 0.5,
        "repetitions": 1,
        "created_at": NOW,
    }
    base.update(overrides)
    return MemoryItem(**base)


def _aged(days, **overrides):
    overrides.setdefault("created_at", NOW - timedelta(days=days))
    return _item(**overrides)


# --- the forgetting curve: priority only, never deletion ------------


def test_correction_never_decays_at_any_age():
    item = _aged(365, is_correction=True, importance=0.3)
    assert forgetting_priority(item, NOW) == 1.0
    assert forgetting_priority(item, NOW + timedelta(days=365)) == 1.0
    assert tier_for(item, NOW + timedelta(days=365)) == "pinned"


def test_pinned_never_decays_at_any_age():
    item = _aged(365, pinned=True, importance=0.3)
    assert forgetting_priority(item, NOW) == 1.0
    assert tier_for(item, NOW + timedelta(days=365)) == "pinned"


def test_low_importance_loses_priority_only_never_hits_zero():
    fresh = _item(importance=0.3)
    assert forgetting_priority(fresh, NOW) == pytest.approx(0.3)
    aged = _aged(60, importance=0.3)
    priority = forgetting_priority(aged, NOW)
    assert 0.05 <= priority < 0.3
    very_old = _aged(365, importance=0.3)
    assert forgetting_priority(very_old, NOW) == pytest.approx(longterm.MIN_PRIORITY)
    assert forgetting_priority(very_old, NOW) > 0


def test_repetition_slows_forgetting():
    once = _aged(60, importance=0.5, repetitions=1)
    repeated = _aged(60, importance=0.5, repetitions=5)
    assert forgetting_priority(repeated, NOW) > forgetting_priority(once, NOW)


def test_recent_access_refreshes_priority():
    item = _aged(60, importance=0.5)
    stale = forgetting_priority(item, NOW)
    touched = touch_item(item, NOW)
    assert forgetting_priority(touched, NOW) == pytest.approx(0.5)
    assert forgetting_priority(touched, NOW) > stale


# --- promotion rules -------------------------------------------------


def test_promotion_decision_correction_is_pinned():
    decision, reason = promotion_decision(_item(is_correction=True))
    assert (decision, reason) == ("pinned", "correction")


def test_promotion_decision_repetition_promotes():
    decision, reason = promotion_decision(_item(repetitions=3, importance=0.3))
    assert (decision, reason) == ("promoted", "repetition")


def test_promotion_decision_high_importance_promotes():
    decision, reason = promotion_decision(_item(importance=0.8, repetitions=1))
    assert (decision, reason) == ("promoted", "importance")


def test_promotion_decision_plain_item_stays():
    assert promotion_decision(_item(importance=0.3, repetitions=1))[0] == "stay"


def test_item_from_fact_marks_corrections_and_goals_pinned():
    correction = item_from_fact(
        {
            "id": "f1",
            "user_id": "user-1",
            "key": "correction:jam",
            "content": "jam yang benar 07.00",
            "source": "user_confirmed",
            "status": "active",
            "created_at": NOW,
        }
    )
    assert correction.is_correction and correction.pinned
    assert correction.importance == pytest.approx(0.8)
    goal = item_from_fact(
        {
            "id": "f2",
            "user_id": "user-1",
            "key": "goal:hemat",
            "content": "hemat 1 miliar",
            "source": "user_stated",
            "status": "active",
            "created_at": NOW,
        }
    )
    assert goal.pinned and not goal.is_correction
    plain = item_from_fact(
        {
            "id": "f3",
            "user_id": "user-1",
            "key": "fact:kopi",
            "content": "suka kopi",
            "source": "user_stated",
            "status": "active",
            "created_at": NOW,
        }
    )
    assert not plain.pinned and not plain.is_correction
    assert promotion_decision(plain)[0] == "stay"


# --- tiers + archive ---------------------------------------------------


def test_tier_for_moves_hot_to_cold_to_archived():
    assert tier_for(_item(importance=0.9), NOW) == "hot"
    assert tier_for(_aged(45, importance=0.5), NOW) == "cold"
    assert tier_for(_aged(100, importance=0.3), NOW) == "archived"
    assert tier_for(_aged(100, importance=0.9, repetitions=10), NOW) != "archived"


def test_archive_and_restore_roundtrip():
    item = _aged(100, importance=0.3)
    archived = archive_item(item, NOW)
    assert archived.tier == "archived"
    assert archived.archived_at == NOW
    restored = restore_item(archived, NOW)
    assert restored.tier == "warm"
    assert restored.archived_at is None
    assert restored.last_accessed_at == NOW


def test_touch_on_archived_restores_touch_on_warm_only_refreshes():
    archived = archive_item(_aged(100, importance=0.3), NOW)
    assert touch_item(archived, NOW).tier == "warm"
    warm = _item(importance=0.9)
    touched = touch_item(warm, NOW)
    assert touched.tier == "warm"
    assert touched.last_accessed_at == NOW


# --- ranking: low priority sinks, never disappears --------------------


def test_rank_low_importance_still_retrievable_but_lower():
    fresh = _item(item_id="fresh", importance=0.9)
    old = _aged(60, item_id="old", importance=0.3)
    ranked = rank_items([old, fresh], {"fresh": 0.9, "old": 0.9}, NOW)
    assert [entry["item"].item_id for entry in ranked] == ["fresh", "old"]
    assert ranked[1]["score"] > 0
    assert ranked[1]["priority"] < ranked[0]["priority"]


def test_rank_archived_excluded_by_default_included_when_explicit():
    archived = archive_item(_aged(100, item_id="cold-one", importance=0.3), NOW)
    hot = _item(item_id="hot-one", importance=0.9)
    assert [
        e["item"].item_id
        for e in rank_items([archived, hot], {"cold-one": 1.0, "hot-one": 1.0}, NOW)
    ] == ["hot-one"]
    found = rank_items(
        [archived, hot], {"cold-one": 1.0, "hot-one": 1.0}, NOW, include_archived=True
    )
    assert [e["item"].item_id for e in found] == ["hot-one", "cold-one"]


# --- in-memory store ---------------------------------------------------


def test_memory_store_roundtrip_and_touch():
    store = MemoryLongTermStore()
    item = _item(importance=0.9)
    store.upsert(item)
    assert store.get("user-1", "fact", "item-1")["tier"] == "warm"
    assert [row["item_id"] for row in store.list_user("user-1")] == ["item-1"]
    assert store.list_user("user-1", tiers=("archived",)) == []
    assert store.get("user-1", "fact", "missing") is None
    assert store.touch("user-1", "fact", "missing", NOW) is None
    archived = archive_item(item, NOW)
    store.upsert(archived)
    touched = store.touch("user-1", "fact", "item-1", NOW)
    assert touched is not None and touched["tier"] == "warm"
    assert touched["archived_at"] is None


# --- K15 seam ------------------------------------------------------------


def test_promote_from_working_seam():
    store = MemoryLongTermStore()
    stayed = promote_from_working(
        {"text": "sekali saja", "importance": 0.3}, user_id="user-1", now=NOW, store=store
    )
    assert stayed["decision"] == "stay"
    assert stayed["stored"] is False
    assert store.list_user("user-1") == []
    promoted = promote_from_working(
        {"text": "sering muncul", "importance": 0.4, "repetitions": 3},
        user_id="user-1",
        now=NOW,
        store=store,
    )
    assert promoted["decision"] == "promoted"
    assert promoted["stored"] is True
    assert len(store.list_user("user-1")) == 1
    pinned = promote_from_working(
        {"text": "ralat: jamnya 07.00", "is_correction": True},
        user_id="user-1",
        now=NOW,
        store=store,
    )
    assert pinned["decision"] == "pinned"
    assert pinned["item"]["tier"] == "pinned"
    assert pinned["priority"] == 1.0
    with pytest.raises(ValueError):
        promote_from_working({"text": ""}, user_id="user-1", now=NOW, store=store)


# --- the consolidation pass ----------------------------------------------


def _episodes():
    return [
        {
            "id": f"ep-pln-{n}",
            "user_id": "user-1",
            "type": "task_completed",
            "content": "check_pln: bill checked successfully",
            "created_at": NOW,
        }
        for n in range(3)
    ] + [
        {
            "id": "ep-kopi",
            "user_id": "user-1",
            "type": "user_message",
            "content": "suka kopi tubruk",
            "created_at": NOW,
        }
    ]


def _facts():
    return [
        {
            "id": "fact-ralat",
            "user_id": "user-1",
            "key": "correction:jam",
            "content": "jam yang benar 07.00",
            "source": "user_confirmed",
            "status": "active",
            "created_at": NOW,
        },
        {
            "id": "fact-kopi",
            "user_id": "user-1",
            "key": "fact:kopi",
            "content": "suka kopi",
            "source": "user_stated",
            "status": "active",
            "created_at": NOW,
        },
    ]


def test_consolidate_promotes_repeated_and_pins_corrections():
    store = MemoryLongTermStore()
    result = consolidate_longterm(
        "user-1", now=NOW, store=store, episodes=_episodes(), facts=_facts()
    )
    assert result["promoted"] == ["check_pln"] * 3
    assert result["pinned"] == ["correction:jam"]
    assert result["stayed"] == 2
    assert result["archived"] == []
    assert result["tiers"] == {"hot": 3, "pinned": 1}
    assert result["k9"] is None
    rows = store.list_user("user-1")
    assert {row["tier"] for row in rows} == {"hot", "pinned"}


def test_consolidate_second_run_promotes_nothing_new():
    store = MemoryLongTermStore()
    consolidate_longterm("user-1", now=NOW, store=store, episodes=_episodes(), facts=_facts())
    again = consolidate_longterm(
        "user-1", now=NOW, store=store, episodes=_episodes(), facts=_facts()
    )
    assert again["promoted"] == []
    assert again["pinned"] == []
    assert again["stayed"] == 2
    assert len(store.list_user("user-1")) == 4


def test_consolidate_archives_cold_items_after_time_passes():
    store = MemoryLongTermStore()
    consolidate_longterm("user-1", now=NOW, store=store, episodes=_episodes(), facts=_facts())
    old_episodes = [dict(row, created_at=NOW - timedelta(days=100)) for row in _episodes()]
    old_facts = [dict(row, created_at=NOW - timedelta(days=100)) for row in _facts()]
    later = NOW + timedelta(days=100)
    result = consolidate_longterm(
        "user-1", now=later, store=store, episodes=old_episodes, facts=old_facts
    )
    assert sorted(result["archived"]) == ["check_pln"] * 3
    assert result["pinned"] == []
    assert result["tiers"]["archived"] == 3
    assert result["tiers"]["pinned"] == 1
    archived_rows = store.list_user("user-1", tiers=("archived",))
    assert len(archived_rows) == 3
    assert all(row["archived_at"] is not None for row in archived_rows)


def test_consolidate_restores_archived_item_accessed_again():
    store = MemoryLongTermStore()
    consolidate_longterm("user-1", now=NOW, store=store, episodes=_episodes(), facts=_facts())
    old_episodes = [dict(row, created_at=NOW - timedelta(days=100)) for row in _episodes()]
    later = NOW + timedelta(days=100)
    consolidate_longterm("user-1", now=later, store=store, episodes=old_episodes, facts=_facts())
    assert len(store.list_user("user-1", tiers=("archived",))) == 3
    # Access recorded without a tier flip (for example by recall):
    # the next pass brings the item back.
    row = store.get("user-1", "episode", "ep-pln-0")
    assert row is not None
    row["last_accessed_at"] = later
    store.upsert(longterm.to_item(row))
    result = consolidate_longterm(
        "user-1", now=later, store=store, episodes=old_episodes, facts=_facts()
    )
    assert result["restored"] == ["check_pln"]
    assert store.get("user-1", "episode", "ep-pln-0")["tier"] == "hot"
    assert store.get("user-1", "episode", "ep-pln-0")["archived_at"] is None


def test_consolidate_runs_k9_first_when_asked(monkeypatch):
    seen = {}

    def fake_idle(user_id, dsn=None, now=None):
        seen["called"] = (user_id, dsn, now)
        return {
            "summaries_created": 2,
            "procedures_promoted": 0,
            "proposals_created": 0,
            "tasks": [],
        }

    monkeypatch.setattr(longterm.consolidate, "consolidate_idle", fake_idle)
    store = MemoryLongTermStore()
    result = consolidate_longterm(
        "user-1", now=NOW, store=store, episodes=[], facts=[], run_k9=True
    )
    assert seen["called"] == ("user-1", None, NOW)
    assert result["k9"]["summaries_created"] == 2
