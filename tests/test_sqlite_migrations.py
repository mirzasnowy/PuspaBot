"""SQLite migration set tests (COW-10, D-SQLite-6).

- All 45 SQLite migrations apply to a fresh file, and a second
  run is a no-op.
- Drift: the SQLite set defines exactly the same tables and
  columns as the Postgres set (parsed from both sets' SQL, so
  the check needs no live Postgres server).
- Round-trip: UUID / timestamp / JSONB / boolean / numeric /
  array values survive a write and read back with the same
  Python types the Postgres driver would return.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from puspa.db import connect as _db_connect
from puspa.db import parse_timestamp as _db_parse_timestamp
from puspa.migrations.runner import (
    MIGRATIONS_DIR,
    MIGRATIONS_DIR_SQLITE,
    apply_migrations,
    list_migrations,
    migration_status,
    rollback_migrations,
)

pytestmark = pytest.mark.integration

#: Tables the suite relies on most; a spot check on top of the
#: full drift comparison below.
KEY_TABLES = {"users", "agents", "agent_meta", "events", "sessions", "vault"}


def test_all_sqlite_migrations_apply(tmp_path):
    db_path = str(tmp_path / "migrations.db")
    applied = apply_migrations(db_path)
    assert len(applied) == len(list_migrations(MIGRATIONS_DIR_SQLITE)) == 45
    assert apply_migrations(db_path) == []
    status = migration_status(db_path)
    assert len(status) == 45
    assert all(row["applied"] for row in status)
    with _db_connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert tables >= KEY_TABLES
    assert "schema_migrations" in tables


def test_rollback_and_reapply_last_migration(tmp_path):
    db_path = str(tmp_path / "rollback.db")
    apply_migrations(db_path)
    rolled_back = rollback_migrations(db_path, target="0044")
    assert rolled_back == ["0045"]
    assert "0045" in {row["version"] for row in migration_status(db_path) if not row["applied"]}
    assert apply_migrations(db_path) == ["0045"]


# --- drift: SQLite set vs Postgres set (pure text parse) --------------------


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on top-level commas only."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


_TABLE_CONSTRAINTS = {"primary", "foreign", "unique", "check", "constraint", "exclude"}


def _normalise_type(raw: str) -> str:
    """One spelling for a column type across both sets."""
    text = re.sub(r"\s+", " ", raw.strip().lower())
    text = re.sub(r"vector\(\d+\)", "blob", text)
    text = re.sub(r"\b(big)?serial\b", "integer", text)
    text = re.sub(r"timestamp with time zone", "timestamptz", text)
    return re.sub(r"timestamp without time zone", "timestamp", text)


def _parse_schema(directory: Path) -> dict[str, list[tuple[str, str]]]:
    """Final table -> [(column, type)] after applying every file in order."""
    schema: dict[str, list[tuple[str, str]]] = {}
    for migration in sorted(directory.glob("*.sql")):
        text = migration.read_text(encoding="utf-8")
        for match in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)\);\s*$",
            text,
            flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
        ):
            table, body = match.group(1).lower(), match.group(2)
            columns: list[tuple[str, str]] = []
            for part in _split_top_level(body):
                tokens = part.strip().split()
                if not tokens:
                    continue
                name = tokens[0].strip('"').lower()
                if name in _TABLE_CONSTRAINTS:
                    continue
                type_tokens = []
                for token in tokens[1:]:
                    if token.upper() in {
                        "PRIMARY",
                        "NOT",
                        "NULL",
                        "DEFAULT",
                        "REFERENCES",
                        "UNIQUE",
                        "CHECK",
                        "CONSTRAINT",
                    }:
                        break
                    type_tokens.append(token)
                columns.append((name, _normalise_type(" ".join(type_tokens))))
            schema[table] = columns
        for match in re.finditer(
            r"ALTER TABLE (?:IF EXISTS )?(\w+)\s+ADD COLUMN (?:IF NOT EXISTS )?(\w+)\s+([^\s,;]+(?:\([^)]*\))?)",
            text,
            flags=re.IGNORECASE,
        ):
            table = match.group(1).lower()
            name = match.group(2).lower()
            column_type = _normalise_type(match.group(3))
            if table in schema and all(col != name for col, _ in schema[table]):
                schema[table].append((name, column_type))
        for match in re.finditer(
            r"ALTER TABLE (?:IF EXISTS )?(\w+)\s+DROP COLUMN (?:IF EXISTS )?(\w+)",
            text,
            flags=re.IGNORECASE,
        ):
            table, name = match.group(1).lower(), match.group(2).lower()
            if table in schema:
                schema[table] = [(col, typ) for col, typ in schema[table] if col != name]
    return schema


def test_sqlite_set_matches_postgres_set():
    """D-SQLite-6: both sets define the same tables and columns."""
    postgres = _parse_schema(MIGRATIONS_DIR)
    sqlite = _parse_schema(MIGRATIONS_DIR_SQLITE)
    assert set(sqlite) == set(postgres), (
        "table mismatch: only in SQLite "
        f"{sorted(set(sqlite) - set(postgres))}, only in Postgres "
        f"{sorted(set(postgres) - set(sqlite))}"
    )
    mismatches = []
    for table in sorted(postgres):
        if sqlite[table] != postgres[table]:
            mismatches.append(f"{table}: Postgres {postgres[table]} vs SQLite {sqlite[table]}")
    assert not mismatches, "\n".join(mismatches)


# --- value round-trip ---------------------------------------------------------


def test_value_round_trip(tmp_path):
    """UUID / timestamp / JSON / bool / numeric / array keep their types."""
    db_path = str(tmp_path / "roundtrip.db")
    apply_migrations(db_path)
    with _db_connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE roundtrip_probe ("
            " id UUID PRIMARY KEY, at TIMESTAMPTZ, payload JSONB,"
            " flag BOOLEAN, amount NUMERIC(10, 2), tags TEXT[])"
        )
        conn.execute(
            "INSERT INTO roundtrip_probe VALUES (%s, %s, %s, %s, %s, %s)",
            (
                "123e4567-e89b-12d3-a456-426614174000",
                "2026-11-23T03:32:57.123+00:00",
                {"kind": "probe", "n": 3},
                True,
                12.5,
                ["a", "b"],
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM roundtrip_probe").fetchone()
    import datetime
    import uuid
    from decimal import Decimal

    assert isinstance(row[0], uuid.UUID)
    assert isinstance(row[1], datetime.datetime)
    assert row[1] == _db_parse_timestamp("2026-11-23T03:32:57.123+00:00")
    assert row[2] == {"kind": "probe", "n": 3}
    assert row[3] is True
    assert isinstance(row[4], Decimal) and row[4] == Decimal("12.5")
    assert row[5] == ["a", "b"]
