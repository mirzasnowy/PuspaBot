"""Graf moat Fase 3 (§3.2, §3.3 revisi 7).

- `puspa.graphs.identity`: Graf Identitas dan Harga.
- `puspa.graphs.service`: Graf Alur Layanan (canary, pakai ulang,
  versi/rollback) di atas tabel `prosedur` Fase 1.

Penyimpanan graf: Postgres + pgvector (§7 definisi). BUKAN graph DB
eksternal — pola graf embedded LadybugDB (MIT) hanya dipakai sebagai
referensi pola (node + edge sebagai baris relasional), lihat REUSE.md.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

TABLES = (
    "canonical_products", "listing_links", "price_series", "watchlist",
    "read_budgets", "evidence_cache", "source_readings", "procedure_versions",
    "procedure_uses", "canary_runs", "false_reports",
    "monthly_credits", "token_metrics", "workflow_library", "block_requests",
    "tos_checks",
)


def create_schema(dsn: str | None = None) -> None:
    """Jalankan `infra/schema_graphs.sql` (idempotent)."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    sql = (Path(__file__).resolve().parent.parent.parent / "infra" / "schema_graphs.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql)


def reset_tables(dsn: str | None = None) -> None:
    """Kosongkan semua tabel Fase 3 (hanya untuk tes)."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE " + ", ".join(TABLES) + " CASCADE")
        conn.commit()
