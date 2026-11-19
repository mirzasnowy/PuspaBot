"""Room schema loader: apply ``infra/schema_rooms.sql``.

The migration file lives in ``infra/`` (not inside the package) so
it sits next to the other schema migrations and is applied the same
way: as a superuser, after ``infra/schema_core.sql`` (the ``users``
table must exist first). See ``docs/rooms-b6.md`` for the exact
commands. This module is only the programmatic entry point — the
test suite calls ``create_schema()`` so each test run starts from
the same schema.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn

__all__ = ["ROOMS_TABLES", "create_schema", "reset_tables", "schema_path"]

ROOMS_TABLES = (
    "rooms",
    "room_members",
    "room_invites",
    "room_memories",
    "room_tasks",
    "room_money_approvals",
    "room_channel_links",
    "delegated_tasks",
    "delegated_task_steps",
    "room_splits",
    "split_shares",
    "split_payments",
    "split_settlements",
)


def schema_path() -> Path:
    """Absolute path of ``infra/schema_rooms.sql`` (two levels above
    this package: ``puspa/rooms/`` -> ``build/`` -> ``infra/``)."""
    return Path(__file__).resolve().parents[2] / "infra" / "schema_rooms.sql"


def create_schema(dsn=None):
    """Run ``infra/schema_rooms.sql`` (CREATE TABLE IF NOT EXISTS +
    RLS policies). Idempotent; safe to call before every test.
    """
    sql = schema_path().read_text(encoding="utf-8")
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute(sql)
        conn.commit()


def reset_tables(dsn=None):
    """Empty all room tables (for tests). Cascades into the
    tables that reference them; the ``users`` table is untouched.
    """
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(ROOMS_TABLES) + " CASCADE")
        conn.commit()
