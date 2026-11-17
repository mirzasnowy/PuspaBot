"""Pemberian + pencabutan konektor per agen (§2.8 revisi 7).

Puspa mulai dengan Gmail, Drive, Calendar. Agen baru mulai kosong;
user yang memberikan konektor yang dibutuhkannya, dari layar
Konektor PWA. Pencabutan menghapus baris pemberian (token di vault
tetap milik pengguna; konektor lain agen yang sama tidak terganggu).
"""

from __future__ import annotations

import os

import psycopg

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
KONEKTOR_DIIZINKAN = ("gmail", "drive", "calendar")
SCOPE_PER_KONEKTOR = {
    "gmail": ["baca", "buat_draf", "kirim"],
    "drive": ["baca"],
    "calendar": ["baca", "buat_event"],
}


class GrantError(Exception):
    """Pemberian konektor tidak sah."""


def _connect(dsn=None):
    return psycopg.connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN))


def beri_konektor(user_id: str, agen_id: str, konektor: str, dsn=None) -> dict:
    """Beri konektor ke satu agen (scope minimal bawaan konektor)."""
    if konektor not in KONEKTOR_DIIZINKAN:
        raise GrantError(f"konektor tidak dikenal: {konektor!r}")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM agen_meta WHERE agen_id = %s AND user_id = %s",
                        (agen_id, user_id))
            if cur.fetchone() is None:
                raise GrantError("agen tidak dikenal / bukan milik pengguna")
            cur.execute(
                "INSERT INTO konektor_agen (user_id, agen_id, konektor, scope, terhubung)"
                " VALUES (%s, %s, %s, %s, TRUE)"
                " ON CONFLICT (user_id, agen_id, konektor) DO UPDATE"
                " SET scope = EXCLUDED.scope, terhubung = TRUE",
                (user_id, agen_id, konektor, SCOPE_PER_KONEKTOR[konektor]),
            )
        conn.commit()
    return {"agen_id": agen_id, "konektor": konektor,
            "scope": SCOPE_PER_KONEKTOR[konektor], "terhubung": True}


def cabut_konektor(user_id: str, agen_id: str, konektor: str, dsn=None) -> None:
    """Cabut konektor dari satu agen."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM konektor_agen WHERE user_id = %s AND agen_id = %s AND konektor = %s",
                (user_id, agen_id, konektor),
            )
        conn.commit()


def daftar_konektor(user_id: str, agen_id: str, dsn=None) -> list[dict]:
    """Konektor + scope + status terhubung untuk satu agen."""
    with _connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT konektor, scope, terhubung FROM konektor_agen"
                " WHERE user_id = %s AND agen_id = %s ORDER BY konektor",
                (user_id, agen_id),
            )
            return [dict(r) for r in cur.fetchall()]


def punya_konektor(user_id: str, agen_id: str, konektor: str, dsn=None) -> bool:
    """True bila agen ini terhubung ke konektor itu."""
    return any(k["konektor"] == konektor and k["terhubung"]
               for k in daftar_konektor(user_id, agen_id, dsn=dsn))
