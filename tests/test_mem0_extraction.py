"""Tests for mem0-style memory extraction + ranked recall.

In-memory only: no Postgres, no fixtures from conftest beyond the
shared autouse reset. The extraction rules, dedup, conflict
flagging, and ranked recall are all exercised on real data, and
recall@k is measured on a small query set written here.
"""

import pytest

from puspa.memory import extraction
from puspa.memory.embeddings import HashEmbedding


def _seed_store():
    """Eight memories on distinct topics; returns (store, ids)."""
    store = extraction.MemoryStore()
    ids = {}
    topics = {
        "coffee": "user suka kopi tubruk",
        "laptop": "harga laptop asus vivobook 14 juta",
        "pln": "tagihan listrik pln bulan ini 350 ribu",
        "gym": "jadwal gym setiap senin dan kamis jam 7 pagi",
        "book": "user sedang baca buku atomic habits",
        "flight": "tiket pesawat jakarta bali bulan desember",
        "allergy": "user alergi kacang tanah",
        "pdam": "nomor pelanggan pdam 12345678",
    }
    for key, text in topics.items():
        outcome = store.add(text)
        assert outcome["action"] == "stored", text
        ids[key] = outcome["memory"]["id"]
    return store, ids


def test_duplicate_observations_merge_into_one_memory():
    store = extraction.MemoryStore()
    first = store.add("user suka kopi tubruk")
    assert first["action"] == "stored"
    second = store.add("user suka kopi tubruk setiap pagi")
    assert second["action"] == "merged"
    assert second["merged_into"] == first["memory"]["id"]
    assert len(store) == 1
    memory = store.get(first["memory"]["id"])
    assert memory["support"] == 2
    assert len(memory["observations"]) == 2
    assert memory["status"] == "active"


def test_exact_repeat_merges_and_keeps_one_memory():
    store = extraction.MemoryStore()
    store.add("tagihan listrik pln bulan ini 350 ribu")
    outcome = store.add("tagihan listrik pln bulan ini 350 ribu")
    assert outcome["action"] == "merged"
    assert len(store) == 1
    assert store.all()[0]["support"] == 2


def test_conflicting_observation_flagged_not_overwritten():
    store = extraction.MemoryStore()
    liked = store.add("user suka kopi tubruk")
    disliked = store.add("user tidak suka kopi tubruk")
    assert disliked["action"] == "conflict"
    assert disliked["conflicts_with"] == [liked["memory"]["id"]]
    assert len(store) == 2
    old = store.get(liked["memory"]["id"])
    assert old["content"] == "user suka kopi tubruk"
    assert old["status"] == "conflicted"
    assert disliked["memory"]["id"] in old["conflicts_with"]
    new = store.get(disliked["memory"]["id"])
    assert new["content"] == "user tidak suka kopi tubruk"
    assert new["status"] == "conflicted"


def test_numeric_conflict_flagged_not_merged():
    store = extraction.MemoryStore()
    first = store.add("budget liburan rp5 juta")
    second = store.add("budget liburan rp10 juta")
    assert second["action"] == "conflict"
    assert second["conflicts_with"] == [first["memory"]["id"]]
    assert len(store) == 2
    assert store.get(first["memory"]["id"])["content"] == "budget liburan rp5 juta"


def test_repeated_conflict_merges_into_conflicted_memory():
    store = extraction.MemoryStore()
    store.add("user suka kopi tubruk")
    store.add("user tidak suka kopi tubruk")
    outcome = store.add("user tidak suka kopi tubruk sama sekali")
    assert outcome["action"] == "merged"
    assert len(store) == 2
    conflicted = [m for m in store.all() if m["status"] == "conflicted"]
    assert len(conflicted) == 2
    assert max(m["support"] for m in conflicted) == 2


def test_different_topic_stored_separately():
    store = extraction.MemoryStore()
    first = store.add("user suka kopi tubruk")
    second = store.add("user suka teh manis")
    assert first["action"] == "stored"
    assert second["action"] == "stored"
    assert len(store) == 2


def test_noise_not_stored():
    store = extraction.MemoryStore()
    for noise in [
        "ok",
        "terima kasih",
        "terima kasih banyak",
        "halo",
        "selamat pagi",
        "berapa harga laptop?",
        "apa kabar?",
    ]:
        outcome = store.add(noise)
        assert outcome["action"] == "ignored", noise
        assert outcome["memory"] is None
    assert len(store) == 0


def test_empty_observation_rejected():
    store = extraction.MemoryStore()
    with pytest.raises(ValueError):
        store.add("")
    with pytest.raises(ValueError):
        store.add("   ")


