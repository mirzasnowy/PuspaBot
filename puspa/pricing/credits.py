"""Kredit cek detail (§4.1, §5.1 revisi 7).

Banding dasar gratis. Cek detail (riwayat 30 hari, alert harga turun)
= 1 kredit. 15 kredit gratis/bulan per pengguna uji.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
CREDIT_FREE_PER_MONTH = 15
COST_CHECK_DETAIL = 1


class CreditError(Exception):
    """Kredit tidak cukup / tidak bisa dipakai."""


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def remaining_credit(user_id: str, dsn=None, month: str | None = None) -> int:
    """Sisa kredit bulan ini (default 15 bila belum ada baris)."""
    month = month or _current_month()
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sisa FROM monthly_credits WHERE user_id = %s AND month = %s",
                (user_id, month))
            row = cur.fetchone()
    return int(row[0]) if row else CREDIT_FREE_PER_MONTH


def use_credit(user_id: str, *, amount: int = COST_CHECK_DETAIL,
                 dsn=None, month: str | None = None) -> int:
    """Pakai kredit; kembalikan sisa. Habis -> KreditError (bukan
    utang diam-diam)."""
    month = month or _current_month()
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO monthly_credits (user_id, month, sisa)"
                " VALUES (%s, %s, %s)"
                " ON CONFLICT (user_id, month) DO NOTHING",
                (user_id, month, CREDIT_FREE_PER_MONTH))
            cur.execute(
                "SELECT sisa FROM monthly_credits WHERE user_id = %s AND month = %s"
                " FOR UPDATE", (user_id, month))
            remaining = int(cur.fetchone()[0])
            if remaining < amount:
                raise CreditError(
                    f"kredit tidak cukup: sisa {remaining}, butuh {amount}")
            cur.execute(
                "UPDATE monthly_credits SET sisa = sisa - %s WHERE user_id = %s AND month = %s",
                (amount, user_id, month))
        conn.commit()
    return remaining - amount
