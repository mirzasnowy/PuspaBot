"""Lapor Salah (§3.8 revisi 7).

Tiap angka di panel Bukti punya tombol Lapor Salah. Laporan:
- menurunkan Skor Keandalan sumbernya (pembacaan baru ber-vonis
  "Tidak bisa dipastikan" ditulis ke `pembacaan_sumber`),
- memicu baca ulang (flag `baca_ulang_diminta`),
- tercatat di Buku Kerja (jenis `laporan_salah` — sudah ada di ledger
  §2.3).

Umpan balik user ke bukti, melengkapi konfirmasi identitas (§3.2).
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def report_wrong(*, evidence_id: str, user_id: str, reason: str = "",
                sumber: str = "", category: str = "",
                task_id: str, agent_id: str, dsn=None,
                ledger=None) -> dict:
    """Catat laporan salah atas satu angka (bukti_id).

    Kembalikan {"laporan_id", "baca_ulang_diminta": True}. `ledger`
    opsional: instance `Ledger` Fase 0 untuk mencatat kejadian
    `laporan_salah` di Buku Kerja.
    """
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO false_reports (evidence_id, user_id, reason)"
                " VALUES (%s, %s, %s) RETURNING id",
                (evidence_id, user_id, reason))
            laporan_id = str(cur.fetchone()["id"])
            if sumber:
                cur.execute(
                    "INSERT INTO source_readings (user_id, sumber, category, vonis)"
                    " VALUES (%s, %s, %s, 'Cannot be determined')",
                    (user_id, sumber, category))
        conn.commit()
    if ledger is not None:
        ledger.append_event(task_id=task_id, agent_id=agent_id,
                            event_type="false_report",
                            content={"evidence_id": evidence_id, "reason": reason})
    return {"laporan_id": laporan_id, "reread_requested": True}


def items_laporan(evidence_id: str, dsn=None) -> list[dict]:
    """Daftar laporan salah untuk satu bukti."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, reason, reread_requested, created_at"
                " FROM false_reports WHERE evidence_id = %s ORDER BY created_at",
                (evidence_id,))
            return [{"id": str(r["id"]), "user_id": str(r["user_id"]),
                     "reason": r["reason"],
                     "reread_requested": r["reread_requested"],
                     "created_at": r["created_at"]} for r in cur.fetchall()]
