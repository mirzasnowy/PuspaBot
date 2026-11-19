"""Startup reconciliation (E-06, plan section 5.1).

Runs once at process startup, before new work is accepted:

1. Scan the ledger for tasks that are still ``running`` but whose
   last heartbeat is older than ``stale_after_seconds``. Each one
   is marked ``interrupted`` with a ``task_stopped`` event
   (append-only: the ledger is never edited). Postgres wins: the
   SQLite session database holds no task status, so there is
   nothing to argue with here.
2. Rebuild the lagging session search index through the routine
   housekeeping pass (``maintenance(..., rebuild_index=True)``),
   which also checks integrity and verifies the FTS5 index
   against the messages table.

The report is a plain dict so the caller can log it or refuse to
start on it; reconciliation itself never raises for a single bad
task — a task that cannot be marked is reported, not fatal.
"""

from __future__ import annotations

from datetime import datetime

from puspa.ledger import Ledger

from .write_ahead import WriteAheadLog


def reconcile_startup(
    ledger: Ledger,
    store=None,
    *,
    stale_after_seconds: float = 300,
    now: datetime | None = None,
) -> dict:
    """Mark stale running tasks interrupted; rebuild the session index.

    ``store`` is a writable ``SessionStore`` (or ``None`` when the
    process has no session database). Returns::

        {
            "interrupted": [task_id, ...],   # newly marked interrupted
            "running_found": int,            # stale running tasks seen
            "fts": {...} | None,            # housekeeping report, if a store
            "errors": [...],                 # tasks that could not be marked
        }
    """
    wal = WriteAheadLog(ledger)
    stale = wal.running_tasks(stale_after_seconds=stale_after_seconds, now=now)
    interrupted: list[str] = []
    errors: list[str] = []
    for entry in stale:
        try:
            wal.mark_interrupted(
                entry["task_id"],
                entry["agent_id"],
                reason="no recent heartbeat at startup",
                last_heartbeat=entry["last_heartbeat"],
            )
            interrupted.append(entry["task_id"])
        except Exception as exc:  # one bad task must not block startup
            errors.append(f"{entry['task_id']}: {exc}")
    fts: dict | None = None
    if store is not None:
        # Local import: sessions must not import consistency.
        from puspa.sessions.housekeeping import maintenance

        fts = maintenance(store, rebuild_index=True)
    return {
        "interrupted": interrupted,
        "running_found": len(stale),
        "fts": fts,
        "errors": errors,
    }
