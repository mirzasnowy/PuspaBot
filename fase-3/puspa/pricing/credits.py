"""Kredit cek detail (§4.1, §5.1 revisi 7).

Banding dasar gratis. Cek detail (riwayat 30 hari, alert harga turun)
= 1 kredit. 15 kredit gratis/bulan per pengguna uji.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
KREDIT_GRATIS_PER_BULAN = 15
BIAYA_CEK_DETAIL = 1


class KreditError(Exception):
    """Kredit tidak cukup / tidak bisa dipakai."""


def _bulan_ini() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def sisa_kredit(user_id: str, dsn=None, bulan: str | None = None) -> int:
    """Sisa kredit bulan ini (default 15 bila belum ada baris)."""
    bulan = bulan or _bulan_ini()
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sisa FROM kredit_bulanan WHERE user_id = %s AND bulan = %s",
                (user_id, bulan))
            row = cur.fetchone()
    return int(row[0]) if row else KREDIT_GRATIS_PER_BULAN


def pakai_kredit(user_id: str, *, jumlah: int = BIAYA_CEK_DETAIL,
                 dsn=None, bulan: str | None = None) -> int:
    """Pakai kredit; kembalikan sisa. Habis -> KreditError (bukan
    utang diam-diam)."""
    bulan = bulan or _bulan_ini()
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kredit_bulanan (user_id, bulan, sisa)"
                " VALUES (%s, %s, %s)"
                " ON CONFLICT (user_id, bulan) DO NOTHING",
                (user_id, bulan, KREDIT_GRATIS_PER_BULAN))
            cur.execute(
                "SELECT sisa FROM kredit_bulanan WHERE user_id = %s AND bulan = %s"
                " FOR UPDATE", (user_id, bulan))
            sisa = int(cur.fetchone()[0])
            if sisa < jumlah:
                raise KreditError(
                    f"kredit tidak cukup: sisa {sisa}, butuh {jumlah}")
            cur.execute(
                "UPDATE kredit_bulanan SET sisa = sisa - %s WHERE user_id = %s AND bulan = %s",
                (jumlah, user_id, bulan))
        conn.commit()
    return sisa - jumlah
