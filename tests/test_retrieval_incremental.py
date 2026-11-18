"""Tests for incremental graph update (LightRAG deepen).

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)

What is proved here, on the Wave 1 price graph (products,
promos, sellers, fraud reports):

- a new passage makes its new entity retrievable;
- old entities, edges and passages survive an update;
- adding the same passage or document twice does not explode
  duplicates (same id, or the same text under another id);
- repeating an existing triple raises its edge weight instead
  of creating a second edge;
- one passage or document can be replaced in place while the
  rest of the graph is untouched;
- recall on the 8 hand-written multi-hop queries from Wave 1
  does not drop after an incremental update.

One honest limit is also locked in as a test, not hidden:
adding new promo passages attached to the fraud-linked seller
dilutes the top 10 (the fraud report falls from rank 8 to
outside the top 10 on two queries), while the report stays in
the graph and stays reachable at top 15. That is a ranking
effect of the new seed-adjacent passages, not data loss.
"""

import pytest

from puspa.memory.embeddings import HashEmbedding
from puspa.retrieval.hipporag import HippoRAGRetriever
from puspa.retrieval.incremental import IncrementalUpdater
from puspa.retrieval.knowledge_graph import KnowledgeGraph
from puspa.retrieval.reranker import DeterministicReranker
from tests.test_retrieval_eval import QUERIES
from tests.test_retrieval_hipporag import PRICE_DOCS, build_price_graph

EMBEDDER = HashEmbedding()

#: New product line from a new seller, plus a fraud report on
#: that seller. Topically separate from the 8 Wave 1 queries,
#: which is the normal shape of an incremental update: new
#: listings arrive, old answers must keep working.
NEW_LINE_DOCS = {
    "d13": "Sony WH-1000XM5 is a headphone. "
           "Sony WH-1000XM5 costs Rp4.999.000 at Shopee.",
    "d14": "Toko Audio Jaya sells Sony WH-1000XM5. "
           "Toko Audio Jaya offers a promo: Sony WH-1000XM5 "
           "for Rp3.999.000.",
    "d15": "Fraud report FR-99: Toko Audio Jaya ran a fake promo. "
           "The seller never shipped the headphone.",
}

#: New promo passages attached to the seller the fraud report
#: is about. Used only by the dilution test.
SAME_SELLER_PROMO = {
    "d13": "Google Pixel 9 is a phone. "
           "Google Pixel 9 costs Rp10.999.000 at Tokopedia.",
    "d14": "Toko Gadget Murah sells Google Pixel 9. "
           "Toko Gadget Murah offers a promo: Google Pixel 9 "
           "for Rp6.999.000.",
}


def build_updater():
    updater = IncrementalUpdater()
    updater.add_documents(PRICE_DOCS)
    return updater


def _recall_at_k(graph, k):
    retriever = HippoRAGRetriever(graph, embedder=EMBEDDER)
    reranker = DeterministicReranker(embedder=EMBEDDER)
    hits = 0
    for question, targets in QUERIES:
        ids = {r["passage_id"]
               for r in retriever.retrieve(
                   question, top_k=k, reranker=reranker)[:k]}
        if ids & targets:
            hits += 1
    return hits / len(QUERIES)


def test_updater_builds_same_graph_as_direct_add():
    updater = build_updater()
    direct = build_price_graph()
    assert updater.graph.node_count == direct.node_count
    assert updater.graph.edge_count == direct.edge_count
    assert updater.graph.passage_count == direct.passage_count
    assert set(updater.graph.nodes) == set(direct.nodes)
    assert set(updater.graph.edges) == set(direct.edges)


def test_new_passage_makes_new_entity_retrievable():
    updater = build_updater()
    assert "google pixel" not in updater.graph.nodes
    stats = updater.add_documents(SAME_SELLER_PROMO)
    assert stats["added_passages"] == 4
    assert stats["new_nodes"] >= 1
    assert "google pixel" in updater.graph.nodes
    # Four passages mention the new product.
    assert updater.node_weight("google pixel") == 4
    retriever = HippoRAGRetriever(updater.graph, embedder=EMBEDDER)
    ids = [r["passage_id"] for r in retriever.retrieve(
        "Where can I buy Google Pixel 9?", top_k=5)]
    assert "d14#p0" in ids
    assert "d13#p0" in ids


def test_old_entities_and_edges_survive_update():
    updater = build_updater()
    before = updater.snapshot()
    stats = updater.add_documents(NEW_LINE_DOCS)
    assert stats["added_passages"] == 6  # 2 passages per document
    # Nothing old is rebuilt or dropped: every old node, edge
    # and passage is still there, with the same identity.
    assert before["nodes"] <= set(updater.graph.nodes)
    assert before["edges"] <= set(updater.graph.edges)
    assert before["passages"] <= set(updater.graph.passages)
    added = updater.added_since(before)
    assert "sony" in added["nodes"]
    assert "wh-1000xm5" in added["nodes"]
    assert "toko audio jaya" in added["nodes"]
    assert "fr-99" in added["nodes"]
    assert updater.graph.node_count == (
        len(before["nodes"]) + len(added["nodes"]))
    assert updater.graph.edge_count == (
        len(before["edges"]) + len(added["edges"]))


