"""Memory schema (Phase 1, definitions 2.7).

`create_schema` applies the memory tables through migration
0002_memory. Called once at setup and by the test fixtures.
"""

from __future__ import annotations

import os

from puspa.migrations import apply_migrations

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def resolve_dsn(dsn=None):
    """Postgres DSN: argument, then env, then the local default."""
    return dsn or os.environ.get("PUSPA_DSN") or os.environ.get("PUSPA_TEST_DSN") or DEFAULT_DSN


def create_schema(dsn=None):
    """Apply the Phase 1 memory tables (migration 0022_embedding_health)."""
    apply_migrations(resolve_dsn(dsn), target="0022")
