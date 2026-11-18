"""Episodik — lapis 2 memori (definisi 2.7, C54).

Riwayat chat + tugas dengan index vektor pgvector (`vector(384)` +
index HNSW, skema `schema.sql`). Jalur tulis dan jalur baca keduanya
jalan di MVP. Embedding default `HashEmbedding` (gratis, lokal —
lihat `embeddings.py`); embedder lain bisa disuntikkan.

Retrieval dual-level (pola LightRAG, MIT, ditulis ulang, bukan
disalin): pencarian menggabungkan dua level, entitas spesifik
(nomor model, nama produk) dan konsep abstrak (kata kunci topik),
di atas kemiripan vektor. `dual_level_search` murni (in-memory,
bisa dites tanpa database); `search_episodes_dual` adalah jalur
database yang sama di atas Postgres + pgvector.
"""

from __future__ import annotations

import re

import psycopg
from psycopg.rows import dict_row

from .embeddings import HashEmbedding, cosine_similarity
from .schema import resolve_dsn

_ENTITY_TOKEN_RE = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b")
_ENTITY_PHRASE_RE = re.compile(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

STOPWORDS = frozenset(
    "yang dan atau di ke dari untuk dengan pada adalah ini itu saya kamu "
    "aku dia mereka kita kalian apa siapa mana kapan berapa bagaimana "
    "the a an and or of to in for on with is are was were be been "
    "this that it its as at by from".split()
)

#: Dual-level weights: entity, concept, vector.
DUAL_WEIGHTS = (0.5, 0.3, 0.2)


def extract_entities(text):
    """Specific entities in a text: model-like codes and Capitalized phrases.

    Returns a sorted list of unique lowercased entities, for
    example ["sm-a546e", "asus vivobook"].
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    found = set()
    for token in _ENTITY_TOKEN_RE.findall(text):
        lowered = token.lower()
        if (
            len(lowered) >= 4
            and any(ch.isdigit() for ch in lowered)
            and any(ch.isalpha() for ch in lowered)
        ):
            found.add(lowered)
    for phrase in _ENTITY_PHRASE_RE.findall(text):
        found.add(" ".join(phrase.lower().split()))
    return sorted(found)


def extract_concepts(text):
    """Abstract concepts in a text: content keywords, stopwords removed.

    Returns a sorted list of unique lowercased keywords.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    concepts = set()
    for token in _TOKEN_RE.findall(text.lower()):
        if len(token) >= 3 and token not in STOPWORDS:
            concepts.add(token)
    return sorted(concepts)


def _overlap(query_items, doc_items):
    if not query_items:
        return 0.0
    query_set, doc_set = set(query_items), set(doc_items)
    return len(query_set & doc_set) / len(query_set)


def dual_level_search(episodes, query, *, embedder=None, limit=5):
    """Rank episodes by dual-level retrieval (pure, in-memory).

    `episodes` is a list of dicts with at least `content` and
    `id`. Score = 0.5 * entity overlap + 0.3 * concept overlap
    + 0.2 * vector similarity (0 when no embedder is given).
    Each result carries `score`, `level` ("entity" when the
    query shares an entity, else "concept" when it shares a
    concept, else "vector") and `evidence_id` (the episode id:
    no retrieved fact is shown without its proof id). Returns
    at most `limit` results, best first.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query must be a non-empty string")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    query_entities = extract_entities(query)
    query_concepts = extract_concepts(query)
    query_vec = embedder.embed(query) if embedder is not None else None
    ranked = []
    for ep in episodes or []:
        content = str(ep.get("content") or "")
        entity_overlap = _overlap(query_entities, extract_entities(content))
        concept_overlap = _overlap(query_concepts, extract_concepts(content))
        vector_sim = 0.0
        if query_vec is not None:
            vector_sim = cosine_similarity(query_vec, embedder.embed(content))
        w_entity, w_concept, w_vector = DUAL_WEIGHTS
        score = (
            w_entity * entity_overlap
            + w_concept * concept_overlap
            + w_vector * max(0.0, vector_sim)
        )
        if entity_overlap > 0:
            level = "entity"
        elif concept_overlap > 0:
            level = "concept"
        else:
            level = "vector"
        result = dict(ep)
        result.update({
            "score": score,
            "level": level,
            "entity_overlap": entity_overlap,
            "concept_overlap": concept_overlap,
            "similarity": vector_sim,
            "evidence_id": str(ep.get("id")) if ep.get("id") is not None else None,
        })
        ranked.append(result)
    ranked.sort(key=lambda r: (-r["score"], str(r.get("id"))))
    return ranked[:limit]


def search_episodes_dual(user_id, query, limit=5, agent_id=None,
                         embedder=None, dsn=None):
    """Dual-level episode search on Postgres + pgvector.

    Candidate episodes come from vector search (limit * 4) plus
    the latest episodes, then `dual_level_search` re-ranks them
    by entity + concept + vector. Only the user's own episodes
    are returned, each with its proof id.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query must be a non-empty string")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    embedder = embedder or HashEmbedding()
    candidates = search_episodes(
        user_id, query, limit=limit * 4, agent_id=agent_id,
        embedder=embedder, dsn=dsn,
    )
    seen = {c["id"] for c in candidates}
    for ep in list_episodes(user_id, limit=limit * 4, dsn=dsn):
        if ep["id"] not in seen:
            candidates.append(ep)
            seen.add(ep["id"])
    return dual_level_search(candidates, query, embedder=embedder, limit=limit)


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _vector_literal(vector):
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


def record_episode(user_id, content, event_type, agent_id=None, task_id=None,
                   event_id=None, embedder=None, dsn=None):
    """Tulis satu episode; kembalikan dict episode (tanpa vektor)."""
    if not isinstance(content, str) or content == "":
        raise ValueError("isi harus string tak kosong")
    if not isinstance(event_type, str) or event_type == "":
        raise ValueError("jenis harus string tak kosong")
    embedder = embedder or HashEmbedding()
    vector = embedder.embed(content)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO episodic (user_id, agent_id, task_id, type, content, "
                "embedding, event_id) VALUES (%s, %s, %s, %s, %s, %s::vector, %s) "
                "RETURNING id, user_id, agent_id, task_id, type, content, "
                "event_id, created_at",
                (user_id, agent_id, task_id, event_type, content,
                 _vector_literal(vector), event_id),
            )
            row = cur.fetchone()
        conn.commit()
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "agent_id": str(row["agent_id"]) if row["agent_id"] else None,
        "task_id": str(row["task_id"]) if row["task_id"] else None,
        "type": row["type"],
        "content": row["content"],
        "event_id": str(row["event_id"]) if row["event_id"] else None,
        "created_at": row["created_at"],
    }


