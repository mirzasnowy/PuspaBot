"""Tabel milik modul (§2.13 revisi 7).

- Kolom bertipe: teks, angka, Rupiah, tanggal, pilihan.
- Maks 5 tabel per modul; dipisahkan per pengguna dan per modul.
- Modul TIDAK BISA membaca data modul lain atau memori agen, kecuali
  lewat irisan yang diberikan user — `baca_tabel` menolak bila
  modul_id / user_id tidak cocok (isolasi ditegakkan kode, bukan
  hanya RLS).
"""

from __future__ import annotations

import json
import os
from datetime import date

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
COLUMN_TYPES = ("text", "number", "Rupiah", "date", "choice")


class TableError(Exception):
    """Tabel modul tidak valid, tidak ditemukan, atau di luar isolasi."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row
    )


def create_table(
    *, module_id: str, user_id: str, name: str, columns: list, dsn=None, _conn=None
) -> dict:
    """Buat satu tabel milik modul. Dipanggil dari buat_modul (dalam
    transaksi yang sama) atau langsung."""
    if not isinstance(columns, list) or not columns:
        raise TableError("kolom wajib daftar tak kosong")
    for column in columns:
        if column.get("type") not in COLUMN_TYPES:
            raise TableError(f"tipe kolom {column.get('type')!r} tidak diizinkan")
    if _conn is not None:
        with _conn.cursor() as cur:
            cur.execute(
                "INSERT INTO module_tables (module_id, user_id, name, kolom)"
                " VALUES (%s, %s, %s, %s) RETURNING id, module_id, user_id, name, kolom",
                (module_id, user_id, name, json.dumps(columns)),
            )
            row = dict(cur.fetchone())
        return _row(row)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO module_tables (module_id, user_id, name, kolom)"
                " VALUES (%s, %s, %s, %s) RETURNING id, module_id, user_id, name, kolom",
                (module_id, user_id, name, json.dumps(columns)),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return _row(row)


def _row(row: dict) -> dict:
    row = dict(row)
    if "kolom" in row:
        row["columns"] = row.pop("kolom")
    for column in ("id", "module_id", "user_id", "table_id"):
        if column in row and row[column] is not None:
            row[column] = str(row[column])
    return row


def list_tables(module_id: str, *, user_id: str, dsn=None) -> list[dict]:
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM module_tables WHERE module_id = %s AND user_id = %s ORDER BY name",
            (module_id, user_id),
        )
        return [_row(r) for r in cur.fetchall()]


def _get_table(table_id: str, dsn=None) -> dict | None:
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM module_tables WHERE id = %s", (table_id,))
        row = cur.fetchone()
    return _row(row) if row else None


def validate_value(columns: dict, value) -> str | None:
    """Periksa satu nilai terhadap tipe kolom. Kembalikan pesan masalah
    atau None bila lolos. `Rupiah` = angka >= 0."""
    column_type = columns.get("type")
    name = columns.get("name")
    if column_type == "text":
        return None if isinstance(value, str) else f"kolom {name!r} harus teks"
    if column_type in ("number", "Rupiah"):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return f"kolom {name!r} harus angka"
        if column_type == "Rupiah" and value < 0:
            return f"kolom {name!r} (Rupiah) tidak boleh negatif"
        return None
    if column_type == "date":
        if isinstance(value, str):
            try:
                date.fromisoformat(value)
                return None
            except ValueError:
                return f"kolom {name!r} harus tanggal ISO (YYYY-MM-DD)"
        return f"kolom {name!r} harus tanggal ISO (YYYY-MM-DD)"
    if column_type == "choice":
        choice = columns.get("choice") or []
        return None if value in choice else (f"kolom {name!r} harus salah satu {choice}")
    return f"tipe kolom {column_type!r} tidak dikenal"


def add_rows(table_id: str, *, user_id: str, data: dict, dsn=None) -> dict:
    """Tambah satu baris ke tabel milik modul. Nilai diperiksa terhadap
    tipe kolom; kolom yang tidak ada di skema ditolak."""
    table = _get_table(table_id, dsn=dsn)
    if table is None:
        raise TableError("tabel tidak ditemukan")
    if str(table["user_id"]) != str(user_id):
        raise TableError("tabel milik pengguna lain — isolasi menolak")
    columns = {column["name"]: column for column in table["columns"]}
    for key in data:
        if key not in columns:
            raise TableError(f"kolom {key!r} tidak ada di tabel {table['name']!r}")
    problems = [
        m for column, v in data.items() if (m := validate_value(columns[column], v)) is not None
    ]
    if problems:
        raise TableError("; ".join(problems))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO module_rows (table_id, module_id, user_id, data)"
                " VALUES (%s, %s, %s, %s) RETURNING id, data",
                (table_id, table["module_id"], user_id, json.dumps(data)),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return {"id": str(row["id"]), "data": row["data"]}


def read_table(table_id: str, *, user_id: str, dsn=None) -> list[dict]:
    """Baca semua baris satu tabel — hanya milik user + modul itu."""
    table = _get_table(table_id, dsn=dsn)
    if table is None:
        raise TableError("tabel tidak ditemukan")
    if str(table["user_id"]) != str(user_id):
        raise TableError("tabel milik pengguna lain — isolasi menolak")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT data FROM module_rows WHERE table_id = %s AND user_id = %s ORDER BY created_at",
            (table_id, user_id),
        )
        return [r["data"] for r in cur.fetchall()]


def read_table_other_module(
    table_id: str, *, requesting_module_id: str, user_id: str, dsn=None
) -> list[dict]:
    """Modul A mencoba membaca tabel milik modul B — SELALU ditolak
    (§2.13). Pengecualian hanya lewat irisan yang diberikan user
    secara eksplisit (belum ada di MVP — dicatat sebagai batas)."""
    table = _get_table(table_id, dsn=dsn)
    if table is None:
        raise TableError("tabel tidak ditemukan")
    if str(table["module_id"]) != str(requesting_module_id):
        raise TableError(
            "modul tidak bisa membaca data modul lain (§2.13) — "
            "kecuali lewat irisan yang diberikan user"
        )
    return read_table(table_id, user_id=user_id, dsn=dsn)
