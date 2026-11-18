"""Tests for multi-hop retrieval by Personalized PageRank.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)
"""

import pytest

from puspa.memory.embeddings import HashEmbedding
from puspa.retrieval.hipporag import (
    HippoRAGRetriever,
    find_seed_nodes,
    personalized_pagerank,
    seed_weights,
)
from puspa.retrieval.knowledge_graph import KnowledgeGraph
from puspa.retrieval.reranker import DeterministicReranker

PRICE_DOCS = {
    "d1": "Samsung Galaxy S24 SM-S921B is a phone. "
    "Samsung Galaxy S24 costs Rp12.999.000 at Tokopedia.",
    "d2": "Toko Gadget Murah sells Samsung Galaxy S24. "
    "Toko Gadget Murah offers a promo: Samsung Galaxy S24 "
    "for Rp5.999.000, half price.",
    "d3": "Toko Gadget Murah is a seller on Tokopedia. "
    "Buyer Ani reports Toko Gadget Murah for a fake promo.",
    "d4": "Fraud report FR-88: Toko Gadget Murah ran a fake promo. "
    "The seller never shipped the phone.",
    "d5": "Xiaomi Redmi Note 13 is a phone. Xiaomi Redmi Note 13 costs Rp3.199.000 at Shopee.",
    "d6": "Toko Resmi Xiaomi sells Xiaomi Redmi Note 13. "
    "Toko Resmi Xiaomi offers a promo: Xiaomi Redmi Note 13 "
    "for Rp2.999.000.",
    "d7": "Asus Vivobook 14 is a laptop. Asus Vivobook 14 costs Rp9.499.000 at Blibli.",
    "d8": "Toko Laptop Jaya sells Asus Vivobook 14. "
    "Toko Laptop Jaya offers free shipping for Asus Vivobook 14.",
    "d9": "Fraud report FR-91: Toko Ponsel Abal ran a fake promo.",
    "d10": "Toko Ponsel Abal sells Asus Vivobook 14. "
    "Toko Ponsel Abal offers a promo: Asus Vivobook 14 "
    "for Rp4.000.000.",
    "d11": "Samsung Galaxy A55 SM-A556E is a phone. "
    "Samsung Galaxy A55 costs Rp5.999.000 at Tokopedia.",
    "d12": "The PLN bill for March is Rp250.000.",
}


def build_price_graph():
    graph = KnowledgeGraph()
    for doc_id, text in PRICE_DOCS.items():
        graph.add_document(doc_id, text)
    return graph


def test_pagerank_scores_fall_with_hops():
    graph = KnowledgeGraph()
    graph.add_document("c1", "Alpha connects Beta.")
    graph.add_document("c2", "Beta connects Gamma.")
    graph.add_document("c3", "Gamma connects Delta.")
    graph.add_document("c4", "Zulu sits alone.")
    scores = personalized_pagerank(graph, ["alpha"])
    # The walk spreads outward: the two-hop node scores below the
    # one-hop node, the three-hop node below that, and the
    # disconnected node below every node on the chain. (On an
    # undirected graph the seed's degree-2 neighbor can outscore
    # the degree-1 seed itself, so the seed is only asserted to
    # outrank the two-hop node.)
    assert scores["alpha"] > scores["gamma"]
    assert scores["beta"] > scores["gamma"] > scores["delta"]
    # The walk never reaches the disconnected node, so it scores
    # below every node on the chain.
    assert scores["delta"] > scores["zulu"]
    assert scores["zulu"] < 0.01
    # Scores are a distribution: they sum to 1.
    assert abs(sum(scores.values()) - 1.0) < 1e-6


def test_pagerank_rejects_bad_damping_and_empty_seeds():
    graph = KnowledgeGraph()
    graph.add_document("c1", "Alpha connects Beta.")
    with pytest.raises(ValueError):
        personalized_pagerank(graph, ["alpha"], damping=1.5)
    assert personalized_pagerank(graph, ["missing"]) == {"alpha": 0.0, "beta": 0.0}
    assert personalized_pagerank(KnowledgeGraph(), ["alpha"]) == {}


def test_seed_weights_favor_the_stronger_match():
    graph = build_price_graph()
    weights = seed_weights(graph, "Is the cheap Galaxy S24 deal trustworthy?")
    assert weights["samsung galaxy s24"] > weights["samsung galaxy a55"]
    assert weights["samsung galaxy s24"] > 0.5
    assert "toko gadget murah" not in weights
    assert find_seed_nodes(graph, "Is the cheap Galaxy S24 deal trustworthy?")[0] == (
        "samsung galaxy s24"
    )


def test_retriever_reaches_the_two_hop_fraud_report():
    graph = build_price_graph()
    retriever = HippoRAGRetriever(graph, embedder=HashEmbedding())
    results = retriever.retrieve("Is the cheap Galaxy S24 deal trustworthy?", top_k=10)
    ids = [r["passage_id"] for r in results]
    # The fraud report shares no word with the query, so only the
    # walk (product, promo, seller, fraud report) can reach it.
    assert "d4#p0" in ids
    report = next(r for r in results if r["passage_id"] == "d4#p0")
    assert report["hops"] == 1
    assert report["graph_score"] > 0
    assert report["evidence_id"] == "d4#p0"
    assert report["seed_nodes"] == ["samsung galaxy s24", "samsung galaxy a55"]


def test_retriever_falls_back_to_vector_without_seeds():
    graph = build_price_graph()
    retriever = HippoRAGRetriever(graph, embedder=HashEmbedding())
    results = retriever.retrieve("quantum entanglement banana", top_k=3)
    assert len(results) == 3
    assert all(r["seed_nodes"] == [] for r in results)
    assert all(r["graph_score"] == 0.0 for r in results)


def test_retriever_with_reranker_keeps_the_walk():
    graph = build_price_graph()
    retriever = HippoRAGRetriever(graph, embedder=HashEmbedding())
    reranker = DeterministicReranker(embedder=HashEmbedding())
    plain = retriever.retrieve("Is the cheap Galaxy S24 deal trustworthy?", top_k=10)
    reranked = retriever.retrieve(
        "Is the cheap Galaxy S24 deal trustworthy?", top_k=10, reranker=reranker
    )
    # Reranking refines the walk instead of discarding it: the
    # two-hop fraud report survives, and every result carries the
    # reranker score next to the retrieval score.
    assert "d4#p0" in [r["passage_id"] for r in reranked]
    assert all("rerank_score" in r for r in reranked)
    scores = [r["score"] for r in reranked]
    assert scores == sorted(scores, reverse=True)
    # The reranker did real work: at least one result's blended
    # score differs from its plain retrieval score.
    plain_by_id = {r["passage_id"]: r["score"] for r in plain}
    assert any(abs(r["score"] - plain_by_id[r["passage_id"]]) > 1e-9 for r in reranked)


def test_retriever_rejects_bad_input():
    graph = build_price_graph()
    retriever = HippoRAGRetriever(graph)
    with pytest.raises(ValueError):
        retriever.retrieve("", top_k=5)
    with pytest.raises(ValueError):
        retriever.retrieve("Galaxy S24", top_k=0)
    with pytest.raises(ValueError):
        HippoRAGRetriever(graph, weights=(0.5, 0.5))
