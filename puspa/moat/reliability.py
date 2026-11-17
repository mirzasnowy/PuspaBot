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


def skor_sumber(sumber: str, category: str = "", dsn=None) -> dict:
    """Skor Keandalan satu sumber (marketplace x kategori).

    Kembalikan {"persen_terverifikasi", "jumlah", "turunkan_vonis",
    "umur_pembacaan_terakhir_detik"}. Tanpa pembacaan -> persen None,
    turunkan_vonis False (belum ada bukti buruk).
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT vonis, created_at FROM source_readings"
                " WHERE sumber = %s AND kategori = %s"
                " ORDER BY created_at DESC LIMIT 50",
                (sumber, category))
            rows = cur.fetchall()
    if not rows:
        return {"persen_verified": None, "amount": 0,
                "turunkan_vonis": False, "umur_pembacaan_terakhir_detik": None}
    # skor_keandalan memakai 50 TERAKHIR dalam urutan kronologis.
    result = skor_keandalan([r["vonis"] for r in reversed(rows)])
    umur = (datetime.now(timezone.utc) - rows[0]["created_at"]).total_seconds()
    result["umur_pembacaan_terakhir_detik"] = round(umur, 1)
    return result


def skor_prosedur(prosedur_id: str, user_id: str, dsn=None) -> dict:
    """Skor Keandalan satu prosedur dari jalan canary + jalan biasa.

    Di sini: % jalan sukses dari 50 jalan terakhir (canary + jalan
    prosedur tercatat di `prosedur`). Tanpa jalan -> persen None.
    """
    from puspa.graphs.service import take_prosedur

    try:
        prosedur = take_prosedur(prosedur_id, user_id, dsn=dsn)
    except KeyError:
        return {"persen_verified": None, "amount": 0, "turunkan_vonis": False}
    total = prosedur["successful_runs"] + prosedur["failed_runs"]
    if total == 0:
        return {"persen_verified": None, "amount": 0, "turunkan_vonis": False}
    persen = round(prosedur["successful_runs"] / total * 100, 1)
    return {"persen_verified": persen, "amount": total,
            "turunkan_vonis": persen < 80.0,
            "status": prosedur["status"]}


def label_panel(sumber: str, category: str = "", dsn=None) -> str:
    """Label jujur untuk panel Bukti, mis. "Shopee, kategori TV: 92%
    terverifikasi dari 50 pembacaan terakhir"."""
    skor = skor_sumber(sumber, category, dsn=dsn)
    if skor["amount"] == 0:
        return f"{sumber}, kategori {category or '-'}: belum ada pembacaan"
    return (f"{sumber}, kategori {category or '-'}: "
            f"{skor['persen_verified']}% terverifikasi dari "
            f"{skor['amount']} pembacaan terakhir")