def search_episodes(user_id, query, limit=5, agent_id=None,
                    embedder=None, dsn=None):
    """Cari episode paling mirip (cosine distance pgvector).

    Kembalikan list dict episode + `jarak` (0 = identik) + `kemiripan`
    (1 - jarak), urut paling mirip dulu. Hanya episode milik user ini.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query harus string tak kosong")
    if limit < 1:
        raise ValueError("limit minimal 1")
    embedder = embedder or HashEmbedding()
    vector = _vector_literal(embedder.embed(query))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            if agent_id is None:
                cur.execute(
                    "SELECT id, user_id, agent_id, task_id, type, content, "
                    "event_id, created_at, "
                    "embedding <=> %s::vector AS distance FROM episodic "
                    "WHERE user_id = %s ORDER BY distance ASC LIMIT %s",
                    (vector, user_id, limit),
                )
            else:
                cur.execute(
                    "SELECT id, user_id, agent_id, task_id, type, content, "
                    "event_id, created_at, "
                    "embedding <=> %s::vector AS distance FROM episodic "
                    "WHERE user_id = %s AND (agent_id IS NULL OR agent_id = %s) "
                    "ORDER BY distance ASC LIMIT %s",
                    (vector, user_id, agent_id, limit),
                )
            out = []
            for r in cur.fetchall():
                out.append({
                    "id": str(r["id"]),
                    "user_id": str(r["user_id"]),
                    "agent_id": str(r["agent_id"]) if r["agent_id"] else None,
                    "task_id": str(r["task_id"]) if r["task_id"] else None,
                    "type": r["type"],
                    "content": r["content"],
                    "event_id": str(r["event_id"]) if r["event_id"] else None,
                    "created_at": r["created_at"],
                    "distance": float(r["distance"]),
                    "similarity": 1.0 - float(r["distance"]),
                })
            return out


def list_episodes(user_id, limit=50, dsn=None):
    """Episode terbaru milik user, urut waktu menurun."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, agent_id, task_id, type, content, "
                "event_id, created_at FROM episodic WHERE user_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (user_id, limit),
            )
            return [
                {
                    "id": str(r["id"]),
                    "user_id": str(r["user_id"]),
                    "agent_id": str(r["agent_id"]) if r["agent_id"] else None,
                    "task_id": str(r["task_id"]) if r["task_id"] else None,
                    "type": r["type"],
                    "content": r["content"],
                    "event_id": str(r["event_id"]) if r["event_id"] else None,
                    "created_at": r["created_at"],
                }
                for r in cur.fetchall()
            ]
