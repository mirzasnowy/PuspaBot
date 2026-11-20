"""Channel schema loader: the channel tables come from migration 0008_channels (``puspa/migrations/versions/0008_channels.sql``). This module is only the programmatic entry point — the test suite calls ``create_schema()`` so each test run starts from the same schema."""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn
from puspa.migrations import apply_migrations

__all__ = ["CHANNEL_TABLES", "create_schema", "reset_tables", "schema_path"]

CHANNEL_TABLES = ("channel_deliveries",)


def schema_path() -> Path:
    """Absolute path of migration 0008_channels."""
    return (
        Path(__file__).resolve().parents[2]
        / "puspa"
        / "migrations"
        / "versions"
        / "0008_channels.sql"
    )


def create_schema(dsn=None):
    """Apply the tables (migration 0008_channels).

    Idempotent; safe to call before every test.
    """
    apply_migrations(resolve_dsn(dsn), target="0008")


def reset_tables(dsn=None):
    """Empty the channel tables (for tests)."""
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(CHANNEL_TABLES) + " CASCADE")
        conn.commit()
