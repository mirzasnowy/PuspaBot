"""Phase 3 moat graphs (revision 7 §3.2, §3.3).

- `puspa.graphs.identity`: Identity and Price graphs.
- `puspa.graphs.service`: Service Flow graphs (canary, reuse,
  version/rollback) over the Phase 1 `procedures` table.
- `puspa.graphs.shared`: B3 shared graphs (anti-fraud + prices,
  per-user privacy).

Graph storage: Postgres + pgvector (§7 definition). NOT an external
graph DB — the embedded-graph LadybugDB (MIT) pattern is used only
as a pattern reference (node + edge as relational rows), see REUSE.md.
"""

from __future__ import annotations

import os

from puspa.db import connect as _db_connect
from puspa.migrations import apply_migrations

DEFAULT_DSN = None

TABLES = (
    "canonical_products",
    "listing_links",
    "price_series",
    "watchlist",
    "read_budgets",
    "evidence_cache",
    "source_readings",
    "procedure_versions",
    "procedure_uses",
    "canary_runs",
    "false_reports",
    "monthly_credits",
    "token_metrics",
    "workflow_library",
    "block_requests",
    "tos_checks",
    "shared_seller_flags",
    "shared_price_observations",
    "shared_graph_private_records",
)


def create_schema(dsn: str | None = None) -> None:
    """Apply the graph tables (migration 0004_graphs)."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    apply_migrations(dsn, target="0004")


def reset_tables(dsn: str | None = None) -> None:
    """Empty all Phase 3 tables (tests only)."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with _db_connect(dsn) as conn:
        conn.execute("TRUNCATE " + ", ".join(TABLES) + " CASCADE")
        conn.commit()
