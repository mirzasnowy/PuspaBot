"""SQLite-vs-Postgres differential tests (COW-10, design item 14).

The same operations run against a migrated SQLite file and a
migrated Postgres database (``puspa_test_sqlite_diff_<pid>`` on
the local server — never the queue's ``puspa_test``), and the
observable results must match. Every test skips when no
Postgres server is reachable, so the file is green on a
desktop-only machine.
"""

from __future__ import annotations

import os
import re

import psycopg
import pytest

from puspa.db import connect as _db_connect
from puspa.ledger.ledger import Ledger
from puspa.memory import episodic, profile
from puspa.memory.embeddings import HashEmbedding
from puspa.migrations.runner import apply_migrations
from puspa.scheduler.claim import claim_work_items, enqueue_work_item

pytestmark = pytest.mark.integration

POSTGRES_MAINTENANCE = "dbname=postgres user=root host=/var/run/postgresql"
DIFF_DB = f"puspa_test_sqlite_diff_{os.getpid()}"
POSTGRES_DSN = f"dbname={DIFF_DB} user=root host=/var/run/postgresql"


def _postgres_available() -> bool:
    try:
        with psycopg.connect(POSTGRES_MAINTENANCE, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except (psycopg.Error, OSError):
        return False


@pytest.fixture(scope="module")
def postgres_dsn():
    """Fresh Postgres database with the Postgres migration set."""
    if not _postgres_available():
        pytest.skip("no Postgres server reachable for the differential tests")
    with psycopg.connect(POSTGRES_MAINTENANCE, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{DIFF_DB}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{DIFF_DB}"')
    apply_migrations(POSTGRES_DSN)
    yield POSTGRES_DSN
    with psycopg.connect(POSTGRES_MAINTENANCE, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{DIFF_DB}" WITH (FORCE)')


@pytest.fixture(scope="module")
def sqlite_dsn(tmp_path_factory):
    """Fresh SQLite file with the SQLite migration set."""
    path = str(tmp_path_factory.mktemp("diff") / "diff.db")
    apply_migrations(path)
    return path


def _normalise_pg_type(data_type: str, udt_name: str) -> str:
    """information_schema type -> the spelling the SQLite set keeps."""
    if data_type == "ARRAY":
        return udt_name.lstrip("_") + "[]"
    if data_type == "USER-DEFINED":
        return {"vector": "blob"}.get(udt_name, udt_name)
    return {
        "timestamp with time zone": "timestamptz",
        "timestamp without time zone": "timestamp",
        "character varying": "varchar",
        "double precision": "double precision",
    }.get(data_type, data_type)


def _normalise_sqlite_type(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw.strip().lower())
    text = re.sub(r"vector\(\d+\)", "blob", text)
    text = re.sub(r"\(\d+(,\s*\d+)?\)", "", text)
    # Postgres canonicalises INT to integer in information_schema.
    return {"int": "integer"}.get(text, text)


def _types_equal(postgres_type: str, sqlite_type: str) -> bool:
    """Same column type on both backends?

    SQLite INTEGER is always 64-bit, so a Postgres BIGSERIAL id
    (rewritten to INTEGER PRIMARY KEY in the SQLite set) and a
    Postgres bigint both land on SQLite ``integer`` without
    losing range; INT and INTEGER are the same Postgres type.
    """
    if postgres_type == sqlite_type:
        return True
    return postgres_type == "bigint" and sqlite_type == "integer"


def test_schema_matches_live_postgres(postgres_dsn, sqlite_dsn):
    """Every table and column, in order, matches the live Postgres schema."""
    with psycopg.connect(postgres_dsn) as conn:
        rows = conn.execute(
            "SELECT table_name, column_name, data_type, udt_name"
            " FROM information_schema.columns"
            " WHERE table_schema = 'public' AND table_name != 'schema_migrations'"
            " ORDER BY table_name, ordinal_position"
        ).fetchall()
    postgres_schema: dict[str, list[tuple[str, str]]] = {}
    for table, column, data_type, udt_name in rows:
        postgres_schema.setdefault(table, []).append(
            (column, _normalise_pg_type(data_type, udt_name))
        )
    with _db_connect(sqlite_dsn) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                " AND name != 'schema_migrations' ORDER BY name"
            ).fetchall()
        ]
        sqlite_schema: dict[str, list[tuple[str, str]]] = {}
        for table in tables:
            sqlite_schema[table] = [
                (row[1], _normalise_sqlite_type(row[2]))
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            ]
    assert set(sqlite_schema) == set(postgres_schema), (
        "table mismatch: only in SQLite "
        f"{sorted(set(sqlite_schema) - set(postgres_schema))}, only in Postgres "
        f"{sorted(set(postgres_schema) - set(sqlite_schema))}"
    )
    mismatches = []
    for table in sorted(postgres_schema):
        pg_columns = postgres_schema[table]
        lite_columns = sqlite_schema[table]
        if len(pg_columns) != len(lite_columns) or any(
            pg_col != lite_col or not _types_equal(pg_type, lite_type)
            for (pg_col, pg_type), (lite_col, lite_type) in zip(pg_columns, lite_columns)
        ):
            mismatches.append(f"{table}: Postgres {pg_columns} vs SQLite {lite_columns}")
    assert not mismatches, "\n".join(mismatches)


def test_ledger_chain_matches(postgres_dsn, sqlite_dsn):
    """Append + hash chain verify identically on both backends."""
    task_id = "00000000-0000-0000-0000-000000000001"
    results = {}
    for label, dsn in (("sqlite", sqlite_dsn), ("postgres", postgres_dsn)):
        ledger = Ledger(dsn)
        first = ledger.append_event(
            task_id=task_id,
            agent_id="puspa",
            event_type="task_created",
            content={"kind": "task_created", "title": "differential probe"},
        )
        second = ledger.append_event(
            task_id=task_id,
            agent_id="puspa",
            event_type="task_completed",
            content={"kind": "task_completed", "title": "differential probe"},
        )
        ok, problems = ledger.verify_chain(task_id)
        results[label] = (first, second, ok, problems)
    for label, (first, second, ok, problems) in results.items():
        assert ok, f"{label}: chain did not verify: {problems}"
        assert second["previous_hash"] == first["hash"]
        assert first["content"]["kind"] == "task_created"
        assert second["content"]["kind"] == "task_completed"
    # Same inputs, same hash inputs apart from the wall-clock
    # timestamp: the two chains agree on structure, not on bytes.
    assert results["sqlite"][0]["content"] == results["postgres"][0]["content"]


def test_profile_facts_match(postgres_dsn, sqlite_dsn):
    """add_fact + list_facts return the same fact on both backends."""
    user_id = "11111111-1111-1111-1111-111111111111"
    for dsn in (sqlite_dsn, postgres_dsn):
        profile.add_fact(user_id, "likes", "kopi", "user_stated", dsn=dsn)
    rows = [profile.list_facts(user_id, dsn=dsn) for dsn in (sqlite_dsn, postgres_dsn)]
    assert len(rows[0]) == len(rows[1]) == 1
    for key in ("key", "value", "status"):
        assert rows[0][0][key] == rows[1][0][key]


def test_episodic_search_matches(postgres_dsn, sqlite_dsn):
    """Brute-force cosine (SQLite) ranks like pgvector (Postgres)."""
    texts = [
        "kopi tubruk dari warung langganan di karawang",
        "jadwal kereta api jakarta bandung hari ini",
        "resep rendang daging sapi untuk lebaran",
    ]
    embedder = HashEmbedding()
    user_id = "22222222-2222-2222-2222-222222222222"
    for dsn in (sqlite_dsn, postgres_dsn):
        for text in texts:
            episodic.record_episode(
                user_id,
                text,
                "note",
                agent_id="puspa",
                embedder=embedder,
                dsn=dsn,
            )
    results = [
        episodic.search_episodes(
            user_id,
            "kopi warung karawang",
            limit=3,
            embedder=embedder,
            dsn=dsn,
        )
        for dsn in (sqlite_dsn, postgres_dsn)
    ]
    assert (
        {row["content"] for row in results[0]}
        == {row["content"] for row in results[1]}
        == set(texts)
    )
    assert results[0][0]["content"] == texts[0]
    assert results[1][0]["content"] == texts[0]
    # The two unrelated texts tie at distance 1.0 exactly (no
    # shared tokens), so their relative order is a tie-break, not
    # a ranking: compare distances per text, not per position.
    by_content = [{row["content"]: row["distance"] for row in rows} for rows in results]
    for text in texts:
        assert abs(by_content[0][text] - by_content[1][text]) < 1e-3


def test_work_claim_matches(postgres_dsn, sqlite_dsn):
    """Enqueue + atomic claim behave the same on both backends."""
    payloads = [{"job": f"diff-{n}"} for n in range(3)]
    for dsn in (sqlite_dsn, postgres_dsn):
        for payload in payloads:
            enqueue_work_item(payload, dsn=dsn)
    claimed = [
        claim_work_items(owner="diff-worker", limit=2, dsn=dsn)
        for dsn in (sqlite_dsn, postgres_dsn)
    ]
    assert len(claimed[0]) == len(claimed[1]) == 2
    assert {item.payload["job"] for item in claimed[0]} == {
        item.payload["job"] for item in claimed[1]
    }
    # Three enqueued, two claimed: a second claim takes the last
    # one, a third finds nothing left on either backend.
    assert len(claim_work_items(owner="diff-worker", limit=2, dsn=sqlite_dsn)) == 1
    assert len(claim_work_items(owner="diff-worker", limit=2, dsn=postgres_dsn)) == 1
    assert claim_work_items(owner="diff-worker", limit=2, dsn=sqlite_dsn) == []
    assert claim_work_items(owner="diff-worker", limit=2, dsn=postgres_dsn) == []
