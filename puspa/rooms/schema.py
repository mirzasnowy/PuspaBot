"""Room schema loader: the room tables come from migration 0010_rooms (``puspa/migrations/versions/0010_rooms.sql``), plus the per-room job queue table from migration 0024 (``0024_room_job_queue.sql``). This module is only the programmatic entry point — the test suite calls ``create_schema()`` so each test run starts from the same schema. See ``docs/rooms-b6.md`` for the design."""

from __future__ import annotations

from pathlib import Path

import psycopg

from puspa.accounts.users import resolve_dsn
from puspa.migrations import apply_migrations

__all__ = ["ROOMS_TABLES", "create_schema", "reset_tables", "schema_path"]

ROOMS_TABLES = (
    "rooms",
    "room_members",
    "room_invites",
    "room_job_queue",
    "room_memories",
    "room_tasks",
    "room_money_approvals",
    "room_channel_links",
    "delegated_tasks",
    "delegated_task_steps",
    "room_research_links",
    "room_splits",
    "split_payments",
    "split_settlements",
    "split_shares",
)


def schema_path() -> Path:
    """Absolute path of migration 0010_rooms."""
    return (
        Path(__file__).resolve().parents[2] / "puspa" / "migrations" / "versions" / "0010_rooms.sql"
    )


def create_schema(dsn=None):
    """Apply the tables (migration 0010_rooms).

    Idempotent; safe to call before every test.
    """
    apply_migrations(resolve_dsn(dsn), target="0010")


def reset_tables(dsn=None):
    """Empty all room tables (for tests). Cascades into the
    tables that reference them; the ``users`` table is untouched.
    Tables that do not exist yet (the 0024 job queue, created only
    by the queue tests through its own ``create_schema``) are
    skipped, so room tests that never touch the queue still pass.
    """
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename = ANY(%s)",
                (list(ROOMS_TABLES),),
            )
            existing = [row[0] for row in cur.fetchall()]
        if existing:
            conn.execute("TRUNCATE " + ", ".join(existing) + " CASCADE")
        conn.commit()
