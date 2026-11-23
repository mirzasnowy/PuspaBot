"""Durable session stores for the hierarchy runtime.

The hierarchy runtime (``puspa.runtime.hierarchy``) defines the
``SessionState`` snapshot: the working memory of one session, that
is, the keys the agent loop reads and writes while it works on a
task. The default store for that snapshot is in-memory
(``InMemorySessionStore``): the state survives across turns inside
one process and is lost when the process restarts. This module
provides the durable backends that close that gap, so a session
resumes after a restart with its working memory intact.

Two backends, one interface (``SessionStore.save`` / ``load`` /
``delete``):

- ``PostgresSessionStore``: the hosted install. One row per session
  id in table ``runtime_session_states`` (migration 0028), holding
  the snapshot as JSONB plus the owning user id and app name. The
  table is created by the migration runner, never by this module:
  constructing the store applies pending migrations up to 0028,
  the same pattern the other stores use.
- ``SqliteSessionStore``: the local install. One file, one table
  with the same columns, JSON text instead of JSONB. The file is
  rebuildable by design, like the ``state.db`` schema in
  ``puspa.sessions``.

What is stored, and what is not: the snapshot is the plain state
dict, with ``temp:`` keys already stripped by ``SessionState``
(those keys live for one turn only and are never saved). Values
are encoded as JSON; a value that is not JSON data (for example a
datetime or a custom object) is stored through ``str`` instead, so
a save never crashes on an odd value, and the loaded value comes
back as that string. Plain JSON values round-trip exactly.

Use ``open_session_store`` to pick a backend: pass a Postgres DSN
for the hosted install, or a file path for the local install.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from puspa.db import connect as _db_connect
from puspa.migrations import apply_migrations

from .hierarchy import SessionStore

__all__ = [
    "PostgresSessionStore",
    "SqliteSessionStore",
    "open_session_store",
]

#: DSN default (peer auth as the OS user, no password).
DEFAULT_DSN = None

#: Migration that creates the Postgres session-state table.
_SESSIONS_MIGRATION = "0028"


def _resolve_dsn(dsn: str | None) -> str:
    return dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


def _encode(snapshot: dict) -> str:
    """Snapshot dict to a JSON string (odd values become strings)."""
    return json.dumps(snapshot, default=str)


def _decode(raw) -> dict | None:
    """Stored JSON value back to a snapshot dict, or None."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    return dict(json.loads(raw))


class PostgresSessionStore(SessionStore):
    """Session snapshots in Postgres table ``runtime_session_states``.

    One row per session id; a later save overwrites the earlier
    row (upsert), so the newest snapshot is always the resumed
    one. ``user_id`` and ``app_name`` ride along for ownership:
    the table's row level security policy isolates rows by user.
    """

    def __init__(self, dsn: str | None = None, *, ensure_schema: bool = True) -> None:
        self.dsn = _resolve_dsn(dsn)
        if ensure_schema:
            apply_migrations(self.dsn, target=_SESSIONS_MIGRATION)

    def save(
        self, session_id: str, snapshot: dict, *, user_id: str = "", app_name: str = "puspa"
    ) -> None:
        with _db_connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO runtime_session_states"
                " (session_id, user_id, app_name, state)"
                " VALUES (%s, %s, %s, %s::jsonb)"
                " ON CONFLICT (session_id) DO UPDATE SET"
                " user_id = EXCLUDED.user_id,"
                " app_name = EXCLUDED.app_name,"
                " state = EXCLUDED.state,"
                " updated_at = now()",
                (str(session_id), str(user_id), str(app_name), _encode(snapshot)),
            )
            conn.commit()

    def load(self, session_id: str) -> dict | None:
        with _db_connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT state FROM runtime_session_states WHERE session_id = %s",
                (str(session_id),),
            ).fetchone()
        return _decode(row[0]) if row is not None else None

    def delete(self, session_id: str) -> None:
        with _db_connect(self.dsn) as conn:
            conn.execute(
                "DELETE FROM runtime_session_states WHERE session_id = %s",
                (str(session_id),),
            )
            conn.commit()


class SqliteSessionStore(SessionStore):
    """Session snapshots in one local SQLite file.

    Same columns as the Postgres table, JSON text instead of
    JSONB. One re-entrant lock guards the shared connection, so
    the store is safe to use from worker threads as well as the
    main thread. The table is created on open: this file is
    rebuildable by design, like ``state.db`` in ``puspa.sessions``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(
            self.path, timeout=5.0, check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS runtime_session_states ("
            " session_id TEXT PRIMARY KEY,"
            " user_id TEXT NOT NULL DEFAULT '',"
            " app_name TEXT NOT NULL DEFAULT 'puspa',"
            " state TEXT NOT NULL DEFAULT '{}',"
            " created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    def _now_iso(self) -> str:
        return datetime.now(UTC).isoformat()

    def save(
        self, session_id: str, snapshot: dict, *, user_id: str = "", app_name: str = "puspa"
    ) -> None:
        now = self._now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO runtime_session_states"
                " (session_id, user_id, app_name, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(session_id) DO UPDATE SET"
                " user_id = excluded.user_id,"
                " app_name = excluded.app_name,"
                " state = excluded.state,"
                " updated_at = excluded.updated_at",
                (str(session_id), str(user_id), str(app_name), _encode(snapshot), now, now),
            )
            self._conn.commit()

    def load(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM runtime_session_states WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
        return _decode(row[0]) if row is not None else None

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM runtime_session_states WHERE session_id = ?",
                (str(session_id),),
            )
            self._conn.commit()

    def close(self) -> None:
        """Close the connection. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def open_session_store(
    *, dsn: str | None = None, sqlite_path: str | Path | None = None
) -> SessionStore:
    """Open a durable session store.

    Pass ``dsn`` for the hosted Postgres backend, or
    ``sqlite_path`` for a local SQLite file. Exactly one of the
    two must be given; giving neither raises ``ValueError``.
    """
    if dsn is not None and sqlite_path is not None:
        raise ValueError("open_session_store takes dsn or sqlite_path, not both")
    if dsn is not None:
        return PostgresSessionStore(dsn)
    if sqlite_path is not None:
        return SqliteSessionStore(sqlite_path)
    raise ValueError("open_session_store needs dsn= (Postgres) or sqlite_path= (local file)")
