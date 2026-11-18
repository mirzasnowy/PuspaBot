"""Multi-hop retrieval by Personalized PageRank (HippoRAG pattern).

HippoRAG retrieves by running Personalized PageRank over the open
knowledge graph: the entities in the query seed the walk, the walk
spreads along graph edges, and passages attached to the highest
scored nodes are returned. That is what lets a query reach an
answer two or three hops away (product, promo, seller, fraud
report) when no single passage shares the query words.

HippoRAG 2 adds two steps on top, both reimplemented here in
Puspa's own code:

- query-to-triple filtering: before the walk, an LLM judge
  (``puspa.retrieval.triple_filter.LLMTripleJudge``) reads the
  triples against the query and drops the ones that do not help
  answer it, so the walk does not spread through unrelated parts
  of the graph. The judge is injected as a completion callable;
  with no LLM wired, the deterministic rule (a triple survives
  when it touches a seed node, touches a node next to a seed, or
  shares a concept token with the query) runs instead, and every
  decision records which source made it;
- passage re-ranking: the final passage score combines the PPR
  node score with vector similarity and lexical overlap, instead
  of trusting the graph score alone.

The PageRank itself is plain power iteration with restart:
``r = (1 - d) * seed + d * M^T r`` where M is the row-normalized
adjacency. A node with no neighbors spreads its mass over all
nodes (the standard dangling-node fix), so every node keeps a
small score and ordering, not zero, is what matters.
"""

from __future__ import annotations

import re

from puspa.memory.embeddings import HashEmbedding, cosine_similarity
from puspa.memory.episodic import extract_concepts, extract_entities

