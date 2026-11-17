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

SYARAT_TERVERIFIKASI_JALAN = 3
SYARAT_TERVERIFIKASI_AGEN = 2
SYARAT_RUSAK_GAGAL_BERTURUT = 2


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _row_to_procedure(row):
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "layanan": row["layanan"],
        "versi": row["versi"],
        "langkah": row["langkah"],
        "skema_keluaran": row["skema_keluaran"],
        "pemeriksaan_wajib": row["pemeriksaan_wajib"],
        "status": row["status"],
        "jalan_sukses": row["jalan_sukses"],
        "jalan_gagal": row["jalan_gagal"],
        "jalan_gagal_berturut": row["jalan_gagal_berturut"],
        "agen_ids": row["agen_ids"],
        "kejadian_asal_id": str(row["kejadian_asal_id"]) if row["kejadian_asal_id"] else None,
        "dibuat": row["dibuat"],
        "diubah": row["diubah"],
    }


def _langkah_hash(langkah):
    canon = json.dumps(langkah, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def record_successful_task(user_id, layanan, langkah, agen_id=None,
                           skema_keluaran=None, pemeriksaan_wajib=None,
                           kejadian_asal_id=None, dsn=None):
    """Tulis prosedur dari tugas yang berhasil (otomatis, tanpa editor).

    Kalau prosedur dengan layanan + langkah yang sama sudah ada,
    catat satu jalan sukses lagi (bukan duplikat). Prosedur baru
    lahir berstatus `percobaan` dengan 1 jalan sukses.
    """
    if not layanan or not isinstance(langkah, list) or not langkah:
        raise ValueError("layanan dan langkah tak kosong wajib diisi")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM prosedur WHERE user_id = %s AND layanan = %s "
                "ORDER BY dibuat DESC",
                (user_id, layanan),
            )
            existing = None
            for row in cur.fetchall():
                if _langkah_hash(row["langkah"]) == _langkah_hash(langkah):
                    existing = row
                    break
            if existing is None:
                agen_ids = [str(agen_id)] if agen_id else []
                cur.execute(
                    "INSERT INTO prosedur (user_id, layanan, langkah, "
                    "skema_keluaran, pemeriksaan_wajib, status, jalan_sukses, "
                    "agen_ids, kejadian_asal_id) VALUES (%s, %s, %s, %s, %s, "
                    "'percobaan', 1, %s, %s) RETURNING *",
                    (user_id, layanan, Jsonb(langkah),
                     Jsonb(skema_keluaran or {}),
                     Jsonb(pemeriksaan_wajib or []),
                     Jsonb(agen_ids), kejadian_asal_id),
                )
                row = cur.fetchone()
            else:
                row = _mark_run_in_cursor(cur, existing, sukses=True, agen_id=agen_id)
        conn.commit()
    return _row_to_procedure(row)


def _mark_run_in_cursor(cur, row, sukses, agen_id):
    agen_ids = list(row["agen_ids"] or [])
    if agen_id and str(agen_id) not in agen_ids:
        agen_ids.append(str(agen_id))
    sukses_baru = row["jalan_sukses"] + (1 if sukses else 0)
    gagal_baru = row["jalan_gagal"] + (0 if sukses else 1)
    gagal_berturut = 0 if sukses else row["jalan_gagal_berturut"] + 1
    status = row["status"]
    if gagal_berturut >= SYARAT_RUSAK_GAGAL_BERTURUT:
        status = "rusak"
    elif (
        status == "percobaan"
        and sukses_baru >= SYARAT_TERVERIFIKASI_JALAN
        and len(agen_ids) >= SYARAT_TERVERIFIKASI_AGEN
    ):
        status = "terverifikasi"
    cur.execute(
        "UPDATE prosedur SET jalan_sukses = %s, jalan_gagal = %s, "
        "jalan_gagal_berturut = %s, agen_ids = %s, status = %s, diubah = now() "
        "WHERE id = %s RETURNING *",
        (sukses_baru, gagal_baru, gagal_berturut, Jsonb(agen_ids), status, row["id"]),
    )
    return cur.fetchone()


def mark_run(procedure_id, user_id, sukses, agen_id=None, dsn=None):
    """Catat satu jalan prosedur (sukses/gagal); kembalikan prosedur baru.

    Raise KeyError kalau prosedur tidak ditemukan.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM prosedur WHERE id = %s AND user_id = %s",
                (procedure_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"prosedur tidak ditemukan: {procedure_id}")
            row = _mark_run_in_cursor(cur, row, sukses=sukses, agen_id=agen_id)
        conn.commit()
    return _row_to_procedure(row)


def get_procedure(procedure_id, user_id, dsn=None):
    """Ambil satu prosedur milik user; KeyError kalau tidak ada."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM prosedur WHERE id = %s AND user_id = %s",
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
                    "SELECT * FROM prosedur WHERE user_id = %s ORDER BY dibuat DESC",
                    (user_id,),
                )
            else:
                cur.execute(
                    "SELECT * FROM prosedur WHERE user_id = %s AND status = %s "
                    "ORDER BY dibuat DESC",
                    (user_id, status),
                )
            return [_row_to_procedure(r) for r in cur.fetchall()]
