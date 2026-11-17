"""Lapisan pemeriksaan kepemilikan untuk API baca Puspa (definisi 2.6,
jalur serangan ke-8).

Belum ada HTTP API di fase 0 (tidak ada FastAPI): "API" di sini adalah
fungsi-fungsi publik paket-paket Puspa yang akan dipakai PWA nanti.
Modul ini adalah SATU-SATUNYA tempat pemeriksaan kepemilikan tinggal:
setiap API baca WAJIB memanggil `require_owner` SEBELUM menyentuh data —
sebelum query isi, sebelum dekripsi — supaya pengguna A tidak pernah
bisa membaca data pengguna B, dan bahkan keberadaan data B tidak bocor
lewat perbedaan pesan error (penolakan karena kepemilikan selalu
`AccessDeniedError`, penolakan karena data memang tidak ada selalu
`KeyError`).

Aturan fail-closed: `require_owner` menolak kalau salah satu id None,
kosong, atau hanya spasi, dan menolak kalau keduanya berbeda. Tidak ada
jalan "baca dulu, cek belakangan".

Alur dan modul (definisi 2.12/2.13) BELUM punya tabel di fase 0 (baru
fase 4), jadi belum ada pembaca bermilik untuk keduanya di sini. Saat
tabelnya ada, API baca alur/modul memakai `require_owner` yang sama
dengan `owner_user_id` dari baris alur/modul itu — tidak perlu fungsi
baru, cukup panggil `require_owner` sebelum SELECT isi. Lihat
`read_vault_entry` sebagai pola yang harus ditiru.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .user_sessions import owner_of_token, validate_session
from .users import resolve_dsn
from .vault import get as vault_get


class AccessDeniedError(Exception):
    """Pembaca bukan pemilik data. Selalu dipakai untuk penolakan karena
    kepemilikan — JANGAN diganti KeyError/SessionError supaya tes dan
    pemanggil bisa membedakan "data ada tapi bukan milikmu" dari
    "data tidak ada"."""


def require_owner(requesting_user_id, owner_user_id):
    """Tolak kalau pembaca bukan pemilik. Fail-closed.

    Raise AccessDeniedError kalau:
      - salah satu id None, bukan string, kosong, atau hanya spasi;
      - keduanya berbeda (setelah str() + strip, tanpa normalisasi lain).

    Kembalikan None kalau lolos. Fungsi ini TIDAK menyentuh database dan
    TIDAK membaca data apa pun: panggil sebelum query/dekripsi.
    """
    if not isinstance(requesting_user_id, str) or not requesting_user_id.strip():
        raise AccessDeniedError(
            "identitas pembaca tidak valid (None/kosong) — ditolak fail-closed"
        )
    if not isinstance(owner_user_id, str) or not owner_user_id.strip():
        raise AccessDeniedError(
            "identitas pemilik tidak valid (None/kosong) — ditolak fail-closed"
        )
    if requesting_user_id.strip() != owner_user_id.strip():
        raise AccessDeniedError(
            "pembaca bukan pemilik data (jalur serangan ke-8 ditolak)"
        )
    return None


# ======================================================================
# Registry agen -> pemilik (dipakai runtime fase 2 untuk memetakan
# agen ke pemiliknya; di fase 0 dipakai pembaca ledger bermilik).
# ======================================================================


def create_agent_schema(dsn=None):
    """Jalankan schema_agen.sql (CREATE TABLE IF NOT EXISTS agen).

    Tabel `pengguna` harus sudah ada (FK agen.user_id -> pengguna(id));
    panggil setelah `puspa.accounts.create_schema`.
    """
    sql = (Path(__file__).parent / "schema_agent.sql").read_text(encoding="utf-8")
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute(sql)
        conn.commit()


def register_agent(agent_id, user_id, dsn=None):
    """Daftarkan agen ke pemiliknya. Idempotent kalau agen sudah terdaftar
    ke pengguna yang sama.

    Raise AccessDeniedError kalau agen_id sudah terdaftar ke pengguna
    LAIN (agen tidak bisa dibajak/dipindah pemilik lewat registrasi
    ulang). Raise ValueError kalau agen_id/user_id kosong.
    """
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("agent_id harus string tak kosong")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id harus string tak kosong")
    agent_id = agent_id.strip()
    user_id = user_id.strip()
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM agents WHERE agent_id = %s", (agent_id,))
            row = cur.fetchone()
            if row is not None:
                if str(row[0]) != user_id:
                    raise AccessDeniedError(
                        f"agen {agent_id!r} sudah terdaftar ke pengguna lain"
                    )
                return  # sudah terdaftar ke pemilik yang sama: no-op
            cur.execute(
                "INSERT INTO agents (agent_id, user_id) VALUES (%s, %s)",
                (agent_id, user_id),
            )
        conn.commit()


def owner_of(agent_id, dsn=None):
    """Kembalikan user_id (str UUID) pemilik agen, atau None kalau agen
    belum terdaftar. None = pemilik tidak diketahui = ditolak fail-closed
    oleh pembaca bermilik (bukan dianggap milik siapa pun)."""
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM agents WHERE agent_id = %s", (agent_id.strip(),))
            row = cur.fetchone()
    return str(row[0]) if row is not None else None


# ======================================================================
# Fungsi baca bermilik (scoped readers). Pola yang sama untuk semua:
#   1. tentukan pemilik (dari argumen, dari token sesi, atau dari registry)
#   2. require_owner(...) — SEBELUM query isi / dekripsi
#   3. baru baca data milik pemilik itu
# ======================================================================


def read_vault_entry(requesting_user_id, owner_user_id, name, dsn=None):
    """Baca entri vault (token konektor) milik `owner_user_id`.

    `require_owner` jalan SEBELUM `vault.get`, jadi kalau pembaca bukan
    pemilik, tidak ada query ciphertext dan tidak ada dekripsi sama sekali
    (bahkan keberadaan entri tidak bocor: selalu AccessDeniedError).
    Kalau pemiliknya benar tapi nama tidak ada -> KeyError (bukan
    AccessDeniedError), supaya "tidak ada" bisa dibedakan dari "ditolak".
    """
    require_owner(requesting_user_id, owner_user_id)
    return vault_get(owner_user_id, name, dsn=dsn)


def resolve_session_as(requesting_user_id, token, dsn=None):
    """Resolusi sesi atas nama `requesting_user_id`.

    Token sesi ITU SENDIRI adalah kredensial kepemilikan: `owner_of_token`
    memetakan token ke tepat satu pemilik TANPA efek samping (tidak
    memperbarui `terakhir_dilihat` — resolusi di sini hanya pemeriksaan
    kepemilikan, bukan pemakaian sesi; lihat `sessions.owner_of_token`),
    lalu `require_owner` memastikan peminta adalah pemilik itu. Token
    milik B yang dipakai A -> pemilik token adalah B, bukan A ->
    AccessDeniedError, dan sesi B tidak berubah sama sekali. Tidak ada
    data sesi yang dikembalikan ke A (hanya user_id pemilik, dan itu pun
    hanya kalau peminta memang pemiliknya).

    Token tidak dikenal / sudah dicabut -> SessionError (dari
    `owner_of_token`), sebelum pemeriksaan kepemilikan sempat jalan.
    """
    owner_user_id = owner_of_token(token, dsn=dsn)
    require_owner(requesting_user_id, owner_user_id)
    return owner_user_id


def read_ledger_event(requesting_user_id, event_id, dsn=None):
    """Baca SATU kejadian Buku Kerja milik agen pengguna yang meminta.

    Pemilik kejadian diturunkan dari agen pemiliknya (agen_id -> user_id
    lewat registry agen). Urutannya:
      1. cek keberadaan kejadian (hanya id, TANPA isi) — kalau tidak ada
         sama sekali -> KeyError;
      2. turunkan pemilik dari registry; agen tidak terdaftar -> pemilik
         tidak diketahui -> AccessDeniedError (fail-closed);
      3. require_owner(...) — SEBELUM isi kejadian dibaca;
      4. baru SELECT isi dan kembalikan dict kejadian (bentuk sama seperti
         `Ledger.events`: id/waktu/tugas_id/agen_id/sub_agen_id/jenis/isi/
         id_induk/hash_sebelumnya/hash).
    """
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("event_id harus string tak kosong")
    event_id = event_id.strip()
    with psycopg.connect(resolve_dsn(dsn), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # Langkah 1: keberadaan saja, tanpa isi.
            cur.execute("SELECT agent_id FROM events WHERE id = %s", (event_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(event_id)
            agent_id = row["agent_id"]
            # Langkah 2-3: pemilik dari registry, lalu cek kepemilikan.
            # (owner_of membuka koneksi sendiri; registry dibaca sebelum
            # isi kejadian disentuh.)
            owner_user_id = owner_of(agent_id, dsn=dsn)
            require_owner(requesting_user_id, owner_user_id)
            # Langkah 4: baru sekarang isi boleh dibaca.
            cur.execute("SELECT * FROM events WHERE id = %s", (event_id,))
            full = cur.fetchone()
    if full is None:  # tidak mungkin terjadi; pertahanan terakhir
        raise KeyError(event_id)
    return {
        "id": str(full["id"]),
        "created_at": full["created_at"],
        "task_id": full["task_id"],
        "agent_id": full["agent_id"],
        "subagent_id": full["subagent_id"],
        "type": full["type"],
        "content": full["content"],
        "parent_id": str(full["parent_id"]) if full["parent_id"] is not None else None,
        "previous_hash": full["previous_hash"],
        "hash": full["hash"],
    }


def read_ledger_events_for_task(requesting_user_id, task_id, dsn=None):
    """Baca SEMUA kejadian satu tugas milik pengguna yang meminta.

    Kepemilikan tugas = kepemilikan SEMUA agen yang menulis kejadian di
    tugas itu: setiap agen_id berbeda di tugas dicek lewat registry +
    require_owner SEBELUM satu pun isi dikembalikan. Satu saja kejadian
    dari agen pengguna lain -> seluruh tugas ditolak (AccessDeniedError,
    daftar kosong, bukan sebagian).
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id harus string tak kosong")
    task_id = task_id.strip()
    with psycopg.connect(resolve_dsn(dsn), row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT agent_id FROM events WHERE task_id = %s",
                (task_id,),
            )
            agent_ids = [r["agent_id"] for r in cur.fetchall()]
    # Cek kepemilikan dulu — sebelum isi kejadian mana pun dibaca.
    for agent_id in agent_ids:
        require_owner(requesting_user_id, owner_of(agent_id, dsn=dsn))
    # Semua agen milik peminta (atau tugas kosong): baru baca isinya.
    from puspa.ledger import Ledger

    return Ledger(dsn).events(task_id)


__all__ = [
    "AccessDeniedError",
    "create_agent_schema",
    "owner_of",
    "read_ledger_event",
    "read_ledger_events_for_task",
    "read_vault_entry",
    "register_agent",
    "require_owner",
    "resolve_session_as",
]
