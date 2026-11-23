"""Buku Kerja (definisi 2.3): log kejadian append-only berantai hash.

Satu-satunya sumber tulisan untuk semua yang terjadi di Puspa; chat, Kantor,
audit, bukti, biaya, dan graf adalah proyeksi dari log ini.

Catatan pemetaan isolasi pengguna (CONTRACTS.md): tabel `kejadian` tidak punya
`user_id`; isolasi pengguna lewat `agen_id`, yang terikat ke `user_id` pemilik
agen (pemetaan agen -> pemilik dicatat di paket `accounts`, fase 0).

Catatan urutan rantai: kontrak menulis "urut `id`", tapi `id` dibuat dengan
`gen_random_uuid()` sehingga tidak terurut menurut waktu sisip — mengurutkan
rantai hanya dengan `id` akan memutus rantai secara acak. Yang dipakai di sini
adalah urutan penyisipan, yaitu `(waktu, id)`: `waktu` dari `now()` di DB
(resolusi mikrodetik, praktis tidak pernah seri antar transaksi) dan `id`
sebagai penentu seri. Ini dicatat sebagai selisih dari kontrak untuk STATUS.md.

Bitemporal note (K3, `docs/bitemporal-state.md`): every event also
carries `valid_at` — when the described event was true in the
world — next to `created_at` (when the event was recorded, the
chain order). `valid_at` is metadata alongside the hashed
payload: `compute_event_hash` does not take it and never did,
so the existing hash chain still verifies after the column is
added. Old entries get `valid_at = created_at` (honest backfill:
the past has no separately recorded valid time). New entries
may state a different `valid_at` (e.g. a payment recorded late),
defaulting to `created_at`. There is no 32nd event type: the
closed set of 31 revision-7 types stays closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from uuid import UUID

from puspa.db import (
    Connection as _db_Connection,
)
from puspa.db import Jsonb, dict_row
from puspa.db import connect as _db_connect
from puspa.db import errors as pg_errors
from puspa.db import maybe_begin_immediate as _db_begin_immediate
from puspa.db import parse_timestamp as _db_parse_timestamp
from puspa.migrations import apply_migrations

from .errors import InvalidEventTypeError, LedgerError

#: Role names must match this: ``SET ROLE`` takes an identifier, so
#: a role name is validated and interpolated, never passed as a
#: value parameter (a parameter would be read as a string literal
#: and rejected by Postgres).
_ROLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

#: Nilai `hash_sebelumnya` untuk kejadian pertama dalam satu tugas.
GENESIS = "GENESIS"

#: Peran aplikasi: hanya INSERT + SELECT pada tabel `kejadian`.
APP_ROLE = "puspa_app"

#: DSN default (peer auth sebagai user OS, tanpa password).
DEFAULT_DSN = None

#: Himpunan event_type kejadian tertutup — 33 event_type (definisi 2.3 revisi 7:
#: 22 event_type lama + 9 event_type baru alur/modul/pemicu/laporan/persetujuan,
#: plus 2 event_type ambil alih dari P-09: takeover_started, takeover_released).
EVENT_TYPES = frozenset(
    {
        "task_created",
        "plan_created",
        "subagent_created",
        "handoff_proposed",
        "handoff_accepted",
        "handoff_handed_over",
        "handoff_rejected",
        "handoff_expired",
        "heartbeat",
        "tool_called",
        "tool_completed",
        "evidence_recorded",
        "verification_completed",
        "approval_requested",
        "approval_granted",
        "approval_rejected",
        "user_message",
        "agent_message",
        "budget_exhausted",
        "task_completed",
        "task_failed",
        "task_stopped",
        "workflow_proposed",
        "workflow_activated",
        "workflow_paused",
        "trigger_fired",
        "trigger_missed",
        "module_proposed",
        "module_activated",
        "false_report",
        "data_consent",
        "takeover_started",
        "takeover_released",
    }
)


def canonical_json(obj) -> str:
    """JSON kanonis: kunci terurut, tanpa spasi, untuk hash yang stabil."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _time_iso(created_at: datetime) -> str:
    """Stempel waktu sebagai ISO-8601 UTC — kebal terhadap timezone sesi DB."""
    return created_at.astimezone(UTC).isoformat()


