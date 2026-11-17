"""Cache bukti bersama (§3.8 revisi 7).

Bukti harga yang masih segar (TTL 30 menit) dipakai ulang antar
pengguna oleh Pembaca — biaya dan beban ke marketplace turun.
Pemeriksa TETAP membaca ulang secara independen untuk vonis; cache
TIDAK PERNAH menggantikan Pemeriksa (ditegakkan arsitektur: modul
ini hanya dipakai jalur Pembaca di `compare.py`; jalur Pemeriksa
memanggil reader langsung).

Tingkat cache hit dicatat dan dilaporkan (tanpa target di MVP).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
TTL_MENIT = 30


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def simpan(kunci: str, *, sumber: str, nilai: dict, bukti_id: str, dsn=None) -> None:
    """Simpan bukti ke cache bersama (TTL 30 menit)."""
    kedaluwarsa = datetime.now(timezone.utc) + timedelta(minutes=TTL_MENIT)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cache_bukti (kunci, sumber, nilai, bukti_id, kedaluwarsa)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (kunci) DO UPDATE SET nilai = EXCLUDED.nilai,"
                " bukti_id = EXCLUDED.bukti_id, kedaluwarsa = EXCLUDED.kedaluwarsa",
                (kunci, sumber, Jsonb(nilai), bukti_id, kedaluwarsa))
        conn.commit()


def ambil(kunci: str, dsn=None) -> dict | None:
    """Ambil dari cache bila masih segar; hit dicatat. None bila
    tidak ada / kedaluwarsa (pemanggil wajib baca ulang sumber)."""
    sekarang = datetime.now(timezone.utc)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nilai, bukti_id FROM cache_bukti WHERE kunci = %s AND kedaluwarsa > %s",
                (kunci, sekarang))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("UPDATE cache_bukti SET hit = hit + 1 WHERE kunci = %s", (kunci,))
        conn.commit()
    return {"nilai": row["nilai"], "bukti_id": row["bukti_id"]}


def statistik_hit(dsn=None) -> dict:
    """Tingkat cache hit (tanpa target di MVP, §3.8)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(hit), 0) AS hit, COUNT(*) AS entri FROM cache_bukti")
            row = cur.fetchone()
    return {"total_hit": int(row["hit"]), "entri_cache": int(row["entri"])}
