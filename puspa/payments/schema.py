"""Payment schema loader: apply ``infra/schema_payments.sql``.

The migration file lives in ``infra/`` (not inside the package)
so it sits next to the other schema migrations and is applied
the same way: as a superuser, after ``infra/schema_core.sql``
(the ``users`` table must exist first). This module is only the
programmatic entry point — the test suite calls
``create_schema()`` so each test run starts from the same
schema.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn

__all__ = ["PAYMENTS_TABLES", "create_schema", "reset_tables", "schema_path"]

PAYMENTS_TABLES = ("prepared_payments",)


def schema_path() -> Path:
    """Absolute path of ``infra/schema_payments.sql`` (two levels
    above this package: ``puspa/payments/`` -> ``build/`` ->
    ``infra/``)."""
    return Path(__file__).resolve().parents[2] / "infra" / "schema_payments.sql"


def create_schema(dsn=None):
    """Run ``infra/schema_payments.sql`` (CREATE TABLE IF NOT
    EXISTS + RLS policies). Idempotent; safe to call before
    every test."""
    sql = schema_path().read_text(encoding="utf-8")
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute(sql)
        conn.commit()


def reset_tables(dsn=None):
    """Empty the payments tables (for tests). The ``users``
    table is untouched."""
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(PAYMENTS_TABLES) + " CASCADE")
        conn.commit()
