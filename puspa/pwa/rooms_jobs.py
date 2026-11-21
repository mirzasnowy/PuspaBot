"""Local per-room job queue (broken-rooms fix).

One serial lane per room, backed by the ``room_job_queue`` table:
a job is either a deterministic chat summary or a recorded note —
no model call anywhere. The runner claims a job under a Postgres
advisory lock keyed on the room id, so two workers can never run
two jobs for the same room at once. A failed job is recorded on
its row (``failed`` + error) and never kills the queue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from puspa.accounts.ports import resolve_dsn
from puspa.rooms import RoomMemoryStore, RoomStore
from puspa.rooms.errors import (
    GuestReadOnlyError,
    NotRoomMemberError,
    RoomError,
)
from puspa.rooms.rooms import ROLE_GUEST
from puspa.rooms.tasks import summarize_messages

__all__ = [
    "JOB_KINDS",
    "JOB_STATUSES",
    "RoomJob",
    "enqueue_job",
    "get_job",
    "job_to_dict",
    "list_jobs",
    "run_job",
]

#: Job kinds the local queue runs (no model call anywhere).
JOB_KINDS = ("summarize", "note")
JOB_STATUSES = ("queued", "processing", "done", "failed")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


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


def job_to_dict(job: RoomJob) -> dict:
    return {
        "id": job.id,
        "room_id": job.room_id,
        "kind": job.kind,
        "payload": job.payload,
        "status": job.status,
        "requested_by": job.requested_by,
        "result": job.result,
        "error": job.error,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }


def _require_writer(dsn, room_id: str, user_id: str) -> None:
    """Owner/member only. Guests are read-only; outsiders are not
    members at all."""
    role = RoomStore(dsn).role_of(room_id, user_id)
    if role is None:
        raise NotRoomMemberError(f"user {user_id} is not a member of room {room_id}")
    if role == ROLE_GUEST:
        raise GuestReadOnlyError(f"guest {user_id} may not write to room {room_id}")


def enqueue_job(dsn, room_id: str, kind: str, payload: dict, *, requested_by: str) -> RoomJob:
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind: {kind!r}")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if kind == "summarize" and not isinstance(payload.get("messages"), list):
        raise ValueError("summarize jobs need payload.messages as a list")
    if kind == "note" and not str(payload.get("content") or "").strip():
        raise ValueError("note jobs need a non-empty payload.content")
    RoomStore(dsn).require_room(room_id)
    _require_writer(dsn, room_id, requested_by)
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO room_job_queue (room_id, kind, payload, requested_by) "
            "VALUES (%s, %s, %s::jsonb, %s) RETURNING *",
            (room_id, kind, json.dumps(payload), requested_by),
        )
        job = _job_row(cur.fetchone())
        conn.commit()
    return job


def list_jobs(dsn, room_id: str, *, viewer_user_id: str) -> list[RoomJob]:
    RoomStore(dsn).require_room(room_id)
    if not RoomStore(dsn).is_member(room_id, viewer_user_id):
        raise NotRoomMemberError(f"user {viewer_user_id} is not a member of room {room_id}")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM room_job_queue WHERE room_id = %s ORDER BY created_at ASC, id",
            (room_id,),
        )
        return [_job_row(row) for row in cur.fetchall()]


def get_job(dsn, job_id: str) -> RoomJob | None:
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM room_job_queue WHERE id = %s", (job_id,))
        row = cur.fetchone()
        return _job_row(row) if row is not None else None


def run_job(dsn, job_id: str, *, actor_user_id: str) -> RoomJob:
    job = get_job(dsn, job_id)
    if job is None:
        raise ValueError(f"job not found: {job_id}")
    RoomStore(dsn).require_room(job.room_id)
    _require_writer(dsn, job.room_id, actor_user_id)
    with _connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (job.room_id,))
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE room_job_queue SET status = 'processing', started_at = now() "
                    "WHERE id = %s AND status = 'queued' RETURNING *",
                    (job_id,),
                )
                row = cur.fetchone()
                conn.commit()
            if row is None:
                raise ValueError(f"job is not queued: {job_id}")
            claimed = _job_row(row)
            return _finish_job(dsn, conn, claimed)
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (job.room_id,))


def _finish_job(dsn, conn, job: RoomJob) -> RoomJob:
    try:
        result = _run_job_handler(dsn, job)
    except (RoomError, ValueError, KeyError, TypeError, AttributeError, psycopg.Error) as exc:
        # A failed job must not kill the queue: record the failure
        # on the job row and let the next job run.
        return _mark_job(conn, job.id, status="failed", error=str(exc))
    return _mark_job(conn, job.id, status="done", result=result)


def _run_job_handler(dsn, job: RoomJob) -> str:
    memory = RoomMemoryStore(dsn)
    if job.kind == "summarize":
        summary = summarize_messages(job.payload.get("messages") or [])
        memory.record_summary(job.room_id, author_user_id=job.requested_by, summary=summary)
        return summary
    if job.kind == "note":
        row = memory.record_memory(
            job.room_id,
            author_user_id=job.requested_by,
            content=str(job.payload.get("content") or ""),
            kind="note",
            visibility=str(job.payload.get("visibility") or "room"),
        )
        return row.id
    raise ValueError(f"unknown job kind: {job.kind!r}")


def _mark_job(
    conn, job_id: str, *, status: str, result: str | None = None, error: str | None = None
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
