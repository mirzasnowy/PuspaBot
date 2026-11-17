"""Skor Keandalan (§3.4 revisi 7).

Per sumber (marketplace x kategori) dan per prosedur: persentase
vonis Terverifikasi dari 50 pembacaan terakhir + umur pembacaan
terakhir.

Dipakai untuk tiga hal: (a) agen memilih jalur + menurunkan vonis
(skor < 80% -> semua hasil dari sumber itu maksimal Belum pasti);
(b) ditampilkan jujur di panel Bukti; (c) bahan laporan bukti bisnis.

Perhitungan di sini memakai tabel `pembacaan_sumber` Fase 3 (sumber +
kategori + vonis + waktu). Fungsi `skor_sumber` memakai ulang
`skor_keandalan` Fase 2 (logika % dari 50 terakhir yang sama).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from puspa.evidence.evidence import skor_keandalan

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def skor_sumber(sumber: str, kategori: str = "", dsn=None) -> dict:
    """Skor Keandalan satu sumber (marketplace x kategori).

    Kembalikan {"persen_terverifikasi", "jumlah", "turunkan_vonis",
    "umur_pembacaan_terakhir_detik"}. Tanpa pembacaan -> persen None,
    turunkan_vonis False (belum ada bukti buruk).
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT vonis, waktu FROM pembacaan_sumber"
                " WHERE sumber = %s AND kategori = %s"
                " ORDER BY waktu DESC LIMIT 50",
                (sumber, kategori))
            rows = cur.fetchall()
    if not rows:
        return {"persen_terverifikasi": None, "jumlah": 0,
                "turunkan_vonis": False, "umur_pembacaan_terakhir_detik": None}
    # skor_keandalan memakai 50 TERAKHIR dalam urutan kronologis.
    hasil = skor_keandalan([r["vonis"] for r in reversed(rows)])
    umur = (datetime.now(timezone.utc) - rows[0]["waktu"]).total_seconds()
    hasil["umur_pembacaan_terakhir_detik"] = round(umur, 1)
    return hasil


def skor_prosedur(prosedur_id: str, user_id: str, dsn=None) -> dict:
    """Skor Keandalan satu prosedur dari jalan canary + jalan biasa.

    Di sini: % jalan sukses dari 50 jalan terakhir (canary + jalan
    prosedur tercatat di `prosedur`). Tanpa jalan -> persen None.
    """
    from puspa.graphs.service import ambil_prosedur

    try:
        prosedur = ambil_prosedur(prosedur_id, user_id, dsn=dsn)
    except KeyError:
        return {"persen_terverifikasi": None, "jumlah": 0, "turunkan_vonis": False}
    total = prosedur["jalan_sukses"] + prosedur["jalan_gagal"]
    if total == 0:
        return {"persen_terverifikasi": None, "jumlah": 0, "turunkan_vonis": False}
    persen = round(prosedur["jalan_sukses"] / total * 100, 1)
    return {"persen_terverifikasi": persen, "jumlah": total,
            "turunkan_vonis": persen < 80.0,
            "status": prosedur["status"]}


def label_panel(sumber: str, kategori: str = "", dsn=None) -> str:
    """Label jujur untuk panel Bukti, mis. "Shopee, kategori TV: 92%
    terverifikasi dari 50 pembacaan terakhir"."""
    skor = skor_sumber(sumber, kategori, dsn=dsn)
    if skor["jumlah"] == 0:
        return f"{sumber}, kategori {kategori or '-'}: belum ada pembacaan"
    return (f"{sumber}, kategori {kategori or '-'}: "
            f"{skor['persen_terverifikasi']}% terverifikasi dari "
            f"{skor['jumlah']} pembacaan terakhir")
