"""Pustaka alur dan modul (§3.8 revisi 7) — FONDASI Fase 4.

Setiap alur dan modul yang sukses menambah data tentang blok mana
yang dipakai dan blok apa yang diminta tapi belum ada (Permintaan
Blok). Di MVP semuanya privat.

Yang dibangun di Fase 3 (fondasi saja, BUKAN alur kustom Fase 4):
- catat alur yang sukses (nama + blok yang dipakai + hitungan jalan),
- catat Permintaan Blok (blok yang diminta user tapi belum ada),
- daftar pemakaian blok (bahan peta jalan Fase 4/6).

Metrik alur aktif (5/10 pengguna) TIDAK diklaim di Fase 3.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def record_workflow_success(*, user_id: str, name: str, block: list,
                      dsn=None) -> dict:
    """Catat satu jalan sukses alur (blok yang dipakai tercatat)."""
    from psycopg.types.json import Jsonb

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, successful_runs FROM workflow_library WHERE user_id = %s AND name = %s",
                (user_id, name))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO workflow_library (user_id, name, block, successful_runs)"
                    " VALUES (%s, %s, %s, 1) RETURNING id, successful_runs",
                    (user_id, name, Jsonb(block)))
                row = cur.fetchone()
            else:
                cur.execute(
                    "UPDATE workflow_library SET successful_runs = successful_runs + 1, block = %s"
                    " WHERE id = %s RETURNING id, successful_runs",
                    (Jsonb(block), row["id"]))
                row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"]), "successful_runs": int(row["successful_runs"])}


def record_block_request(*, user_id: str, block: str, reason: str = "",
                          dsn=None) -> dict:
    """Catat blok yang diminta tapi belum ada (Permintaan Blok)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO block_requests (user_id, block, reason)"
                " VALUES (%s, %s, %s) RETURNING id",
                (user_id, block, reason))
            row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"])}


def block_usage(dsn=None) -> list[dict]:
    """Blok apa yang dipakai, berapa kali — bahan peta jalan."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT block, successful_runs FROM workflow_library")
            counts: dict[str, int] = {}
            for r in cur.fetchall():
                for b in (r["block"] or []):
                    counts[str(b)] = counts.get(str(b), 0) + int(r["successful_runs"])
            cur.execute(
                "SELECT block, COUNT(*) AS n FROM block_requests GROUP BY block")
            requested = {r["block"]: int(r["n"]) for r in cur.fetchall()}
    return [{"block": b, "used": n, "diminta_tapi_not_yet_ada": requested.get(b, 0)}
            for b, n in sorted(counts.items(), key=lambda kv: -kv[1])]
