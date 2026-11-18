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
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .errors import InvalidEventTypeError, LedgerError

#: Nilai `hash_sebelumnya` untuk kejadian pertama dalam satu tugas.
GENESIS = "GENESIS"

#: Peran aplikasi: hanya INSERT + SELECT pada tabel `kejadian`.
APP_ROLE = "puspa_app"

#: DSN default (peer auth sebagai user OS, tanpa password).
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: Himpunan event_type kejadian tertutup — 31 event_type (definisi 2.3 revisi 7:
#: 22 event_type lama + 9 event_type baru alur/modul/pemicu/laporan/persetujuan).
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
    """Baris DB -> dict kejadian (UUID jadi string, `waktu` tetap datetime)."""
    return {
        "id": str(row["id"]),
        "created_at": row["created_at"],
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

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)

    def _connect(self, **kwargs):
        return psycopg.connect(self.dsn, row_factory=dict_row, **kwargs)

    def create_schema(self) -> None:
        """Menjalankan `schema.sql` (CREATE TABLE IF NOT EXISTS + REVOKE)."""
        sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        with self._connect(autocommit=True) as conn:
            conn.execute(sql)

    def append_event(
        self,
        *,
        task_id: str,
        agent_id: str,
        subagent_id: str | None = None,
        event_type: str,
        content: dict,
        parent_id: str | None = None,
    ) -> dict:
        """Menambah satu kejadian; kembalikan baris yang dibaca kembali dari DB.

        `waktu` diambil dari DB (`now()`) di transaksi yang sama dengan INSERT,
        jadi nilai yang masuk hash sama persis dengan yang tersimpan.
        `hash_sebelumnya` = hash kejadian sebelumnya dalam tugas yang sama
        (urut penyisipan), atau "GENESIS" untuk kejadian pertama.
        """
        if not isinstance(task_id, str) or not task_id:
            raise LedgerError("task_id harus string tak kosong")
        if not isinstance(agent_id, str) or not agent_id:
            raise LedgerError("agent_id harus string tak kosong")
        if event_type not in EVENT_TYPES:
            raise InvalidEventTypeError(event_type)
        if not isinstance(content, dict):
            raise LedgerError("isi harus dict")
        try:
            canonical_json(content)
        except (TypeError, ValueError) as exc:
            raise LedgerError(f"isi tidak bisa dijadikan JSON: {exc}") from exc
        if parent_id is not None:
            try:
                UUID(str(parent_id))
            except ValueError as exc:
                raise LedgerError(f"id_induk bukan UUID: {parent_id!r}") from exc

        with self._connect() as conn:
            with conn.cursor() as cur:
                # Kunci per tugas: dua append bersamaan tidak boleh bercabang.
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (task_id,))
                cur.execute("SELECT now() AS created_at")
                created_at = cur.fetchone()["created_at"]
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
                event_id = cur.fetchone()["id"]
            conn.commit()

        # Baca kembali dari DB (waktu dari DB, bukan dari memori).
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
            row = cur.fetchone()
        if row is None:  # tidak mungkin terjadi; pertahanan terakhir
            raise LedgerError(f"kejadian {event_id} hilang setelah INSERT")
        return _row_to_event(row)

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
