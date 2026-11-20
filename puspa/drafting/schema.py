"""Drafting schema loader: the drafting tables come from migration 0012_drafting (``puspa/migrations/versions/0012_drafting.sql``). This module is only the programmatic entry point — the test suite calls ``create_schema()`` so each test run starts from the same schema. See ``docs/drafting-b13.md`` for the design."""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn
from puspa.migrations import apply_migrations

__all__ = ["DRAFTING_TABLES", "create_schema", "reset_tables", "schema_path"]

DRAFTING_TABLES = ("drafts", "draft_approvals")


def schema_path() -> Path:
    """Absolute path of migration 0012_drafting."""
    return (
        Path(__file__).resolve().parents[2]
        / "puspa"
        / "migrations"
        / "versions"
        / "0012_drafting.sql"
    )


def create_schema(dsn=None):
    """Apply the tables (migration 0012_drafting).

    Idempotent; safe to call before every test.
    """
    apply_migrations(resolve_dsn(dsn), target="0012")


def reset_tables(dsn=None):
    """Empty all drafting tables (for tests)."""
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(DRAFTING_TABLES) + " CASCADE")
        conn.commit()
