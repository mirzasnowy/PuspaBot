"""Work claiming for multi-replica execution (G8, OpenBot pattern).

One ``work_items`` table, claimed with
``SELECT ... FOR UPDATE SKIP LOCKED``, a lease measured on the
database clock, and an attempt cap. Only one replica holds the
Postgres advisory lock that scans for work, so two replicas never
claim the same item; the server runs the claim loop itself, with
no separate worker process.

An in-memory queue (:class:`InMemoryWorkQueue`) implements the
same claim/complete/fail surface for tests without a database.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg

__all__ = [
    "DEFAULT_DSN",
    "SCAN_LOCK_KEY",
    "InMemoryWorkQueue",
    "WorkItem",
    "claim_work_items",
    "complete_work_item",
    "count_work_items",
    "enqueue_work_item",
    "ensure_work_items_schema",
    "fail_work_item",
]

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: Advisory-lock key for the single replica that scans for work.
SCAN_LOCK_KEY = 918273001

#: Default lease length for a claimed item.
DEFAULT_LEASE_SECONDS = 300

#: Default attempt cap: after this many failures the item is terminal.
DEFAULT_MAX_ATTEMPTS = 3


@dataclass
class WorkItem:
    """One queued unit of work."""

    id: str
    payload: dict
    status: str = "pending"
    attempts: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    lease_owner: str | None = None
    lease_until: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "payload": dict(self.payload),
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "lease_owner": self.lease_owner,
            "lease_until": (self.lease_until.isoformat() if self.lease_until else None),
            "created_at": self.created_at.isoformat(),
        }


def ensure_work_items_schema(dsn: str | None = None) -> None:
    """Create the ``work_items`` table if it does not exist."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS work_items ("
            "  id TEXT PRIMARY KEY,"
            "  payload JSONB NOT NULL DEFAULT '{}'::jsonb,"
            "  status TEXT NOT NULL DEFAULT 'pending',"
            "  attempts INTEGER NOT NULL DEFAULT 0,"
            "  max_attempts INTEGER NOT NULL DEFAULT 3,"
            "  lease_owner TEXT,"
            "  lease_until TIMESTAMPTZ,"
            "  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
            "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_items_claimable"
            " ON work_items (created_at)"
            " WHERE status = 'pending'"
        )


def enqueue_work_item(
    payload: dict, *, dsn: str | None = None, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> str:
    """Insert one pending work item; return its id."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    ensure_work_items_schema(dsn)
    item_id = f"work-{uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO work_items (id, payload, max_attempts) VALUES (%s, %s::jsonb, %s)",
            (item_id, json.dumps(dict(payload)), max_attempts),
        )
    return item_id


def claim_work_items(
    *,
    owner: str,
    limit: int = 1,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    dsn: str | None = None,
) -> list[WorkItem]:
    """Claim up to ``limit`` pending items for ``owner``.

    One transaction: take the advisory scan lock (transaction
    scoped, so it is released on commit), then claim the oldest
    pending items plus claimed items whose lease has expired, with
    ``FOR UPDATE SKIP LOCKED``. The lease is stamped from the
    database clock (``now()``), never the replica's clock. When
    another replica holds the scan lock, this call claims nothing
    and returns ``[]`` instead of waiting.
    """
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    ensure_work_items_schema(dsn)
    claimed: list[WorkItem] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (SCAN_LOCK_KEY,))
            lock_row = cur.fetchone()
            if lock_row is None or not lock_row[0]:
                conn.rollback()
                return []
            cur.execute(
                "SELECT id, payload, status, attempts, max_attempts,"
                " lease_owner, lease_until, created_at"
                " FROM work_items"
                " WHERE status IN ('pending', 'claimed')"
                " AND (lease_until IS NULL OR lease_until < now())"
                " ORDER BY created_at ASC LIMIT %s"
                " FOR UPDATE SKIP LOCKED",
                (limit,),
            )
            rows = cur.fetchall()
            for row in rows:
                cur.execute(
                    "UPDATE work_items SET status = 'claimed',"
                    " lease_owner = %s,"
                    " lease_until = now() + (%s || ' seconds')::interval,"
                    " updated_at = now() WHERE id = %s",
                    (owner, str(lease_seconds), row[0]),
                )
                claimed.append(
                    WorkItem(
                        id=row[0],
                        payload=dict(row[1] or {}),
                        status="claimed",
                        attempts=row[3],
                        max_attempts=row[4],
                        lease_owner=owner,
                        lease_until=datetime.now(UTC) + timedelta(seconds=lease_seconds),
                        created_at=row[7],
                    )
                )
        conn.commit()
    return claimed


def complete_work_item(item_id: str, *, dsn: str | None = None) -> bool:
    """Mark a claimed item done; True when the row existed."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.execute(
            "UPDATE work_items SET status = 'done', lease_until = NULL,"
            " updated_at = now() WHERE id = %s AND status = 'claimed'",
            (item_id,),
        )
        return cur.rowcount == 1


