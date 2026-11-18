"""The session store: one SQLite database per profile (G26).

``SessionStore`` is the only class that touches ``state.db`` directly,
and it can only be opened for writing by the profile gateway: the
constructor requires a writer token, and the only code that mints one
is ``puspa.sessions.gateway``. Everyone else either goes through the
gateway API or opens a read-only reader (``read_only=True``, SQLite
``mode=ro``), which physically cannot write.

Write rules enforced here:

- Every mutation runs in a single transaction (``BEGIN IMMEDIATE``),
  so a multi-row append is atomic: either every row lands or none
  does.
- ``busy_timeout`` makes SQLite wait for a lock holder instead of
  failing at once; when the lock still cannot be acquired the write
  raises :class:`WriteContentionError` instead of silently dropping
  the write.
- WAL mode keeps readers from blocking the writer.
- Full-text search reuses the G7 safe MATCH query builder from
  ``puspa.memory.recall`` (see ``puspa.sessions.schema`` for what was
  fixed relative to G7).
"""

from __future__ import annotations

import contextlib
import functools
import json
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from puspa.memory.recall import _fts5_query as _g7_match_query

from .errors import (
    CorruptDatabaseError,
    SessionNotFoundError,
    SingleWriterError,
    WriteContentionError,
)
from .schema import create_schema, rebuild_fts, verify_fts, verify_schema

#: Message roles accepted by the messages table (mirrors the CHECK).
ROLES = ("system", "user", "assistant", "tool")

#: How many times a contended write is retried inside one call.
_MAX_WRITE_ATTEMPTS = 3

#: Base sleep (seconds) between contended-write retries; doubled each time.
_RETRY_BASE_SECONDS = 0.05


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _synchronized(fn):
    """Serialize access to the shared connection across threads.

    The gateway is used from worker threads as well as the main
    thread; one re-entrant lock guards every public store method
    (nested calls re-enter safely).
    """

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


class _WriterToken:
    """Proof that the opener is the profile gateway.

    Only ``puspa.sessions.gateway`` mints this (it is constructed
    there and passed in as ``_writer_token``). Direct construction of
    a writable ``SessionStore`` without one raises
    :class:`SingleWriterError`.
    """

    __slots__ = ()


