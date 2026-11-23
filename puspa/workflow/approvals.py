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
from datetime import UTC, datetime, timedelta

from puspa.db import connect as _db_connect
from puspa.db import dict_row

DEFAULT_DSN = None
EXPIRY_HOURS = 24


class ApprovalError(Exception):
    """Persetujuan tidak ditemukan, sudah diputuskan, atau kedaluwarsa."""


def _connect(dsn=None):
    return _db_connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row
    )


def content_hash(action: dict) -> str:
    """Hash isi aksi persis (kanonik, urut kunci) — pengikat persetujuan."""
    canonical = json.dumps(action, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_approval(row: dict) -> dict:
    """Baris persetujuan dengan kunci Inggris (`jalan_id` -> `run_id`)."""
    row = dict(row)
    if "jalan_id" in row:
        row["run_id"] = row.pop("jalan_id")
    return row


def request_approval(
    *,
    run_id: str,
    workflow_id: str,
    user_id: str,
    action: dict,
    now: datetime | None = None,
    dsn=None,
) -> dict:
    """Antrekan satu aksi keluar dari jalan alur. Kembalikan baris
    persetujuan (status 'menunggu', kedaluwarsa +24 jam)."""
    now = now or datetime.now(UTC)
    expires_at = now + timedelta(hours=EXPIRY_HOURS)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO workflow_approvals (jalan_id, workflow_id, user_id, action,"
                " content_hash, status, created_at, expires_at)"
                " VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)"
                " RETURNING id, jalan_id, workflow_id, status, content_hash, expires_at",
                (
                    run_id,
                    workflow_id,
                    user_id,
                    json.dumps(action),
                    content_hash(action),
                    now,
                    expires_at,
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_approval(row)


def get_approval(approval_id: str, *, user_id: str, dsn=None) -> dict | None:
    """Baca satu persetujuan — hanya milik user itu (isolasi §2.15)."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM workflow_approvals WHERE id = %s AND user_id = %s",
            (approval_id, user_id),
        )
        row = cur.fetchone()
    return _row_approval(row) if row else None


def check_expiry(
    approval_id: str, *, user_id: str, now: datetime | None = None, dsn=None
) -> dict | None:
    """Tandai 'kedaluwarsa' bila sudah lewat 24 jam dan masih menunggu.
    Kembalikan baris terbaru (apa pun statusnya)."""
    now = now or datetime.now(UTC)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workflow_approvals SET status = 'expired'"
                " WHERE id = %s AND user_id = %s AND status = 'pending'"
                " AND expires_at <= %s RETURNING *",
                (approval_id, user_id, now),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "SELECT * FROM workflow_approvals WHERE id = %s AND user_id = %s",
                    (approval_id, user_id),
                )
                row = cur.fetchone()
        conn.commit()
    return _row_approval(row) if row else None


def decide(
    *,
    approval_id: str,
    user_id: str,
    approved: bool,
    current_action: dict | None = None,
    now: datetime | None = None,
    dsn=None,
) -> dict:
    """User menyetujui / menolak. Aturan:
    - sudah kedaluwarsa (lewat 24 jam) → status jadi 'kedaluwarsa',
      ApprovalError (aksi dibatalkan, bukan dieksekusi terlambat);
    - isi aksi kini berbeda dari isi saat diminta (hash beda) →
      persetujuan batal + ApprovalError (minta ulang);
    - sudah diputuskan sebelumnya → ApprovalError.
    Kembalikan baris terbaru bila berhasil diputuskan.
    """
    now = now or datetime.now(UTC)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM workflow_approvals WHERE id = %s AND user_id = %s",
                (approval_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ApprovalError("persetujuan tidak ditemukan")
            if row["status"] != "pending":
                raise ApprovalError(f"persetujuan sudah {row['status']}")
            if row["expires_at"] <= now:
                cur.execute(
                    "UPDATE workflow_approvals SET status = 'expired' WHERE id = %s RETURNING *",
                    (approval_id,),
                )
                row = cur.fetchone()
                conn.commit()
                raise ApprovalError("approval already expired (24h) - action cancelled")
            if current_action is not None and content_hash(current_action) != row["content_hash"]:
                cur.execute(
                    "UPDATE workflow_approvals SET status = 'cancelled' WHERE id = %s RETURNING *",
                    (approval_id,),
                )
                row = cur.fetchone()
                conn.commit()
                raise ApprovalError(
                    "action content changed after being requested - approval void, request again"
                )
            status_new = "granted" if approved else "rejected"
            cur.execute(
                "UPDATE workflow_approvals SET status = %s WHERE id = %s RETURNING *",
                (status_new, approval_id),
            )
            row = cur.fetchone()
        conn.commit()
    return _row_approval(row)


def list_pending(user_id: str, dsn=None) -> list[dict]:
    """Antrean persetujuan alur yang masih menunggu (layar Tugas)."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM workflow_approvals WHERE user_id = %s"
            " AND status = 'pending' ORDER BY created_at",
            (user_id,),
        )
        return [_row_approval(r) for r in cur.fetchall()]
