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
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from puspa.evidence.evidence import reliability_score

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row
    )


def source_score(source: str, category: str = "", dsn=None) -> dict:
    """Skor Keandalan satu sumber (marketplace x kategori).

    Kembalikan {"persen_terverifikasi", "jumlah", "turunkan_vonis",
    "umur_pembacaan_terakhir_detik"}. Tanpa pembacaan -> persen None,
    turunkan_vonis False (belum ada bukti buruk).
    """
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT vonis, created_at FROM source_readings"
            " WHERE sumber = %s AND category = %s"
            " ORDER BY created_at DESC LIMIT 50",
            (source, category),
        )
        rows = cur.fetchall()
    if not rows:
        return {
            "percent_verified": None,
            "amount": 0,
            "downgrade_verdict": False,
            "last_reading_age_seconds": None,
        }
    # skor_keandalan memakai 50 TERAKHIR dalam urutan kronologis.
    result = reliability_score([r["vonis"] for r in reversed(rows)])
    umur = (datetime.now(UTC) - rows[0]["created_at"]).total_seconds()
    result["last_reading_age_seconds"] = round(umur, 1)
    return result


def procedure_score(procedure_id: str, user_id: str, dsn=None) -> dict:
    """Skor Keandalan satu prosedur dari jalan canary + jalan biasa.

    Di sini: % jalan sukses dari 50 jalan terakhir (canary + jalan
    prosedur tercatat di `prosedur`). Tanpa jalan -> persen None.
    """
    from puspa.graphs.service import get_procedure

    try:
        procedure = get_procedure(procedure_id, user_id, dsn=dsn)
    except KeyError:
        return {"percent_verified": None, "amount": 0, "downgrade_verdict": False}
    total = procedure["successful_runs"] + procedure["failed_runs"]
    if total == 0:
        return {"percent_verified": None, "amount": 0, "downgrade_verdict": False}
    percent = round(procedure["successful_runs"] / total * 100, 1)
    return {
        "percent_verified": percent,
        "amount": total,
        "downgrade_verdict": percent < 80.0,
        "status": procedure["status"],
    }


def label_panel(source: str, category: str = "", dsn=None) -> str:
    """Label jujur untuk panel Bukti, mis. "Shopee, kategori TV: 92%
    terverifikasi dari 50 pembacaan terakhir"."""
    score = source_score(source, category, dsn=dsn)
    if score["amount"] == 0:
        return f"{source}, kategori {category or '-'}: belum ada pembacaan"
    return (
        f"{source}, kategori {category or '-'}: "
        f"{score['percent_verified']}% terverifikasi dari "
        f"{score['amount']} pembacaan terakhir"
    )