def compute_event_hash(
    *,
    task_id: str,
    agent_id: str,
    subagent_id: str | None,
    event_type: str,
    content: dict,
    parent_id: str | None,
    previous_hash: str,
    created_at: datetime,
) -> str:
    """sha256 dari JSON kanonis isi kejadian (tanpa `id`, yang acak)."""
    payload = {
        "task_id": task_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
        "type": event_type,
        "content": content,
        "parent_id": parent_id,
        "previous_hash": previous_hash,
        "time_iso": _time_iso(created_at),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _row_to_event(row: dict) -> dict:
    """Baris DB -> dict kejadian (UUID jadi string, `waktu` tetap datetime).

    `valid_at` is read defensively: a database that predates the
    K3 migration has no such column, and those rows report
    `valid_at` as None instead of failing.
    """
    return {
        "id": str(row["id"]),
        "created_at": row["created_at"],
        "valid_at": row.get("valid_at"),
        "task_id": row["task_id"],
        "agent_id": row["agent_id"],
        "subagent_id": row["subagent_id"],
        "type": row["type"],
        "content": row["content"],
        "parent_id": str(row["parent_id"]) if row["parent_id"] is not None else None,
        "previous_hash": row["previous_hash"],
        "hash": row["hash"],
    }


class Ledger:
    """Buku Kerja: append-only, rantai hash per tugas."""

    def __init__(self, dsn: str | None = None, *, role: str | None = None):
        """``role`` (K12): when given, every connection runs as that
        Postgres role (``SET ROLE`` right after connecting), so the
        ledger is written through the role's privileges — the agent
        role ``puspa_app`` can INSERT and SELECT but never UPDATE or
        DELETE (see migration 0001_core). ``None`` (the default) keeps
        the old behaviour: the connection runs as the DSN user.
        ``create_schema`` always runs as the DSN user, never as the
        role: schema setup is an operator step."""
        if role is not None and not _ROLE_NAME_RE.match(role):
            raise LedgerError(f"invalid role name: {role!r}")
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
        self.role = role

    def _connect(self, **kwargs):
        conn = _db_connect(self.dsn, row_factory=dict_row, **kwargs)
        if self.role is not None:
            with conn.cursor() as cur:
                cur.execute(f"SET ROLE {self.role}")
        return conn

    def create_schema(self) -> None:
        """Apply the ledger tables (migration 0001_core).

        Always as the DSN user (the table owner), never as ``role``:
        schema setup is an operator step, not an agent step.
        """
        apply_migrations(self.dsn, target="0001")

    def append_event(
        self,
        *,
        task_id: str,
        agent_id: str,
        subagent_id: str | None = None,
        event_type: str,
        content: dict,
        parent_id: str | None = None,
        initiator: dict | None = None,
        valid_at: datetime | None = None,
    ) -> dict:
        """Menambah satu kejadian; kembalikan baris yang dibaca kembali dari DB.

        `waktu` diambil dari DB (`now()`) di transaksi yang sama dengan INSERT,
        jadi nilai yang masuk hash sama persis dengan yang tersimpan.
        `hash_sebelumnya` = hash kejadian sebelumnya dalam tugas yang sama
        (urut penyisipan), atau "GENESIS" untuk kejadian pertama.

        `initiator` (G6, audit trail): optional attribute
        ``{"kind", "id"}`` — who or what started the run (person,
        deployment, routine, responsibility, memory, handoff).
        Stored inside `content`, NOT a new event type: the closed
        set of 31 types is untouched.

        `valid_at` (K3, bitemporal): when the described event was
        true in the world. Defaults to the recorded time
        (`created_at`) when the caller does not know better, and
        must be a timezone-aware datetime when given. It is
        stored in its own column, OUTSIDE the hashed payload:
        the hash inputs are exactly as before, so appending with
        a `valid_at` never changes any hash and never breaks the
        chain. On a database that predates the K3 migration (no
        `valid_at` column yet) the append still succeeds and the
        event reads back with `valid_at` None — run
        `create_schema()` (migration 0001_core) to
        get the column.
        """
        content, valid_at = self._prepare_append(
            task_id=task_id,
            agent_id=agent_id,
            event_type=event_type,
            content=content,
            parent_id=parent_id,
            initiator=initiator,
            valid_at=valid_at,
        )

        try:
            event_id = self._insert_event(
                task_id=task_id,
                agent_id=agent_id,
                subagent_id=subagent_id,
                event_type=event_type,
                content=content,
                parent_id=parent_id,
                valid_at=valid_at,
                with_valid_at=True,
            )
        except pg_errors.UndefinedColumn:
            # Database predates the K3 migration: no valid_at
            # column yet. Retry without it; the event still
            # appends and the chain still holds.
            event_id = self._insert_event(
                task_id=task_id,
                agent_id=agent_id,
                subagent_id=subagent_id,
                event_type=event_type,
                content=content,
                parent_id=parent_id,
                valid_at=None,
                with_valid_at=False,
            )

        # Baca kembali dari DB (waktu dari DB, bukan dari memori).
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
            row = cur.fetchone()
        if row is None:  # tidak mungkin terjadi; pertahanan terakhir
            raise LedgerError(f"kejadian {event_id} hilang setelah INSERT")
        return _row_to_event(row)

    def append_event_on(
        self,
        conn,
        *,
        task_id: str,
        agent_id: str,
        subagent_id: str | None = None,
        event_type: str,
        content: dict,
        parent_id: str | None = None,
        initiator: dict | None = None,
        valid_at: datetime | None = None,
    ) -> dict:
        """Append one event on a caller-supplied connection.

        Same validation and hash chain as :meth:`append_event`,
        but the INSERT runs on ``conn`` and this method never
        commits: the caller owns the transaction. That is the
        seam ``puspa.memory.commit_turn`` uses to write the
        ledger entry and the durable memory in a single
        transaction: when the memory write fails, the caller
        rolls back and this event never lands. The row is read
        back on the same connection, so the caller sees the
        uncommitted event. ``conn`` needs the dict row factory
        (a plain connection still works: the read-back uses an
        explicit ``dict_row`` cursor).
        """
        content, valid_at = self._prepare_append(
            task_id=task_id,
            agent_id=agent_id,
            event_type=event_type,
            content=content,
            parent_id=parent_id,
            initiator=initiator,
            valid_at=valid_at,
        )
        event_id = self._insert_event_on(
            conn,
            task_id=task_id,
            agent_id=agent_id,
            subagent_id=subagent_id,
            event_type=event_type,
            content=content,
            parent_id=parent_id,
            valid_at=valid_at,
            with_valid_at=None,
        )
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
            row = cur.fetchone()
        if row is None:  # tidak mungkin terjadi; pertahanan terakhir
            raise LedgerError(f"kejadian {event_id} hilang setelah INSERT")
        return _row_to_event(row)

    def _prepare_append(
        self,
        *,
        task_id: str,
        agent_id: str,
        event_type: str,
        content: dict,
        parent_id: str | None,
        initiator: dict | None,
        valid_at: datetime | None,
    ) -> tuple[dict, datetime | None]:
        """Validate one append; return the stored content and valid time.

        Shared by :meth:`append_event` and
        :meth:`append_event_on`, so both paths reject the same
        bad input with the same error. The returned content
        carries the initiator when one was given (see
        :meth:`append_event`).
        """
        if not isinstance(task_id, str) or not task_id:
            raise LedgerError("task_id harus string tak kosong")
        if not isinstance(agent_id, str) or not agent_id:
            raise LedgerError("agent_id harus string tak kosong")
        if event_type not in EVENT_TYPES:
            raise InvalidEventTypeError(event_type)
        if not isinstance(content, dict):
            raise LedgerError("isi harus dict")
        if valid_at is not None and (not isinstance(valid_at, datetime) or valid_at.tzinfo is None):
            raise LedgerError("valid_at must be a timezone-aware datetime")
        try:
            canonical_json(content)
        except (TypeError, ValueError) as exc:
            raise LedgerError(f"isi tidak bisa dijadikan JSON: {exc}") from exc
        if initiator is not None:
            # Validated here (lazy import: audit.py lives in this
            # same package, but ledger.py must not import it at
            # module level — audit.py imports from ledger).
            from .audit import INITIATOR_KINDS, attach_initiator

            if not isinstance(initiator, dict) or initiator.get("kind") not in INITIATOR_KINDS:
                raise LedgerError(
                    f"unknown initiator kind: {initiator!r} "
                    f"(must be one of {sorted(INITIATOR_KINDS)})"
                )
            content = attach_initiator(content, initiator)
        if parent_id is not None:
            try:
                UUID(str(parent_id))
            except ValueError as exc:
                raise LedgerError(f"id_induk bukan UUID: {parent_id!r}") from exc
        return content, valid_at

    def _insert_event(
        self,
        *,
        task_id: str,
        agent_id: str,
        subagent_id: str | None,
        event_type: str,
        content: dict,
        parent_id: str | None,
        valid_at: datetime | None,
        with_valid_at: bool,
    ) -> UUID:
        """One append transaction; returns the new event id.

        Opens its own connection and commits: the
        :meth:`append_event` path. Callers that share one
        transaction across stores use :meth:`_insert_event_on`
        directly and commit themselves.
        """
        with self._connect() as conn:
            event_id = self._insert_event_on(
                conn,
                task_id=task_id,
                agent_id=agent_id,
                subagent_id=subagent_id,
                event_type=event_type,
                content=content,
                parent_id=parent_id,
                valid_at=valid_at,
                with_valid_at=with_valid_at,
            )
            conn.commit()
        return event_id

    def _insert_event_on(
        self,
        conn,
        *,
        task_id: str,
        agent_id: str,
        subagent_id: str | None,
        event_type: str,
        content: dict,
        parent_id: str | None,
        valid_at: datetime | None,
        with_valid_at: bool | None,
    ) -> UUID:
        """Insert one event on ``conn``; no commit.

        The per-task lock (advisory lock on Postgres,
        ``BEGIN IMMEDIATE`` on SQLite), the DB `now()`, the
        previous hash, the hash computation, and the INSERT are
        one transaction owned by the caller, so the value that
        enters the hash is exactly the value that is stored.
        ``with_valid_at`` selects the INSERT shape: True forces
        the column, False leaves it out (a database that predates
        the K3 migration), None detects the column first with a
        catalog lookup, so no failed statement ever poisons the
        caller's transaction (a failed statement would abort it:
        Postgres aborts the whole transaction on the first error).
        """
        with conn.cursor(row_factory=dict_row) as cur:
            if with_valid_at is None:
                if isinstance(conn, _db_Connection):
                    # SQLite has no information_schema: read the
                    # column list straight from the table. The rows
                    # follow the connection's row factory (dicts
                    # when dict_row was requested, tuples otherwise),
                    # so the name is read by key or by position.
                    info = conn.execute("PRAGMA table_info(events)").fetchall()
                    with_valid_at = any(
                        (row["name"] if isinstance(row, dict) else row[1]) == "valid_at"
                        for row in info
                    )
                else:
                    cur.execute(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'events' AND column_name = 'valid_at'"
                    )
                    with_valid_at = cur.fetchone() is not None
            # One writer per task: on Postgres the advisory lock,
            # on SQLite BEGIN IMMEDIATE (serialises all appends,
            # which is right for the single-user desktop).
            if isinstance(conn, _db_Connection):
                _db_begin_immediate(conn)
            else:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (task_id,))
            cur.execute("SELECT now() AS created_at")
            created_at = _db_parse_timestamp(cur.fetchone()["created_at"])
            cur.execute(
                "SELECT hash FROM events WHERE task_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (task_id,),
            )
            prev = cur.fetchone()
            previous_hash = prev["hash"] if prev else GENESIS
            event_hash = compute_event_hash(
                task_id=task_id,
                agent_id=agent_id,
                subagent_id=subagent_id,
                event_type=event_type,
                content=content,
                parent_id=str(parent_id) if parent_id is not None else None,
                previous_hash=previous_hash,
                created_at=created_at,
            )
            if with_valid_at:
                cur.execute(
                    "INSERT INTO events "
                    "(created_at, valid_at, task_id, agent_id, subagent_id, type, "
                    " content, parent_id, previous_hash, hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id",
                    (
                        created_at,
                        valid_at if valid_at is not None else created_at,
                        task_id,
                        agent_id,
                        subagent_id,
                        event_type,
                        Jsonb(content),
                        parent_id,
                        previous_hash,
                        event_hash,
                    ),
                )
            else:
                cur.execute(
                    "INSERT INTO events "
                    "(created_at, task_id, agent_id, subagent_id, type, content, "
                    " parent_id, previous_hash, hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id",
                    (
                        created_at,
                        task_id,
                        agent_id,
                        subagent_id,
                        event_type,
                        Jsonb(content),
                        parent_id,
                        previous_hash,
                        event_hash,
                    ),
                )
            return cur.fetchone()["id"]

    def events(self, task_id: str, event_type: str | None = None) -> list[dict]:
        """Semua kejadian satu tugas, urut penyisipan; opsional saring `event_type`."""
        if event_type is not None and event_type not in EVENT_TYPES:
            raise InvalidEventTypeError(event_type)
        with self._connect() as conn, conn.cursor() as cur:
            if event_type is None:
                cur.execute(
                    "SELECT * FROM events WHERE task_id = %s ORDER BY created_at ASC, id ASC",
                    (task_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM events WHERE task_id = %s AND type = %s "
                    "ORDER BY created_at ASC, id ASC",
                    (task_id, event_type),
                )
            rows = cur.fetchall()
        return [_row_to_event(row) for row in rows]

    def recent_events(self, since: datetime, *, limit: int = 10000) -> list[dict]:
        """All events created at or after `since`, insertion order.

        Read-only window query for the sentinel scanner
        (`puspa/sentinel/`), which scans across tasks instead of
        one task at a time. `since` should be timezone-aware
        (UTC); a naive value is read as UTC by Postgres.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM events WHERE created_at >= %s "
                "ORDER BY created_at ASC, id ASC LIMIT %s",
                (since, limit),
            )
            rows = cur.fetchall()
        return [_row_to_event(row) for row in rows]

    def events_for_agent(self, agent_id: str, *, limit: int = 100) -> list[dict]:
        """Newest-first events of one agent, capped (P-09 live view).

        The live view reads the agent's recent ledger events to
        show what the bot is doing right now and to build the
        replayable frame P-10 replays later. ``limit`` is the
        number of newest events kept; the caller reverses them
        into oldest-first order for display and replay.
        """
        if not isinstance(agent_id, str) or not agent_id:
            raise LedgerError("agent_id harus string tak kosong")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise LedgerError("limit must be a positive integer")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM events WHERE agent_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (agent_id, limit),
            )
            rows = cur.fetchall()
        return [_row_to_event(row) for row in rows]

    def events_by_valid_time(
        self,
        task_id: str,
        *,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
    ) -> list[dict]:
        """Events of one task ordered by valid time (K3, bitemporal).

        Answers "what happened in the world between these two
        moments", in the order the events were true — not the
        order they were recorded. A payment that happened on
        Monday but was recorded on Friday sorts with Monday.
        Bounds are half-open: ``valid_from <= valid_at < valid_to``;
        omit a bound for open-ended. Both bounds must be
        timezone-aware when given. Rows without a recorded
        valid time (a database that predates the K3 migration)
        fall back to their recorded time (`created_at`), so the
        query works on unmigrated databases too.
        """
        for name, bound in (("valid_from", valid_from), ("valid_to", valid_to)):
            if bound is not None and (not isinstance(bound, datetime) or bound.tzinfo is None):
                raise LedgerError(f"{name} must be a timezone-aware datetime")
        query = "SELECT * FROM events WHERE task_id = %s"
        params: list[object] = [task_id]
        if valid_from is not None:
            query += " AND COALESCE(valid_at, created_at) >= %s"
            params.append(valid_from)
        if valid_to is not None:
            query += " AND COALESCE(valid_at, created_at) < %s"
            params.append(valid_to)
        query += " ORDER BY COALESCE(valid_at, created_at) ASC, created_at ASC, id ASC"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return [_row_to_event(row) for row in rows]

    def verify_chain(self, task_id: str) -> tuple[bool, list[str]]:
        """Periksa rantai hash satu tugas.

        Kembalikan (True, []) kalau utuh, atau (False, [id kejadian yang
        putus]) — putus karena `hash_sebelumnya` tidak cocok dengan hash
        kejadian sebelumnya, atau hash yang dihitung ulang tidak cocok dengan
        hash tersimpan (isi/waktu/field lain diubah diam-diam).
        """
        rows = self.events(task_id)
        broken: list[str] = []
        expected_prev = GENESIS
        for ev in rows:
            recomputed = compute_event_hash(
                task_id=ev["task_id"],
                agent_id=ev["agent_id"],
                subagent_id=ev["subagent_id"],
                event_type=ev["type"],
                content=ev["content"],
                parent_id=ev["parent_id"],
                previous_hash=ev["previous_hash"],
                created_at=ev["created_at"],
            )
            if ev["previous_hash"] != expected_prev or recomputed != ev["hash"]:
                broken.append(ev["id"])
            # Tautan berikutnya memakai hash TERSIMPAN, supaya satu kejadian
            # yang diubah hanya menandai dirinya sendiri, bukan seluruh sisa
            # rantai.
            expected_prev = ev["hash"]
        return (not broken, broken)
