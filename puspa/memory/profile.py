"""Profil — lapis 1 memori (definisi 2.7, C53, C58, C59).

Pola yang diadaptasi (ditulis ulang, bukan disalin):
- mem0 (Apache-2.0): ekstrak fakta dari percakapan, scoped per user,
  fakta punya asal; Puspa memakai asal tiga nilai revisi 7 dan
  menyimpan fakta disimpulkan di Usulan, bukan di profil.
- Graphiti (Apache-2.0): fakta temporal — tiap fakta punya masa
  berlaku; Puspa memakai `berlaku_dari`/`berlaku_sampai`, satu versi
  berlaku, versi lama tetap bisa dilihat.
- NusaNara (proyek Nero): merge deterministik UNION.

Aturan yang ditegakkan kode ini:
- Fakta `disimpulkan` TIDAK masuk profil: status `usulan`, hanya
  masuk profil setelah user mengonfirmasi.
- Fakta baru yang diucapkan/dikonfirmasi user menggantikan versi lama
  dengan kunci sama — tapi versi lama TIDAK dihapus: statusnya
  `diganti`, `berlaku_sampai` diisi, riwayat tetap bisa dibaca.
  Konflik tidak pernah ditimpa diam-diam: pemanggil mendapat
  `konflik_dengan` (id fakta lama) di hasil.
- Profil inti (agen_id NULL, dipegang Puspa) terlihat semua agen;
  fakta per agen hanya terlihat agen itu + Puspa (agen_id NULL).
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from .schema import resolve_dsn

SOURCE_VALUES = ("user_stated", "user_confirmed", "inferred")
STATUS_ACTIVE = "active"
STATUS_PROPOSED = "proposed"


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _row_to_fact(row):
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "agent_id": str(row["agent_id"]) if row["agent_id"] else None,
        "key": row["key"],
        "value": row["value"],
        "source": row["source"],
        "status": row["status"],
        "valid_from": row["valid_from"],
        "valid_until": row["valid_until"],
        "event_id": str(row["event_id"]) if row["event_id"] else None,
        "created_at": row["created_at"],
    }


def add_fact(user_id, key, value, source, agent_id=None, event_id=None, dsn=None):
    """Tambah fakta. Kembalikan dict fakta + `konflik_dengan` (id atau None).

    - `asal='disimpulkan'` -> status `usulan` (tidak masuk profil).
    - `asal` lain -> status `berlaku`; fakta aktif dengan kunci (+agen)
      yang sama diganti (status `diganti`, `berlaku_sampai`=now).
    """
    if source not in SOURCE_VALUES:
        raise ValueError(f"asal tidak dikenal: {source!r}")
    if not key or not isinstance(key, str):
        raise ValueError("kunci harus string tak kosong")
    if not isinstance(value, str) or value == "":
        raise ValueError("nilai harus string tak kosong")
    status = STATUS_PROPOSED if source == "inferred" else STATUS_ACTIVE
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            konflik_dengan = None
            if status == STATUS_ACTIVE:
                cur.execute(
                    "SELECT id, value FROM profile_facts WHERE user_id = %s "
                    "AND key = %s AND status = 'active' "
                    "AND (agent_id IS NOT DISTINCT FROM %s)",
                    (user_id, key, agent_id),
                )
                for old in cur.fetchall():
                    konflik_dengan = str(old["id"])
                    cur.execute(
                        "UPDATE profile_facts SET status = 'replaced', "
                        "valid_until = now() WHERE id = %s",
                        (old["id"],),
                    )
            cur.execute(
                "INSERT INTO profile_facts (user_id, agent_id, key, value, "
                "source, status, event_id) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "RETURNING *",
                (user_id, agent_id, key, value, source, status, event_id),
            )
            row = cur.fetchone()
        conn.commit()
    fact = _row_to_fact(row)
    fact["konflik_dengan"] = konflik_dengan
    return fact


def get_profile(user_id, agent_id=None, dsn=None):
    """Profil aktif: dict kunci -> nilai.

    Selalu memuat profil inti (agen_id NULL, milik Puspa); ditambah
    fakta agen itu sendiri kalau `agen_id` diberikan.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            if agent_id is None:
                cur.execute(
                    "SELECT key, value FROM profile_facts WHERE user_id = %s "
                    "AND agent_id IS NULL AND status = 'active'",
                    (user_id,),
                )
            else:
                cur.execute(
                    "SELECT key, value FROM profile_facts WHERE user_id = %s "
                    "AND (agent_id IS NULL OR agent_id = %s) AND status = 'active'",
                    (user_id, agent_id),
                )
            return {r["key"]: r["value"] for r in cur.fetchall()}


