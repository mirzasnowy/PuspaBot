"""Tests for the reranker and query decomposition.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)
"""

import pytest

from puspa.memory.embeddings import HashEmbedding
from puspa.retrieval.reranker import (
    CrossEncoderReranker,
    DeterministicReranker,
    RerankerUnavailable,
    decompose_query,
    make_reranker,
    retrieve_with_decomposition,
)

EMBEDDER = HashEmbedding()


def test_deterministic_reranker_changes_the_order():
    reranker = DeterministicReranker(
        embedder=EMBEDDER, weights=(0.2, 0.2, 0.6))
    candidates = [
        {"passage_id": "near-miss",
         "text": "Samsung Galaxy S24 costs Rp12.999.000 at Tokopedia."},
        {"passage_id": "graph-close",
         "text": "The seller never shipped the phone."},
    ]
    # Input order is the vector order (the price passage shares
    # the query words). With the graph weight dominant, the
    # passage one hop from the seeds wins even though it shares
    # no query word.
    ranked = reranker.rerank(
        "Is the Galaxy S24 deal trustworthy?", candidates,
        graph_distances={"near-miss": 0, "graph-close": 1})
    assert [r["passage_id"] for r in ranked] == [
        "near-miss", "graph-close"]
    # With the graph term dominant and the near passage far from
    # the seeds, the order flips.
    ranked = reranker.rerank(
        "Is the Galaxy S24 deal trustworthy?", candidates,
        graph_distances={"near-miss": 5, "graph-close": 0})
    assert [r["passage_id"] for r in ranked] == [
        "graph-close", "near-miss"]
    assert all("rerank_score" in r for r in ranked)
    assert all("rerank_signals" in r for r in ranked)
    scores = [r["rerank_score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)


def test_deterministic_reranker_scores_signals():
    reranker = DeterministicReranker(embedder=EMBEDDER)
    detail = reranker.score("galaxy s24 price",
                            "Samsung Galaxy S24 costs Rp12.999.000.")
    assert detail["lexical"] > 0.5
    assert detail["vector"] > 0.0
    assert detail["graph"] == 0.0
    close = reranker.score("galaxy s24 price",
                           "Samsung Galaxy S24 costs Rp12.999.000.",
                           graph_distance=0)
    assert close["graph"] == 1.0
    assert close["score"] > detail["score"]
    with pytest.raises(ValueError):
        reranker.rerank("", [{"text": "x"}])


def test_cross_encoder_fastembed_backend_is_honestly_unavailable():
    # fastembed 0.9.1 ships no reranker, so that backend raises
    # instead of pretending a cross-encoder exists. The working
    # backend is sentence-transformers, covered in
    # tests/test_reranker_cross_encoder.py.
    with pytest.raises(RerankerUnavailable):
        CrossEncoderReranker.from_fastembed()
    with pytest.raises(RerankerUnavailable):
        make_reranker("cross_encoder", backend="fastembed")
    with pytest.raises(ValueError):
        make_reranker("bogus")
    with pytest.raises(ValueError):
        make_reranker("cross_encoder", backend="bogus")


def test_cross_encoder_reranker_uses_an_injected_scorer():
    # Any cross-encoder plugs in as one callable; a stub proves
    # the seam re-orders by the injected scores.
    scores = {"a": 0.1, "b": 0.9, "c": 0.5}
    reranker = CrossEncoderReranker(
        lambda query, text: scores[text])
    ranked = reranker.rerank("q", [
        {"passage_id": "p1", "text": "a"},
        {"passage_id": "p2", "text": "b"},
        {"passage_id": "p3", "text": "c"},
    ])
    assert [r["passage_id"] for r in ranked] == ["p2", "p3", "p1"]


def test_decompose_query_splits_and_keeps_simple_queries():
    assert decompose_query(
        "Which seller ran a fake promo for Galaxy S24 and what was "
        "the price?") == [
        "Which seller ran a fake promo for Galaxy S24",
        "what was the price"]
    assert decompose_query("What is the price of Galaxy S24?") == [
        "What is the price of Galaxy S24"]
    assert decompose_query("Galaxy S24 vs Redmi Note 13") == [
        "Galaxy S24", "Redmi Note 13"]
    with pytest.raises(ValueError):
        decompose_query("")


def test_retrieve_with_decomposition_merges_by_rrf():
    def fake_retrieve(sub_query, top_k):
        if "promo" in sub_query:
            return [{"passage_id": "p-promo", "score": 0.9},
                    {"passage_id": "p-shared", "score": 0.8}]
        return [{"passage_id": "p-shared", "score": 0.9},
                {"passage_id": "p-price", "score": 0.8}]

    merged = retrieve_with_decomposition(
        fake_retrieve, "fake promo and price", top_k=3)
    # The passage ranked first by both sub-questions wins the
    # fusion; both legs contribute their own passage too.
    assert [r["passage_id"] for r in merged] == [
        "p-shared", "p-promo", "p-price"]
    assert all("rrf_score" in r for r in merged)
    # A single sub-question runs the plain retrieval unchanged.
    single = retrieve_with_decomposition(
        fake_retrieve, "fake promo", top_k=2)
    assert [r["passage_id"] for r in single] == ["p-promo", "p-shared"]
