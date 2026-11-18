"""Hybrid recall over versioned facts (G7, DuskBot pattern).

Four signals fused with reciprocal rank fusion (k=60):

1. FTS5 BM25 (keyword) — the fact index is a SQLite FTS5 sidecar.
2. Local embedding cosine (default: the deterministic
   ``HashEmbedding``; any embedder with ``embed(text)`` works).
3. Name match — token overlap between the query and the fact's key.
4. Time — recency of the fact (``valid_from`` / ``created_at``).

A fact is returned only when its cosine similarity is >= 0.6, or
>= 0.5 when it also has a keyword hit (FTS5 or name match). At most
14 facts are returned, best fused score first.

FTS5 home decision: SQLite sidecar, not Postgres tsvector.
Postgres ``ts_rank`` is a coverage/density rank, not BM25, so the
native equivalent would not be the specified signal; SQLite FTS5
``bm25()`` is the literal signal. The sidecar is a *derived* index
only — ``profile_facts`` stays the source of truth — and it can be
rebuilt at any time with :meth:`FtsIndex.rebuild`. By default the
index lives in memory; pass a ``path`` for a persistent sidecar
file (``state.db`` in the DuskBot pattern). The G24 worker is
forbidden from building FTS5: this module owns it.
"""

from __future__ import annotations

import math
import re
import sqlite3
from datetime import datetime, timezone

from .embeddings import HashEmbedding, cosine_similarity

#: Reciprocal rank fusion constant.
RRF_K = 60

#: Cosine threshold for recall.
COSINE_THRESHOLD = 0.6

#: Lower cosine threshold when the fact also has a keyword hit.
COSINE_THRESHOLD_WITH_KEYWORD = 0.5

#: Maximum facts returned by one recall call.
MAX_FACTS = 14

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text):
    return _TOKEN_RE.findall(str(text or "").lower())


def _fts5_query(query):
    """Build a safe FTS5 MATCH query from free text.

    Tokens are quoted individually and OR-ed, so user punctuation
    can never break out of the query syntax.
    """
    tokens = _tokens(query)
    if not tokens:
        raise ValueError("query must contain at least one word")
    return " OR ".join(f'"{token}"' for token in tokens)


class FtsIndex:
    """SQLite FTS5 sidecar index over fact content + key.

    ``path`` is a file path for a persistent sidecar, or ``None``
    for an in-memory index. Only the fact id, key and content are
    indexed; all other fields stay in Postgres.
    """

    def __init__(self, path=None):
        self.path = path
        self._conn = sqlite3.connect(path or ":memory:")
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts "
            "USING fts5(id UNINDEXED, key, content)")
        self._conn.commit()

    def close(self):
        self._conn.close()

    def index_fact(self, fact):
        """Insert or replace one fact in the index."""
        self.remove_fact(fact["id"])
        self._conn.execute(
            "INSERT INTO facts_fts (id, key, content) VALUES (?, ?, ?)",
            (str(fact["id"]), str(fact.get("key") or ""),
             str(fact.get("content") or "")),
        )
        self._conn.commit()

    def remove_fact(self, fact_id):
        self._conn.execute(
            "DELETE FROM facts_fts WHERE id = ?", (str(fact_id),))
        self._conn.commit()

    def rebuild(self, facts):
        """Rebuild the whole index from the source of truth."""
        self._conn.execute("DELETE FROM facts_fts")
        self._conn.executemany(
            "INSERT INTO facts_fts (id, key, content) VALUES (?, ?, ?)",
            [(str(f["id"]), str(f.get("key") or ""),
              str(f.get("content") or "")) for f in facts],
        )
        self._conn.commit()

    def search_bm25(self, query, limit=MAX_FACTS * 4):
        """BM25-ranked fact ids for ``query`` (best first).

        Returns ``[(fact_id, bm25_score), ...]`` where a lower
        ``bm25_score`` is a better match. An empty list means no
        keyword hit at all.
        """
        try:
            match = _fts5_query(query)
        except ValueError:
            return []
        rows = self._conn.execute(
            "SELECT id, bm25(facts_fts) AS score FROM facts_fts "
            "WHERE facts_fts MATCH ? ORDER BY score ASC LIMIT ?",
            (match, limit),
        ).fetchall()
        return [(row[0], float(row[1])) for row in rows]


