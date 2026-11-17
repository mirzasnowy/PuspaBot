"""Prosedural — lapis 3 memori (definisi 2.7 C55, 3.3).

Cara menyelesaikan tugas yang pernah berhasil, disimpan sebagai
prosedur. Di MVP prosedur ditulis OTOMATIS dari tugas yang berhasil,
tanpa editor prosedur. Graf Alur Layanan penuh (canary, pemakaian
ulang lintas agen, verifikasi silang) dibangun Fase 3 — yang dibangun
di sini: jalur tulis prosedural + penyimpanan + siklus hidup status
dasar (percobaan -> terverifikasi -> rusak).

Pola yang diadaptasi (ditulis ulang, bukan disalin):
- Letta (Apache-2.0): sleep-time compute — agen kedua merapikan
  memori saat idle; Puspa memakai pola yang sama di `consolidate.py`.
- Graphiti (Apache-2.0): prosedur sebagai templat berparameter tanpa
  data pribadi; nomor pelanggan tidak pernah masuk prosedur.

Siklus hidup di sini (angka awal, revisi 7 §3.3):
- `percobaan`: lahir dari tugas sukses yang lolos Pemeriksa.
- `terverifikasi`: setelah 3 jalan sukses, oleh minimal 2 agen berbeda.
- `rusak`: 2 jalan gagal berturut-turut; prosedur rusak tidak dipakai.
"""

from __future__ import annotations

import hashlib
import json

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .schema import resolve_dsn

SYARAT_VERIFIED_JALAN = 3
SYARAT_VERIFIED_AGENT = 2
SYARAT_BROKEN_FAILED_BERTURUT = 2


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _row_to_procedure(row):
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "service": row["service"],
        "versi": row["versi"],
        "step": row["step"],
        "output_schema": row["output_schema"],
        "required_checks": row["required_checks"],
        "status": row["status"],
        "successful_runs": row["successful_runs"],
        "failed_runs": row["failed_runs"],
        "consecutive_failed_runs": row["consecutive_failed_runs"],
        "agent_ids": row["agent_ids"],
        "source_event_id": str(row["source_event_id"]) if row["source_event_id"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _step_hash(step):
    canon = json.dumps(step, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def record_successful_task(user_id, service, step, agent_id=None,
                           output_schema=None, required_checks=None,
                           source_event_id=None, dsn=None):
    """Tulis prosedur dari tugas yang berhasil (otomatis, tanpa editor).

    Kalau prosedur dengan layanan + langkah yang sama sudah ada,
    catat satu jalan sukses lagi (bukan duplikat). Prosedur baru
    lahir berstatus `percobaan` dengan 1 jalan sukses.
    """
    if not service or not isinstance(step, list) or not step:
        raise ValueError("layanan dan langkah tak kosong wajib diisi")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM procedures WHERE user_id = %s AND service = %s "
                "ORDER BY created_at DESC",
                (user_id, service),
            )
            existing = None
            for row in cur.fetchall():
                if _step_hash(row["step"]) == _step_hash(step):
                    existing = row
                    break
            if existing is None:
                agent_ids = [str(agent_id)] if agent_id else []
                cur.execute(
                    "INSERT INTO procedures (user_id, service, step, "
                    "output_schema, required_checks, status, successful_runs, "
                    "agent_ids, source_event_id) VALUES (%s, %s, %s, %s, %s, "
                    "'trial', 1, %s, %s) RETURNING *",
                    (user_id, service, Jsonb(step),
                     Jsonb(output_schema or {}),
                     Jsonb(required_checks or []),
                     Jsonb(agent_ids), source_event_id),
                )
                row = cur.fetchone()
            else:
                row = _mark_run_in_cursor(cur, existing, success=True, agent_id=agent_id)
        conn.commit()
    return _row_to_procedure(row)


def _mark_run_in_cursor(cur, row, success, agent_id):
    agent_ids = list(row["agent_ids"] or [])
    if agent_id and str(agent_id) not in agent_ids:
        agent_ids.append(str(agent_id))
    success_new = row["successful_runs"] + (1 if success else 0)
    failed_new = row["failed_runs"] + (0 if success else 1)
    consecutive_failures = 0 if success else row["consecutive_failed_runs"] + 1
    status = row["status"]
    if consecutive_failures >= SYARAT_BROKEN_FAILED_BERTURUT:
        status = "broken"
    elif (
        status == "trial"
        and success_new >= SYARAT_VERIFIED_JALAN
        and len(agent_ids) >= SYARAT_VERIFIED_AGENT
    ):
        status = "verified"
    cur.execute(
        "UPDATE procedures SET successful_runs = %s, failed_runs = %s, "
        "consecutive_failed_runs = %s, agent_ids = %s, status = %s, updated_at = now() "
        "WHERE id = %s RETURNING *",
        (success_new, failed_new, consecutive_failures, Jsonb(agent_ids), status, row["id"]),
    )
    return cur.fetchone()


def mark_run(procedure_id, user_id, success, agent_id=None, dsn=None):
    """Catat satu jalan prosedur (sukses/gagal); kembalikan prosedur baru.

    Raise KeyError kalau prosedur tidak ditemukan.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM procedures WHERE id = %s AND user_id = %s",
                (procedure_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"prosedur tidak ditemukan: {procedure_id}")
            row = _mark_run_in_cursor(cur, row, success=success, agent_id=agent_id)
        conn.commit()
    return _row_to_procedure(row)


def get_procedure(procedure_id, user_id, dsn=None):
    """Ambil satu prosedur milik user; KeyError kalau tidak ada."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM procedures WHERE id = %s AND user_id = %s",
                (procedure_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"prosedur tidak ditemukan: {procedure_id}")
    return _row_to_procedure(row)


def list_procedures(user_id, status=None, dsn=None):
    """Daftar prosedur milik user, terbaru dulu."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            if status is None:
                cur.execute(
                    "SELECT * FROM procedures WHERE user_id = %s ORDER BY created_at DESC",
                    (user_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM procedures WHERE user_id = %s AND status = %s "
                    "ORDER BY created_at DESC",
                    (user_id, status),
                )
            return [_row_to_procedure(r) for r in cur.fetchall()]
