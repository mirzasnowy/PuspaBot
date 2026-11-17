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
KEDALUWARSA_HOUR = 24


class ApprovalError(Exception):
    """Persetujuan tidak ditemukan, sudah diputuskan, atau kedaluwarsa."""


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def content_hash(action: dict) -> str:
    """Hash isi aksi persis (kanonik, urut kunci) — pengikat persetujuan."""
    kanonik = json.dumps(action, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(kanonik.encode("utf-8")).hexdigest()


def request_approval(*, jalan_id: str, workflow_id: str, user_id: str,
                      action: dict, sekarang: datetime | None = None,
                      dsn=None) -> dict:
    """Antrekan satu aksi keluar dari jalan alur. Kembalikan baris
    persetujuan (status 'menunggu', kedaluwarsa +24 jam)."""
    sekarang = sekarang or datetime.now(timezone.utc)
    expires_at = sekarang + timedelta(hours=KEDALUWARSA_HOUR)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO workflow_approvals (jalan_id, workflow_id, user_id, action,"
                " content_hash, status, created_at, expires_at)"
                " VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)"
                " RETURNING id, jalan_id, workflow_id, status, content_hash, expires_at",
                (jalan_id, workflow_id, user_id, json.dumps(action),
                 content_hash(action), sekarang, expires_at))
            row = cur.fetchone()
        conn.commit()
    return dict(row)


def take_approval(approval_id: str, *, user_id: str, dsn=None) -> dict | None:
    """Baca satu persetujuan — hanya milik user itu (isolasi §2.15)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM workflow_approvals WHERE id = %s AND user_id = %s",
                (approval_id, user_id))
            row = cur.fetchone()
    return dict(row) if row else None


def periksa_kedaluwarsa(approval_id: str, *, user_id: str,
                        sekarang: datetime | None = None, dsn=None) -> dict:
    """Tandai 'kedaluwarsa' bila sudah lewat 24 jam dan masih menunggu.
    Kembalikan baris terbaru (apa pun statusnya)."""
    sekarang = sekarang or datetime.now(timezone.utc)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workflow_approvals SET status = 'expired'"
                " WHERE id = %s AND user_id = %s AND status = 'pending'"
                " AND expires_at <= %s RETURNING *",
                (approval_id, user_id, sekarang))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "SELECT * FROM workflow_approvals WHERE id = %s AND user_id = %s",
                    (approval_id, user_id))
                row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def putuskan(*, approval_id: str, user_id: str, setuju: bool,
             action_kini: dict | None = None,
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
                "SELECT * FROM workflow_approvals WHERE id = %s AND user_id = %s",
                (approval_id, user_id))
            row = cur.fetchone()
            if row is None:
                raise ApprovalError("persetujuan tidak ditemukan")
            if row["status"] != "pending":
                raise ApprovalError(
                    f"persetujuan sudah {row['status']}")
            if row["expires_at"] <= sekarang:
                cur.execute(
                    "UPDATE workflow_approvals SET status = 'expired'"
                    " WHERE id = %s RETURNING *", (approval_id,))
                row = cur.fetchone()
                conn.commit()
                raise ApprovalError(
                    "approval already expired (24h) - action cancelled")
            if action_kini is not None and content_hash(action_kini) != row["content_hash"]:
                cur.execute(
                    "UPDATE workflow_approvals SET status = 'cancelled'"
                    " WHERE id = %s RETURNING *", (approval_id,))
                row = cur.fetchone()
                conn.commit()
                raise ApprovalError(
                    "action content changed after being requested - approval void, request again")
            status_new = "granted" if setuju else "rejected"
            cur.execute(
                "UPDATE workflow_approvals SET status = %s WHERE id = %s RETURNING *",
                (status_new, approval_id))
            row = cur.fetchone()
        conn.commit()
    return dict(row)


def items_pending(user_id: str, dsn=None) -> list[dict]:
    """Antrean persetujuan alur yang masih menunggu (layar Tugas)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM workflow_approvals WHERE user_id = %s"
                " AND status = 'pending' ORDER BY created_at",
                (user_id,))
            return [dict(r) for r in cur.fetchall()]
