"""Payment schema loader: the payment tables come from migration 0013_payments (``puspa/migrations/versions/0013_payments.sql``). This module is only the programmatic entry point — the test suite calls ``create_schema()`` so each test run starts from the same schema."""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn
from puspa.migrations import apply_migrations

__all__ = ["PAYMENTS_TABLES", "create_schema", "reset_tables", "schema_path"]

PAYMENTS_TABLES = ("prepared_payments",)


def schema_path() -> Path:
    """Absolute path of migration 0013_payments."""
    return (
        Path(__file__).resolve().parents[2]
        / "puspa"
        / "migrations"
        / "versions"
        / "0013_payments.sql"
    )


def create_schema(dsn=None):
    """Apply the tables (migration 0013_payments).

    Idempotent; safe to call before every test.
    """
    apply_migrations(resolve_dsn(dsn), target="0013")


def reset_tables(dsn=None):
    """Empty the payments tables (for tests). The ``users``
    table is untouched."""
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(PAYMENTS_TABLES) + " CASCADE")
        conn.commit()
