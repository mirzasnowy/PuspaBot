"""Tests for the retrieval wiring into episodic recall and products.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)
The database-backed path (`search_episodes_pipeline` against
Postgres) lives in tests/test_retrieval_wiring_db.py, which the
coordinator runs.
"""

from puspa.graphs.identity import product_passages, rank_products_pipeline
from puspa.memory.episodic import episodes_to_graph

PRODUCTS = [
    {"id": "p1", "brand": "Samsung", "series": "Galaxy S24",
     "model_number": "SM-S921B", "category": "phone"},
    {"id": "p2", "brand": "Samsung", "series": "Galaxy A55",
     "model_number": "SM-A556E", "category": "phone"},
    {"id": "p3", "brand": "Asus", "series": "Vivobook 14",
     "model_number": "VIVOBOOK14", "category": "laptop"},
]


def test_episodes_to_graph_maps_passages_back_to_episodes():
    episodes = [
        {"id": "ep1", "content": "Toko Gadget Murah sells Samsung Galaxy S24."},
        {"id": "ep2", "content": "Fraud report FR-88: Toko Gadget Murah ran a fake promo."},
    ]
    graph = episodes_to_graph(episodes)
    assert "samsung galaxy s24" in graph.nodes
    assert "toko gadget murah" in graph.nodes
    assert graph.passages["ep1#p0"].startswith("Toko Gadget Murah")
    assert graph.passage_entities["ep2#p0"] >= {"toko gadget murah", "fr-88"}


def test_episodes_to_graph_skips_empty_content():
    graph = episodes_to_graph([{"id": "ep1", "content": ""}])
    assert graph.passage_count == 0


def test_product_passages_shape():
    passages = product_passages(PRODUCTS)
    assert passages[0] == {
        "id": "product:p1",
        "content": "Samsung Galaxy S24 SM-S921B phone",
    }


def test_rank_products_pipeline_finds_the_product():
    ranked = rank_products_pipeline(PRODUCTS, "Galaxy S24", top_k=3)
    assert ranked[0]["product"]["id"] == "p1"
    assert ranked[0]["level"] == "entity"
    assert ranked[0]["mode"] == "local"
    # No price is returned here, so no evidence-free price
    # exists; price evidence still comes from the database path.
    assert ranked[0]["evidence_ids"] == []
    ranked_laptop = rank_products_pipeline(PRODUCTS, "Asus laptop", top_k=3)
    assert ranked_laptop[0]["product"]["id"] == "p3"


def test_rank_products_pipeline_rejects_bad_input():
    import pytest

    with pytest.raises(ValueError):
        rank_products_pipeline(PRODUCTS, "")
