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
from datetime import UTC, datetime, timedelta

from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.db import Jsonb

DEFAULT_DSN = None
TTL_MINUTES = 30


def _connect(dsn=None):
    return _db_connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row
    )


def save(key: str, *, source: str, value: dict, evidence_id: str, dsn=None) -> None:
    """Simpan bukti ke cache bersama (TTL 30 menit)."""
    expires_at = datetime.now(UTC) + timedelta(minutes=TTL_MINUTES)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO evidence_cache (key, sumber, value, evidence_id, expires_at)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,"
                " evidence_id = EXCLUDED.evidence_id, expires_at = EXCLUDED.expires_at",
                (key, source, Jsonb(value), evidence_id, expires_at),
            )
        conn.commit()


def take(key: str, dsn=None) -> dict | None:
    """Ambil dari cache bila masih segar; hit dicatat. None bila
    tidak ada / kedaluwarsa (pemanggil wajib baca ulang sumber)."""
    now = datetime.now(UTC)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value, evidence_id FROM evidence_cache WHERE key = %s AND expires_at > %s",
                (key, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("UPDATE evidence_cache SET hit = hit + 1 WHERE key = %s", (key,))
        conn.commit()
    return {"value": row["value"], "evidence_id": row["evidence_id"]}


def hit_statistics(dsn=None) -> dict:
    """Tingkat cache hit (tanpa target di MVP, §3.8)."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(SUM(hit), 0) AS hit, COUNT(*) AS entri FROM evidence_cache")
        row = cur.fetchone()
    return {"total_hit": int(row["hit"]), "entri_cache": int(row["entri"])}
