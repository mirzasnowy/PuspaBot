"""Channel schema loader: apply ``infra/schema_channels.sql``.

The migration file lives in ``infra/`` (not inside the package) so
it sits next to the other schema migrations and is applied the same
way: as a superuser, after ``infra/schema_core.sql`` (the ``users``
table must exist first). See ``docs/channels.md`` for the exact
commands. This module is only the programmatic entry point — the
test suite calls ``create_schema()`` so each test run starts from
the same schema.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn

__all__ = ["CHANNEL_TABLES", "create_schema", "reset_tables", "schema_path"]

CHANNEL_TABLES = ("channel_deliveries",)


def schema_path() -> Path:
    """Absolute path of ``infra/schema_channels.sql``."""
    return Path(__file__).resolve().parents[2] / "infra" / "schema_channels.sql"


def create_schema(dsn=None):
    """Run ``infra/schema_channels.sql`` (CREATE TABLE IF NOT EXISTS).

    Idempotent; safe to call before every test.
    """
    sql = schema_path().read_text(encoding="utf-8")
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute(sql)
        conn.commit()


def reset_tables(dsn=None):
    """Empty the channel tables (for tests)."""
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(CHANNEL_TABLES) + " CASCADE")
        conn.commit()
