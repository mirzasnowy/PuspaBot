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


def catat_alur_sukses(*, user_id: str, nama: str, blok: list,
                      dsn=None) -> dict:
    """Catat satu jalan sukses alur (blok yang dipakai tercatat)."""
    from psycopg.types.json import Jsonb

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, jalan_sukses FROM pustaka_alur WHERE user_id = %s AND nama = %s",
                (user_id, nama))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO pustaka_alur (user_id, nama, blok, jalan_sukses)"
                    " VALUES (%s, %s, %s, 1) RETURNING id, jalan_sukses",
                    (user_id, nama, Jsonb(blok)))
                row = cur.fetchone()
            else:
                cur.execute(
                    "UPDATE pustaka_alur SET jalan_sukses = jalan_sukses + 1, blok = %s"
                    " WHERE id = %s RETURNING id, jalan_sukses",
                    (Jsonb(blok), row["id"]))
                row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"]), "jalan_sukses": int(row["jalan_sukses"])}


def catat_permintaan_blok(*, user_id: str, blok: str, alasan: str = "",
                          dsn=None) -> dict:
    """Catat blok yang diminta tapi belum ada (Permintaan Blok)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO permintaan_blok (user_id, blok, alasan)"
                " VALUES (%s, %s, %s) RETURNING id",
                (user_id, blok, alasan))
            row = cur.fetchone()
        conn.commit()
    return {"id": str(row["id"])}


def pemakaian_blok(dsn=None) -> list[dict]:
    """Blok apa yang dipakai, berapa kali — bahan peta jalan."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT blok, jalan_sukses FROM pustaka_alur")
            hitung: dict[str, int] = {}
            for r in cur.fetchall():
                for b in (r["blok"] or []):
                    hitung[str(b)] = hitung.get(str(b), 0) + int(r["jalan_sukses"])
            cur.execute(
                "SELECT blok, COUNT(*) AS n FROM permintaan_blok GROUP BY blok")
            diminta = {r["blok"]: int(r["n"]) for r in cur.fetchall()}
    return [{"blok": b, "dipakai": n, "diminta_tapi_belum_ada": diminta.get(b, 0)}
            for b, n in sorted(hitung.items(), key=lambda kv: -kv[1])]
