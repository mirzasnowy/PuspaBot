"""Reranking after retrieval (LlamaIndex pattern, rewritten).

In LlamaIndex a reranker is a node postprocessor: it runs between
retrieval and response synthesis, takes the retrieved nodes and
re-orders them before the LLM sees them. The recommended local
default is a cross-encoder (``SentenceTransformerRerank`` with a
MS MARCO model such as ``cross-encoder/ms-marco-MiniLM-L6-v2``,
or a ``BAAI/bge-reranker-*`` model). LlamaIndex also decomposes
a complex query into sub-questions (``SubQuestionQueryEngine`` /
``DecomposeQueryTransform``): each sub-question is retrieved
separately and the answers are combined.

This module implements the same two hooks in Puspa's own code:

- ``DeterministicReranker``: a scorer with no model, combining
  three signals, lexical overlap, vector similarity and graph
  distance. It genuinely re-orders the shortlist and is what
  tests and the offline path use.
- ``CrossEncoderReranker``: the seam for a real cross-encoder.
  It takes any ``score_fn(query, text)`` callable, so a
  sentence-transformers or FlagEmbedding model plugs in with one
  function. ``from_fastembed`` tries the fastembed reranker and
  raises ``RerankerUnavailable`` with an honest reason when it
  cannot load: fastembed 0.9.1, the version pinned in this repo,
  ships no reranker at all, so a real cross-encoder backend
  (``BAAI/bge-reranker-v2-m3`` through FlagEmbedding, or a
  ``cross-encoder/ms-marco-MiniLM-L6-v2`` through
  sentence-transformers) is a follow-up dependency, not
  something this module pretends to have.
- ``decompose_query`` plus ``retrieve_with_decomposition``: a
  deterministic stand-in for the LLM question generator. The
  query is split on conjunctions into sub-questions, each is
  retrieved separately, and the rankings are merged with
  reciprocal rank fusion. A real LLM decomposer plugs into the
  same shape by replacing ``decompose_query``.
"""

from __future__ import annotations

import re

from puspa.memory.embeddings import HashEmbedding, cosine_similarity
from puspa.memory.episodic import extract_concepts

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SPLIT_RE = re.compile(r"\s+and\s+|\s+vs\.?\s+|;\s*|\?\s*")


class RerankerUnavailable(Exception):
    """A real cross-encoder reranker was requested but cannot load."""


def _candidate_text(candidate: dict) -> str:
    return str(candidate.get("text") or candidate.get("content") or "")


def _candidate_id(candidate: dict) -> str:
    return str(candidate.get("passage_id") or candidate.get("id") or "")


class DeterministicReranker:
    """Reranker from three cheap signals, no model.

    Score = lexical weight * concept overlap + vector weight *
    vector similarity + graph weight * graph closeness, where
    graph closeness is ``1 / (1 + hops)`` from the query seed
    nodes. ``graph_distances`` maps passage id to hops and is
    optional; without it the graph term is 0.
    """

    def __init__(self, embedder=None, weights=(0.4, 0.4, 0.2)):
        if len(weights) != 3:
            raise ValueError("weights must be (lexical, vector, graph)")
        self.embedder = embedder or HashEmbedding()
        self.weights = weights

    def score(self, query: str, text: str, graph_distance=None) -> dict:
        """Score one passage, returning the total plus each signal."""
        query_concepts = extract_concepts(query)
        tokens = set(_TOKEN_RE.findall(text.lower()))
        lexical = (
            len(set(query_concepts) & tokens) / len(query_concepts)
            if query_concepts else 0.0
        )
        vector = max(0.0, cosine_similarity(
            self.embedder.embed(query), self.embedder.embed(text)))
        graph = 0.0
        if graph_distance is not None:
            graph = 1.0 / (1.0 + max(0, graph_distance))
        weight_lexical, weight_vector, weight_graph = self.weights
        total = (
            weight_lexical * lexical
            + weight_vector * vector
            + weight_graph * graph
        )
        return {"score": total, "lexical": lexical,
                "vector": vector, "graph": graph}

    def rerank(self, query: str, candidates, top_n=None,
               graph_distances=None) -> list:
        """Re-order candidates by reranker score, best first.

        Each returned candidate is a copy with ``rerank_score``
        and ``rerank_signals`` attached. The input order is never
        trusted: the output order is the score order.
        """
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        distances = graph_distances or {}
        scored = []
        for candidate in candidates or []:
            text = _candidate_text(candidate)
            detail = self.score(
                query, text,
                graph_distance=distances.get(_candidate_id(candidate)))
            entry = dict(candidate)
            entry["rerank_score"] = detail["score"]
            entry["rerank_signals"] = detail
            scored.append(entry)
        scored.sort(key=lambda r: (-r["rerank_score"], _candidate_id(r)))
        if top_n is not None:
            scored = scored[:top_n]
        return scored