def test_is_memorable_and_extract_facts():
    assert extraction.is_memorable("user suka kopi tubruk") is True
    assert extraction.is_memorable("harga laptop asus vivobook 14 juta") is True
    assert extraction.is_memorable("ok") is False
    assert extraction.is_memorable("berapa harga laptop?") is False
    assert extraction.extract_facts("user suka kopi tubruk. user benci teh manis.") == [
        "user suka kopi tubruk",
        "user benci teh manis",
    ]
    assert extraction.extract_facts("ok") == []
    with pytest.raises(ValueError):
        extraction.is_memorable(123)


def test_polarity_detection():
    assert extraction.detect_polarity("user suka kopi tubruk") == 1
    assert extraction.detect_polarity("user tidak suka kopi tubruk") == -1
    assert extraction.detect_polarity("user benci teh manis") == -1
    assert extraction.detect_polarity("tagihan listrik pln bulan ini") == 0


def test_merged_memory_keeps_longer_canonical_text():
    store = extraction.MemoryStore()
    store.add("user suka kopi tubruk")
    store.add("user suka kopi tubruk setiap pagi hari")
    assert len(store) == 1
    assert store.all()[0]["content"] == "user suka kopi tubruk setiap pagi hari"


def test_recall_returns_most_relevant_first():
    store, ids = _seed_store()
    ranked = store.recall("kopi tubruk favorit user", limit=3)
    assert ranked[0]["id"] == ids["coffee"]
    assert ranked[0]["evidence_id"] == ids["coffee"]
    assert ranked[0]["score"] > ranked[1]["score"]
    ranked = store.recall("berapa harga laptop asus vivobook", limit=3)
    assert ranked[0]["id"] == ids["laptop"]
    ranked = store.recall("tagihan listrik pln", limit=3)
    assert ranked[0]["id"] == ids["pln"]


def test_recall_at_k_measured():
    store, ids = _seed_store()
    queries = [
        ("kopi tubruk favorit user", ids["coffee"]),
        ("berapa harga laptop asus vivobook", ids["laptop"]),
        ("tagihan listrik pln", ids["pln"]),
        ("jadwal gym hari apa", ids["gym"]),
        ("buku apa yang sedang dibaca user", ids["book"]),
        ("tiket pesawat ke bali", ids["flight"]),
    ]
    measured = store.measure_recall(queries, ks=(1, 3, 5))
    assert measured[3] == 1.0
    assert measured[5] == 1.0
    assert measured[1] >= 0.8
    print(f"\nmeasured recall: {measured}")


def test_recall_at_k_counts_partial_hits():
    store, ids = _seed_store()
    queries = [
        ("kopi tubruk favorit user", ids["coffee"]),
        ("tiket pesawat ke bali", ids["allergy"]),
    ]
    assert extraction.recall_at_k(store.all(), queries, 1) == 0.5
    assert extraction.recall_at_k(store.all(), queries, 3) == 0.5
    with pytest.raises(ValueError):
        extraction.recall_at_k(store.all(), [], 1)
    with pytest.raises(ValueError):
        extraction.recall_at_k(store.all(), queries, 0)


def test_rank_memories_is_pure_and_validates_input():
    memories = [
        {
            "id": "m1",
            "content": "user suka kopi tubruk",
            "support": 1,
            "status": "active",
            "created_at": None,
        },
        {
            "id": "m2",
            "content": "tagihan listrik pln bulan ini",
            "support": 1,
            "status": "active",
            "created_at": None,
        },
    ]
    ranked = extraction.rank_memories(memories, "kopi tubruk", embedder=HashEmbedding(), limit=2)
    # m2 shares no signal with the query, so its zero base score
    # drops it instead of filling the ranking by tie-break.
    assert [r["id"] for r in ranked] == ["m1"]
    assert ranked[0]["score"] > 0
    assert extraction.rank_memories([], "kopi") == []
    with pytest.raises(ValueError):
        extraction.rank_memories(memories, "")
    with pytest.raises(ValueError):
        extraction.rank_memories(memories, "kopi", limit=0)


def test_conflict_helpers_agree_with_store():
    coffee = {
        "content": "user suka kopi tubruk",
        "entities": [],
        "concepts": ["kopi", "suka", "tubruk", "user"],
    }
    nocoffee = {
        "content": "user tidak suka kopi tubruk",
        "entities": [],
        "concepts": ["kopi", "suka", "tidak", "tubruk", "user"],
    }
    tea = {
        "content": "user suka teh manis",
        "entities": [],
        "concepts": ["manis", "suka", "teh", "user"],
    }
    assert extraction.memories_conflict(coffee, nocoffee) is True
    assert extraction.memories_conflict(coffee, tea) is False
    assert extraction.is_duplicate(coffee, nocoffee, embedder=HashEmbedding()) is False
    assert extraction.is_duplicate(coffee, coffee, embedder=HashEmbedding()) is True
