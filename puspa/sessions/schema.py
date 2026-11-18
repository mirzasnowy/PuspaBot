"""SQLite schema for the per-profile session database (G26).

One database file per profile (``<home>/state.db``) holds:

- ``sessions`` — session metadata, including ``parent_id`` for the
  compression lineage (a compression births a child session).
- ``messages`` — the full message history of every session.
- ``session_lineage`` — explicit parent/child edges between sessions,
  with the kind of event that created the edge (``compression``).
- ``model_config`` — the profile's model configuration.
- ``state_meta`` — database housekeeping metadata (schema version,
  creation time, last maintenance run).
- ``messages_fts`` — an FTS5 full-text index over message content.

FTS5 reuse note (G7): the keyword-search mechanism is the one built
in ``puspa.memory.recall`` (``FtsIndex`` over a SQLite FTS5 sidecar),
not a second mechanism. The session store reuses G7's safe MATCH
query builder (``_fts5_query``) so user punctuation can never break
out of the FTS5 query syntax, and it fixes three G7 shortcomings:

1. G7 commits after every single indexed row; here the FTS5 table is
   kept in sync by SQLite triggers inside the writer's own
   transaction, so indexing costs zero extra commits.
2. G7 has no way to detect index drift; here ``verify_fts`` probes
   the index itself (a MATCH query per message, since ``COUNT(*)``
   on an external-content FTS5 table reads the content table, not
   the index) and runs the FTS5 ``integrity-check``, and
   ``rebuild_fts`` restores the index.
3. G7's default index is in-memory and lost on close; here the FTS5
   table lives in the same ``state.db`` file as the messages, so the
   index is persistent by construction.
"""

from __future__ import annotations

import sqlite3

from puspa.memory.recall import _tokens as _g7_tokens

#: Current schema version, recorded in ``state_meta``.
SCHEMA_VERSION = 1

#: DDL for the whole database. Idempotent: safe to run on an existing
#: database (``IF NOT EXISTS`` everywhere, plus the trigger drops).
SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS state_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        parent_id TEXT REFERENCES sessions(id),
        model TEXT,
        metadata TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
        content TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        seq INTEGER NOT NULL,
        UNIQUE (session_id, seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq)",
    """
    CREATE TABLE IF NOT EXISTS session_lineage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_session_id TEXT NOT NULL REFERENCES sessions(id),
        child_session_id TEXT NOT NULL REFERENCES sessions(id),
        kind TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (parent_session_id, child_session_id, kind)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # External-content FTS5 table: the index rows live in the FTS5
    # table, the content lives in ``messages``. Triggers below keep
    # the two in sync inside the writer's transaction.
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
        USING fts5(content, content='messages', content_rowid='id',
                    tokenize='unicode61')
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages
    BEGIN
        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages
    BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages
    BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
    END
    """,
)


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the schema (idempotent) and stamp the schema version."""
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO state_meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def verify_schema(conn: sqlite3.Connection) -> list[str]:
    """Return a list of schema problems (empty means healthy)."""
    problems: list[str] = []
    expected = {"state_meta", "sessions", "messages", "session_lineage", "model_config"}
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    present = {row[0] for row in rows}
    for table in sorted(expected - present):
        problems.append(f"missing table: {table}")
    fts = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'messages_fts'"
    ).fetchone()
    if fts is None:
        problems.append("missing table: messages_fts")
    version = conn.execute("SELECT value FROM state_meta WHERE key = 'schema_version'").fetchone()
    if version is None:
        problems.append("missing state_meta: schema_version")
    elif version[0] != str(SCHEMA_VERSION):
        problems.append(f"schema_version mismatch: got {version[0]!r}, want {SCHEMA_VERSION!r}")
    return problems


def verify_fts(conn: sqlite3.Connection) -> dict:
    """Check the FTS5 index against its source table.

    Returns ``{"ok": bool, "messages": int, "indexed": int,
    "integrity": str, "problems": [...]}``. A mismatch means the
    index drifted (for example after a manual edit or a crashed
    rebuild) and ``rebuild_fts`` should be run.

    Note: for an external-content FTS5 table, ``COUNT(*)`` and a
    bare rowid scan read the *content* table, so they cannot detect
    drift. The check therefore probes the index itself: for every
    message it runs a MATCH query for the message's first token
    constrained to that message's rowid. A message whose token is
    not found is unindexed.
    """
    problems: list[str] = []
    rows = conn.execute("SELECT id, content FROM messages").fetchall()
    indexed = 0
    for row in rows:
        tokens = _g7_tokens(row[1])
        if not tokens:
            indexed += 1
            continue
        hit = conn.execute(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? AND rowid = ?",
            (f'"{tokens[0]}"', row[0]),
        ).fetchone()
        if hit is None:
            problems.append(f"message {row[0]} is not in the FTS5 index")
        else:
            indexed += 1
    integrity_rows = conn.execute(
        "INSERT INTO messages_fts(messages_fts) VALUES ('integrity-check')"
    ).fetchall()
    # No rows means no errors; some SQLite versions return one 'ok' row.
    integrity = "ok"
    bad = [row[0] for row in integrity_rows if row[0] != "ok"]
    if bad:
        integrity = "; ".join(bad)
        problems.append(f"fts integrity-check: {integrity}")
    return {
        "ok": not problems,
        "messages": len(rows),
        "indexed": indexed,
        "integrity": integrity,
        "problems": problems,
    }


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the whole FTS5 index from the ``messages`` table."""
    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
    conn.commit()
