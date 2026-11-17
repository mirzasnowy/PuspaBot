"""Persetujuan data pribadi (definisi 2.17, C127, C128, gerbang 15).

Satu layar pendek: data apa, di mana, berapa lama, siapa bisa
melihat, cara ekspor/hapus, dan bahwa fakta harga publik masuk graf
bersama tanpa identitas. Persetujuan dicatat di Buku Kerja (jenis
kejadian `persetujuan_data` SUDAH ADA di ledger Fase 0) + tabel
`persetujuan_data`.

TEKS DI BAWAH INI VERSI `v1-2026-11-17` — perlu ditinjau ahli hukum
sebelum pengguna di luar lingkaran dekat (keputusan 20). Dokumen ini
BUKAN nasihat hukum; tinjauan hukum dicatat sebagai langkah tertunda
di STATUS_FASE1.md, bukan dikerjakan di sini.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from ..memory.schema import resolve_dsn

TEKS_VERSI = "v1-2026-11-17"

TEXT_APPROVAL = """Puspa data consent (version v1-2026-11-17)

What data is stored: chats with your agent, memory (profile, history,
procedures), the customer numbers you grant, and your Gmail, Drive,
and Calendar tokens.

Where: on the Puspa server (PostgreSQL + encrypted vault). Tokens are
stored encrypted; the master key does not exist in the database.

How long: for as long as your account is active. Backups are kept for
14 days. Evidence screenshots are deleted after 30 days.

Who can see it: only you and your agent. Operators only see metadata
and cost, not chat content or memory. Support access needs your
permission per case and is recorded.

Export and delete: you can export all your data at any time, and
delete your account (memory, workflows, modules, and tokens are
deleted with it; audit records remain as tombstones without content).

Shared graph: public price facts from your comparisons go into the
shared graph WITHOUT your identity.

By agreeing, you allow Puspa to store and use the data above to run
your tasks.
"""


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def record_consent(user_id, dsn=None, ledger=None, agent_id=None, task_id=None):
    """Catat persetujuan user: tabel + (kalau ledger diberi) Buku Kerja.

    Kembalikan dict {persetujuan_id, kejadian_id, versi_teks}.
    """
    event_id = None
    if ledger is not None:
        if agent_id is None or task_id is None:
            raise ValueError("ledger butuh agent_id + task_id")
        ev = ledger.append_event(
            task_id=task_id, agent_id=agent_id, event_type="data_consent",
            content={"text_version": TEKS_VERSI, "user_id": str(user_id)},
        )
        event_id = str(ev["id"])
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO data_consents (user_id, text_version, event_id) "
                "VALUES (%s, %s, %s) RETURNING id",
                (user_id, TEKS_VERSI, event_id),
            )
            row = cur.fetchone()
        conn.commit()
    return {
        "approval_id": str(row["id"]),
        "event_id": event_id,
        "text_version": TEKS_VERSI,
    }


def has_consented(user_id, dsn=None):
    """True kalau user pernah menyetujui (versi apa pun)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM data_consents WHERE user_id = %s LIMIT 1",
                (user_id,),
            )
            return cur.fetchone() is not None


def list_consents(user_id, dsn=None):
    """Riwayat persetujuan user, terbaru dulu."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, text_version, approved_at, event_id "
                "FROM data_consents WHERE user_id = %s "
                "ORDER BY approved_at DESC",
                (user_id,),
            )
            return [
                {
                    "id": str(r["id"]),
                    "user_id": str(r["user_id"]),
                    "text_version": r["text_version"],
                    "approved_at": r["approved_at"],
                    "event_id": str(r["event_id"]) if r["event_id"] else None,
                }
                for r in cur.fetchall()
            ]