def _rrf(ranks):
    """Reciprocal rank fusion score for a list of 1-based ranks."""
    return sum(1.0 / (RRF_K + rank) for rank in ranks)


def _recency_rank(facts, now):
    """Facts ordered newest first; returns {fact_id: rank}."""
    def age(fact):
        stamp = fact.get("valid_from") or fact.get("created_at")
        if stamp is None:
            return float("inf")
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (now - stamp).total_seconds()

    ordered = sorted(facts, key=age)
    return {fact["id"]: rank + 1 for rank, fact in enumerate(ordered)}


def hybrid_recall(query, facts, embedder=None, fts_index=None,
                  now=None, limit=MAX_FACTS,
                  cosine_threshold=COSINE_THRESHOLD,
                  keyword_threshold=COSINE_THRESHOLD_WITH_KEYWORD):
    """Recall facts for ``query`` from four fused signals.

    ``facts`` is a list of shared-shape fact records (see
    ``puspa/memory/facts.py``). Only active facts are considered;
    pass a pre-filtered list when other statuses are wanted. The
    embedder defaults to ``HashEmbedding``; ``fts_index`` defaults
    to a throwaway in-memory index built from ``facts``. Returns a
    list of ``{"fact", "score", "cosine", "bm25_rank", "name_rank",
    "recency_rank", "keyword_hit"}`` dicts, best fused score first,
    at most ``limit`` entries, all passing the cosine threshold
    rule.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    now = now or datetime.now(timezone.utc)
    active = [f for f in (facts or []) if f.get("status") == "active"]
    if not active:
        return []
    embedder = embedder or HashEmbedding()
    own_index = fts_index is None
    if own_index:
        fts_index = FtsIndex()
        fts_index.rebuild(active)
    try:
        bm25_hits = fts_index.search_bm25(query)
    finally:
        if own_index:
            fts_index.close()
    bm25_rank = {fact_id: rank + 1
                 for rank, (fact_id, _score) in enumerate(bm25_hits)}

    query_tokens = set(_tokens(query))
    query_vector = embedder.embed(query)
    scored = []
    for fact in active:
        content = str(fact.get("content") or "")
        fact_vector = embedder.embed(content)
        cosine = cosine_similarity(query_vector, fact_vector)
        name_tokens = set(_tokens(fact.get("key") or ""))
        name_overlap = len(query_tokens & name_tokens)
        keyword_hit = fact["id"] in bm25_rank or name_overlap > 0
        threshold = (keyword_threshold if keyword_hit
                     else cosine_threshold)
        if cosine < threshold:
            continue
        scored.append({
            "fact": fact, "cosine": cosine,
            "keyword_hit": keyword_hit,
            "name_overlap": name_overlap,
        })
    if not scored:
        return []

    by_cosine = sorted(scored, key=lambda s: -s["cosine"])
    cosine_rank = {s["fact"]["id"]: rank + 1
                   for rank, s in enumerate(by_cosine)}
    by_name = sorted(scored, key=lambda s: (-s["name_overlap"],
                                            -s["cosine"]))
    name_rank = {}
    for rank, item in enumerate(by_name):
        if item["name_overlap"] > 0:
            name_rank[item["fact"]["id"]] = rank + 1
    recency = _recency_rank([s["fact"] for s in scored], now)

    results = []
    for item in scored:
        fact_id = item["fact"]["id"]
        ranks = [cosine_rank[fact_id], recency[fact_id]]
        if fact_id in bm25_rank:
            ranks.append(bm25_rank[fact_id])
        if fact_id in name_rank:
            ranks.append(name_rank[fact_id])
        results.append({
            "fact": item["fact"],
            "score": _rrf(ranks),
            "cosine": item["cosine"],
            "bm25_rank": bm25_rank.get(fact_id),
            "name_rank": name_rank.get(fact_id),
            "recency_rank": recency[fact_id],
            "keyword_hit": item["keyword_hit"],
        })
    results.sort(key=lambda r: -r["score"])
    return results[:limit]