def test_same_passage_twice_is_idempotent():
    updater = build_updater()
    counts = (updater.graph.node_count, updater.graph.edge_count,
              updater.graph.passage_count)
    first = updater.add_passage(
        "n1#p0", "Toko Baru sells Samsung Galaxy S24.")
    assert first["added_passages"] == 1
    assert first["new_nodes"] == 1  # toko baru; the product merges
    counts = (updater.graph.node_count, updater.graph.edge_count,
              updater.graph.passage_count)
    # Same id, same text: a no-op.
    again = updater.add_passage(
        "n1#p0", "Toko Baru sells Samsung Galaxy S24.")
    assert again["already_indexed"] is True
    assert again["added_passages"] == 0
    assert (updater.graph.node_count, updater.graph.edge_count,
            updater.graph.passage_count) == counts
    # Same text under a different id: recognised as a duplicate
    # by content hash, not added as a second passage.
    other = updater.add_passage(
        "n2#p0", "Toko Baru sells Samsung Galaxy S24.")
    assert other["duplicate_passages"] == 1
    assert other["duplicate_of"] == "n1#p0"
    assert other["added_passages"] == 0
    assert (updater.graph.node_count, updater.graph.edge_count,
            updater.graph.passage_count) == counts


def test_same_document_twice_is_idempotent():
    updater = build_updater()
    counts = (updater.graph.node_count, updater.graph.edge_count,
              updater.graph.passage_count)
    first = updater.add_document(
        "d13", "Google Pixel 9 is a phone.")
    assert first["added_passages"] == 1
    counts = (updater.graph.node_count, updater.graph.edge_count,
              updater.graph.passage_count)
    again = updater.add_document(
        "d13", "Google Pixel 9 is a phone.")
    assert again["already_indexed"] is True
    assert again["added_passages"] == 0
    assert (updater.graph.node_count, updater.graph.edge_count,
            updater.graph.passage_count) == counts


def test_repeated_triple_raises_edge_weight_not_duplicate_edge():
    updater = build_updater()
    key = ("toko gadget murah", "sells", "samsung galaxy s24")
    assert key in updater.graph.edges
    assert updater.edge_weight(*key) == 1
    seller_before = updater.node_weight("toko gadget murah")
    edges_before = updater.graph.edge_count
    nodes_before = updater.graph.node_count
    # Different wording, same triple: not a content duplicate,
    # so it merges and the edge weight goes up by one.
    stats = updater.add_passage(
        "n1#p0", "Toko Gadget Murah sells Samsung Galaxy S24 today.")
    assert stats["added_passages"] == 1
    assert stats["new_edges"] == 0
    assert stats["merged_edges"] == 1
    assert updater.graph.edge_count == edges_before
    assert updater.graph.node_count == nodes_before
    assert updater.edge_weight(*key) == 2
    assert updater.node_weight("toko gadget murah") == seller_before + 1
    change = next(
        c for c in stats["weight_changes"]
        if c["kind"] == "edge" and c["subject"] == key[0])
    assert (change["weight_before"], change["weight_after"]) == (1, 2)


def test_update_passage_replaces_old_facts():
    updater = build_updater()
    updater.add_passage("u1#p0", "Toko Baru sells Samsung Galaxy S24.")
    assert updater.edge_weight(
        "toko baru", "sells", "samsung galaxy s24") == 1
    stats = updater.update_passage(
        "u1#p0", "Toko Baru sells Xiaomi Redmi Note 13.")
    assert stats["removed_passages"] == 1
    assert stats["removed_edges"] == 1
    assert stats["added_passages"] == 1
    assert stats["new_edges"] == 1
    # The old fact is gone, the new fact is in, the seller node
    # survives, and the passage text is the new text.
    assert updater.edge_weight(
        "toko baru", "sells", "samsung galaxy s24") == 0
    assert updater.edge_weight(
        "toko baru", "sells", "xiaomi redmi note 13") == 1
    assert "toko baru" in updater.graph.nodes
    assert updater.graph.passages["u1#p0"] == (
        "Toko Baru sells Xiaomi Redmi Note 13.")
    # The shared product node survives: other passages still
    # mention it.
    assert "samsung galaxy s24" in updater.graph.nodes
    # Repeating the update with the same text is a no-op.
    again = updater.update_passage(
        "u1#p0", "Toko Baru sells Xiaomi Redmi Note 13.")
    assert again["already_indexed"] is True
    assert again["added_passages"] == 0


def test_remove_passage_drops_orphans_only():
    updater = build_updater()
    updater.add_passage("u2#p0", "Toko Unik sells Nokia 3310.")
    assert "nokia 3310" in updater.graph.nodes
    assert "toko unik" in updater.graph.nodes
    stats = updater.remove_passage("u2#p0")
    assert stats["removed_passages"] == 1
    assert stats["removed_nodes"] >= 2
    assert "nokia 3310" not in updater.graph.nodes
    assert "toko unik" not in updater.graph.nodes
    # Shared nodes survive: other passages still mention them.
    assert "samsung galaxy s24" in updater.graph.nodes
    assert "toko gadget murah" in updater.graph.nodes
    with pytest.raises(KeyError):
        updater.remove_passage("u2#p0")


