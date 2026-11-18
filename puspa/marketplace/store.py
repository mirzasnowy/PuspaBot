"""Marketplace-owned storage (G3).

The marketplace keeps its own tables — install/rollback/audit notes
live here, never as ledger events (the ledger has a closed set of
31 event types and must not gain new ones for marketplace bookkeeping).

Tables (all idempotent, ``CREATE TABLE IF NOT EXISTS``):

- ``marketplace_installs``: one row per (skill, user) install:
  current version, sha256, source, licence, price in credits,
  status (``enabled`` / ``disabled`` / ``removed``).
- ``marketplace_versions``: every installed version of a skill, so
  rollback can restore an earlier sha256 + source exactly.
- ``marketplace_audit``: append-only audit trail of every install,
  rollback, uninstall and refusal, with sha256 + source + licence.

``MemoryStore`` is the same interface in memory and is what the
tests use. ``PostgresStore`` is the same interface on Postgres.
``create_schema(dsn)`` applies the SQL; it never touches any other
schema.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime

__all__ = [
    "DEFAULT_DSN",
    "MemoryStore",
    "PostgresStore",
    "create_schema",
    "resolve_dsn",
]

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS marketplace_installs (
    skill_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    version       TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    source        TEXT NOT NULL,
    license       TEXT NOT NULL DEFAULT '',
    price_credits INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'disabled',
    installed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_id, user_id)
);

CREATE TABLE IF NOT EXISTS marketplace_versions (
    skill_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    version       TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    source        TEXT NOT NULL,
    license       TEXT NOT NULL DEFAULT '',
    price_credits INTEGER NOT NULL DEFAULT 0,
    manifest      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_id, user_id, version)
);

CREATE TABLE IF NOT EXISTS marketplace_audit (
    id            BIGSERIAL PRIMARY KEY,
    event         TEXT NOT NULL,
    skill_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    sha256        TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    license       TEXT NOT NULL DEFAULT '',
    price_credits INTEGER NOT NULL DEFAULT 0,
    detail        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def resolve_dsn(dsn=None):
    """Postgres DSN: argument, then env, then the local test default."""
    return dsn or os.environ.get("PUSPA_DSN") or os.environ.get("PUSPA_TEST_DSN") or DEFAULT_DSN


def create_schema(dsn=None):
    """Apply the marketplace schema (idempotent)."""
    import psycopg

    with psycopg.connect(resolve_dsn(dsn), autocommit=True) as conn:
        conn.execute(SCHEMA_SQL)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class MemoryStore:
    """In-memory marketplace store (tests + single-process use)."""

    def __init__(self):
        self.installs = {}  # (skill_id, user_id) -> dict
        self.versions = {}  # (skill_id, user_id, version) -> dict
        self.audit = []  # list[dict]
        self.skill_texts = {}  # skill_id -> SKILL.md text

    # -- installs ------------------------------------------------------
    def record_install(self, record: dict) -> dict:
        stored = copy.deepcopy(record)
        stored.setdefault("installed_at", _now_iso())
        stored["updated_at"] = _now_iso()
        self.installs[(stored["skill_id"], stored["user_id"])] = stored
        return copy.deepcopy(stored)

    def get_install(self, skill_id: str, user_id: str) -> dict | None:
        found = self.installs.get((skill_id, user_id))
        return copy.deepcopy(found) if found else None

    def list_installs(self, user_id: str) -> list[dict]:
        return [copy.deepcopy(v) for (s, u), v in sorted(self.installs.items()) if u == user_id]

    # -- versions ------------------------------------------------------
    def record_version(self, record: dict) -> dict:
        stored = copy.deepcopy(record)
        stored.setdefault("created_at", _now_iso())
        self.versions[(stored["skill_id"], stored["user_id"], stored["version"])] = stored
        return copy.deepcopy(stored)

    def get_version(self, skill_id: str, user_id: str, version: str) -> dict | None:
        found = self.versions.get((skill_id, user_id, version))
        return copy.deepcopy(found) if found else None

    def list_versions(self, skill_id: str, user_id: str) -> list[dict]:
        return [
            copy.deepcopy(v)
            for (s, u, _ver), v in sorted(self.versions.items())
            if s == skill_id and u == user_id
        ]

    # -- audit ----------------------------------------------------------
    def record_audit(self, event: dict) -> dict:
        stored = copy.deepcopy(event)
        stored.setdefault("created_at", _now_iso())
        stored["id"] = len(self.audit) + 1
        self.audit.append(stored)
        return copy.deepcopy(stored)

    def list_audit(self, *, skill_id: str | None = None, user_id: str | None = None) -> list[dict]:
        rows = self.audit
        if skill_id is not None:
            rows = [r for r in rows if r.get("skill_id") == skill_id]
        if user_id is not None:
            rows = [r for r in rows if r.get("user_id") == user_id]
        return copy.deepcopy(rows)

    # -- skill texts ----------------------------------------------------
    def save_skill_text(self, skill_id: str, text: str) -> None:
        self.skill_texts[skill_id] = text

    def get_skill_text(self, skill_id: str) -> str | None:
        return self.skill_texts.get(skill_id)


class PostgresStore:
    """Postgres marketplace store (same interface as MemoryStore)."""

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.dsn, row_factory=dict_row)

    # -- installs ------------------------------------------------------
    def record_install(self, record: dict) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO marketplace_installs"
                    " (skill_id, user_id, version, sha256, source, license,"
                    "  price_credits, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (skill_id, user_id) DO UPDATE SET"
                    "  version = EXCLUDED.version, sha256 = EXCLUDED.sha256,"
                    "  source = EXCLUDED.source, license = EXCLUDED.license,"
                    "  price_credits = EXCLUDED.price_credits,"
                    "  status = EXCLUDED.status, updated_at = now()"
                    " RETURNING *",
                    (
                        record["skill_id"],
                        record["user_id"],
                        record["version"],
                        record["sha256"],
                        record["source"],
                        record.get("license", ""),
                        record.get("price_credits", 0),
                        record.get("status", "disabled"),
                    ),
                )
                row = dict(cur.fetchone())
            conn.commit()
        return _serialise(row)

    def get_install(self, skill_id: str, user_id: str) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM marketplace_installs WHERE skill_id = %s AND user_id = %s",
                (skill_id, user_id),
            )
            row = cur.fetchone()
        return _serialise(dict(row)) if row else None

    def list_installs(self, user_id: str) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM marketplace_installs WHERE user_id = %s ORDER BY skill_id",
                (user_id,),
            )
            return [_serialise(dict(r)) for r in cur.fetchall()]

    # -- versions ------------------------------------------------------
    def record_version(self, record: dict) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO marketplace_versions"
                    " (skill_id, user_id, version, sha256, source, license,"
                    "  price_credits, manifest) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (skill_id, user_id, version) DO UPDATE SET"
                    "  sha256 = EXCLUDED.sha256, source = EXCLUDED.source,"
                    "  license = EXCLUDED.license,"
                    "  price_credits = EXCLUDED.price_credits,"
                    "  manifest = EXCLUDED.manifest"
                    " RETURNING *",
                    (
                        record["skill_id"],
                        record["user_id"],
                        record["version"],
                        record["sha256"],
                        record["source"],
                        record.get("license", ""),
                        record.get("price_credits", 0),
                        json.dumps(record.get("manifest", {})),
                    ),
                )
                row = dict(cur.fetchone())
            conn.commit()
        return _serialise(row)

    def get_version(self, skill_id: str, user_id: str, version: str) -> dict | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM marketplace_versions"
                " WHERE skill_id = %s AND user_id = %s AND version = %s",
                (skill_id, user_id, version),
            )
            row = cur.fetchone()
        return _serialise(dict(row)) if row else None

    def list_versions(self, skill_id: str, user_id: str) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM marketplace_versions"
                " WHERE skill_id = %s AND user_id = %s ORDER BY created_at",
                (skill_id, user_id),
            )
            return [_serialise(dict(r)) for r in cur.fetchall()]

    # -- audit ----------------------------------------------------------
    def record_audit(self, event: dict) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO marketplace_audit"
                    " (event, skill_id, user_id, sha256, source, license,"
                    "  price_credits, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " RETURNING *",
                    (
                        event["event"],
                        event["skill_id"],
                        event["user_id"],
                        event.get("sha256", ""),
                        event.get("source", ""),
                        event.get("license", ""),
                        event.get("price_credits", 0),
                        json.dumps(event.get("detail", {})),
                    ),
                )
                row = dict(cur.fetchone())
            conn.commit()
        return _serialise(row)

    def list_audit(self, *, skill_id: str | None = None, user_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM marketplace_audit WHERE 1 = 1"
        params: list = []
        if skill_id is not None:
            query += " AND skill_id = %s"
            params.append(skill_id)
        if user_id is not None:
            query += " AND user_id = %s"
            params.append(user_id)
        query += " ORDER BY id"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            return [_serialise(dict(r)) for r in cur.fetchall()]

    # -- skill texts ----------------------------------------------------
    # Skill text is not stored in Postgres: the registry keeps the
    # validated folder on disk (see registry.import_skill) and the
    # store only records hashes. This method exists so callers can
    # use either store behind one interface.
    def save_skill_text(self, skill_id: str, text: str) -> None:
        return None

    def get_skill_text(self, skill_id: str) -> str | None:
        return None


def _serialise(row: dict) -> dict:
    """Convert a Postgres row to plain JSON-able data."""
    out = dict(row)
    for key, value in out.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out