class SessionStore:
    """Read/write access to one profile's ``state.db``.

    Open through ``puspa.sessions.assembly.open_persistence`` (writer,
    via the gateway) or ``open_persistence_reader`` (read-only).
    Do not construct directly outside the gateway.
    """

    def __init__(
        self,
        path,
        *,
        read_only=False,
        busy_timeout_ms=5000,
        _writer_token=None,
    ) -> None:
        self.path = Path(path)
        self.read_only = read_only
        if not read_only and not isinstance(_writer_token, _WriterToken):
            raise SingleWriterError(
                "direct writes to state.db are forbidden: only the profile "
                "gateway may write sessions; open a writer through "
                "open_persistence() or a reader through "
                "open_persistence_reader()"
            )
        self._busy_timeout_ms = busy_timeout_ms
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = self._connect()
        if not read_only:
            create_schema(self._conn)

    # -- connection ------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        try:
            if self.read_only:
                uri = f"file:{self.path}?mode=ro"
                conn = sqlite3.connect(
                    uri,
                    uri=True,
                    timeout=self._busy_timeout_ms / 1000,
                    check_same_thread=False,
                    isolation_level=None,
                )
            else:
                conn = sqlite3.connect(
                    self.path,
                    timeout=self._busy_timeout_ms / 1000,
                    check_same_thread=False,
                    isolation_level=None,
                )
        except sqlite3.DatabaseError as exc:
            raise CorruptDatabaseError(f"cannot open session database {self.path}: {exc}") from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            conn.execute("PRAGMA foreign_keys = ON")
            if not self.read_only:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
            # Touch the schema: a garbage file fails here, before any
            # caller can mistake it for an empty database.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise CorruptDatabaseError(
                f"session database {self.path} is not a readable SQLite "
                f"database ({exc}); refusing to open it — see "
                "docs/state-db-recovery.md"
            ) from exc
        return conn

    @_synchronized
    def close(self) -> None:
        """Close the connection. Idempotent."""
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- transactions ----------------------------------------------------

    def _run_write(self, fn):
        """Run ``fn(conn)`` in one ``BEGIN IMMEDIATE`` transaction.

        Retries on a locked database up to ``_MAX_WRITE_ATTEMPTS``
        times, then raises :class:`WriteContentionError`. Any other
        error rolls the transaction back and propagates.
        """
        assert self._conn is not None
        last_exc: Exception | None = None
        for attempt in range(_MAX_WRITE_ATTEMPTS):
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    result = fn(self._conn)
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
                self._conn.execute("COMMIT")
                return result
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                last_exc = exc
                with contextlib.suppress(sqlite3.Error):
                    self._conn.execute("ROLLBACK")
                if attempt < _MAX_WRITE_ATTEMPTS - 1:
                    time.sleep(_RETRY_BASE_SECONDS * (2**attempt))
        raise WriteContentionError(
            f"could not acquire the write lock on {self.path} after "
            f"{_MAX_WRITE_ATTEMPTS} attempts ({last_exc})"
        ) from last_exc

    def _require_writable(self) -> None:
        if self.read_only:
            raise SingleWriterError(
                "this store is a read-only reader; writes go through the "
                "profile gateway (open_persistence())"
            )

    # -- sessions --------------------------------------------------------

    @_synchronized
    def create_session(self, title="", *, model=None, parent_id=None, metadata=None):
        """Create a session and return its record (without messages)."""
        self._require_writable()
        session_id = uuid.uuid4().hex
        now = _now_iso()
        meta = json.dumps(metadata or {})

        def _insert(conn):
            if parent_id is not None and not self._session_exists(conn, parent_id):
                raise SessionNotFoundError(f"no session {parent_id!r}")
            conn.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at,"
                " parent_id, model, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, title, now, now, parent_id, model, meta),
            )
            return self._session_row(conn, session_id)

        return self._run_write(_insert)

    @staticmethod
    def _session_exists(conn, session_id) -> bool:
        row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row is not None

    @staticmethod
    def _session_row(conn, session_id):
        row = conn.execute(
            "SELECT id, title, created_at, updated_at, parent_id, model,"
            " metadata FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise SessionNotFoundError(f"no session {session_id!r}")
        record = dict(row)
        record["metadata"] = json.loads(record["metadata"] or "{}")
        return record

    @_synchronized
    def get_session(self, session_id, *, with_messages=True):
        """Return a session record, optionally with its messages."""
        assert self._conn is not None
        record = self._session_row(self._conn, session_id)
        if with_messages:
            record["messages"] = self._messages(self._conn, session_id)
        return record

    @_synchronized
    def list_sessions(self, *, limit=50):
        """Sessions newest first (``limit`` caps the result)."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT id, title, created_at, updated_at, parent_id, model,"
            " metadata FROM sessions ORDER BY created_at DESC, id DESC"
            " LIMIT ?",
            (limit,),
        ).fetchall()
        records = []
        for row in rows:
            record = dict(row)
            record["metadata"] = json.loads(record["metadata"] or "{}")
            records.append(record)
        return records

    @staticmethod
    def _messages(conn, session_id):
        rows = conn.execute(
            "SELECT id, role, content, created_at, seq FROM messages"
            " WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- messages --------------------------------------------------------

    @_synchronized
    def append_message(self, session_id, role, content):
        """Append one message; returns the stored message record."""
        return self.append_messages(session_id, [(role, content)])[0]

    @_synchronized
    def append_messages(self, session_id, messages):
        """Append several messages atomically; returns the records.

        All rows are validated first, then inserted in one
        transaction: a single bad row rolls the whole batch back.
        """
        self._require_writable()
        batch = [(role, str(content)) for role, content in messages]
        for role, _content in batch:
            if role not in ROLES:
                raise ValueError(f"unknown message role: {role!r}")

        def _insert(conn):
            if not self._session_exists(conn, session_id):
                raise SessionNotFoundError(f"no session {session_id!r}")
            last = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            now = _now_iso()
            stored = []
            for offset, (role, content) in enumerate(batch, start=1):
                seq = last + offset
                cursor = conn.execute(
                    "INSERT INTO messages (session_id, role, content,"
                    " created_at, seq) VALUES (?, ?, ?, ?, ?)",
                    (session_id, role, content, now, seq),
                )
                stored.append(
                    {
                        "id": cursor.lastrowid,
                        "role": role,
                        "content": content,
                        "created_at": now,
                        "seq": seq,
                    }
                )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            return stored

        return self._run_write(_insert)

    # -- full-text search (FTS5, G7 mechanism reused) --------------------

    @_synchronized
    def search_messages(self, query, *, limit=20, session_id=None):
        """BM25-ranked messages matching ``query`` (best first).

        The MATCH query is built by G7's safe builder
        (``puspa.memory.recall._fts5_query``), so user punctuation can
        never break out of the FTS5 syntax. A query with no word
        characters matches nothing. Returns message records with
        ``session_id`` and ``bm25`` (lower is a better match).
        """
        assert self._conn is not None
        try:
            match = _g7_match_query(query)
        except ValueError:
            return []
        sql = (
            "SELECT m.id, m.session_id, m.role, m.content, m.created_at,"
            " m.seq, bm25(messages_fts) AS bm25 FROM messages_fts"
            " JOIN messages m ON m.id = messages_fts.rowid"
            " WHERE messages_fts MATCH ?"
        )
        params: list = [match]
        if session_id is not None:
            sql += " AND m.session_id = ?"
            params.append(session_id)
        sql += " ORDER BY bm25 ASC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    # -- model config ----------------------------------------------------

    @_synchronized
    def set_model_config(self, key, value):
        """Store one model-config value (JSON-encoded)."""
        self._require_writable()
        now = _now_iso()

        def _upsert(conn):
            conn.execute(
                "INSERT INTO model_config (key, value, updated_at)"
                " VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET"
                " value = excluded.value, updated_at = excluded.updated_at",
                (key, json.dumps(value), now),
            )

        self._run_write(_upsert)

    @_synchronized
    def get_model_config(self, key, default=None):
        """Return one model-config value (decoded), or ``default``."""
        assert self._conn is not None
        row = self._conn.execute("SELECT value FROM model_config WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    # -- lineage ---------------------------------------------------------

    @_synchronized
    def create_child_session(
        self, parent_session_id, *, summary, kind="compression", reason=None, title=None, model=None
    ):
        """Birth a child session from a parent, atomically.

        Inserts the child session (``parent_id`` set), its first
        message (the summary, role ``assistant``), and the
        ``session_lineage`` edge — all in one transaction, so a
        compression can never leave a child without its edge or an
        edge without its child. Returns the child session record
        (with messages).
        """
        self._require_writable()
        child_id = uuid.uuid4().hex
        now = _now_iso()

        def _insert(conn):
            if not self._session_exists(conn, parent_session_id):
                raise SessionNotFoundError(f"no session {parent_session_id!r}")
            conn.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at,"
                " parent_id, model, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    child_id,
                    title or f"Compressed from {parent_session_id[:8]}",
                    now,
                    now,
                    parent_session_id,
                    model,
                    "{}",
                ),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at, seq)"
                " VALUES (?, 'assistant', ?, ?, 1)",
                (child_id, str(summary), now),
            )
            conn.execute(
                "INSERT INTO session_lineage (parent_session_id,"
                " child_session_id, kind, reason, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (parent_session_id, child_id, kind, reason, now),
            )
            return self.get_session(child_id)

        return self._run_write(_insert)

    @_synchronized
    def lineage_chain(self, session_id):
        """Walk a session's ancestry: [self, parent, grandparent, ...]."""
        assert self._conn is not None
        chain = []
        current = session_id
        seen = set()
        while current is not None and current not in seen:
            seen.add(current)
            chain.append(self._session_row(self._conn, current))
            row = self._conn.execute(
                "SELECT parent_session_id FROM session_lineage"
                " WHERE child_session_id = ? ORDER BY id DESC LIMIT 1",
                (current,),
            ).fetchone()
            current = row[0] if row else None
        return chain

    @_synchronized
    def children(self, session_id):
        """Direct child sessions of ``session_id`` (newest first)."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT child_session_id, kind, reason, created_at"
            " FROM session_lineage WHERE parent_session_id = ?"
            " ORDER BY id DESC",
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- housekeeping ----------------------------------------------------

    @_synchronized
    def check_integrity(self):
        """``PRAGMA integrity_check`` + foreign-key check + schema check."""
        assert self._conn is not None
        problems: list[str] = []
        integrity = self._conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            problems.append(f"integrity_check: {integrity}")
        fk_rows = self._conn.execute("PRAGMA foreign_key_check").fetchall()
        for row in fk_rows:
            problems.append(f"foreign_key_check: {dict(row)}")
        problems.extend(verify_schema(self._conn))
        return problems

    @_synchronized
    def verify_fts(self):
        """Check the FTS5 index against the messages table."""
        assert self._conn is not None
        return verify_fts(self._conn)

    @_synchronized
    def rebuild_fts(self):
        """Rebuild the FTS5 index from the messages table."""
        self._require_writable()
        assert self._conn is not None
        rebuild_fts(self._conn)

    @_synchronized
    def get_state_meta(self, key, default=None):
        """Return one ``state_meta`` value, or ``default``."""
        assert self._conn is not None
        row = self._conn.execute("SELECT value FROM state_meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else row[0]

    @_synchronized
    def set_state_meta(self, key, value):
        """Store one ``state_meta`` value."""
        self._require_writable()

        def _upsert(conn):
            conn.execute(
                "INSERT INTO state_meta(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )

        self._run_write(_upsert)

    @_synchronized
    def counts(self):
        """Row counts per table (for maintenance reports)."""
        assert self._conn is not None
        out = {}
        for table in ("sessions", "messages", "session_lineage", "model_config"):
            out[table] = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return out
