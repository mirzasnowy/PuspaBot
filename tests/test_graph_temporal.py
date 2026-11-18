"""Tests for temporal graph edges (Graphiti pattern, rewritten).

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.graphs.temporal import (
    TemporalEdge,
    TemporalError,
    TemporalGraph,
    from_knowledge_graph,
    stale_fact_rate,
)
from puspa.retrieval.knowledge_graph import KnowledgeGraph

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
PAST = NOW - timedelta(days=30)
RECENT = NOW - timedelta(days=2)
FUTURE = NOW + timedelta(days=30)


def _price_graph():
    """Small price/fraud graph: 12 facts, 4 expired, 2 superseded,
    6 current. The fixed set behind the stale-fact-rate test."""
    graph = TemporalGraph()
    # 4 expired price facts (validity window already closed).
    for i in range(4):
        graph.add_edge(
            f"product-{i}", "costs", f"price-{i}", valid_from=PAST, valid_until=RECENT, as_of=NOW
        )
    # 2 superseded: an old price replaced by a new one.
    for i in range(4, 6):
        graph.add_edge(f"product-{i}", "costs", f"old-price-{i}", valid_from=PAST, as_of=NOW)
        graph.add_edge(f"product-{i}", "costs", f"new-price-{i}", valid_from=RECENT, as_of=NOW)
    # 4 current facts: 2 open-ended, 2 inside a live window.
    graph.add_edge("product-6", "costs", "price-6", as_of=NOW)
    graph.add_edge("product-7", "costs", "price-7", as_of=NOW)
    graph.add_edge("product-8", "costs", "price-8", valid_from=PAST, valid_until=FUTURE, as_of=NOW)
    graph.add_edge(
        "seller-a", "flagged_fraud", "report-1", valid_from=RECENT, valid_until=FUTURE, as_of=NOW
    )
    return graph


def test_expired_edge_excluded_from_query():
    graph = TemporalGraph()
    graph.add_edge(
        "samsung galaxy s24", "costs", "15000000", valid_from=PAST, valid_until=RECENT, as_of=NOW
    )
    assert graph.query(as_of=NOW) == []
    assert graph.current_fact("samsung galaxy s24", "costs", as_of=NOW) is None
    stale = graph.query(as_of=NOW, include_stale=True)
    assert len(stale) == 1
    assert stale[0]["stale"] is True
    assert stale[0]["stale_reason"] == "expired"


def test_validity_window_boundaries():
    graph = TemporalGraph()
    graph.add_edge(
        "samsung galaxy s24", "costs", "15000000", valid_from=RECENT, valid_until=FUTURE, as_of=NOW
    )
    # valid_from is inclusive, valid_until is exclusive.
    assert len(graph.query(as_of=RECENT)) == 1
    assert len(graph.query(as_of=FUTURE)) == 0
    assert len(graph.query(as_of=RECENT - timedelta(seconds=1))) == 0
    not_yet = graph.query(as_of=RECENT - timedelta(seconds=1), include_stale=True)
    assert not_yet[0]["stale_reason"] == "not_yet_valid"


def test_new_fact_supersedes_old_same_subject_relation():
    graph = TemporalGraph()
    old = graph.add_edge("samsung galaxy s24", "costs", "15000000", valid_from=PAST, as_of=NOW)
    new = graph.add_edge("samsung galaxy s24", "costs", "14200000", valid_from=RECENT, as_of=NOW)
    assert new["superseded"] == [old["edge"].id]
    current = graph.query(as_of=NOW)
    assert len(current) == 1
    assert current[0]["object"] == "14200000"
    assert current[0]["stale"] is False
    # The old fact is gone from current results but kept in history.
    history = graph.history("samsung galaxy s24", "costs", as_of=NOW)
    assert [h["object"] for h in history] == ["14200000", "15000000"]
    assert history[1]["stale"] is True
    assert history[1]["stale_reason"] == "superseded"
    assert history[1]["superseded"] is True
    assert history[1]["valid_until"] == RECENT


def test_supersede_only_same_subject_relation():
    graph = TemporalGraph()
    graph.add_edge("seller-a", "sells", "samsung galaxy s24", as_of=NOW)
    graph.add_edge("seller-a", "flagged_fraud", "report-1", as_of=NOW)
    graph.add_edge("seller-b", "flagged_fraud", "report-2", as_of=NOW)
    graph.add_edge("seller-a", "flagged_fraud", "report-3", as_of=NOW)
    current = graph.query(as_of=NOW)
    assert len(current) == 3
    objects = {(c["subject"], c["relation"], c["object"]) for c in current}
    assert ("seller-a", "sells", "samsung galaxy s24") in objects
    assert ("seller-a", "flagged_fraud", "report-3") in objects
    assert ("seller-b", "flagged_fraud", "report-2") in objects
    assert ("seller-a", "flagged_fraud", "report-1") not in objects


def test_supersede_off_keeps_multi_valued_edges():
    graph = TemporalGraph()
    graph.add_edge("seller-a", "sells", "samsung galaxy s24", supersede=False, as_of=NOW)
    graph.add_edge("seller-a", "sells", "xiaomi redmi note 13", supersede=False, as_of=NOW)
    assert len(graph.query(as_of=NOW)) == 2


def test_stale_fact_rate_drops_with_validity():
    graph = _price_graph()
    # Baseline: what a graph without validity would return, every
    # edge, stale ones included.
    baseline = graph.query(as_of=NOW, include_stale=True)
    assert len(baseline) == 12
    assert stale_fact_rate(baseline) == 0.5
    # Filtered: the same query with validity enforced.
    filtered = graph.query(as_of=NOW)
    assert len(filtered) == 6
    assert stale_fact_rate(filtered) == 0.0
    assert all(not entry["stale"] for entry in filtered)


def test_stale_fact_rate_empty_is_zero():
    assert stale_fact_rate([]) == 0.0


def test_edge_without_validity_still_works():
    graph = TemporalGraph()
    graph.add_edge("samsung galaxy s24", "costs", "15000000")
    graph.add_edge("seller-a", "sells", "samsung galaxy s24")
    # No as_of given: defaults to now, open-ended edges are current.
    assert len(graph.query()) == 2
    assert len(graph.query(as_of=FUTURE)) == 2
    assert graph.neighbors("seller-a") == ["samsung galaxy s24"]
    # Supersede still applies to open-ended edges.
    graph.add_edge("samsung galaxy s24", "costs", "14200000")
    assert graph.current_fact("samsung galaxy s24", "costs")["object"] == "14200000"


def test_neighbors_and_relations_honor_validity():
    graph = TemporalGraph()
    graph.add_edge(
        "seller-a", "sells", "samsung galaxy s24", valid_from=PAST, valid_until=RECENT, as_of=NOW
    )
    graph.add_edge("seller-a", "sells", "xiaomi redmi note 13", as_of=NOW)
    assert graph.neighbors("seller-a", as_of=NOW) == ["xiaomi redmi note 13"]
    assert graph.relations("seller-a", as_of=NOW) == [("seller-a", "sells", "xiaomi redmi note 13")]
    assert len(graph.relations("seller-a", as_of=NOW, include_stale=True)) == 2


def test_current_fact_returns_latest_price():
    graph = TemporalGraph()
    graph.add_edge("samsung galaxy s24", "costs", "15000000", valid_from=PAST, as_of=NOW)
    graph.add_edge("samsung galaxy s24", "costs", "14200000", valid_from=RECENT, as_of=NOW)
    fact = graph.current_fact("samsung galaxy s24", "costs", as_of=NOW)
    assert fact["object"] == "14200000"
    assert fact["valid_from"] == RECENT


def test_fraud_report_expires():
    graph = TemporalGraph()
    graph.add_edge(
        "seller-a", "flagged_fraud", "report-1", valid_from=PAST, valid_until=RECENT, as_of=NOW
    )
    assert (
        graph.current_fact("seller-a", "flagged_fraud", as_of=RECENT - timedelta(seconds=1))
        is not None
    )
    assert graph.current_fact("seller-a", "flagged_fraud", as_of=NOW) is None
    assert graph.neighbors("seller-a", "flagged_fraud", as_of=NOW) == []


def test_prune_expired_removes_only_expired():
    graph = _price_graph()
    result = graph.prune_expired(as_of=NOW)
    assert result == {"removed": 4, "edges": 8}
    # Superseded history is kept: pruning drops expiry, not versions.
    history = graph.history("product-4", "costs", as_of=NOW)
    assert len(history) == 2
    assert len(graph.query(as_of=NOW)) == 6
    assert len(graph.query(as_of=NOW, include_stale=True)) == 8


def test_from_knowledge_graph_adapter():
    kg = KnowledgeGraph()
    kg.add_document("doc-1", "Andi reports Budi.")
    kg.add_document("doc-2", "Citra reports Budi.")
    validity = {("andi", "reports", "budi"): (PAST, RECENT)}
    temporal = from_knowledge_graph(kg, validity)
    assert temporal.edge_count == 2
    current = temporal.query(as_of=NOW)
    assert len(current) == 1
    assert (current[0]["subject"], current[0]["relation"], current[0]["object"]) == (
        "citra",
        "reports",
        "budi",
    )
    stale = temporal.query(as_of=NOW, include_stale=True)
    assert stale_fact_rate(stale) == 0.5
    # An edge with no validity entry keeps working like before.
    assert temporal.current_fact("citra", "reports", as_of=NOW) is not None


def test_from_knowledge_graph_callable_validity():
    kg = KnowledgeGraph()
    kg.add_document("doc-1", "Andi reports Budi.")
    temporal = from_knowledge_graph(
        kg, lambda triple: (PAST, RECENT) if triple.object == "budi" else None
    )
    assert temporal.query(as_of=NOW) == []


def test_invalid_windows_rejected():
    graph = TemporalGraph()
    naive = datetime(2026, 11, 18, 12, 0)
    with pytest.raises(TemporalError):
        graph.add_edge("a", "costs", "1", valid_from=naive)
    with pytest.raises(TemporalError):
        graph.add_edge("a", "costs", "1", valid_until=naive)
    with pytest.raises(TemporalError):
        graph.add_edge("a", "costs", "1", valid_from=FUTURE, valid_until=PAST)
    with pytest.raises(TemporalError):
        graph.add_edge("a", "costs", "1", valid_from=RECENT, valid_until=RECENT)
    with pytest.raises(TemporalError):
        graph.add_edge("", "costs", "1")
    with pytest.raises(TemporalError):
        graph.add_edge("a", "", "1")
    with pytest.raises(TemporalError):
        graph.add_edge("a", "costs", "")


def test_edge_is_current_standalone():
    edge = TemporalEdge(
        subject="a", relation="costs", object="1", valid_from=PAST, valid_until=FUTURE
    )
    assert edge.is_current(as_of=NOW) is True
    assert edge.is_current(as_of=FUTURE) is False
    assert edge.stale_reason(as_of=NOW) is None
    assert edge.stale_reason(as_of=FUTURE) == "expired"
    edge.superseded = True
    assert edge.is_current(as_of=NOW) is False
    assert edge.stale_reason(as_of=NOW) == "superseded"
