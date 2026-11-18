"""Housekeeping and recovery inspection for session databases (G26).

Two responsibilities:

- ``maintenance()`` — the routine health pass: integrity check,
  FTS5 verification, row counts, and (on request) a rebuild of the
  FTS5 index and a ``VACUUM``. It never deletes session data; it
  reports, and it stamps ``state_meta.last_maintenance``.
- ``recover_report()`` — a read-only inspection of a database file
  that may be corrupt. It never opens the file for writing and never
  deletes or overwrites anything: it reports the file's state
  (missing / empty / healthy / corrupt) and the recovery steps from
  ``docs/state-db-recovery.md`` that apply. Recovery itself is a
  human (or operator-script) decision, not an automatic repair.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .home import state_db_path
from .schema import rebuild_fts

#: SQLite file header magic (first 16 bytes of a database file).
SQLITE_MAGIC = b"SQLite format 3\x00"


def maintenance(store, *, vacuum=False, rebuild_index=False):
    """Run the routine health pass over an open store.

    Returns a report dict: ``{"ok": bool, "problems": [...],
    "fts": {...}, "counts": {...}, "vacuumed": bool,
    "fts_rebuilt": bool}``. ``ok`` is False when any integrity,
    schema, or FTS problem is found. With ``rebuild_index=True`` a
    drifted FTS5 index is rebuilt first (then re-verified); with
    ``vacuum=True`` a ``VACUUM`` runs last (requires no open
    transaction and briefly takes the write lock).
    """
    problems = list(store.check_integrity())
    fts = store.verify_fts()
    if rebuild_index and not fts["ok"]:
        store.rebuild_fts()
        fts = store.verify_fts()
    problems.extend(fts["problems"])
    vacuumed = False
    if vacuum:
        assert store._conn is not None
        store._conn.execute("VACUUM")
        store._conn.commit()
        vacuumed = True
    store.set_state_meta("last_maintenance", datetime.now(UTC).isoformat())
    return {
        "ok": not problems,
        "problems": problems,
        "fts": fts,
        "counts": store.counts(),
        "vacuumed": vacuumed,
        "fts_rebuilt": rebuild_index and not fts["problems"],
    }


def recover_report(db_path=None, *, profile=None, home=None):
    """Inspect a database file without opening it for writing.

    Returns ``{"status": "missing" | "empty" | "healthy" | "corrupt",
    "path": str, "size": int | None, "problems": [...], "steps": [...]}``
    where ``steps`` names the recovery procedure in
    ``docs/state-db-recovery.md`` that applies. Nothing is modified.
    """
    path = Path(db_path) if db_path is not None else state_db_path(profile, home=home)
    report = {"path": str(path), "size": None, "problems": [], "steps": []}
    if not path.exists():
        report["status"] = "missing"
        report["steps"] = ["create a fresh database by opening the writer"]
        return report
    report["size"] = path.stat().st_size
    if report["size"] == 0:
        report["status"] = "empty"
        report["steps"] = ["zero-byte file: safe to delete, then open the writer"]
        return report
    header = path.read_bytes()[:16]
    if header != SQLITE_MAGIC:
        report["status"] = "corrupt"
        report["problems"] = ["file header is not a SQLite database"]
        report["steps"] = [
            "do NOT delete the file",
            "restore state.db from the latest backup (see state-db-recovery.md)",
            "or rebuild from the ledger and re-index FTS5",
        ]
        return report
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                report["problems"].append(f"integrity_check: {integrity}")
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table in ("sessions", "messages", "session_lineage"):
                if table not in tables:
                    report["problems"].append(f"missing table: {table}")
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        report["problems"].append(f"unreadable database: {exc}")
    if report["problems"]:
        report["status"] = "corrupt"
        report["steps"] = [
            "do NOT delete the file",
            "restore state.db from the latest backup (see state-db-recovery.md)",
            "or rebuild from the ledger and re-index FTS5",
        ]
    else:
        report["status"] = "healthy"
        report["steps"] = ["no action needed"]
    return report


def rebuild_fts_index(store):
    """Rebuild the FTS5 index from the messages table (via the store)."""
    rebuild_fts(store._conn)
    return store.verify_fts()
