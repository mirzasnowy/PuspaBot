"""Skema Fase 4 (alur + modul kustom).

Dipakai conftest tes: terapkan skema, lalu kosongkan tabel tiap tes.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
TABEL = ("workflows", "workflow_versi", "jalan_workflow", "approval_workflow",
         "modules", "module_versi", "table_module", "rows_module")


def _connect(dsn=None):
    return psycopg.connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN))


def create_schema(dsn=None) -> None:
    sql = (Path(__file__).resolve().parent.parent.parent / "infra" / "schema_workflow.sql").read_text()
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def reset_tables(dsn=None) -> None:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE " + ", ".join(TABEL) + " CASCADE")
        conn.commit()
