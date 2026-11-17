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

TEKS_APPROVAL = """Persetujuan data Puspa (versi v1-2026-11-17)

Data yang disimpan: chat dengan agen, memori (profil, riwayat,
prosedur), nomor pelanggan yang Anda berikan, token Gmail, Drive,
dan Calendar Anda.

Di mana: di server Puspa (PostgreSQL + vault terenkripsi). Token
disimpan terenkripsi; kunci master tidak ada di database.

Berapa lama: selama akun Anda aktif. Cadangan disimpan 14 hari.
Tangkapan layar bukti dihapus setelah 30 hari.

Siapa bisa melihat: hanya Anda dan agen Anda. Operator hanya melihat
metadata dan biaya, bukan isi chat atau memori. Akses dukungan butuh
izin Anda per kasus dan dicatat.

Ekspor dan hapus: Anda bisa mengekspor semua data Anda kapan saja,
dan menghapus akun Anda (memori, alur, modul, dan token ikut terhapus;
catatan audit tetap sebagai tombstone tanpa isi).

Graf bersama: fakta harga publik dari perbandingan Anda masuk graf
bersama TANPA identitas Anda.

Dengan menyetujui, Anda mengizinkan Puspa menyimpan dan memakai data
di atas untuk menjalankan tugas Anda.
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
            task_id=task_id, agent_id=agent_id, event_type="data_consents",
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