class CrossEncoderReranker:
    """Reranker backed by a real cross-encoder score function.

    ``score_fn(query, text)`` returns a relevance float. Any
    cross-encoder plugs in here: a sentence-transformers
    ``CrossEncoder`` wrapped as ``lambda q, t:
    model.predict([(q, t)])[0]``, or a FlagEmbedding reranker.
    This class holds no model and downloads nothing itself.
    """

    def __init__(self, score_fn):
        if not callable(score_fn):
            raise ValueError("score_fn must be a callable")
        self.score_fn = score_fn

    @classmethod
    def from_fastembed(cls, model_name: str = "BAAI/bge-reranker-v2-m3"):
        """Build a cross-encoder reranker through fastembed.

        Raises ``RerankerUnavailable`` when fastembed has no
        reranker to offer. fastembed 0.9.1 ships no reranker
        class, so on the pinned version this always raises with
        the reason and the follow-up (FlagEmbedding or
        sentence-transformers) named.
        """
        try:
            from fastembed import TextReranker
        except ImportError as exc:
            raise RerankerUnavailable(
                "fastembed 0.9.1 ships no reranker (no TextReranker); "
                "a real cross-encoder backend such as "
                "BAAI/bge-reranker-v2-m3 needs FlagEmbedding or "
                "sentence-transformers, which is not a dependency yet"
            ) from exc
        try:
            model = TextReranker(model_name)
        except Exception as exc:  # noqa: BLE001 - any load failure
            raise RerankerUnavailable(
                f"could not load cross-encoder {model_name!r}: {exc}"
            ) from exc

        def _score(query, text):
            return float(model.rerank(query, [text])[0])

        return cls(_score)

    def score(self, query: str, text: str, graph_distance=None) -> dict:
        """Score one passage with the cross-encoder."""
        return {"score": float(self.score_fn(query, text)),
                "lexical": 0.0, "vector": 0.0, "graph": 0.0}

    def rerank(self, query: str, candidates, top_n=None,
               graph_distances=None) -> list:
        """Re-order candidates by cross-encoder score, best first."""
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        scored = []
        for candidate in candidates or []:
            entry = dict(candidate)
            entry["rerank_score"] = float(
                self.score_fn(query, _candidate_text(candidate)))
            entry["rerank_signals"] = {"score": entry["rerank_score"]}
            scored.append(entry)
        scored.sort(key=lambda r: (-r["rerank_score"], _candidate_id(r)))
        if top_n is not None:
            scored = scored[:top_n]
        return scored


def make_reranker(kind: str = "deterministic", **kwargs):
    """Build a reranker by kind: "deterministic" or "cross_encoder".

    "cross_encoder" tries the fastembed backend and raises
    ``RerankerUnavailable`` when it cannot load (see
    ``CrossEncoderReranker.from_fastembed``).
    """
    if kind == "deterministic":
        return DeterministicReranker(**kwargs)
    if kind == "cross_encoder":
        return CrossEncoderReranker.from_fastembed(
            **kwargs)
    raise ValueError(f"unknown reranker kind: {kind!r}")


def decompose_query(query: str) -> list:
    """Split a complex query into sub-questions, deterministically.

    Splits on "and", "vs", ";" and "?" boundaries. A query with
    no such boundary returns itself unchanged. This is a
    deterministic stand-in for LlamaIndex's LLM question
    generator: it cannot keep shared context across the split
    the way an LLM can, so a sub-question may read
    under-specified on its own. A real LLM decomposer replaces
    this function with the same return shape.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query must be a non-empty string")
    parts = [p.strip() for p in _SPLIT_RE.split(query)]
    parts = [p for p in parts if p]
    return parts or [query]


def retrieve_with_decomposition(retrieve_fn, query: str, top_k: int = 5,
                                max_subquestions: int = 4) -> list:
    """Retrieve per sub-question, merge with reciprocal rank fusion.

    ``retrieve_fn(sub_query, top_k)`` returns ranked dicts with
    a "passage_id" (or "id") and a "score". Each sub-question
    contributes ``1 / (60 + rank)`` per passage; the merged list
    is sorted by the fused score. A single sub-question just runs
    the plain retrieval.
    """
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    subquestions = decompose_query(query)[:max_subquestions]
    if len(subquestions) <= 1:
        return retrieve_fn(query, top_k=top_k)
    fused: dict[str, dict] = {}
    for sub in subquestions:
        for rank, result in enumerate(
                retrieve_fn(sub, top_k=top_k)):
            passage_id = _candidate_id(result)
            if not passage_id:
                continue
            entry = fused.setdefault(passage_id, dict(result))
            entry["rrf_score"] = entry.get("rrf_score", 0.0) + (
                1.0 / (60.0 + rank))
            entry.setdefault("subquestions", []).append(sub)
    merged = sorted(
        fused.values(), key=lambda r: (-r["rrf_score"], _candidate_id(r)))
    for entry in merged:
        entry["score"] = entry["rrf_score"]
    return merged[:top_k]
