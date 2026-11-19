"""Meeting-minutes schema loader: apply ``infra/schema_meetings.sql``.

The migration file lives in ``infra/`` (not inside the package)
so it sits next to the other schema migrations and is applied
the same way: as a superuser, after ``infra/schema_core.sql``
and ``infra/schema_rooms.sql`` (the ``users``, ``rooms``, and
``room_tasks`` tables must exist first). See
``docs/meetings-b10.md`` for the exact commands. This module is
only the programmatic entry point — the test suite calls
``create_schema()`` so each test run starts from the same schema.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn

__all__ = ["MEETINGS_TABLES", "create_schema", "reset_tables", "schema_path"]

MEETINGS_TABLES = ("meeting_minutes",)


def schema_path() -> Path:
    """Absolute path of ``infra/schema_meetings.sql`` (two levels
    above this package: ``puspa/meetings/`` -> ``build/`` ->
    ``infra/``)."""
    return Path(__file__).resolve().parents[2] / "infra" / "schema_meetings.sql"


def create_schema(dsn=None):
    """Run ``infra/schema_meetings.sql`` (CREATE TABLE IF NOT
    EXISTS + RLS policies). Idempotent; safe to call before every
    test.
    """
    sql = schema_path().read_text(encoding="utf-8")
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute(sql)
        conn.commit()


def reset_tables(dsn=None):
    """Empty the meeting-minutes table (for tests). The ``rooms``
    table is untouched.
    """
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(MEETINGS_TABLES) + " CASCADE")
        conn.commit()
