"""Memory schema (Phase 1, definitions 2.7).

`create_schema` applies the memory tables through migration
0002_memory. Called once at setup and by the test fixtures.
"""

from __future__ import annotations

from puspa.accounts.ports import resolve_dsn
from puspa.migrations import apply_migrations

DEFAULT_DSN = None


def create_schema(dsn=None):
    """Apply the Phase 1 memory tables (migration 0034_longterm_tiers)."""
    apply_migrations(resolve_dsn(dsn), target="0034")
