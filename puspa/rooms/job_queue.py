"""Per-room job queue (P-05, plan section 12.1).

One serial queue per room: two members asking for conflicting
things (summarise this chat, record that note) are serialised
instead of racing. A job is a durable row in ``room_job_queue``
(migration 0024); the worker claims the oldest queued job for a
room while holding a Postgres advisory lock keyed on the room id,
runs its handler, then marks it done (with the handler's result)
or failed (with the error). The lock is session-level and is held
for the whole handler run, so two workers can never run two jobs
for the same room at once — while jobs for different rooms still
run in parallel.

Kinds (no model call anywhere: the queue is plumbing, not a brain):

- ``summarize`` — payload ``{"messages": [{"author", "text"}]}``;
  the deterministic ``summarize_messages`` runs and the result is
  recorded as a room summary, attributed to the requester.
- ``note`` — payload ``{"content", "visibility"}``; records one
  room-memory note (visibility ``room`` by default, ``private``
  for the requester's own eyes only).

Only owners and members may enqueue or run jobs: guests are
read-only (``GuestReadOnlyError``), non-members get
``NotRoomMemberError``. The room itself is the principal for a
job's work — the handler runs as the requester, never as another
member — so a job can never spend another member's personal
memory or approve anything on their behalf.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.users import resolve_dsn

from .errors import GuestReadOnlyError, NotRoomMemberError
from .memory import RoomMemoryStore
from .rooms import ROLE_GUEST, RoomStore
from .tasks import summarize_messages

__all__ = [
    "JOB_KINDS",
    "JOB_STATUSES",
    "RoomJob",
    "RoomJobQueue",
    "create_schema",
]

JOB_KINDS = ("summarize", "note")
JOB_STATUSES = ("queued", "processing", "done", "failed")


@dataclass(frozen=True)
class RoomJob:
    """One queued job for one room."""

    id: str
    room_id: str
    kind: str
    payload: dict
    status: str
    requested_by: str
    result: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _job_row(row) -> RoomJob:
    payload = row["payload"]
    if isinstance(payload, str):  # psycopg may return JSONB as text
        payload = json.loads(payload)
    return RoomJob(
        id=str(row["id"]),
        room_id=str(row["room_id"]),
        kind=row["kind"],
        payload=dict(payload or {}),
        status=row["status"],
        requested_by=str(row["requested_by"]),
        result=row["result"],
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def create_schema(dsn=None):
    """Create the queue table by applying migration 0024 through
    the migration runner (table DDL lives only in migrations —
    see tests/test_migrations.py::test_no_table_ddl_in_python_code).
    Idempotent; safe to call before every test. The room test
    fixtures apply only migration 0010, so queue tests call this
    themselves; the test database already records 0023 (the memory
    loader runs in conftest), so only 0024 is applied here."""
    from puspa.migrations import apply_migrations

    apply_migrations(resolve_dsn(dsn), target="0024")


class RoomJobQueue:
    """Postgres-backed serial queue, one lane per room."""

    def __init__(
        self,
        dsn=None,
        room_store: RoomStore | None = None,
        memory_store: RoomMemoryStore | None = None,
    ):
        self.dsn = resolve_dsn(dsn)
        self.rooms = room_store or RoomStore(dsn)
        self.memory = memory_store or RoomMemoryStore(dsn)

    def _require_writer(self, room_id: str, user_id: str) -> None:
        """Owner/member only. Guests are read-only; outsiders are
        not members at all."""
        role = self.rooms.role_of(room_id, user_id)
        if role is None:
            raise NotRoomMemberError(f"user {user_id} is not a member of room {room_id}")
        if role == ROLE_GUEST:
            raise GuestReadOnlyError(f"guest {user_id} may not write to room {room_id}")

    def enqueue(self, room_id: str, kind: str, payload: dict, *, requested_by: str) -> RoomJob:
        """Queue one job. Returns the queued row.

        Raises ``ValueError`` for an unknown kind or a payload that
        is not a JSON object, ``GuestReadOnlyError`` for a guest,
        ``NotRoomMemberError`` for a non-member.
        """
        if kind not in JOB_KINDS:
            raise ValueError(f"unknown job kind: {kind!r}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        if kind == "summarize" and not isinstance(payload.get("messages"), list):
            raise ValueError("summarize jobs need payload.messages as a list")
        if kind == "note" and not str(payload.get("content") or "").strip():
            raise ValueError("note jobs need a non-empty payload.content")
        self.rooms.require_room(room_id)
        self._require_writer(room_id, requested_by)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO room_job_queue (room_id, kind, payload, requested_by) "
                "VALUES (%s, %s, %s::jsonb, %s) RETURNING *",
                (room_id, kind, json.dumps(payload), requested_by),
            )
            job = _job_row(cur.fetchone())
            conn.commit()
        return job

    def list_jobs(self, room_id: str, *, viewer_user_id: str) -> list[RoomJob]:
        """Every job of the room, oldest first (queue order). Any
        member may read, including guests."""
        self.rooms.require_room(room_id)
        if not self.rooms.is_member(room_id, viewer_user_id):
            raise NotRoomMemberError(f"user {viewer_user_id} is not a member of room {room_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM room_job_queue WHERE room_id = %s ORDER BY created_at ASC, id",
                (room_id,),
            )
            return [_job_row(row) for row in cur.fetchall()]

    def get_job(self, job_id: str) -> RoomJob | None:
        """One job by id, or None when it does not exist."""
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM room_job_queue WHERE id = %s", (job_id,))
            row = cur.fetchone()
            return _job_row(row) if row is not None else None

    def process_next(self, room_id: str, *, actor_user_id: str, handler=None) -> RoomJob | None:
        """Run the room's oldest queued job, serialised by the room
        advisory lock. Returns the finished job, or None when the
        queue is empty.

        ``handler`` is an optional ``job -> str | None`` override
        (tests use it to watch the serialisation); the default
        handler runs the job's kind (see the module docstring).
        """
        self.rooms.require_room(room_id)
        self._require_writer(room_id, actor_user_id)
        with _connect(self.dsn) as conn:
            conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (room_id,))
            try:
                job = self._claim_oldest(conn, room_id)
                if job is None:
                    return None
                return self._run_claimed(conn, job, handler)
            finally:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (room_id,))

    def run_job(self, job_id: str, *, actor_user_id: str, handler=None) -> RoomJob:
        """Run one specific queued job, under the same room lock.
        Raises ``ValueError`` when the job is not queued any more."""
        job = self.get_job(job_id)
        if job is None:
            raise ValueError(f"job not found: {job_id}")
        self.rooms.require_room(job.room_id)
        self._require_writer(job.room_id, actor_user_id)
        with _connect(self.dsn) as conn:
            conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (job.room_id,))
            try:
                claimed = self._claim(conn, job.id)
                if claimed is None:
                    raise ValueError(f"job is not queued: {job_id}")
                return self._run_claimed(conn, claimed, handler)
            finally:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (job.room_id,))

    def _claim_oldest(self, conn, room_id: str) -> RoomJob | None:
        """Claim the oldest queued job for the room. The caller
        holds the room advisory lock, so no other worker can claim
        the same job."""
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE room_job_queue SET status = 'processing', started_at = now() "
                "WHERE id = (SELECT id FROM room_job_queue WHERE room_id = %s "
                "AND status = 'queued' ORDER BY created_at ASC, id LIMIT 1) "
                "RETURNING *",
                (room_id,),
            )
            row = cur.fetchone()
            conn.commit()
            return _job_row(row) if row is not None else None

    def _claim(self, conn, job_id: str) -> RoomJob | None:
        """Claim one job by id, only when it is still queued."""
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE room_job_queue SET status = 'processing', started_at = now() "
                "WHERE id = %s AND status = 'queued' RETURNING *",
                (job_id,),
            )
            row = cur.fetchone()
            conn.commit()
            return _job_row(row) if row is not None else None

    def _run_claimed(self, conn, job: RoomJob, handler) -> RoomJob:
        """Run a claimed job and record its outcome. The room
        advisory lock is still held by the caller for the whole
        run, so a second worker for the same room waits outside."""
        try:
            result = handler(job) if handler is not None else self._default_handler(job)
        except Exception as exc:  # a failed job must not kill the queue
            return self._finish(conn, job.id, status="failed", error=str(exc))
        return self._finish(conn, job.id, status="done", result=result)

    def _default_handler(self, job: RoomJob) -> str:
        """The built-in handler for a job kind."""
        if job.kind == "summarize":
            summary = summarize_messages(job.payload.get("messages") or [])
            self.memory.record_summary(
                job.room_id, author_user_id=job.requested_by, summary=summary
            )
            return summary
        if job.kind == "note":
            memory = self.memory.record_memory(
                job.room_id,
                author_user_id=job.requested_by,
                content=str(job.payload.get("content") or ""),
                kind="note",
                visibility=str(job.payload.get("visibility") or "room"),
            )
            return memory.id
        raise ValueError(f"unknown job kind: {job.kind!r}")  # pragma: no cover

    def _finish(
        self,
        conn,
        job_id: str,
        *,
        status: str,
        result: str | None = None,
        error: str | None = None,
    ) -> RoomJob:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE room_job_queue SET status = %s, result = %s, error = %s, "
                "finished_at = now() WHERE id = %s RETURNING *",
                (status, result, error, job_id),
            )
            row = cur.fetchone()
            conn.commit()
            return _job_row(row)
