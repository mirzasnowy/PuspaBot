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
    from puspa.accounts.sessions import list_sessions

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT teks FROM contoh_gaya WHERE user_id = %s ORDER BY dibuat ASC",
                (user_id,),
            )
            contoh = [r["teks"] for r in cur.fetchall()]
            cur.execute(
                "SELECT * FROM onboarding WHERE user_id = %s", (user_id,)
            )
            onb = cur.fetchone()
    sesi = [
        {k: v for k, v in s.items() if k != "id"}
        for s in list_sessions(user_id, dsn=dsn)
    ]
    return {
        "user_id": str(user_id),
        "profil": profile.get_profile(user_id, dsn=dsn),
        "fakta_riwayat": profile.list_facts(user_id, include_history=True, dsn=dsn),
        "usulan": profile.list_proposals(user_id, dsn=dsn),
        "episodik": episodic.list_episodes(user_id, limit=1000, dsn=dsn),
        "prosedur": procedural.list_procedures(user_id, dsn=dsn),
        "ringkasan": consolidate.list_summaries(user_id, dsn=dsn),
        "contoh_gaya": contoh,
        "persetujuan": list_consents(user_id, dsn=dsn),
        "onboarding": (
            {
                "langkah_saat_ini": onb["langkah_saat_ini"],
                "nama": onb["nama"],
                "gaya": onb["gaya"],
                "konektor": onb["konektor"],
                "tugas_pertama": onb["tugas_pertama"],
                "dimulai": str(onb["dimulai"]),
                "selesai": str(onb["selesai"]) if onb["selesai"] else None,
                "durasi_detik": float(onb["durasi_detik"]) if onb["durasi_detik"] is not None else None,
            }
            if onb else None
        ),
        "sesi": sesi,
    }


def delete_account(user_id, dsn=None):
    """Hapus akun + semua data pengguna di luar Buku Kerja.

    Kembalikan dict jumlah baris yang dihapus per tabel.
    """
    counts = {}
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            for tabel in (
                "fakta_profil", "episodik", "prosedur", "pemberian_irisan",
                "ringkasan", "contoh_gaya", "onboarding", "persetujuan_data",
            ):
                cur.execute(f"DELETE FROM {tabel} WHERE user_id = %s", (user_id,))
                counts[tabel] = cur.rowcount
        conn.commit()
    from puspa.accounts.users import delete_user

    delete_user(user_id, dsn=dsn)
    counts["pengguna"] = 1
    return counts
