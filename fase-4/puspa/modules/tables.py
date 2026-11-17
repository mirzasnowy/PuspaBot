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
TIPE_KOLOM = ("teks", "angka", "Rupiah", "tanggal", "pilihan")


class TableError(Exception):
    """Tabel modul tidak valid, tidak ditemukan, atau di luar isolasi."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def buat_tabel(*, modul_id: str, user_id: str, nama: str, kolom: list,
               dsn=None, _conn=None) -> dict:
    """Buat satu tabel milik modul. Dipanggil dari buat_modul (dalam
    transaksi yang sama) atau langsung."""
    if not isinstance(kolom, list) or not kolom:
        raise TableError("kolom wajib daftar tak kosong")
    for k in kolom:
        if k.get("tipe") not in TIPE_KOLOM:
            raise TableError(f"tipe kolom {k.get('tipe')!r} tidak diizinkan")
    if _conn is not None:
        with _conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tabel_modul (modul_id, user_id, nama, kolom)"
                " VALUES (%s, %s, %s, %s) RETURNING id, modul_id, user_id, nama, kolom",
                (modul_id, user_id, nama, json.dumps(kolom)))
            row = dict(cur.fetchone())
        return _row(row)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tabel_modul (modul_id, user_id, nama, kolom)"
                " VALUES (%s, %s, %s, %s) RETURNING id, modul_id, user_id, nama, kolom",
                (modul_id, user_id, nama, json.dumps(kolom)))
            row = dict(cur.fetchone())
        conn.commit()
    return _row(row)


def _row(row: dict) -> dict:
    row = dict(row)
    for k in ("id", "modul_id", "user_id", "tabel_id"):
        if k in row and row[k] is not None:
            row[k] = str(row[k])
    return row


def daftar_tabel(modul_id: str, *, user_id: str, dsn=None) -> list[dict]:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM tabel_modul WHERE modul_id = %s AND user_id = %s"
                " ORDER BY nama", (modul_id, user_id))
            return [_row(r) for r in cur.fetchall()]


def _ambil_tabel(tabel_id: str, dsn=None) -> dict | None:
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tabel_modul WHERE id = %s", (tabel_id,))
            row = cur.fetchone()
    return _row(row) if row else None


def periksa_nilai(kolom: dict, nilai) -> str | None:
    """Periksa satu nilai terhadap tipe kolom. Kembalikan pesan masalah
    atau None bila lolos. `Rupiah` = angka >= 0."""
    tipe = kolom.get("tipe")
    nama = kolom.get("nama")
    if tipe == "teks":
        return None if isinstance(nilai, str) else f"kolom {nama!r} harus teks"
    if tipe in ("angka", "Rupiah"):
        if not isinstance(nilai, (int, float)) or isinstance(nilai, bool):
            return f"kolom {nama!r} harus angka"
        if tipe == "Rupiah" and nilai < 0:
            return f"kolom {nama!r} (Rupiah) tidak boleh negatif"
        return None
    if tipe == "tanggal":
        if isinstance(nilai, str):
            try:
                date.fromisoformat(nilai)
                return None
            except ValueError:
                return f"kolom {nama!r} harus tanggal ISO (YYYY-MM-DD)"
        return f"kolom {nama!r} harus tanggal ISO (YYYY-MM-DD)"
    if tipe == "pilihan":
        pilihan = kolom.get("pilihan") or []
        return None if nilai in pilihan else (
            f"kolom {nama!r} harus salah satu {pilihan}")
    return f"tipe kolom {tipe!r} tidak dikenal"


def tambah_baris(tabel_id: str, *, user_id: str, data: dict, dsn=None) -> dict:
    """Tambah satu baris ke tabel milik modul. Nilai diperiksa terhadap
    tipe kolom; kolom yang tidak ada di skema ditolak."""
    tabel = _ambil_tabel(tabel_id, dsn=dsn)
    if tabel is None:
        raise TableError("tabel tidak ditemukan")
    if str(tabel["user_id"]) != str(user_id):
        raise TableError("tabel milik pengguna lain — isolasi menolak")
    kolom = {k["nama"]: k for k in tabel["kolom"]}
    for kunci in data:
        if kunci not in kolom:
            raise TableError(f"kolom {kunci!r} tidak ada di tabel {tabel['nama']!r}")
    masalah = [m for k, v in data.items()
               if (m := periksa_nilai(kolom[k], v)) is not None]
    if masalah:
        raise TableError("; ".join(masalah))
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO baris_modul (tabel_id, modul_id, user_id, data)"
                " VALUES (%s, %s, %s, %s) RETURNING id, data",
                (tabel_id, tabel["modul_id"], user_id,
                 json.dumps(data)))
            row = dict(cur.fetchone())
        conn.commit()
    return {"id": str(row["id"]), "data": row["data"]}


def baca_tabel(tabel_id: str, *, user_id: str, dsn=None) -> list[dict]:
    """Baca semua baris satu tabel — hanya milik user + modul itu."""
    tabel = _ambil_tabel(tabel_id, dsn=dsn)
    if tabel is None:
        raise TableError("tabel tidak ditemukan")
    if str(tabel["user_id"]) != str(user_id):
        raise TableError("tabel milik pengguna lain — isolasi menolak")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data FROM baris_modul WHERE tabel_id = %s AND user_id = %s"
                " ORDER BY dibuat", (tabel_id, user_id))
            return [r["data"] for r in cur.fetchall()]


def baca_tabel_modul_lain(tabel_id: str, *, modul_id_peminta: str,
                          user_id: str, dsn=None) -> list[dict]:
    """Modul A mencoba membaca tabel milik modul B — SELALU ditolak
    (§2.13). Pengecualian hanya lewat irisan yang diberikan user
    secara eksplisit (belum ada di MVP — dicatat sebagai batas)."""
    tabel = _ambil_tabel(tabel_id, dsn=dsn)
    if tabel is None:
        raise TableError("tabel tidak ditemukan")
    if str(tabel["modul_id"]) != str(modul_id_peminta):
        raise TableError(
            "modul tidak bisa membaca data modul lain (§2.13) — "
            "kecuali lewat irisan yang diberikan user")
    return baca_tabel(tabel_id, user_id=user_id, dsn=dsn)
