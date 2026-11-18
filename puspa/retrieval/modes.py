"""Query modes over the dual-level index (LightRAG pattern, rewritten).

LightRAG defines five query modes over one entity/relation graph
plus the text chunks behind it. This module implements the same
five modes over Puspa's knowledge graph, reusing the dual-level
weights already in ``puspa.memory.episodic`` (entity 0.5, concept
0.3, vector 0.2) instead of inventing a second scoring scheme:

- ``local``: precise matching of local context. Candidate
  entities from the query and the passages directly attached to
  them. Good for questions about a specific object or fact.
- ``global``: macro themes and cross-document reasoning.
  Relationship chains scored by concept overlap, walked up to two
  hops, returning the passages along the chains. Good for
  questions that need the whole picture (which seller, which
  pattern).
- ``hybrid``: local plus global merged, so a specific entity and
  its wider context are recalled together.
- ``naive``: traditional RAG. No graph at all: pure vector
  similarity over the passages.
- ``mix``: local plus global plus naive merged. The default,
  like in LightRAG, because it recalls the most complete
  context.

The modes genuinely differ: local never returns a passage that
is two hops from every seed, global never returns a passage
whose only link is vector similarity, and naive never uses the
graph. Tests assert those differences on a fixed graph.
"""

from __future__ import annotations

import re

from puspa.memory.embeddings import HashEmbedding, cosine_similarity
from puspa.memory.episodic import (
    DUAL_WEIGHTS,
    extract_concepts,
    extract_entities,
)

from .hipporag import find_seed_nodes
from .knowledge_graph import KnowledgeGraph, hop_distances, normalize_entity_name

_TOKEN_RE = re.compile(r"[a-z0-9]+")
QUERY_MODES = ("local", "global", "hybrid", "naive", "mix")


def _dual_score(query_entities, query_concepts, query_vec, text, embedder):
    """One passage scored by the dual-level weights (0.5/0.3/0.2)."""
    entity_overlap = 0.0
    if query_entities:
        found = {normalize_entity_name(e) for e in extract_entities(text)}
        entity_overlap = len(set(query_entities) & found) / len(query_entities)
    concept_overlap = 0.0
    if query_concepts:
        found = set(extract_concepts(text))
        concept_overlap = len(set(query_concepts) & found) / len(query_concepts)
    vector_score = 0.0
    if query_vec is not None:
        vector_score = max(
            0.0, cosine_similarity(query_vec, embedder.embed(text)))
    weight_entity, weight_concept, weight_vector = DUAL_WEIGHTS
    return (
        weight_entity * entity_overlap
        + weight_concept * concept_overlap
        + weight_vector * vector_score
    )


def _result(passage_id, graph, score, mode, level, extra=None):
    result = {
        "passage_id": passage_id,
        "text": graph.passages[passage_id],
        "score": score,
        "mode": mode,
        "level": level,
        "evidence_id": passage_id,
    }
    if extra:
        result.update(extra)
    return result


def _prepare(graph, query, embedder):
    query_entities = [normalize_entity_name(e) for e in extract_entities(query)]
    query_concepts = extract_concepts(query)
    query_vec = embedder.embed(query) if embedder is not None else None
    return query_entities, query_concepts, query_vec


def local_query(graph: KnowledgeGraph, query: str, embedder=None,
                top_k: int = 5) -> list:
    """Local mode: passages directly attached to the query entities."""
    embedder = embedder or HashEmbedding()
    query_entities, query_concepts, query_vec = _prepare(graph, query, embedder)
    seeds = find_seed_nodes(graph, query)
    results = []
    for passage_id in graph.passages_for_nodes(seeds):
        score = _dual_score(
            query_entities, query_concepts, query_vec,
            graph.passages[passage_id], embedder)
        level = "entity" if seeds else "concept"
        results.append(_result(
            passage_id, graph, score, "local", level,
            {"seed_nodes": seeds}))
    results.sort(key=lambda r: (-r["score"], r["passage_id"]))
    return results[:top_k]


