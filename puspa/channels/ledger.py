"""Delivery ledger (G14).

Every send through a channel adapter is recorded: the inbound
message it answers (when any), the chat, the direction, the
status, and the attempt count. The ledger serves two jobs:

1. **Idempotency** — ``seen()`` answers whether an inbound
   message (by its ``dedup_key``) was already processed, so a
   redelivered update is never answered twice.
2. **Audit** — each outbound attempt is a row, so a failed or
   retried send can be inspected later.

Design decision (documented in ``docs/channels.md``): the ledger
is its OWN Postgres table (``channel_deliveries``, see
``infra/schema_channels.sql``), NOT the core ledger. The core
ledger (``puspa.ledger``) is a closed set of 31 task event types
in a hash-chained audit log for tasks; delivery records are
operational rows that need lookup by platform message id and
per-attempt updates, which the append-only task log cannot do
without a new event type — and no new core event type is added
here. The in-memory ledger below has the same interface and is
what tests and single-process runs use; the Postgres ledger is
what production uses.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

__all__ = [
    "DeliveryRecord",
    "InMemoryDeliveryLedger",
    "PostgresDeliveryLedger",
    "new_delivery_id",
]

#: Delivery statuses. ``sent`` / ``failed`` are terminal; ``retrying``
#: means more attempts are still to come.
STATUSES = ("pending", "sent", "failed", "retrying")


def new_delivery_id() -> str:
    return f"delivery-{uuid.uuid4().hex[:12]}"


@dataclass
class DeliveryRecord:
    """One recorded delivery attempt (inbound or outbound)."""

    id: str
    platform: str
    chat_id: str
    direction: str  # "inbound" or "outbound"
    status: str  # one of STATUSES
    dedup_key: str  # platform:chat:message id (inbound) or reply target
    attempts: int = 1
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "platform": self.platform,
            "chat_id": self.chat_id,
            "direction": self.direction,
            "status": self.status,
            "dedup_key": self.dedup_key,
            "attempts": self.attempts,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class InMemoryDeliveryLedger:
    """Thread-safe in-memory delivery ledger (tests, single-process runs)."""

    def __init__(self) -> None:
        self._records: dict[str, DeliveryRecord] = {}
        self._by_dedup: dict[str, str] = {}
        self._lock = threading.Lock()

    def record(
        self,
        *,
        platform: str,
        chat_id: str,
        direction: str,
        status: str,
        dedup_key: str,
        attempts: int = 1,
        error: str | None = None,
    ) -> DeliveryRecord:
        if status not in STATUSES:
            raise ValueError(f"unknown delivery status: {status!r}")
        if direction not in ("inbound", "outbound"):
            raise ValueError(f"unknown direction: {direction!r}")
        with self._lock:
            record = DeliveryRecord(
                id=new_delivery_id(),
                platform=platform,
                chat_id=chat_id,
                direction=direction,
                status=status,
                dedup_key=dedup_key,
                attempts=attempts,
                error=error,
            )
            self._records[record.id] = record
            # First write wins for dedup: a redelivery must not move
            # the key to the newer record.
            self._by_dedup.setdefault(dedup_key, record.id)
            return record

    def update(
        self,
        delivery_id: str,
        *,
        status: str,
        attempts: int | None = None,
        error: str | None = None,
    ) -> DeliveryRecord | None:
        if status not in STATUSES:
            raise ValueError(f"unknown delivery status: {status!r}")
        with self._lock:
            record = self._records.get(delivery_id)
            if record is None:
                return None
            record.status = status
            if attempts is not None:
                record.attempts = attempts
            record.error = error
            record.updated_at = datetime.now(UTC)
            return record

    def seen(self, dedup_key: str) -> bool:
        """True when this inbound message was already processed."""
        with self._lock:
            return dedup_key in self._by_dedup

    def get(self, delivery_id: str) -> DeliveryRecord | None:
        with self._lock:
            return self._records.get(delivery_id)

    def by_dedup(self, dedup_key: str) -> DeliveryRecord | None:
        with self._lock:
            delivery_id = self._by_dedup.get(dedup_key)
            return self._records.get(delivery_id) if delivery_id else None

    def list_for_chat(
        self, platform: str, chat_id: str, *, limit: int = 50
    ) -> list[DeliveryRecord]:
        with self._lock:
            rows = [
                r for r in self._records.values() if r.platform == platform and r.chat_id == chat_id
            ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[:limit]


class PostgresDeliveryLedger:
    """Delivery ledger backed by the ``channel_deliveries`` table.

    Same interface as :class:`InMemoryDeliveryLedger`. The table is
    created by ``puspa.channels.schema.create_schema()`` (which
    applies ``infra/schema_channels.sql``). All writes are
    best-effort from the caller's point of view: a ledger failure
    must never drop a user message — callers that need that
    guarantee wrap the call (see ``puspa.channels.inbound``).
    """

    def __init__(self, dsn=None) -> None:
        from puspa.accounts.users import resolve_dsn

        self.dsn = resolve_dsn(dsn)

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row)

    def record(
        self, *, platform, chat_id, direction, status, dedup_key, attempts=1, error=None
    ) -> DeliveryRecord:
        if status not in STATUSES:
            raise ValueError(f"unknown delivery status: {status!r}")
        if direction not in ("inbound", "outbound"):
            raise ValueError(f"unknown direction: {direction!r}")
        delivery_id = new_delivery_id()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO channel_deliveries"
                    " (id, platform, chat_id, direction, status, dedup_key, attempts, error)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (dedup_key, direction) DO NOTHING"
                    " RETURNING *",
                    (delivery_id, platform, chat_id, direction, status, dedup_key, attempts, error),
                )
                row = cur.fetchone()
                if row is None:
                    # A row for this (dedup_key, direction) already
                    # exists: return the existing one (first write wins).
                    cur.execute(
                        "SELECT * FROM channel_deliveries WHERE dedup_key = %s AND direction = %s",
                        (dedup_key, direction),
                    )
                    row = cur.fetchone()
            conn.commit()
        return DeliveryRecord(
            id=str(row["id"]),
            platform=platform,
            chat_id=chat_id,
            direction=direction,
            status=str(row["status"]),
            dedup_key=dedup_key,
            attempts=int(row["attempts"]),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def update(self, delivery_id, *, status, attempts=None, error=None):
        if status not in STATUSES:
            raise ValueError(f"unknown delivery status: {status!r}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                if attempts is None:
                    cur.execute(
                        "UPDATE channel_deliveries SET status = %s, error = %s,"
                        " updated_at = now() WHERE id = %s RETURNING *",
                        (status, error, delivery_id),
                    )
                else:
                    cur.execute(
                        "UPDATE channel_deliveries SET status = %s, attempts = %s,"
                        " error = %s, updated_at = now() WHERE id = %s RETURNING *",
                        (status, attempts, error, delivery_id),
                    )
                row = cur.fetchone()
            conn.commit()
        if row is None:
            return None
        return DeliveryRecord(
            id=str(row["id"]),
            platform=row["platform"],
            chat_id=row["chat_id"],
            direction=row["direction"],
            status=str(row["status"]),
            dedup_key=row["dedup_key"],
            attempts=int(row["attempts"]),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def seen(self, dedup_key: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM channel_deliveries"
                " WHERE dedup_key = %s AND direction = 'inbound' LIMIT 1",
                (dedup_key,),
            )
            return cur.fetchone() is not None

    def get(self, delivery_id: str):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM channel_deliveries WHERE id = %s", (delivery_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return DeliveryRecord(
            id=str(row["id"]),
            platform=row["platform"],
            chat_id=row["chat_id"],
            direction=row["direction"],
            status=str(row["status"]),
            dedup_key=row["dedup_key"],
            attempts=int(row["attempts"]),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def by_dedup(self, dedup_key: str):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM channel_deliveries WHERE dedup_key = %s"
                " ORDER BY created_at ASC LIMIT 1",
                (dedup_key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return DeliveryRecord(
            id=str(row["id"]),
            platform=row["platform"],
            chat_id=row["chat_id"],
            direction=row["direction"],
            status=str(row["status"]),
            dedup_key=row["dedup_key"],
            attempts=int(row["attempts"]),
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_for_chat(self, platform: str, chat_id: str, *, limit: int = 50):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM channel_deliveries WHERE platform = %s AND chat_id = %s"
                " ORDER BY created_at DESC LIMIT %s",
                (platform, chat_id, limit),
            )
            rows = cur.fetchall()
        return [
            DeliveryRecord(
                id=str(row["id"]),
                platform=row["platform"],
                chat_id=row["chat_id"],
                direction=row["direction"],
                status=str(row["status"]),
                dedup_key=row["dedup_key"],
                attempts=int(row["attempts"]),
                error=row["error"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
