"""Tests for hybrid recall (G7). Pure logic, no database.

A stub embedder with exact 4-d vectors makes the cosine
thresholds deterministic instead of relying on hash collisions.
"""

import math
from datetime import datetime, timezone

from puspa.memory import recall

UTC = timezone.utc


class StubEmbedder:
    """Embedder returning caller-chosen vectors per exact text."""

    dim = 4

    def __init__(self, vectors):
        self._vectors = vectors

    def embed(self, text):
        return list(self._vectors[text])


def _fact(fid, key, content, when):
    return {
        "id": fid, "user_id": "user-1", "key": key, "content": content,
        "status": "active", "valid_from": when, "valid_until": None,
        "superseded_by": None, "source": "user_stated",
        "created_at": when, "updated_at": when,
    }


def _t(day):
    return datetime(2026, 11, day, 12, 0, tzinfo=UTC)


def test_fts5_bm25_ranks_keyword_match_first():
    index = recall.FtsIndex()
    try:
        index.index_fact(_fact("a", "coffee", "I drink coffee every morning", _t(10)))
        index.index_fact(_fact("b", "tea", "I drink tea in the evening", _t(10)))
        index.index_fact(_fact("c", "other", "unrelated weather report", _t(10)))
        hits = index.search_bm25("coffee")
        assert [h[0] for h in hits] == ["a"]
        assert hits[0][1] <= 0  # FTS5 bm25: lower (more negative) is better
    finally:
        index.close()


def test_fts5_rebuild_is_source_of_truth_replay():
    index = recall.FtsIndex()
    try:
        facts = [
            _fact("a", "coffee", "coffee in the morning", _t(10)),
            _fact("b", "tea", "tea in the evening", _t(10)),
        ]
        index.rebuild(facts)
        assert [h[0] for h in index.search_bm25("tea")] == ["b"]
        index.rebuild(facts[:1])
        assert index.search_bm25("tea") == []
    finally:
        index.close()


def test_hybrid_recall_keyword_hit_lowers_threshold():
    # Query vector [1,0,0,0]. Fact A cosine 0.55 WITH a keyword hit
    # (its content shares the query token) is recalled; fact B with
    # the same cosine and no keyword hit is not.
    vectors = {
        "coffee": [1.0, 0.0, 0.0, 0.0],
        "coffee morning ritual": [0.55, math.sqrt(1 - 0.55 ** 2), 0.0, 0.0],
        "unrelated evening walk": [0.55, math.sqrt(1 - 0.55 ** 2), 0.0, 0.0],
        "coffee strong brew": [0.4, math.sqrt(1 - 0.4 ** 2), 0.0, 0.0],
    }
    embedder = StubEmbedder(vectors)
    facts = [
        _fact("a", "morning", "coffee morning ritual", _t(10)),
        _fact("b", "evening", "unrelated evening walk", _t(10)),
        _fact("c", "brew", "coffee strong brew", _t(10)),
    ]
    results = recall.hybrid_recall("coffee", facts, embedder=embedder)
    ids = [r["fact"]["id"] for r in results]
    assert ids == ["a"]  # B: 0.55 < 0.6 without keyword; C: 0.4 < 0.5
    assert results[0]["keyword_hit"] is True
    assert abs(results[0]["cosine"] - 0.55) < 1e-9


def test_hybrid_recall_high_cosine_without_keyword_passes():
    vectors = {
        "coffee": [1.0, 0.0, 0.0, 0.0],
        "dark roast beans": [0.9, math.sqrt(1 - 0.9 ** 2), 0.0, 0.0],
    }
    embedder = StubEmbedder(vectors)
    facts = [_fact("a", "beans", "dark roast beans", _t(10))]
    results = recall.hybrid_recall("coffee", facts, embedder=embedder)
    assert [r["fact"]["id"] for r in results] == ["a"]
    assert results[0]["keyword_hit"] is False


def test_hybrid_recall_fusion_order_and_max_14():
    # 20 short facts sharing the query token "coffee": each has
    # cosine 1/sqrt(2) ~= 0.707 with the hash embedder, so all pass
    # the threshold; only the best 14 fused ranks come back.
    facts = [
        _fact(f"f{i:02d}", f"topic{i}", f"coffee note{i}", _t(10))
        for i in range(20)
    ]
    results = recall.hybrid_recall("coffee", facts)
    assert len(results) == 14
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert all(r["cosine"] >= 0.6 for r in results)


def test_hybrid_recall_fusion_prefers_combined_signals():
    # Same cosine for both facts; the one with the keyword hit and
    # the name match outranks the one with neither.
    vectors = {
        "coffee": [1.0, 0.0, 0.0, 0.0],
        "coffee beans": [0.8, 0.6, 0.0, 0.0],
        "dark roast": [0.8, 0.6, 0.0, 0.0],
    }
    embedder = StubEmbedder(vectors)
    facts = [
        _fact("plain", "roast", "dark roast", _t(10)),
        _fact("rich", "coffee", "coffee beans", _t(10)),
    ]
    results = recall.hybrid_recall("coffee", facts, embedder=embedder)
    assert [r["fact"]["id"] for r in results] == ["rich", "plain"]
    assert results[0]["score"] > results[1]["score"]


def test_hybrid_recall_ignores_non_active_and_empty():
    facts = [_fact("a", "x", "coffee", _t(10))]
    facts[0]["status"] = "superseded"
    assert recall.hybrid_recall("coffee", facts) == []
    assert recall.hybrid_recall("coffee", []) == []
    import pytest

    with pytest.raises(ValueError):
        recall.hybrid_recall("   ", facts)
