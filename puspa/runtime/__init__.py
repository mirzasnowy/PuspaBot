"""Phase 2 Postgres schema (runtime team + evidence + scheduler + cassettes).

New tables (all have a `user_id` column except `system_heartbeats`):
- `agent_meta`: agent name/role/purpose + the mandatory (Puspa) marker.
- `tasks`: one row per task (status + running cost). Fine-grained events
  stay in the ledger (`events`, Phase 0); this table is a projection
  that can be rebuilt from it.
- `handoffs`: proposed handoff contracts + their state machine status.
- `evidence`: one evidence record per number (revision 7 section 2.5).
- `triggers`: scheduler triggers (section 2.14) + `trigger_runs`
  (idempotency key per trigger run).
- `agent_connectors`: connectors granted by the user per agent (section 2.8).
- `cassettes`: cassette metadata; cassette contents are JSON files in
  `cassettes/` (local, deleted with the task).
- `push_subscriptions`: per-user Web Push subscriptions (section 2.14).
- `system_heartbeats`: scheduler heartbeats (component `scheduler`).

RLS: enabled on the `user_id` tables with the same policies as
Phase 0/1 (`app.current_user_id`, see `puspa.accounts.rls`).
The table owner/superuser bypasses RLS (a Postgres property); real
enforcement is only for connections as the `puspa_app` role (see the
Phase 0 status log, decision 15). The `events` table deliberately has
no RLS (Phase 2 decision, see STATUS_FASE2.md): it has no `user_id`
column and its isolation runs through the agent registry +
`require_owner`.
"""

from __future__ import annotations

import os

import psycopg

from puspa.migrations import apply_migrations

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

TABLES = (
    "agent_meta",
    "tasks",
    "handoffs",
    "evidence",
    "triggers",
    "trigger_runs",
    "agent_connectors",
    "cassettes",
    "push_subscriptions",
    "system_heartbeats",
)


def create_schema(dsn: str | None = None) -> None:
    """Apply the runtime tables (migration 0003_runtime).

    Phase 2 decision (Phase 0 question, item c): the `puspa_app` role
    is given LOGIN + password — but only when the `PUSPA_APP_PASSWORD`
    env var is set (without it, the role stays NOLOGIN as in Phase 0;
    no default password is written anywhere).
    """
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    apply_migrations(dsn, target="0003")
    password = os.environ.get("PUSPA_APP_PASSWORD")
    if password:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("ALTER ROLE puspa_app WITH LOGIN PASSWORD %s", (password,))


def reset_tables(dsn: str | None = None) -> None:
    """Kosongkan semua tabel Fase 2 (hanya untuk tes)."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE " + ", ".join(TABLES) + " CASCADE")
        conn.commit()