def test_update_document_replaces_only_that_doc():
    updater = build_updater()
    updater.add_document(
        "d15", "Toko Kamera Jaya sells Canon EOS R50.")
    assert "canon" in updater.graph.nodes
    other_before = {
        pid: text for pid, text in updater.graph.passages.items()
        if not pid.startswith("d15#")
    }
    stats = updater.update_document(
        "d15", "Toko Kamera Jaya sells Nikon Z30.")
    assert stats["removed_passages"] == 1
    assert stats["added_passages"] == 1
    assert stats["removed_nodes"] >= 1
    assert "canon" not in updater.graph.nodes
    assert "nikon z30" in updater.graph.nodes
    assert "toko kamera jaya" in updater.graph.nodes
    # Every passage outside d15 is byte-for-byte untouched.
    other_after = {
        pid: text for pid, text in updater.graph.passages.items()
        if not pid.startswith("d15#")
    }
    assert other_after == other_before


def test_recall_does_not_drop_after_incremental_update():
    updater = build_updater()
    before_10 = _recall_at_k(updater.graph, 10)
    before_5 = _recall_at_k(updater.graph, 5)
    assert before_10 == 1.0
    updater.add_documents(NEW_LINE_DOCS)
    after_10 = _recall_at_k(updater.graph, 10)
    after_5 = _recall_at_k(updater.graph, 5)
    assert after_10 >= before_10
    assert after_10 == 1.0
    assert after_5 >= before_5


def test_same_seller_promo_update_dilutes_top10_but_keeps_reach():
    updater = build_updater()
    assert _recall_at_k(updater.graph, 10) == 1.0
    assert _recall_at_k(updater.graph, 15) == 1.0
    updater.add_documents(SAME_SELLER_PROMO)
    # The new promo passages attach to the fraud-linked seller,
    # so they outrank the fraud report on two queries: recall@10
    # falls from 1.0 to 0.875 (7 of 8). The report itself is
    # still in the graph and still reachable one step further
    # out, so recall@15 does not move.
    assert "d4#p0" in updater.graph.passages
    assert _recall_at_k(updater.graph, 10) == 0.875
    assert _recall_at_k(updater.graph, 15) == 1.0


def test_new_passage_bridges_old_seller_to_new_fraud_report():
    updater = build_updater()
    updater.add_passage(
        "n1#p0", "Fraud report FR-99: Toko Ponsel Abal ran a fake "
        "promo for Asus Vivobook 14.")
    assert "fr-99" in updater.graph.nodes
    assert "fr-99" in updater.graph.neighbors("toko ponsel abal")
    assert "asus vivobook 14" in updater.graph.neighbors(
        "toko ponsel abal")
    retriever = HippoRAGRetriever(updater.graph, embedder=EMBEDDER)
    ids = [r["passage_id"] for r in retriever.retrieve(
        "Who should I avoid when buying Asus Vivobook 14?", top_k=10)]
    assert "n1#p0" in ids
    assert "d9#p0" in ids


def test_batch_add_reports_merged_vs_new():
    updater = build_updater()
    counts = (updater.graph.node_count, updater.graph.edge_count,
              updater.graph.passage_count)
    stats = updater.add_passages({
        "b1#p0": "Toko Baru sells Samsung Galaxy S24.",
        "b2#p0": "Toko Baru sells Samsung Galaxy S24.",
        "b3#p0": "Toko Baru offers a promo: Samsung Galaxy S24 "
                  "for Rp5.000.000.",
    })
    assert stats["added_passages"] == 2
    assert stats["duplicate_passages"] == 1
    assert stats["new_edges"] == 2
    assert "toko baru" in updater.graph.nodes
    assert updater.edge_weight(
        "toko baru", "sells", "samsung galaxy s24") == 1
    assert updater.edge_weight(
        "toko baru", "offers", "samsung galaxy s24") == 1
    assert (updater.graph.node_count, updater.graph.edge_count,
            updater.graph.passage_count) == (
        counts[0] + stats["new_nodes"],
        counts[1] + stats["new_edges"],
        counts[2] + 2)


def test_weights_are_zero_for_unknown_nodes_and_edges():
    updater = build_updater()
    assert updater.node_weight("no such seller") == 0
    assert updater.edge_weight(
        "no such seller", "sells", "no such product") == 0
    assert updater.node_weight("samsung galaxy s24") > 0


def test_add_passage_rejects_bad_input():
    updater = IncrementalUpdater(KnowledgeGraph())
    with pytest.raises(ValueError):
        updater.add_passage("", "Toko Baru sells Samsung Galaxy S24.")
    with pytest.raises(ValueError):
        updater.add_passage("x#p0", "")
    with pytest.raises(ValueError):
        updater.add_passage("x#p0", 42)
    with pytest.raises(ValueError):
        updater.add_document("", "Some text.")
    with pytest.raises(ValueError):
        updater.update_passage("x#p0", "")
    with pytest.raises(KeyError):
        updater.remove_document("missing")
