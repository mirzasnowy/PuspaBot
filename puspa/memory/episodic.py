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

from puspa.db import Connection as _db_Connection
from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.db import errors as pg_errors

from .embeddings import HashEmbedding, cosine_distance, cosine_similarity, parse_stored_vector
from .schema import resolve_dsn

#: Most candidates the SQLite brute-force search scores (D-SQLite-4):
#: the newest rows for this user and embedder. The Postgres path
#: has no cap (the index ranks everything); the cap keeps the
#: single-user desktop exact in practice and bounded in theory.
BRUTE_FORCE_CANDIDATES = 2000

_ENTITY_TOKEN_RE = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b")
_ENTITY_PHRASE_RE = re.compile(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

STOPWORDS = frozenset(
    [
        "yang",
        "dan",
        "atau",
        "di",
        "ke",
        "dari",
        "untuk",
        "dengan",
        "pada",
        "adalah",
        "ini",
        "itu",
        "saya",
        "kamu",
        "aku",
        "dia",
        "mereka",
        "kita",
        "kalian",
        "apa",
        "siapa",
        "mana",
        "kapan",
        "berapa",
        "bagaimana",
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "this",
        "that",
        "it",
        "its",
        "as",
        "at",
        "by",
        "from",
    ]
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
        result.update(
            {
                "score": score,
                "level": level,
                "entity_overlap": entity_overlap,
                "concept_overlap": concept_overlap,
                "similarity": vector_sim,
                "evidence_id": str(ep.get("id")) if ep.get("id") is not None else None,
            }
        )
        ranked.append(result)
    ranked.sort(key=lambda r: (-r["score"], str(r.get("id"))))
    return ranked[:limit]


def search_episodes_dual(user_id, query, limit=5, agent_id=None, embedder=None, dsn=None):
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
        user_id,
        query,
        limit=limit * 4,
        agent_id=agent_id,
        embedder=embedder,
        dsn=dsn,
    )
    seen = {c["id"] for c in candidates}
    for ep in list_episodes(user_id, limit=limit * 4, dsn=dsn):
        if ep["id"] not in seen:
            candidates.append(ep)
            seen.add(ep["id"])
    return dual_level_search(candidates, query, embedder=embedder, limit=limit)


def episodes_to_graph(episodes, extractor=None):
    """Build a knowledge graph from episode dicts (pure, in-memory).

    Each episode becomes one document, so its passage ids look
    like "<episode id>#p0" and map back to the episode. This is
    the seam where episodic recall meets the retrieval pipeline.
    The retrieval package is imported lazily so this module keeps
    no import cycle with it.
    """
    from puspa.retrieval.knowledge_graph import KnowledgeGraph

    graph = KnowledgeGraph(extractor=extractor)
    for episode in episodes or []:
        content = str(episode.get("content") or "")
        if not content:
            continue
        graph.add_document(str(episode.get("id")), content)
    return graph


def search_episodes_pipeline(
    user_id, query, mode="mix", limit=5, agent_id=None, embedder=None, dsn=None, reranker=None
):
    """Episodic recall through the retrieval pipeline.

    Same candidates as `search_episodes_dual` (vector search plus
    the latest episodes), but instead of plain dual-level scoring
    the candidates are indexed into a knowledge graph and read
    back through a LightRAG query mode, with an optional
    reranker on the shortlist. A multi-hop question (product,
    promo, seller, fraud report) can reach an episode no single
    passage shares words with. Every result carries `score`,
    `mode`, `level` and `evidence_id` (the episode id).
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query must be a non-empty string")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    from puspa.retrieval.modes import query as mode_query

    embedder = embedder or HashEmbedding()
    candidates = search_episodes(
        user_id,
        query,
        limit=limit * 4,
        agent_id=agent_id,
        embedder=embedder,
        dsn=dsn,
    )
    seen = {c["id"] for c in candidates}
    for ep in list_episodes(user_id, limit=limit * 4, dsn=dsn):
        if ep["id"] not in seen:
            candidates.append(ep)
            seen.add(ep["id"])
    graph = episodes_to_graph(candidates)
    ranked = mode_query(graph, query, mode=mode, embedder=embedder, top_k=limit)
    if reranker is not None:
        ranked = reranker.rerank(query, ranked, top_n=limit)
        for result in ranked:
            result["score"] = result.get("rerank_score", result["score"])
    by_id = {str(ep["id"]): ep for ep in candidates}
    out = []
    for result in ranked:
        episode_id = result["passage_id"].split("#p")[0]
        episode = by_id.get(episode_id)
        if episode is None:
            continue
        entry = dict(episode)
        entry.update(
            {
                "score": result["score"],
                "mode": result.get("mode", mode),
                "level": result.get("level", "concept"),
                "evidence_id": episode_id,
            }
        )
        out.append(entry)
    return out


def _connect(dsn=None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


def _vector_literal(vector):
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


def _check_active_embedder(embedder_id, dimension, dsn=None):
    """Refuse a write whose embedder differs from the active one.

    The active embedder lives in the ``embedding_state`` singleton
    (migration 0022). The first writer claims it; later writers
    with a different embedder (or dimension) are refused with
    ``MixedEmbedderError``. The sanctioned way to change it is a
    tiered re-embed job, never a silent switch. On a database that
    predates migration 0022 there is no state to enforce, so the
    check is skipped (the insert itself then fails loudly on the
    missing columns, which is the operator's cue to migrate).
    """
    from puspa.retrieval.embedding_health import MixedEmbedderError

    try:
        with _connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT active_embedder_id, active_dimension FROM embedding_state WHERE id = 1"
                )
                row = cur.fetchone()
            if row is None:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO embedding_state "
                        "(id, active_embedder_id, active_dimension) "
                        "VALUES (1, %s, %s) ON CONFLICT (id) DO NOTHING",
                        (embedder_id, dimension),
                    )
            elif (row["active_embedder_id"], int(row["active_dimension"])) != (
                embedder_id,
                dimension,
            ):
                raise MixedEmbedderError(
                    f"refusing episode from embedder {embedder_id} "
                    f"(dim {dimension}): active embedder is "
                    f"{row['active_embedder_id']} "
                    f"(dim {row['active_dimension']}); "
                    "finish a re-embed job before switching embedders"
                )
            conn.commit()
    except pg_errors.UndefinedTable:
        return


def record_episode(
    user_id,
    content,
    event_type,
    agent_id=None,
    task_id=None,
    event_id=None,
    embedder=None,
    dsn=None,
):
    """Tulis satu episode; kembalikan dict episode (tanpa vektor).

    The row stores the embedder that produced its vector
    (``embedder_id`` + ``dimension``, E-14), and the write is
    refused when that embedder differs from the active one, so
    vectors from different embedders never share one index.
    """
    if not isinstance(content, str) or content == "":
        raise ValueError("isi harus string tak kosong")
    if not isinstance(event_type, str) or event_type == "":
        raise ValueError("jenis harus string tak kosong")
    embedder = embedder or HashEmbedding()
    vector = embedder.embed(content)
    from puspa.retrieval.embedding_health import dimension_of, embedder_id_of

    embedder_id = embedder_id_of(embedder)
    dimension = dimension_of(embedder)
    _check_active_embedder(embedder_id, dimension, dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO episodic (user_id, agent_id, task_id, type, content, "
                "embedding, embedder_id, dimension, event_id) "
                "VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s, %s) "
                "RETURNING id, user_id, agent_id, task_id, type, content, "
                "embedder_id, dimension, event_id, created_at",
                (
                    user_id,
                    agent_id,
                    task_id,
                    event_type,
                    content,
                    _vector_literal(vector),
                    embedder_id,
                    dimension,
                    event_id,
                ),
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
        "embedder_id": row["embedder_id"],
        "dimension": int(row["dimension"]),
        "event_id": str(row["event_id"]) if row["event_id"] else None,
        "created_at": row["created_at"],
    }


def _search_episodes_sqlite(conn, user_id, agent_id, embedder_id, dimension, vector, limit):
    """Brute-force cosine search (D-SQLite-4, no pgvector).

    The newest candidate rows for this user and embedder are
    scored in Python with :func:`cosine_distance` (exact, not
    approximate) and the closest ``limit`` are returned in the
    same shape as the pgvector path below.
    """
    with conn.cursor() as cur:
        if agent_id is None:
            cur.execute(
                "SELECT id, user_id, agent_id, task_id, type, content, "
                "embedder_id, dimension, event_id, created_at, embedding "
                "FROM episodic "
                "WHERE user_id = %s AND embedder_id = %s AND dimension = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (user_id, embedder_id, dimension, BRUTE_FORCE_CANDIDATES),
            )
        else:
            cur.execute(
                "SELECT id, user_id, agent_id, task_id, type, content, "
                "embedder_id, dimension, event_id, created_at, embedding "
                "FROM episodic "
                "WHERE user_id = %s AND (agent_id IS NULL OR agent_id = %s) "
                "AND embedder_id = %s AND dimension = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (user_id, agent_id, embedder_id, dimension, BRUTE_FORCE_CANDIDATES),
            )
        scored = []
        for r in cur.fetchall():
            stored = parse_stored_vector(r["embedding"])
            if stored is None:
                continue
            distance = cosine_distance(vector, stored)
            scored.append((distance, r))
    scored.sort(key=lambda item: item[0])
    out = []
    for distance, r in scored[:limit]:
        out.append(
            {
                "id": str(r["id"]),
                "user_id": str(r["user_id"]),
                "agent_id": str(r["agent_id"]) if r["agent_id"] else None,
                "task_id": str(r["task_id"]) if r["task_id"] else None,
                "type": r["type"],
                "content": r["content"],
                "embedder_id": r["embedder_id"],
                "dimension": int(r["dimension"]),
                "event_id": str(r["event_id"]) if r["event_id"] else None,
                "created_at": r["created_at"],
                "distance": float(distance),
                "similarity": 1.0 - float(distance),
            }
        )
    return out


def search_episodes(user_id, query, limit=5, agent_id=None, embedder=None, dsn=None):
    """Cari episode paling mirip (cosine distance pgvector).

    Kembalikan list dict episode + `jarak` (0 = identik) + `kemiripan`
    (1 - jarak), urut paling mirip dulu. Hanya episode milik user ini.
    Only rows from the query embedder are compared (E-14): vectors
    from another embedder never score, even when they share a table.
    On SQLite the same ranking is computed by brute-force cosine in
    Python (D-SQLite-4): exact, no pgvector, no new dependency.
    """
    if not isinstance(query, str) or query == "":
        raise ValueError("query harus string tak kosong")
    if limit < 1:
        raise ValueError("limit minimal 1")
    embedder = embedder or HashEmbedding()
    from puspa.retrieval.embedding_health import dimension_of, embedder_id_of

    embedder_id = embedder_id_of(embedder)
    dimension = dimension_of(embedder)
    vector = embedder.embed(query)
    with _connect(dsn) as conn:
        if isinstance(conn, _db_Connection):
            return _search_episodes_sqlite(
                conn, user_id, agent_id, embedder_id, dimension, vector, limit
            )
        return _search_episodes_pgvector(
            conn, user_id, agent_id, embedder_id, dimension, vector, limit
        )


def _search_episodes_pgvector(conn, user_id, agent_id, embedder_id, dimension, vector, limit):
    """The Postgres path: pgvector ``<=>`` ranks the rows."""
    vector = _vector_literal(vector)
    with conn.cursor() as cur:
        if agent_id is None:
            cur.execute(
                "SELECT id, user_id, agent_id, task_id, type, content, "
                "embedder_id, dimension, "
                "event_id, created_at, "
                "embedding <=> %s::vector AS distance FROM episodic "
                "WHERE user_id = %s AND embedder_id = %s AND dimension = %s "
                "ORDER BY distance ASC LIMIT %s",
                (vector, user_id, embedder_id, dimension, limit),
            )
        else:
            cur.execute(
                "SELECT id, user_id, agent_id, task_id, type, content, "
                "embedder_id, dimension, "
                "event_id, created_at, "
                "embedding <=> %s::vector AS distance FROM episodic "
                "WHERE user_id = %s AND (agent_id IS NULL OR agent_id = %s) "
                "AND embedder_id = %s AND dimension = %s "
                "ORDER BY distance ASC LIMIT %s",
                (vector, user_id, agent_id, embedder_id, dimension, limit),
            )
        out = []
        for r in cur.fetchall():
            out.append(
                {
                    "id": str(r["id"]),
                    "user_id": str(r["user_id"]),
                    "agent_id": str(r["agent_id"]) if r["agent_id"] else None,
                    "task_id": str(r["task_id"]) if r["task_id"] else None,
                    "type": r["type"],
                    "content": r["content"],
                    "embedder_id": r["embedder_id"],
                    "dimension": int(r["dimension"]),
                    "event_id": str(r["event_id"]) if r["event_id"] else None,
                    "created_at": r["created_at"],
                    "distance": float(r["distance"]),
                    "similarity": 1.0 - float(r["distance"]),
                }
            )
        return out


def list_episodes(user_id, limit=50, dsn=None):
    """Episode terbaru milik user, urut waktu menurun."""
    with _connect(dsn) as conn, conn.cursor() as cur:
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
