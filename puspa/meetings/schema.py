"""Meeting-minutes schema loader: the meeting table comes from migration 0011_meetings (``puspa/migrations/versions/0011_meetings.sql``). This module is only the programmatic entry point — the test suite calls ``create_schema()`` so each test run starts from the same schema. See ``docs/meetings.md`` for the design."""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn
from puspa.migrations import apply_migrations

__all__ = ["MEETINGS_TABLES", "create_schema", "reset_tables", "schema_path"]

MEETINGS_TABLES = ("meeting_minutes",)


def schema_path() -> Path:
    """Absolute path of migration 0011_meetings."""
    return (
        Path(__file__).resolve().parents[2]
        / "puspa"
        / "migrations"
        / "versions"
        / "0011_meetings.sql"
    )


def create_schema(dsn=None):
    """Apply the tables (migration 0011_meetings).

    Idempotent; safe to call before every test.
    """
    apply_migrations(resolve_dsn(dsn), target="0011")


def reset_tables(dsn=None):
    """Empty the meeting-minutes table (for tests). The ``rooms``
    table is untouched.
    """
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(MEETINGS_TABLES) + " CASCADE")
        conn.commit()
