"""Tests for the schema-less knowledge graph.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)
"""

import pytest

from puspa.retrieval.knowledge_graph import (
    DeterministicExtractor,
    JsonTripleExtractor,
    KnowledgeGraph,
)


def test_deterministic_extractor_reads_triples():
    extractor = DeterministicExtractor()
    found = extractor.extract(
        "Toko Gadget Murah sells Samsung Galaxy S24.", "doc#p0")
    names = {entity.name for entity in found.entities}
    assert "toko gadget murah" in names
    assert "samsung galaxy s24" in names
    assert ("toko gadget murah", "sells", "samsung galaxy s24") in [
        (t.subject, t.relation, t.object) for t in found.triples
    ]


def test_deterministic_extractor_reads_kind_and_code_phrase():
    extractor = DeterministicExtractor()
    found = extractor.extract(
        "Xiaomi Redmi Note 13 is a phone. "
        "Samsung Galaxy S24 SM-S921B is a phone.", "doc#p0")
    by_name = {entity.name: entity for entity in found.entities}
    assert "xiaomi redmi note 13" in by_name
    assert by_name["xiaomi redmi note 13"].kind == "phone"
    assert "samsung galaxy s24" in by_name
    assert "sm-s921b" in by_name


def test_deterministic_extractor_rejects_bad_input():
    extractor = DeterministicExtractor()
    with pytest.raises(ValueError):
        extractor.extract("", "doc#p0")
    with pytest.raises(ValueError):
        extractor.extract(42, "doc#p0")


def test_json_extractor_uses_a_stub_completion():
    def fake_complete(prompt):
        assert "Toko Gadget Murah" in prompt
        return (
            '{"entities": [{"name": "Toko Gadget Murah", "kind": "seller"}], '
            '"triples": [{"subject": "Toko Gadget Murah", '
            '"relation": "sells", "object": "Samsung Galaxy S24"}]}'
        )

    found = JsonTripleExtractor(fake_complete).extract(
        "Toko Gadget Murah sells Samsung Galaxy S24.", "doc#p0")
    assert found.entities[0].name == "toko gadget murah"
    assert found.entities[0].kind == "seller"
    assert (found.triples[0].subject, found.triples[0].relation,
            found.triples[0].object) == (
        "toko gadget murah", "sells", "samsung galaxy s24")


def test_json_extractor_rejects_bad_json():
    extractor = JsonTripleExtractor(lambda prompt: "not json")
    with pytest.raises(ValueError):
        extractor.extract("Some text.", "doc#p0")
    with pytest.raises(ValueError):
        JsonTripleExtractor("not a callable")


def test_add_document_merges_shared_entities():
    graph = KnowledgeGraph()
    first = graph.add_document(
        "d1", "Samsung Galaxy S24 SM-S921B is a phone.")
    second = graph.add_document(
        "d2", "Toko Gadget Murah sells Samsung Galaxy S24.")
    assert first["new_nodes"] == 2  # samsung galaxy s24, sm-s921b
    assert second["new_nodes"] == 1  # toko gadget murah (product shared)
    # The shared product node is one node, not two, and it gains
    # the second document as a source.
    assert graph.node_count == first["nodes"] + second["new_nodes"]
    assert "d2#p0" in graph.nodes["samsung galaxy s24"].source_ids
    assert "d1#p0" in graph.nodes["samsung galaxy s24"].source_ids


def test_add_document_is_incremental():
    graph = KnowledgeGraph()
    graph.add_document("d1", "Samsung Galaxy S24 is a phone.")
    before_nodes = graph.node_count
    before_edges = graph.edge_count
    stats = graph.add_document("d2", "Toko Ponsel Abal is a seller.")
    # Only the new document is processed: nothing from d1 is
    # rebuilt, and the new node is exactly the new entity ("seller"
    # is a kind, not a node).
    assert stats["new_nodes"] == 1
    assert graph.nodes["toko ponsel abal"].kind == "seller"
    assert graph.node_count == before_nodes + 1
    assert graph.edge_count == before_edges + stats["new_edges"]
    with pytest.raises(ValueError):
        graph.add_document("d1", "Samsung Galaxy S24 is a phone.")


def test_remove_document_drops_orphans_only():
    graph = KnowledgeGraph()
    graph.add_document(
        "d1", "Toko Gadget Murah sells Samsung Galaxy S24.")
    graph.add_document(
        "d2", "Fraud report FR-88: Toko Gadget Murah ran a fake promo.")
    assert "fr-88" in graph.nodes
    removed = graph.remove_document("d2")
    assert "fr-88" not in graph.nodes
    assert removed["removed_nodes"] >= 1
    # The shared seller node survives because d1 still mentions it.
    assert "toko gadget murah" in graph.nodes
    assert "samsung galaxy s24" in graph.nodes
    with pytest.raises(KeyError):
        graph.remove_document("d2")


def test_neighbors_and_relations():
    graph = KnowledgeGraph()
    graph.add_document(
        "d1", "Toko Gadget Murah sells Samsung Galaxy S24. "
        "Toko Gadget Murah offers a promo.")
    assert "samsung galaxy s24" in graph.neighbors("toko gadget murah")
    assert "samsung galaxy s24" in graph.neighbors(
        "toko gadget murah", relation="sells")
    assert "samsung galaxy s24" not in graph.neighbors(
        "toko gadget murah", relation="offers")
    assert ("toko gadget murah", "sells", "samsung galaxy s24") in (
        graph.relations("toko gadget murah"))
    assert graph.passages_for_nodes(["samsung galaxy s24"]) == ["d1#p0"]
