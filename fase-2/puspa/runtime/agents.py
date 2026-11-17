"""Registry agen (§2.2 revisi 7).

Aturan yang ditegakkan di sini (kode, bukan UI):
- Maks 3 agen per pengguna (Puspa + 2 buatan user).
- Puspa wajib dan tidak bisa dihapus: penolakan di API (`hapus_agen`)
  dan di database (trigger `trg_tolak_hapus_puspa` di `agen_meta`:
  DELETE baris `wajib=TRUE` melempar exception).
- Agen baru mulai tanpa konektor (§2.8); Puspa mulai dengan Gmail,
  Drive, Calendar (diberikan di `ensure_puspa_agent`).

Keputusan Fase 2 (pertanyaan Fase 0, butir a): pendaftaran agen Puspa
per pengguna WAJIB — satu agen Puspa dibuat otomatis saat pengguna
dibuat / login pertama (`ensure_puspa_agent`). Alasan: pembaca
bermilik Fase 0 menolak fail-closed kejadian dari agen yang belum
terdaftar, jadi agen tanpa Puspa terdaftar akan membuat semua
kejadian tugasnya ditolak. Fungsi ini idempotent.
"""

from __future__ import annotations

import os
import uuid

import psycopg

from puspa.accounts.access import register_agent

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
MAX_AGEN = 3
NAMA_PUSPA = "Puspa"


class AgentError(Exception):
    """Kegagalan registry agen (batas, hapus Puspa, pemilik salah)."""


def _connect(dsn=None):
    return psycopg.connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN))


def _agen_id_puspa(user_id: str) -> str:
    """ID agen Puspa deterministik per pengguna (stabil lintas login)."""
    return f"puspa-{user_id}"


def ensure_puspa_agent(user_id: str, dsn=None) -> str:
    """Buat + daftarkan agen Puspa pengguna ini kalau belum ada.

    Kembalikan `agen_id`. Idempotent. Juga memberikan ketiga konektor
    bawaan Puspa (Gmail, Drive, Calendar) lewat `konektor_agen`.
    """
    agen_id = _agen_id_puspa(str(user_id))
    register_agent(agen_id, str(user_id), dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agen_meta (agen_id, user_id, nama, peran, tujuan, wajib)"
                " VALUES (%s, %s, %s, %s, %s, TRUE)"
                " ON CONFLICT (agen_id) DO NOTHING",
                (agen_id, user_id, NAMA_PUSPA, "agen utama",
                 "Agen wajib Puspa: memegang profil inti user dan menjadi fallback."),
            )
            for konektor, scope in (
                ("gmail", ["baca", "buat_draf", "kirim"]),
                ("drive", ["baca"]),
                ("calendar", ["baca", "buat_event"]),
            ):
                cur.execute(
                    "INSERT INTO konektor_agen (user_id, agen_id, konektor, scope, terhubung)"
                    " VALUES (%s, %s, %s, %s, TRUE)"
                    " ON CONFLICT (user_id, agen_id, konektor) DO NOTHING",
                    (user_id, agen_id, konektor, scope),
                )
        conn.commit()
    return agen_id


def buat_agen(user_id: str, nama: str, peran: str = "", tujuan: str = "", dsn=None) -> dict:
    """Buat agen baru milik pengguna. Agen ke-4 ditolak (maks 3)."""
    if not nama or not nama.strip():
        raise AgentError("nama agen tidak boleh kosong")
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agen_meta WHERE user_id = %s", (user_id,))
            if cur.fetchone()[0] >= MAX_AGEN:
                raise AgentError(f"maks {MAX_AGEN} agen per pengguna")
    agen_id = f"agen-{uuid.uuid4().hex[:12]}"
    register_agent(agen_id, str(user_id), dsn=dsn)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agen_meta (agen_id, user_id, nama, peran, tujuan, wajib)"
                " VALUES (%s, %s, %s, %s, %s, FALSE)",
                (agen_id, user_id, nama.strip(), peran, tujuan),
            )
        conn.commit()
    return {"agen_id": agen_id, "nama": nama.strip(), "peran": peran,
            "tujuan": tujuan, "wajib": False}


def daftar_agen(user_id: str, dsn=None) -> list[dict]:
    """Semua agen milik pengguna, Puspa wajib selalu pertama."""
    with _connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT agen_id, nama, peran, tujuan, wajib FROM agen_meta"
                " WHERE user_id = %s ORDER BY wajib DESC, dibuat ASC",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def hapus_agen(user_id: str, agen_id: str, dsn=None) -> None:
    """Hapus agen. Puspa (wajib) DITOLAK; agen orang lain DITOLAK."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT wajib, user_id FROM agen_meta WHERE agen_id = %s", (agen_id,)
            )
            row = cur.fetchone()
            if row is None:
                raise AgentError(f"agen tidak dikenal: {agen_id!r}")
            if str(row[1]) != str(user_id):
                raise AgentError("agen milik pengguna lain")
            if row[0]:
                raise AgentError("Puspa tidak bisa dihapus")
            cur.execute("DELETE FROM agen_meta WHERE agen_id = %s", (agen_id,))
            cur.execute("DELETE FROM agen WHERE agen_id = %s", (agen_id,))
        conn.commit()