def fail_work_item(item_id: str, *, dsn: str | None = None) -> dict | None:
    """Record one failed attempt for a claimed item.

    The item returns to ``pending`` with its lease cleared while
    attempts remain; once ``attempts >= max_attempts`` it becomes
    ``failed`` (terminal) and is never claimed again. Returns the
    new ``{"status": ..., "attempts": ...}`` or ``None`` when the
    row did not exist or was not claimed.
    """
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT attempts, max_attempts FROM work_items"
                " WHERE id = %s AND status = 'claimed' FOR UPDATE",
                (item_id,),
            )
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return None
            attempts = row[0] + 1
            status = "failed" if attempts >= row[1] else "pending"
            cur.execute(
                "UPDATE work_items SET status = %s, attempts = %s,"
                " lease_owner = NULL, lease_until = NULL,"
                " updated_at = now() WHERE id = %s",
                (status, attempts, item_id),
            )
        conn.commit()
    return {"status": status, "attempts": attempts}


def count_work_items(status: str | None = None, *, dsn: str | None = None) -> int:
    """Count work items, optionally filtered by status."""
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    ensure_work_items_schema(dsn)
    with psycopg.connect(dsn) as conn:
        if status is None:
            row = conn.execute("SELECT COUNT(*) FROM work_items").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM work_items WHERE status = %s", (status,)
            ).fetchone()
    if row is None:
        return 0
    return row[0]


class InMemoryWorkQueue:
    """Same claim/complete/fail surface as the Postgres table.

    For tests without a database and for single-process runs. The
    scan lock is a plain lock held only for the duration of one
    claim call; leases use wall-clock time.
    """

    def __init__(self):
        self._items: dict[str, WorkItem] = {}
        self._lock = threading.Lock()

    def enqueue(self, payload: dict, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> str:
        item = WorkItem(
            id=f"work-{uuid.uuid4().hex[:12]}", payload=dict(payload), max_attempts=max_attempts
        )
        with self._lock:
            self._items[item.id] = item
        return item.id

    def claim(
        self,
        *,
        owner: str,
        limit: int = 1,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> list[WorkItem]:
        now = now or datetime.now(UTC)
        claimed: list[WorkItem] = []
        with self._lock:
            pending = [
                item
                for item in self._items.values()
                if item.status in ("pending", "claimed")
                and (item.lease_until is None or item.lease_until < now)
            ]
            pending.sort(key=lambda item: item.created_at)
            for item in pending[:limit]:
                item.status = "claimed"
                item.lease_owner = owner
                item.lease_until = now + timedelta(seconds=lease_seconds)
                claimed.append(item)
        return claimed

    def complete(self, item_id: str) -> bool:
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.status != "claimed":
                return False
            item.status = "done"
            item.lease_until = None
            return True

    def fail(self, item_id: str) -> dict | None:
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.status != "claimed":
                return None
            item.attempts += 1
            item.status = "failed" if item.attempts >= item.max_attempts else "pending"
            item.lease_owner = None
            item.lease_until = None
            return {"status": item.status, "attempts": item.attempts}

    def count(self, status: str | None = None) -> int:
        with self._lock:
            if status is None:
                return len(self._items)
            return sum(1 for item in self._items.values() if item.status == status)
