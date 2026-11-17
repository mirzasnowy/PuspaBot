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


def record_episode(user_id, isi, jenis, agen_id=None, tugas_id=None,
                   kejadian_id=None, embedder=None, dsn=None):
    """Tulis satu episode; kembalikan dict episode (tanpa vektor)."""
    if not isinstance(isi, str) or isi == "":
        raise ValueError("isi harus string tak kosong")
    if not isinstance(jenis, str) or jenis == "":
        raise ValueError("jenis harus string tak kosong")
    embedder = embedder or HashEmbedding()
    vector = embedder.embed(isi)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO episodik (user_id, agen_id, tugas_id, jenis, isi, "
                "embedding, kejadian_id) VALUES (%s, %s, %s, %s, %s, %s::vector, %s) "
                "RETURNING id, user_id, agen_id, tugas_id, jenis, isi, "
                "kejadian_id, dibuat",
                (user_id, agen_id, tugas_id, jenis, isi,
                 _vector_literal(vector), kejadian_id),
            )
            row = cur.fetchone()
        conn.commit()
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "agen_id": str(row["agen_id"]) if row["agen_id"] else None,
        "tugas_id": str(row["tugas_id"]) if row["tugas_id"] else None,
        "jenis": row["jenis"],
        "isi": row["isi"],
        "kejadian_id": str(row["kejadian_id"]) if row["kejadian_id"] else None,
        "dibuat": row["dibuat"],
    }


def search_episodes(user_id, query, limit=5, agen_id=None,
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
            if agen_id is None:
                cur.execute(
                    "SELECT id, user_id, agen_id, tugas_id, jenis, isi, "
                    "kejadian_id, dibuat, "
                    "embedding <=> %s::vector AS jarak FROM episodik "
                    "WHERE user_id = %s ORDER BY jarak ASC LIMIT %s",
                    (vector, user_id, limit),
                )
            else:
                cur.execute(
                    "SELECT id, user_id, agen_id, tugas_id, jenis, isi, "
                    "kejadian_id, dibuat, "
                    "embedding <=> %s::vector AS jarak FROM episodik "
                    "WHERE user_id = %s AND (agen_id IS NULL OR agen_id = %s) "
                    "ORDER BY jarak ASC LIMIT %s",
                    (vector, user_id, agen_id, limit),
                )
            out = []
            for r in cur.fetchall():
                out.append({
                    "id": str(r["id"]),
                    "user_id": str(r["user_id"]),
                    "agen_id": str(r["agen_id"]) if r["agen_id"] else None,
                    "tugas_id": str(r["tugas_id"]) if r["tugas_id"] else None,
                    "jenis": r["jenis"],
                    "isi": r["isi"],
                    "kejadian_id": str(r["kejadian_id"]) if r["kejadian_id"] else None,
                    "dibuat": r["dibuat"],
                    "jarak": float(r["jarak"]),
                    "kemiripan": 1.0 - float(r["jarak"]),
                })
            return out


def list_episodes(user_id, limit=50, dsn=None):
    """Episode terbaru milik user, urut waktu menurun."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, agen_id, tugas_id, jenis, isi, "
                "kejadian_id, dibuat FROM episodik WHERE user_id = %s "
                "ORDER BY dibuat DESC LIMIT %s",
                (user_id, limit),
            )
            return [
                {
                    "id": str(r["id"]),
                    "user_id": str(r["user_id"]),
                    "agen_id": str(r["agen_id"]) if r["agen_id"] else None,
                    "tugas_id": str(r["tugas_id"]) if r["tugas_id"] else None,
                    "jenis": r["jenis"],
                    "isi": r["isi"],
                    "kejadian_id": str(r["kejadian_id"]) if r["kejadian_id"] else None,
                    "dibuat": r["dibuat"],
                }
                for r in cur.fetchall()
            ]
