"""Phase 4 schema (custom workflows + modules).

Used by the test conftest: apply the schema, then empty the tables
per test.
"""

from __future__ import annotations

import os

from puspa.db import connect as _db_connect

from puspa.migrations import apply_migrations

DEFAULT_DSN = None
TABLES = (
    "workflows",
    "workflow_versions",
    "workflow_runs",
    "workflow_approvals",
    "modules",
    "module_versions",
    "module_tables",
    "module_rows",
)


def _connect(dsn=None):
    return _db_connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN))


def create_schema(dsn=None) -> None:
    """Apply the workflow tables (migration 0005_workflow)."""
    apply_migrations(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), target="0005")


def reset_tables(dsn=None) -> None:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE " + ", ".join(TABLES) + " CASCADE")
        conn.commit()