def global_query(graph: KnowledgeGraph, query: str, embedder=None,
                 top_k: int = 5, max_hops: int = 2) -> list:
    """Global mode: relationship chains, walked up to ``max_hops``.

    Triples are scored by concept overlap of subject plus
    relation plus object. Each node on an overlapping triple
    carries a chain score: the triple overlap decayed by 0.7 per
    hop from a seed node, so a chain one hop out still matters
    and a chain three hops out does not. A passage scores by its
    best linked chain plus a small dual-level term, which lets a
    two-hop passage (a fraud report attached to the seller) rank
    above a one-hop passage whose chain matches the query less.
    """
    embedder = embedder or HashEmbedding()
    query_entities, query_concepts, query_vec = _prepare(graph, query, embedder)
    concept_set = set(query_concepts)
    scored_triples = []
    for (subject, relation, obj) in graph.edges:
        tokens = set(_TOKEN_RE.findall(subject + " " + relation + " " + obj))
        overlap = len(concept_set & tokens) / len(concept_set) if concept_set else 0.0
        scored_triples.append((overlap, subject, relation, obj))
    scored_triples.sort(key=lambda t: (-t[0], t[1], t[2], t[3]))
    seeds = find_seed_nodes(graph, query)
    distances = hop_distances(graph.adjacency(), seeds) if seeds else {}
    node_chain: dict[str, float] = {}
    for overlap, subject, _, obj in scored_triples:
        if overlap <= 0.0:
            continue
        for node in (subject, obj):
            hops = distances.get(node, max_hops + 1)
            if hops > max_hops:
                continue
            value = overlap * (0.7 ** hops)
            if value > node_chain.get(node, 0.0):
                node_chain[node] = value
    results = []
    for passage_id in graph.passages_for_nodes(node_chain):
        linked = graph.passage_entities.get(passage_id, set())
        on_chain = [n for n in linked if n in node_chain]
        chain = max(node_chain[n] for n in on_chain)
        best_hop = min(distances.get(n, max_hops + 1) for n in on_chain)
        dual = _dual_score(
            query_entities, query_concepts, query_vec,
            graph.passages[passage_id], embedder)
        score = chain + 0.3 * dual
        results.append(_result(
            passage_id, graph, score, "global", "concept",
            {"hops": best_hop, "chain_score": chain}))
    results.sort(key=lambda r: (-r["score"], r["passage_id"]))
    return results[:top_k]


def naive_query(graph: KnowledgeGraph, query: str, embedder=None,
                top_k: int = 5) -> list:
    """Naive mode: pure vector similarity over all passages, no graph."""
    embedder = embedder or HashEmbedding()
    query_vec = embedder.embed(query)
    results = []
    for passage_id, text in sorted(graph.passages.items()):
        score = max(0.0, cosine_similarity(query_vec, embedder.embed(text)))
        results.append(_result(passage_id, graph, score, "naive", "vector"))
    results.sort(key=lambda r: (-r["score"], r["passage_id"]))
    return results[:top_k]


def _merge(rankings, mode):
    """Merge several ranked lists: best score plus half the rest."""
    combined: dict[str, dict] = {}
    for ranking in rankings:
        for result in ranking:
            passage_id = result["passage_id"]
            if passage_id not in combined:
                combined[passage_id] = dict(result)
                combined[passage_id]["mode"] = mode
                combined[passage_id]["modes"] = [result["mode"]]
                combined[passage_id]["score"] = result["score"]
            else:
                entry = combined[passage_id]
                entry["score"] = max(entry["score"], result["score"]) + (
                    0.5 * min(entry["score"], result["score"]))
                if result["mode"] not in entry["modes"]:
                    entry["modes"].append(result["mode"])
    merged = sorted(
        combined.values(), key=lambda r: (-r["score"], r["passage_id"]))
    return merged


def hybrid_query(graph: KnowledgeGraph, query: str, embedder=None,
                 top_k: int = 5) -> list:
    """Hybrid mode: local plus global merged."""
    merged = _merge([
        local_query(graph, query, embedder, top_k=top_k),
        global_query(graph, query, embedder, top_k=top_k),
    ], "hybrid")
    return merged[:top_k]


def mix_query(graph: KnowledgeGraph, query: str, embedder=None,
              top_k: int = 5) -> list:
    """Mix mode: local plus global plus naive merged (the default)."""
    merged = _merge([
        local_query(graph, query, embedder, top_k=top_k),
        global_query(graph, query, embedder, top_k=top_k),
        naive_query(graph, query, embedder, top_k=top_k),
    ], "mix")
    return merged[:top_k]


def query(graph: KnowledgeGraph, query_text: str, mode: str = "mix",
          embedder=None, top_k: int = 5) -> list:
    """Run one query in one mode. ``mode`` is one of QUERY_MODES."""
    if not isinstance(query_text, str) or query_text == "":
        raise ValueError("query must be a non-empty string")
    if mode not in QUERY_MODES:
        raise ValueError(f"unknown query mode: {mode!r}")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if mode == "local":
        return local_query(graph, query_text, embedder, top_k)
    if mode == "global":
        return global_query(graph, query_text, embedder, top_k)
    if mode == "hybrid":
        return hybrid_query(graph, query_text, embedder, top_k)
    if mode == "naive":
        return naive_query(graph, query_text, embedder, top_k)
    return mix_query(graph, query_text, embedder, top_k)