def list_facts(user_id, key=None, status=None, include_history=False, dsn=None):
    """Daftar fakta user; `include_history=True` memuat versi `diganti`."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            query = "SELECT * FROM profile_facts WHERE user_id = %s"
            params = [user_id]
            if key is not None:
                query += " AND key = %s"
                params.append(key)
            if status is not None:
                query += " AND status = %s"
                params.append(status)
            elif not include_history:
                query += " AND status IN ('berlaku', 'usulan')"
            query += " ORDER BY created_at ASC"
            cur.execute(query, params)
            return [_row_to_fact(r) for r in cur.fetchall()]


def list_proposals(user_id, dsn=None):
    """Usulan: fakta disimpulkan yang menunggu konfirmasi user."""
    return list_facts(user_id, status=STATUS_PROPOSED, dsn=dsn)


def confirm_proposal(fact_id, user_id, dsn=None):
    """User mengonfirmasi Usulan -> masuk profil (`berlaku`).

    Kalau ada fakta aktif dengan kunci sama, fakta lama diganti
    (riwayat dipertahankan). Raise KeyError kalau usulan tidak ada.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM profile_facts WHERE id = %s AND user_id = %s "
                "AND status = 'usulan'",
                (fact_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"usulan tidak ditemukan: {fact_id}")
            cur.execute(
                "UPDATE profile_facts SET status = 'replaced', "
                "valid_until = now() WHERE user_id = %s AND key = %s "
                "AND status = 'active' AND (agent_id IS NOT DISTINCT FROM %s)",
                (user_id, row["key"], row["agent_id"]),
            )
            cur.execute(
                "UPDATE profile_facts SET status = 'active', source = 'user_confirmed' "
                "WHERE id = %s RETURNING *",
                (fact_id,),
            )
            updated = cur.fetchone()
        conn.commit()
    return _row_to_fact(updated)


def reject_proposal(fact_id, user_id, dsn=None):
    """User menolak Usulan -> status `ditolak` (tetap tercatat)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE profile_facts SET status = 'rejected', valid_until = now() "
                "WHERE id = %s AND user_id = %s AND status = 'proposed' RETURNING *",
                (fact_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"usulan tidak ditemukan: {fact_id}")
        conn.commit()
    return _row_to_fact(row)


def correct_fact(user_id, key, value, agent_id=None, event_id=None, dsn=None):
    """Koreksi user: sama dengan tambah fakta `dikonfirmasi_user`."""
    return add_fact(
        user_id, key, value, "user_confirmed",
        agent_id=agent_id, event_id=event_id, dsn=dsn,
    )


def delete_fact(fact_id, user_id, dsn=None):
    """Hapus fakta dari profil: status `ditolak`, riwayat tetap ada.

    Penghapusan keras hanya lewat hapus akun (consent.account).
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE profile_facts SET status = 'rejected', valid_until = now() "
                "WHERE id = %s AND user_id = %s AND status IN ('active', 'proposed') "
                "RETURNING *",
                (fact_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"fakta tidak ditemukan: {fact_id}")
        conn.commit()
    return _row_to_fact(row)