from .knowledge_graph import KnowledgeGraph, hop_distances, normalize_entity_name
from .triple_filter import (
    DeterministicTripleFilter,
    LLMTripleJudge,
    TripleDecision,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def personalized_pagerank(graph: KnowledgeGraph, seeds,
                          damping: float = 0.85, max_iter: int = 100,
                          tol: float = 1e-6) -> dict:
    """Personalized PageRank over the graph, power iteration.

    ``seeds`` are node names, or a mapping of node name to
    restart weight (a stronger match restarts the walk there
    more often). A plain list means uniform weights. Returns
    every node name mapped to its score. Scores fall with
    distance from the seeds, which is what makes the walk
    multi-hop: a node two hops away still scores above an
    unrelated node.
    """
    if not 0.0 < damping < 1.0:
        raise ValueError("damping must be between 0 and 1")
    nodes = sorted(graph.nodes)
    if not nodes:
        return {}
    index = {name: i for i, name in enumerate(nodes)}
    if isinstance(seeds, dict):
        weighted = {
            normalize_entity_name(name): float(weight)
            for name, weight in seeds.items()
            if normalize_entity_name(name) in index and float(weight) > 0
        }
    else:
        weighted = {
            normalize_entity_name(s): 1.0 for s in seeds or []
            if normalize_entity_name(s) in index
        }
    if not weighted:
        return {name: 0.0 for name in nodes}
    total = sum(weighted.values())
    count = len(nodes)
    seed_vec = [0.0] * count
    for name, weight in weighted.items():
        seed_vec[index[name]] = weight / total
    adjacency = graph.adjacency()
    rank = list(seed_vec)
    for _ in range(max_iter):
        nxt = [(1.0 - damping) * value for value in seed_vec]
        for i, name in enumerate(nodes):
            mass = rank[i]
            if mass == 0.0:
                continue
            outs = adjacency.get(name, set())
            if outs:
                share = damping * mass / len(outs)
                for neighbor in outs:
                    nxt[index[neighbor]] += share
            else:
                share = damping * mass / count
                for j in range(count):
                    nxt[j] += share
        delta = sum(abs(a - b) for a, b in zip(nxt, rank))
        rank = nxt
        if delta < tol:
            break
    return {name: rank[index[name]] for name in nodes}


def find_seed_nodes(graph: KnowledgeGraph, query: str) -> list:
    """Graph nodes a query starts from.

    Entity matches first (exact name, or one name containing the
    other), then concept matches (a query concept token appears in
    a node name). Returns node names sorted by match strength,
    strongest first.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query must be a non-empty string")
    exact, partial, concept = [], [], []
    query_entities = [normalize_entity_name(e) for e in extract_entities(query)]
    query_concepts = set(extract_concepts(query))
    for name in graph.nodes:
        if name in query_entities:
            exact.append(name)
            continue
        if any(e in name or name in e for e in query_entities if e):
            partial.append(name)
            continue
        tokens = set(_TOKEN_RE.findall(name))
        matched = tokens & query_concepts
        if matched:
            concept.append((len(matched) / len(tokens), name))
    concept.sort(key=lambda item: (-item[0], item[1]))
    return (
        sorted(set(exact)) + sorted(set(partial))
        + [name for _, name in concept]
    )


def seed_weights(graph: KnowledgeGraph, query: str) -> dict:
    """Restart weights for the query seeds, by match strength.

    An exact entity match restarts with weight 1.0, a partial
    entity match with 0.7, and a concept match with the fraction
    of the node name tokens the query covers (a node matching
    two of three tokens restarts twice as often as one matching
    a single token). A weak concept match therefore cannot steal
    the walk from the node the query really names.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query must be a non-empty string")
    query_entities = [normalize_entity_name(e) for e in extract_entities(query)]
    query_concepts = set(extract_concepts(query))
    weights = {}
    for name in graph.nodes:
        if name in query_entities:
            weights[name] = 1.0
            continue
        if any(e in name or name in e for e in query_entities if e):
            weights[name] = 0.7
            continue
        tokens = set(_TOKEN_RE.findall(name))
        matched = tokens & query_concepts
        if matched:
            weights[name] = len(matched) / len(tokens)
    return weights


def _lexical_overlap(query_concepts, text: str) -> float:
    if not query_concepts:
        return 0.0
    tokens = set(_TOKEN_RE.findall(text.lower()))
    return len(set(query_concepts) & tokens) / len(query_concepts)


class HippoRAGRetriever:
    """Retrieve passages by walking the knowledge graph.

    ``graph`` is the index, ``embedder`` supplies vector
    similarity (HashEmbedding by default, so retrieval works
    offline). ``weights`` are (graph, vector, lexical) and
    default to the dual-level split already used in episodic
    retrieval: the graph score carries the entity weight.

    ``triple_filter`` is the query-to-triple filter (see
    ``puspa.retrieval.triple_filter``): an LLM judge when one is
    injected, the deterministic rule otherwise. ``llm_complete``
    is a shortcut for the judge: pass a ``complete(prompt)``
    callable and the retriever builds an ``LLMTripleJudge`` from
    it, with the deterministic filter as its fallback. The string
    ``"default"`` is the explicit opt-in to the router's live
    completion (``puspa.router.complete.default_llm_complete``):
    it is resolved here, and the first retrieval then calls the
    live model (a missing key raises there, it is never faked).
    With neither, the deterministic filter runs and every decision
    records ``source="deterministic"``. The decisions of the
    last retrieval are kept on ``last_filter_decisions``.
    """

    def __init__(self, graph: KnowledgeGraph, embedder=None,
                 damping: float = 0.85, top_nodes: int = 10,
                 weights=(0.5, 0.3, 0.2), triple_filter=None,
                 llm_complete=None):
        self.graph = graph
        self.embedder = embedder or HashEmbedding()
        self.damping = damping
        self.top_nodes = top_nodes
        if len(weights) != 3:
            raise ValueError("weights must be (graph, vector, lexical)")
        self.weights = weights
        if llm_complete is not None and triple_filter is not None:
            raise ValueError(
                "pass only one of triple_filter and llm_complete")
        if isinstance(llm_complete, str):
            # "default": explicit opt-in to the router's live
            # completion; resolved lazily so importing this module
            # never needs a key or the network.
            from puspa.router.complete import resolve_llm_complete

            llm_complete = resolve_llm_complete(llm_complete)
        if llm_complete is not None:
            self.triple_filter = LLMTripleJudge(llm_complete)
        elif triple_filter is not None:
            self.triple_filter = triple_filter
        else:
            self.triple_filter = DeterministicTripleFilter()
        self.last_filter_decisions: list[TripleDecision] = []

    def seed_nodes(self, query: str) -> list:
        """Seed nodes for a query (see ``find_seed_nodes``)."""
        return find_seed_nodes(self.graph, query)

    def _filtered_adjacency(self, query, query_concepts, seeds):
        """Adjacency kept after HippoRAG 2 style triple filtering.

        Every triple in the graph is judged against the query by
        the retriever's triple filter (an LLM judge when one is
        injected, the deterministic rule otherwise) and only the
        kept triples become edges of the graph the walk runs on.
        The filter sees the seeds widened by one hop, so a triple
        touching a node next to a seed can still survive and the
        walk can reach two hops out. Every decision (keep or drop,
        with its reason and its source) is kept on
        ``last_filter_decisions``.
        """
        adjacency = {name: set() for name in self.graph.nodes}
        full = self.graph.adjacency()
        near_seeds = set(seeds)
        for seed in seeds:
            near_seeds |= full.get(seed, set())
        triples = sorted(self.graph.edges)
        decisions = self.triple_filter.filter(
            query, triples, seeds=near_seeds,
            query_concepts=query_concepts)
        self.last_filter_decisions = list(decisions)
        for decision in decisions:
            if not decision.keep:
                continue
            subject, obj = decision.subject, decision.object
            if subject in adjacency and obj in adjacency:
                adjacency[subject].add(obj)
                adjacency[obj].add(subject)
        return adjacency

    def retrieve(self, query: str, top_k: int = 5, reranker=None) -> list:
        """Multi-hop retrieval for one query.

        Seeds from the query, weighted by match strength, PPR over
        the triple-filtered graph, passages attached to the top
        nodes, then a HippoRAG 2 style re-rank combining graph,
        vector and lexical scores. With no seed node the graph
        cannot help and the retriever falls back to plain vector
        ranking over all passages. An optional ``reranker`` (see
        ``puspa.retrieval.reranker``) re-scores the shortlist; the
        reranker is handed hop distances from the seeds, and its
        score is averaged with the retrieval score so reranking
        refines the walk instead of discarding it. Every result
        carries ``evidence_id`` (the passage id).
        """
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        weights = seed_weights(self.graph, query)
        seeds = sorted(weights, key=lambda n: (-weights[n], n))
        query_concepts = extract_concepts(query)
        query_vec = self.embedder.embed(query)
        if not seeds:
            return self._vector_fallback(
                query, query_vec, query_concepts, top_k, reranker)
        filtered = self._filtered_adjacency(
            query, query_concepts, set(seeds))
        proxy = _FilteredGraph(self.graph, filtered)
        scores = personalized_pagerank(
            proxy, weights, damping=self.damping)
        ranked_nodes = sorted(scores, key=lambda n: -scores[n])
        # Only nodes the walk actually reached carry signal; zero
        # score nodes would enter on an alphabetical tie-break and
        # drag in unrelated passages.
        top = [n for n in ranked_nodes if scores[n] > 0][: self.top_nodes]
        passage_ids = self.graph.passages_for_nodes(top)
        # Passages with no extracted entity must not be hidden by
        # the graph: they compete on vector and lexical score
        # alone, with a zero graph score.
        passage_ids += [
            pid for pid, entities in self.graph.passage_entities.items()
            if not entities and pid not in passage_ids
        ]
        distances = hop_distances(filtered, seeds)
        return self._rank_passages(
            query, passage_ids, scores, query_vec, query_concepts,
            top_k, reranker, seeds, distances)

    def _rank_passages(self, query, passage_ids, node_scores, query_vec,
                       query_concepts, top_k, reranker, seeds, distances):
        weight_graph, weight_vector, weight_lexical = self.weights
        results = []
        for passage_id in passage_ids:
            text = self.graph.passages[passage_id]
            linked = self.graph.passage_entities.get(passage_id, set())
            # HippoRAG passage score: the sum of the PPR scores of
            # the passage's linked nodes. A passage touching several
            # walked-to nodes outranks one touching a single node.
            graph_score = sum(node_scores.get(n, 0.0) for n in linked)
            vector_score = max(
                0.0, cosine_similarity(
                    query_vec, self.embedder.embed(text)))
            lexical_score = _lexical_overlap(query_concepts, text)
            score = (
                weight_graph * graph_score
                + weight_vector * vector_score
                + weight_lexical * lexical_score
            )
            hops = min(
                (distances.get(n, 99) for n in linked), default=99)
            results.append({
                "passage_id": passage_id,
                "text": text,
                "score": score,
                "graph_score": graph_score,
                "vector_score": vector_score,
                "lexical_score": lexical_score,
                "hops": hops,
                "seed_nodes": list(seeds),
                "evidence_id": passage_id,
            })
        results.sort(key=lambda r: (-r["score"], r["passage_id"]))
        if reranker is not None:
            original = {r["passage_id"]: r["score"] for r in results}
            graph_distances = {
                r["passage_id"]: r["hops"] for r in results}
            results = reranker.rerank(
                query, results, top_n=top_k * 2,
                graph_distances=graph_distances)
            for result in results:
                result["score"] = 0.5 * original[result["passage_id"]] + (
                    0.5 * result.get(
                        "rerank_score", original[result["passage_id"]]))
            results.sort(key=lambda r: (-r["score"], r["passage_id"]))
        return results[:top_k]

    def _vector_fallback(self, query, query_vec, query_concepts,
                         top_k, reranker):
        results = []
        for passage_id, text in sorted(self.graph.passages.items()):
            vector_score = max(
                0.0, cosine_similarity(
                    query_vec, self.embedder.embed(text)))
            lexical_score = _lexical_overlap(query_concepts, text)
            results.append({
                "passage_id": passage_id,
                "text": text,
                "score": 0.7 * vector_score + 0.3 * lexical_score,
                "graph_score": 0.0,
                "vector_score": vector_score,
                "lexical_score": lexical_score,
                "seed_nodes": [],
                "evidence_id": passage_id,
            })
        results.sort(key=lambda r: (-r["score"], r["passage_id"]))
        if reranker is not None:
            original = {r["passage_id"]: r["score"] for r in results}
            results = reranker.rerank(query, results, top_n=top_k * 2)
            for result in results:
                result["score"] = 0.5 * original[result["passage_id"]] + (
                    0.5 * result.get(
                        "rerank_score", original[result["passage_id"]]))
            results.sort(key=lambda r: (-r["score"], r["passage_id"]))
        return results[:top_k]


class _FilteredGraph:
    """Thin view of a graph with a replaced adjacency.

    ``personalized_pagerank`` only needs ``nodes`` and
    ``adjacency()``, so the filtered walk reuses it unchanged.
    """

    def __init__(self, graph: KnowledgeGraph, adjacency: dict):
        self.nodes = graph.nodes
        self._adjacency = adjacency

    def adjacency(self) -> dict:
        return self._adjacency
