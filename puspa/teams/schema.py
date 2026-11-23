"""Team schema loader: the team tables come from migration 0044
(``puspa/migrations/versions/0044_multi_agent_teams.sql``). This
module is only the programmatic entry point — the test suite calls
``create_schema()`` so each test run starts from the same schema.
See ``docs/multi-agent-teams.md`` for the design."""

from __future__ import annotations

from pathlib import Path

from puspa.db import Connection as _db_Connection
from puspa.db import connect as _db_connect

from puspa.accounts.ports import resolve_dsn
from puspa.migrations import apply_migrations

__all__ = ["TEAM_TABLES", "create_schema", "reset_tables", "schema_path"]

TEAM_TABLES = (
    "teams",
    "team_members",
    "team_conversations",
    "team_messages",
)


def schema_path() -> Path:
    """Absolute path of migration 0044."""
    return (
        Path(__file__).resolve().parents[2]
        / "puspa"
        / "migrations"
        / "versions"
        / "0044_multi_agent_teams.sql"
    )


def create_schema(dsn=None):
    """Apply the tables (migration 0044).

    Idempotent; safe to call before every test.
    """
    apply_migrations(resolve_dsn(dsn), target="0044")


def reset_tables(dsn=None):
    """Empty all team tables (for tests). Cascades into the
    tables that reference them; the ``users`` table is untouched.
    Tables that do not exist yet are skipped.
    """
    with _db_connect(resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            if isinstance(conn, _db_Connection):
                # SQLite has no pg_tables catalog: sqlite_master
                # holds the same existence check.
                placeholders = ", ".join(["%s"] * len(TEAM_TABLES))
                cur.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                    f" AND name IN ({placeholders})",
                    tuple(TEAM_TABLES),
                )
            else:
                cur.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename = ANY(%s)",
                    (list(TEAM_TABLES),),
                )
            existing = [row[0] for row in cur.fetchall()]
        if existing:
            conn.execute("TRUNCATE " + ", ".join(existing) + " CASCADE")
        conn.commit()
