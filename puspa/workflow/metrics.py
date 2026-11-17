"""Metrik alur (§3.8 revisi 7) — fondasi gerbang 8 + gerbang 13.

- minimal 5 dari 10 pengguna uji punya satu alur aktif setelah 2 minggu;
- minimal 50% jalan alur selesai tanpa campur tangan;
- Permintaan Blok tercatat dan diurutkan.

Tanpa pengguna uji, metrik pertama dilaporkan `tertunda` (bukan
diklaim). Jalan tanpa campur tangan terukur lokal dari jalan_alur.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
TARGET_PENGGUNA_AKTIF = 5
TARGET_JALAN_TANPA_CAMPUR = 50.0


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def metrik_alur(dsn=None) -> dict:
    """Hitung metrik alur dari tabel Fase 4 + pustaka Fase 3."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(DISTINCT user_id) AS n FROM alur WHERE status = 'aktif'")
            pengguna_aktif = int(cur.fetchone()["n"])
            cur.execute("SELECT status, COUNT(*) AS n FROM jalan_alur GROUP BY status")
            jalan = {r["status"]: int(r["n"]) for r in cur.fetchall()}
            cur.execute(
                "SELECT blok, COUNT(*) AS n FROM permintaan_blok"
                " GROUP BY blok ORDER BY n DESC")
            permintaan = [(r["blok"], int(r["n"])) for r in cur.fetchall()]
    total_jalan = sum(jalan.values())
    selesai = jalan.get("selesai", 0)
    tanpa_campur = round(selesai / total_jalan * 100, 1) if total_jalan else None
    return {
        "pengguna_dengan_alur_aktif": pengguna_aktif,
        "target_pengguna_aktif": TARGET_PENGGUNA_AKTIF,
        "status_pengguna_aktif": (
            "tertunda" if pengguna_aktif < TARGET_PENGGUNA_AKTIF else "lolos"),
        "alasan_pengguna_aktif": (
            "butuh pengguna uji (10 orang) + masa uji 2 minggu"
            if pengguna_aktif < TARGET_PENGGUNA_AKTIF else ""),
        "jalan_total": total_jalan,
        "jalan_selesai_tanpa_campur": selesai,
        "persen_jalan_tanpa_campur": tanpa_campur,
        "target_jalan_tanpa_campur": TARGET_JALAN_TANPA_CAMPUR,
        "permintaan_blok": [{"blok": b, "jumlah": n} for b, n in permintaan],
    }
