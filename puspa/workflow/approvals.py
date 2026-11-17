"""Antrean persetujuan alur (§2.12 revisi 7).

- Persetujuan terikat isi (§2.6): hash isi aksi persis. Isi berubah
  setelah disetujui → persetujuan batal, diminta ulang.
- Persetujuan alur terjadwal KEDALUWARSA 24 jam, lalu aksinya
  DIBATALKAN — bukan dieksekusi terlambat.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
KEDALUWARSA_JAM = 24


class ApprovalError(Exception):
    """Persetujuan tidak ditemukan, sudah diputuskan, atau kedaluwarsa."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def isi_hash(aksi: dict) -> str:
    """Hash isi aksi persis (kanonik, urut kunci) — pengikat persetujuan."""
    kanonik = json.dumps(aksi, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(kanonik.encode("utf-8")).hexdigest()


def minta_persetujuan(*, jalan_id: str, alur_id: str, user_id: str,
                      aksi: dict, sekarang: datetime | None = None,
                      dsn=None) -> dict:
    """Antrekan satu aksi keluar dari jalan alur. Kembalikan baris
    persetujuan (status 'menunggu', kedaluwarsa +24 jam)."""
    sekarang = sekarang or datetime.now(timezone.utc)
    kedaluwarsa = sekarang + timedelta(hours=KEDALUWARSA_JAM)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO persetujuan_alur (jalan_id, alur_id, user_id, aksi,"
                " isi_hash, status, dibuat, kedaluwarsa)"
                " VALUES (%s, %s, %s, %s, %s, 'menunggu', %s, %s)"
                " RETURNING id, jalan_id, alur_id, status, isi_hash, kedaluwarsa",
                (jalan_id, alur_id, user_id, json.dumps(aksi),
                 isi_hash(aksi), sekarang, kedaluwarsa))
            row = cur.fetchone()
        conn.commit()
    return dict(row)


def ambil_persetujuan(persetujuan_id: str, *, user_id: str, dsn=None) -> dict | None:
    """Baca satu persetujuan — hanya milik user itu (isolasi §2.15)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM persetujuan_alur WHERE id = %s AND user_id = %s",
                (persetujuan_id, user_id))
            row = cur.fetchone()
    return dict(row) if row else None


def periksa_kedaluwarsa(persetujuan_id: str, *, user_id: str,
                        sekarang: datetime | None = None, dsn=None) -> dict:
    """Tandai 'kedaluwarsa' bila sudah lewat 24 jam dan masih menunggu.
    Kembalikan baris terbaru (apa pun statusnya)."""
    sekarang = sekarang or datetime.now(timezone.utc)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE persetujuan_alur SET status = 'kedaluwarsa'"
                " WHERE id = %s AND user_id = %s AND status = 'menunggu'"
                " AND kedaluwarsa <= %s RETURNING *",
                (persetujuan_id, user_id, sekarang))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "SELECT * FROM persetujuan_alur WHERE id = %s AND user_id = %s",
                    (persetujuan_id, user_id))
                row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def putuskan(*, persetujuan_id: str, user_id: str, setuju: bool,
             aksi_kini: dict | None = None,
             sekarang: datetime | None = None, dsn=None) -> dict:
    """User menyetujui / menolak. Aturan:
    - sudah kedaluwarsa (lewat 24 jam) → status jadi 'kedaluwarsa',
      ApprovalError (aksi dibatalkan, bukan dieksekusi terlambat);
    - isi aksi kini berbeda dari isi saat diminta (hash beda) →
      persetujuan batal + ApprovalError (minta ulang);
    - sudah diputuskan sebelumnya → ApprovalError.
    Kembalikan baris terbaru bila berhasil diputuskan.
    """
    sekarang = sekarang or datetime.now(timezone.utc)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM persetujuan_alur WHERE id = %s AND user_id = %s",
                (persetujuan_id, user_id))
            row = cur.fetchone()
            if row is None:
                raise ApprovalError("persetujuan tidak ditemukan")
            if row["status"] != "menunggu":
                raise ApprovalError(
                    f"persetujuan sudah {row['status']}")
            if row["kedaluwarsa"] <= sekarang:
                cur.execute(
                    "UPDATE persetujuan_alur SET status = 'kedaluwarsa'"
                    " WHERE id = %s RETURNING *", (persetujuan_id,))
                row = cur.fetchone()
                conn.commit()
                raise ApprovalError(
                    "persetujuan sudah kedaluwarsa (24 jam) — aksi dibatalkan")
            if aksi_kini is not None and isi_hash(aksi_kini) != row["isi_hash"]:
                cur.execute(
                    "UPDATE persetujuan_alur SET status = 'dibatalkan'"
                    " WHERE id = %s RETURNING *", (persetujuan_id,))
                row = cur.fetchone()
                conn.commit()
                raise ApprovalError(
                    "isi aksi berubah setelah diminta — persetujuan batal, minta ulang")
            status_baru = "diberikan" if setuju else "ditolak"
            cur.execute(
                "UPDATE persetujuan_alur SET status = %s WHERE id = %s RETURNING *",
                (status_baru, persetujuan_id))
            row = cur.fetchone()
        conn.commit()
    return dict(row)


def daftar_menunggu(user_id: str, dsn=None) -> list[dict]:
    """Antrean persetujuan alur yang masih menunggu (layar Tugas)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM persetujuan_alur WHERE user_id = %s"
                " AND status = 'menunggu' ORDER BY dibuat",
                (user_id,))
            return [dict(r) for r in cur.fetchall()]
