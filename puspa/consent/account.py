"""Ekspor + hapus akun (definisi 2.17, C129).

- Ekspor: semua data pengguna (profil, episodik tanpa vektor, prosedur,
  contoh gaya, persetujuan, onboarding, daftar sesi tanpa hash token).
  TIDAK memuat rahasia vault (token konektor) dan TIDAK memuat isi
  Buku Kerja mentah.
- Hapus: menghapus memori, contoh gaya, prosedur, ringkasan, irisan,
  onboarding, persetujuan, dan akun (sesi + vault ikut terhapus lewat
  `users.delete_user` Fase 0). Rantai hash Buku Kerja tetap utuh
  (append-only, tidak ada UPDATE/DELETE di tabel `kejadian`).

BATAS YANG JUJUR (lihat STATUS_FASE1.md): kejadian Buku Kerja milik
pengguna yang dihapus TETAP ADA — `isi`-nya bisa memuat data pengguna.
Penghapusan isi kejadian butuh jenis kejadian tombstone khusus
(proposal revisi 8 / Fase 2); hapus akun Fase 1 menghapus semua data
pengguna DI LUAR Buku Kerja.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from ..memory import consolidate, episodic, procedural, profile
from ..memory.schema import resolve_dsn
from .consent import list_consents


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def export_account(user_id, dsn=None):
    """Ekspor penuh data pengguna sebagai dict (siap JSON)."""
    from puspa.accounts.user_sessions import list_sessions

    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT text FROM style_examples WHERE user_id = %s ORDER BY created_at ASC",
            (user_id,),
        )
        example = [r["text"] for r in cur.fetchall()]
        cur.execute("SELECT * FROM onboarding WHERE user_id = %s", (user_id,))
        onb = cur.fetchone()
    sesi = [{k: v for k, v in s.items() if k != "id"} for s in list_sessions(user_id, dsn=dsn)]
    return {
        "user_id": str(user_id),
        "profile": profile.get_profile(user_id, dsn=dsn),
        "fact_history": profile.list_facts(user_id, include_history=True, dsn=dsn),
        "proposed": profile.list_proposals(user_id, dsn=dsn),
        "episodic": episodic.list_episodes(user_id, limit=1000, dsn=dsn),
        "procedures": procedural.list_procedures(user_id, dsn=dsn),
        "summaries": consolidate.list_summaries(user_id, dsn=dsn),
        "style_examples": example,
        "approval": list_consents(user_id, dsn=dsn),
        "onboarding": (
            {
                "current_step": onb["current_step"],
                "name": onb["name"],
                "style": onb["style"],
                "connector": onb["connector"],
                "first_task": onb["first_task"],
                "started_at": str(onb["started_at"]),
                "completed_at": str(onb["completed_at"]) if onb["completed_at"] else None,
                "duration_seconds": float(onb["duration_seconds"])
                if onb["duration_seconds"] is not None
                else None,
            }
            if onb
            else None
        ),
        "sessions": sesi,
    }


def delete_account(user_id, dsn=None):
    """Hapus akun + semua data pengguna di luar Buku Kerja.

    Kembalikan dict jumlah baris yang dihapus per tabel.
    """
    counts = {}
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            for table in (
                "profile_facts",
                "episodic",
                "procedures",
                "slice_grants",
                "summaries",
                "style_examples",
                "onboarding",
                "data_consents",
            ):
                cur.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
                counts[table] = cur.rowcount
            # Account deletion is the one sanctioned path that removes
            # the mandatory Puspa agent: the database trigger blocks
            # deleting a mandatory agent row, and deleting the user row
            # would cascade into it. Un-mark it first (the account
            # itself is going away); a direct agent delete stays blocked.
            cur.execute(
                "UPDATE agent_meta SET required = FALSE WHERE user_id = %s",
                (user_id,),
            )
        conn.commit()
    from puspa.accounts.users import delete_user

    delete_user(user_id, dsn=dsn)
    counts["users"] = 1
    return counts
