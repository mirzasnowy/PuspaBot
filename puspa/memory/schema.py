"""Pembuat skema memori Fase 1 (definisi 2.7).

`create_schema` menjalankan `schema.sql` (idempotent). Dipanggil
sekali saat setup dan oleh fixture tes.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def resolve_dsn(dsn=None):
    """DSN Postgres: argumen, lalu env, lalu default peer-auth lokal."""
    return dsn or os.environ.get("PUSPA_DSN") or os.environ.get("PUSPA_TEST_DSN") or DEFAULT_DSN


def create_schema(dsn=None):
    """Terapkan skema memori Fase 1 ke database tes/dev."""
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(resolve_dsn(dsn), autocommit=True) as conn:
        conn.execute(sql)
