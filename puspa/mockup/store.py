"""Persistence for mockup design docs (B7 workspace accumulation).

One row per mockup: the owner, the template it came from, and the
whole design doc as JSONB (doc + numbered op log + design
decisions). Assets and decisions therefore accumulate in the
user's workspace simply by editing: every applied op is stored
with the doc, and listing a user's mockups shows their whole
design history. The schema lives in migration 0015_mockup
(next to the other migrations) and is applied the same way.

Row-level security mirrors the other PWA tables: the
``mockups_owner`` policy keys on ``app.current_user_id``
(repaired by migration 0021; migration 0015 pointed at the
never-set ``app.user_id`` by mistake).
"""

from __future__ import annotations

import json
from pathlib import Path

from puspa.accounts.ports import resolve_dsn
from puspa.db import connect as _db_connect
from puspa.db import dict_row
from puspa.migrations import apply_migrations
from puspa.mockup.doc import DesignError, DesignSession, validate_doc

__all__ = ["MOCKUP_TABLES", "MockupStore", "create_schema", "reset_tables", "schema_path"]

MOCKUP_TABLES = ("mockups",)


def schema_path() -> Path:
    """Absolute path of migration 0015_mockup."""
    return (
        Path(__file__).resolve().parents[2]
        / "puspa"
        / "migrations"
        / "versions"
        / "0015_mockup.sql"
    )


def create_schema(dsn=None):
    """Apply the mockup table (migration 0015_mockup). Idempotent; safe per test run."""
    apply_migrations(resolve_dsn(dsn), target="0015")


def reset_tables(dsn=None):
    """Empty the mockup tables (for tests)."""
    with _db_connect(resolve_dsn(dsn)) as conn:
        conn.execute("TRUNCATE " + ", ".join(MOCKUP_TABLES) + " CASCADE")
        conn.commit()


class MockupStore:
    """CRUD + op application for one user's mockups.

    Ops are applied through :class:`DesignSession` (numbered,
    validated, undoable in memory) and the resulting doc is written
    back in the same transaction, so the stored doc is always the
    doc the op log describes. The store never accepts a doc that
    fails ``validate_doc`` — a hostile or corrupt payload cannot
    land in the workspace.
    """

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def create(self, user_id: str, doc: dict) -> dict:
        """Store a new validated doc; returns the stored doc."""
        validate_doc(doc)
        with _db_connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO mockups (id, user_id, title, template, doc) "
                "VALUES (%s, %s, %s, %s, %s::jsonb)",
                (doc["id"], user_id, doc["title"], doc["template"], json.dumps(doc)),
            )
            conn.commit()
        return doc

    def get(self, user_id: str, mockup_id: str) -> dict:
        """Load one mockup the user owns; KeyError when absent."""
        with _db_connect(self.dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT doc FROM mockups WHERE id = %s AND user_id = %s",
                (mockup_id, user_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"mockup {mockup_id!r} not found")
        return row["doc"]

    def list(self, user_id: str) -> list[dict]:
        """All of the user's mockups, newest first (summary shape)."""
        with _db_connect(self.dsn, row_factory=dict_row) as conn:
            rows = conn.execute(
                "SELECT id, title, template, updated_at FROM mockups "
                "WHERE user_id = %s ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "template": row["template"],
                "updated_at": row["updated_at"].isoformat(),
            }
            for row in rows
        ]

    def save(self, user_id: str, doc: dict) -> dict:
        """Overwrite a mockup's doc (validated first)."""
        validate_doc(doc)
        with _db_connect(self.dsn) as conn:
            cursor = conn.execute(
                "UPDATE mockups SET doc = %s::jsonb, title = %s, updated_at = now() "
                "WHERE id = %s AND user_id = %s",
                (json.dumps(doc), doc["title"], doc["id"], user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"mockup {doc['id']!r} not found")
            conn.commit()
        return doc

    def apply(self, user_id: str, mockup_id: str, op: dict) -> dict:
        """Apply one op to a stored mockup and persist the result."""
        doc = self.get(user_id, mockup_id)
        session = DesignSession(doc)
        session.apply(op)
        return self.save(user_id, session.doc)

    def delete(self, user_id: str, mockup_id: str) -> None:
        """Delete a mockup; KeyError when it is not the user's."""
        with _db_connect(self.dsn) as conn:
            cursor = conn.execute(
                "DELETE FROM mockups WHERE id = %s AND user_id = %s", (mockup_id, user_id)
            )
            if cursor.rowcount == 0:
                raise KeyError(f"mockup {mockup_id!r} not found")
            conn.commit()


def apply_op_to_stored(store: MockupStore, user_id: str, mockup_id: str, op: dict) -> dict:
    """Module-level convenience for ``store.apply`` (kept for symmetry
    with the ``apply_op`` doc function)."""
    if not isinstance(op, dict):
        raise DesignError("op must be an object")
    return store.apply(user_id, mockup_id, op)
