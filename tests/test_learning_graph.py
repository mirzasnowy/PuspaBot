"""Tests for the learning graph (G24).

Run with ``--noconftest``: everything here is in-memory / tmp
directories. No database, no live LLM.
"""

import json

import pytest

from puspa.memory.learning_graph import (
    EDGE_DECLARED,
    EDGE_LEXICAL,
    LearningGraph,
    declarations_from_skill_dir,
)


def _graph():
    graph = LearningGraph()
    graph.add_skill(
        "bill-payer",
        declares=["bill", "payment", "pln", "electricity"],
    )
    graph.add_skill("price-compare", declares=["price", "shop", "compare"])
    graph.add_memory_chunk("c1", "User pays the PLN electricity bill every month on the 5th.")
    graph.add_memory_chunk("c2", "User compared rice cooker prices across shops yesterday.")
    graph.add_memory_chunk("c3", "User likes hiking in the mountains on weekends.")
    return graph


# --- nodes and declared links ------------------------------------------


def test_add_skill_and_chunk_nodes():
    graph = _graph()
    assert len(graph.skills()) == 2
    assert len(graph.chunks()) == 3
    skill = graph.nodes["skill:bill-payer"]
    assert skill.kind == "skill"
    assert "pln" in skill.declares
    chunk = graph.nodes["memory:c1"]
    assert chunk.kind == "memory"
    assert "PLN" in chunk.content


def test_declared_link_and_related_queries():
    graph = _graph()
    graph.link_declared("bill-payer", "c1")
    assert [node.id for node in graph.related_chunks("bill-payer")] == ["memory:c1"]
    assert [node.id for node in graph.related_skills("c1")] == ["skill:bill-payer"]
    assert graph.related_skills("c3") == []


def test_add_link_rejects_unknown_nodes_and_kinds():
    graph = _graph()
    with pytest.raises(KeyError):
        graph.add_link("skill:nope", "memory:c1", EDGE_DECLARED)
    with pytest.raises(ValueError):
        graph.add_link("skill:bill-payer", "memory:c1", "mystery")


def test_add_link_keeps_max_weight_on_duplicates():
    graph = _graph()
    first = graph.add_link("skill:bill-payer", "memory:c1", EDGE_DECLARED, 0.5)
    second = graph.add_link("skill:bill-payer", "memory:c1", EDGE_DECLARED, 1.0)
    assert first is second
    assert second.weight == 1.0
    assert len(graph.edges) == 1


# --- lexical overlap ---------------------------------------------------


def test_lexical_overlap_links_matching_pairs_only():
    graph = _graph()
    created = graph.link_lexical_overlap()
    kinds = {(edge.source, edge.target) for edge in created}
    assert ("skill:bill-payer", "memory:c1") in kinds
    assert ("skill:price-compare", "memory:c2") in kinds
    assert not any(target == "memory:c3" for _, target in kinds)
    # c1 links to bill-payer on bill/payment/pln/electricity overlap.
    edge = next(e for e in created if e.source == "skill:bill-payer" and e.target == "memory:c1")
    assert edge.kind == EDGE_LEXICAL
    assert 0.3 <= edge.weight <= 1.0


def test_lexical_overlap_threshold_is_honest():
    graph = _graph()
    full = graph.link_lexical_overlap(threshold=1.0)
    # Only price-compare/c2 overlaps fully (price, shop, compare).
    assert [(e.source, e.target) for e in full] == [("skill:price-compare", "memory:c2")]
    assert graph.link_lexical_overlap(threshold=0.1)
    with pytest.raises(ValueError):
        graph.link_lexical_overlap(threshold=0.0)
    with pytest.raises(ValueError):
        graph.link_lexical_overlap(threshold=1.5)


def test_related_queries_rank_by_weight():
    graph = _graph()
    graph.link_declared("bill-payer", "c1")
    graph.link_lexical_overlap()
    skills = graph.related_skills("c1")
    assert skills[0].id == "skill:bill-payer"
    assert graph.related_chunks("price-compare")[0].id == "memory:c2"


# --- declarations from a skill directory --------------------------------


def test_declarations_from_manifest(tmp_path):
    skill_dir = tmp_path / "bill-payer"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Bill Payer\n", encoding="utf-8")
    (skill_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "bill-payer",
                "tools": ["pay_bill", "check_balance"],
                "domains": ["electricity"],
            }
        ),
        encoding="utf-8",
    )
    declared = declarations_from_skill_dir(skill_dir)
    assert "pay_bill" in declared
    assert "electricity" in declared


def test_declarations_from_skill_md_frontmatter(tmp_path):
    skill_dir = tmp_path / "price-compare"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: price-compare\ntools: [compare_prices, search_shops]\n"
        "domains:\n  - marketplace\n  - price\n---\n# Price Compare\n",
        encoding="utf-8",
    )
    declared = declarations_from_skill_dir(skill_dir)
    assert "compare_prices" in declared
    assert "marketplace" in declared
    assert "price" in declared


def test_add_skill_from_dir_reads_declarations(tmp_path):
    skill_dir = tmp_path / "bill-payer"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Bill Payer\n", encoding="utf-8")
    (skill_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "bill-payer",
                "tools": ["pay_bill"],
                "domains": ["pln"],
            }
        ),
        encoding="utf-8",
    )
    graph = LearningGraph()
    node = graph.add_skill_from_dir(skill_dir)
    assert node.id == "skill:bill-payer"
    assert "pay_bill" in node.declares
    assert node.metadata["path"] == str(skill_dir)


# --- JSON round trip ----------------------------------------------------


def test_json_round_trip(tmp_path):
    graph = _graph()
    graph.link_declared("bill-payer", "c1")
    graph.link_lexical_overlap()
    path = tmp_path / "graph.json"
    graph.save_json(path)
    restored = LearningGraph.load_json(path)
    assert sorted(restored.nodes) == sorted(graph.nodes)
    assert len(restored.edges) == len(graph.edges)
    assert [node.id for node in restored.related_skills("c1")] == [
        node.id for node in graph.related_skills("c1")
    ]
    assert restored.nodes["skill:bill-payer"].declares == (graph.nodes["skill:bill-payer"].declares)
