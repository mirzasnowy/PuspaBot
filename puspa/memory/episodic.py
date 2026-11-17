"""Episodik — lapis 2 memori (definisi 2.7, C54).

Riwayat chat + tugas dengan index vektor pgvector (`vector(384)` +
index HNSW, skema `schema.sql`). Jalur tulis dan jalur baca keduanya
jalan di MVP. Embedding default `HashEmbedding` (gratis, lokal —
lihat `embeddings.py`); embedder lain bisa disuntikkan.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from .embeddings import HashEmbedding
from .schema import resolve_dsn


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
                    "embedding <=> %s::vector AS jarak FROM episodic "
                    "WHERE user_id = %s ORDER BY jarak ASC LIMIT %s",
                    (vector, user_id, limit),
                )
            else:
                cur.execute(
                    "SELECT id, user_id, agent_id, task_id, type, content, "
                    "event_id, created_at, "
                    "embedding <=> %s::vector AS jarak FROM episodic "
                    "WHERE user_id = %s AND (agent_id IS NULL OR agent_id = %s) "
                    "ORDER BY jarak ASC LIMIT %s",
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
                    "jarak": float(r["jarak"]),
                    "kemiripan": 1.0 - float(r["jarak"]),
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
