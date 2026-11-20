"""Team schema loader: the team tables come from migration 0009_teams (``puspa/migrations/versions/0009_teams.sql``). This module is only the programmatic entry point — the test suite calls ``create_schema()`` so each test run starts from the same schema. See ``docs/teams.md`` for the design."""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn
from puspa.migrations import apply_migrations

__all__ = ["create_schema", "reset_tables", "schema_path"]

TEAMS_TABLES = (
    "teams",
    "team_members",
    "invitations",
    "bot_access_grants",
    "bot_access_requests",
    "entitlements",
    "workspace_moves",
)


def schema_path() -> Path:
    """Absolute path of migration 0009_teams."""
    return (
        Path(__file__).resolve().parents[2] / "puspa" / "migrations" / "versions" / "0009_teams.sql"
    )


def create_schema(dsn=None):
    """Apply the tables (migration 0009_teams).

    Idempotent; safe to call before every test.
    """
    apply_migrations(resolve_dsn(dsn), target="0009")


def reset_tables(dsn=None):
    """Empty all seven team tables (for tests). Cascades into the
    tables that reference them; the ``users`` table is untouched.
    """
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(TEAMS_TABLES) + " CASCADE")
        conn.commit()
