"""Lapor Salah (§3.8 revisi 7).

Tiap angka di panel Bukti punya tombol Lapor Salah. Laporan:
- menurunkan Skor Keandalan sumbernya (pembacaan baru ber-vonis
  "Tidak bisa dipastikan" ditulis ke `pembacaan_sumber`),
- memicu baca ulang (flag `baca_ulang_diminta`),
- tercatat di Buku Kerja (jenis `laporan_salah` — sudah ada di ledger
  §2.3).

Umpan balik user ke bukti, melengkapi konfirmasi identitas (§3.2).
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def lapor_salah(*, bukti_id: str, user_id: str, alasan: str = "",
                sumber: str = "", kategori: str = "",
                tugas_id: str, agen_id: str, dsn=None,
                ledger=None) -> dict:
    """Catat laporan salah atas satu angka (bukti_id).

    Kembalikan {"laporan_id", "baca_ulang_diminta": True}. `ledger`
    opsional: instance `Ledger` Fase 0 untuk mencatat kejadian
    `laporan_salah` di Buku Kerja.
    """
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO laporan_salah (bukti_id, user_id, alasan)"
                " VALUES (%s, %s, %s) RETURNING id",
                (bukti_id, user_id, alasan))
            laporan_id = str(cur.fetchone()["id"])
            if sumber:
                cur.execute(
                    "INSERT INTO pembacaan_sumber (user_id, sumber, kategori, vonis)"
                    " VALUES (%s, %s, %s, 'Tidak bisa dipastikan')",
                    (user_id, sumber, kategori))
        conn.commit()
    if ledger is not None:
        ledger.append_event(tugas_id=tugas_id, agen_id=agen_id,
                            jenis="laporan_salah",
                            isi={"bukti_id": bukti_id, "alasan": alasan})
    return {"laporan_id": laporan_id, "baca_ulang_diminta": True}


def daftar_laporan(bukti_id: str, dsn=None) -> list[dict]:
    """Daftar laporan salah untuk satu bukti."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, alasan, baca_ulang_diminta, dibuat"
                " FROM laporan_salah WHERE bukti_id = %s ORDER BY dibuat",
                (bukti_id,))
            return [{"id": str(r["id"]), "user_id": str(r["user_id"]),
                     "alasan": r["alasan"],
                     "baca_ulang_diminta": r["baca_ulang_diminta"],
                     "dibuat": r["dibuat"]} for r in cur.fetchall()]
