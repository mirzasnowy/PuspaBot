"""Team schema loader: apply ``infra/schema_teams.sql``.

The migration file lives in ``infra/`` (not inside the package) so
it sits next to the other schema migrations and is applied the same
way: as a superuser, after ``infra/schema_core.sql`` (the ``users``
table must exist first). See ``docs/teams.md`` for the exact
commands. This module is only the programmatic entry point — the
test suite calls ``create_schema()`` so each test run starts from
the same schema.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn

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
    """Absolute path of ``infra/schema_teams.sql`` (two levels above
    this package: ``puspa/teams/`` -> ``build/`` -> ``infra/``)."""
    return Path(__file__).resolve().parents[2] / "infra" / "schema_teams.sql"


def create_schema(dsn=None):
    """Run ``infra/schema_teams.sql`` (CREATE TABLE IF NOT EXISTS +
    RLS policies). Idempotent; safe to call before every test.
    """
    sql = schema_path().read_text(encoding="utf-8")
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute(sql)
        conn.commit()


def reset_tables(dsn=None):
    """Empty all seven team tables (for tests). Cascades into the
    tables that reference them; the ``users`` table is untouched.
    """
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(TEAMS_TABLES) + " CASCADE")
        conn.commit()
