"""Tests for the LightRAG query modes.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)
"""

import pytest

from puspa.memory.embeddings import HashEmbedding
from puspa.retrieval.knowledge_graph import KnowledgeGraph
from puspa.retrieval.modes import QUERY_MODES, query
from tests.test_retrieval_hipporag import PRICE_DOCS, build_price_graph

EMBEDDER = HashEmbedding()
QUESTION = "Is the cheap Galaxy S24 deal trustworthy?"


def _ids(results):
    return [r["passage_id"] for r in results]


def test_modes_are_all_known():
    assert QUERY_MODES == ("local", "global", "hybrid", "naive", "mix")


def test_local_returns_only_seed_attached_passages():
    graph = build_price_graph()
    results = query(graph, QUESTION, mode="local", embedder=EMBEDDER, top_k=10)
    # Every local passage mentions a seed entity (the product);
    # the fraud report is two hops away and never appears.
    assert "d4#p0" not in _ids(results)
    assert "d1#p0" in _ids(results)
    assert all(r["mode"] == "local" for r in results)
    assert all(r["evidence_id"] == r["passage_id"] for r in results)


def test_global_reaches_two_hops_out():
    graph = build_price_graph()
    results = query(graph, QUESTION, mode="global", embedder=EMBEDDER, top_k=10)
    # Global walks the relationship chains, so the fraud report
    # (product, promo, seller, fraud report) is recalled even
    # though it shares no word with the query.
    assert "d4#p0" in _ids(results)
    report = next(r for r in results if r["passage_id"] == "d4#p0")
    assert report["hops"] >= 1
    assert all(r["mode"] == "global" for r in results)


def test_naive_ignores_the_graph():
    graph = build_price_graph()
    results = query(graph, QUESTION, mode="naive", embedder=EMBEDDER, top_k=10)
    # Pure vector similarity: the zero-overlap fraud report ranks
    # last, below every passage sharing a query word.
    assert _ids(results)[0] in ("d1#p0", "d2#p0")
    assert "d4#p0" not in _ids(results)[:5]
    assert all(r["mode"] == "naive" for r in results)


def test_modes_differ_on_the_same_query():
    graph = build_price_graph()
    local = _ids(query(graph, QUESTION, mode="local",
                       embedder=EMBEDDER, top_k=5))
    glob = _ids(query(graph, QUESTION, mode="global",
                      embedder=EMBEDDER, top_k=5))
    naive = _ids(query(graph, QUESTION, mode="naive",
                       embedder=EMBEDDER, top_k=5))
    assert local != glob
    assert local != naive
    assert glob != naive


def test_hybrid_and_mix_merge_modes():
    graph = build_price_graph()
    hybrid = query(graph, QUESTION, mode="hybrid",
                   embedder=EMBEDDER, top_k=10)
    mix = query(graph, QUESTION, mode="mix", embedder=EMBEDDER, top_k=10)
    # Hybrid recalls the two-hop fraud report (from global) and
    # the seed passages (from local).
    assert "d4#p0" in _ids(hybrid)
    assert "d1#p0" in _ids(hybrid)
    # Mix adds the naive leg: a passage found by more than one
    # mode records every mode that found it.
    assert "d4#p0" in _ids(mix)
    shared = next(r for r in mix if r["passage_id"] == "d1#p0")
    assert len(shared["modes"]) >= 2
    assert all(r["mode"] == "mix" for r in mix)


def test_query_rejects_bad_input():
    graph = build_price_graph()
    with pytest.raises(ValueError):
        query(graph, "", mode="mix")
    with pytest.raises(ValueError):
        query(graph, QUESTION, mode="bogus")
    with pytest.raises(ValueError):
        query(graph, QUESTION, mode="mix", top_k=0)
