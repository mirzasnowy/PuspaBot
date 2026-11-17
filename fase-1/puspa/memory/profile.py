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

ASAL_VALUES = ("diucapkan_user", "dikonfirmasi_user", "disimpulkan")
STATUS_BERLAKU = "berlaku"
STATUS_USULAN = "usulan"


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _row_to_fact(row):
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "agen_id": str(row["agen_id"]) if row["agen_id"] else None,
        "kunci": row["kunci"],
        "nilai": row["nilai"],
        "asal": row["asal"],
        "status": row["status"],
        "berlaku_dari": row["berlaku_dari"],
        "berlaku_sampai": row["berlaku_sampai"],
        "kejadian_id": str(row["kejadian_id"]) if row["kejadian_id"] else None,
        "dibuat": row["dibuat"],
    }


def add_fact(user_id, kunci, nilai, asal, agen_id=None, kejadian_id=None, dsn=None):
    """Tambah fakta. Kembalikan dict fakta + `konflik_dengan` (id atau None).

    - `asal='disimpulkan'` -> status `usulan` (tidak masuk profil).
    - `asal` lain -> status `berlaku`; fakta aktif dengan kunci (+agen)
      yang sama diganti (status `diganti`, `berlaku_sampai`=now).
    """
    if asal not in ASAL_VALUES:
        raise ValueError(f"asal tidak dikenal: {asal!r}")
    if not kunci or not isinstance(kunci, str):
        raise ValueError("kunci harus string tak kosong")
    if not isinstance(nilai, str) or nilai == "":
        raise ValueError("nilai harus string tak kosong")
    status = STATUS_USULAN if asal == "disimpulkan" else STATUS_BERLAKU
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            konflik_dengan = None
            if status == STATUS_BERLAKU:
                cur.execute(
                    "SELECT id, nilai FROM fakta_profil WHERE user_id = %s "
                    "AND kunci = %s AND status = 'berlaku' "
                    "AND (agen_id IS NOT DISTINCT FROM %s)",
                    (user_id, kunci, agen_id),
                )
                for old in cur.fetchall():
                    konflik_dengan = str(old["id"])
                    cur.execute(
                        "UPDATE fakta_profil SET status = 'diganti', "
                        "berlaku_sampai = now() WHERE id = %s",
                        (old["id"],),
                    )
            cur.execute(
                "INSERT INTO fakta_profil (user_id, agen_id, kunci, nilai, "
                "asal, status, kejadian_id) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "RETURNING *",
                (user_id, agen_id, kunci, nilai, asal, status, kejadian_id),
            )
            row = cur.fetchone()
        conn.commit()
    fact = _row_to_fact(row)
    fact["konflik_dengan"] = konflik_dengan
    return fact


def get_profile(user_id, agen_id=None, dsn=None):
    """Profil aktif: dict kunci -> nilai.

    Selalu memuat profil inti (agen_id NULL, milik Puspa); ditambah
    fakta agen itu sendiri kalau `agen_id` diberikan.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            if agen_id is None:
                cur.execute(
                    "SELECT kunci, nilai FROM fakta_profil WHERE user_id = %s "
                    "AND agen_id IS NULL AND status = 'berlaku'",
                    (user_id,),
                )
            else:
                cur.execute(
                    "SELECT kunci, nilai FROM fakta_profil WHERE user_id = %s "
                    "AND (agen_id IS NULL OR agen_id = %s) AND status = 'berlaku'",
                    (user_id, agen_id),
                )
            return {r["kunci"]: r["nilai"] for r in cur.fetchall()}


def list_facts(user_id, kunci=None, status=None, include_history=False, dsn=None):
    """Daftar fakta user; `include_history=True` memuat versi `diganti`."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            query = "SELECT * FROM fakta_profil WHERE user_id = %s"
            params = [user_id]
            if kunci is not None:
                query += " AND kunci = %s"
                params.append(kunci)
            if status is not None:
                query += " AND status = %s"
                params.append(status)
            elif not include_history:
                query += " AND status IN ('berlaku', 'usulan')"
            query += " ORDER BY dibuat ASC"
            cur.execute(query, params)
            return [_row_to_fact(r) for r in cur.fetchall()]


def list_proposals(user_id, dsn=None):
    """Usulan: fakta disimpulkan yang menunggu konfirmasi user."""
    return list_facts(user_id, status=STATUS_USULAN, dsn=dsn)


def confirm_proposal(fact_id, user_id, dsn=None):
    """User mengonfirmasi Usulan -> masuk profil (`berlaku`).

    Kalau ada fakta aktif dengan kunci sama, fakta lama diganti
    (riwayat dipertahankan). Raise KeyError kalau usulan tidak ada.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM fakta_profil WHERE id = %s AND user_id = %s "
                "AND status = 'usulan'",
                (fact_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"usulan tidak ditemukan: {fact_id}")
            cur.execute(
                "UPDATE fakta_profil SET status = 'diganti', "
                "berlaku_sampai = now() WHERE user_id = %s AND kunci = %s "
                "AND status = 'berlaku' AND (agen_id IS NOT DISTINCT FROM %s)",
                (user_id, row["kunci"], row["agen_id"]),
            )
            cur.execute(
                "UPDATE fakta_profil SET status = 'berlaku', asal = 'dikonfirmasi_user' "
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
                "UPDATE fakta_profil SET status = 'ditolak', berlaku_sampai = now() "
                "WHERE id = %s AND user_id = %s AND status = 'usulan' RETURNING *",
                (fact_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"usulan tidak ditemukan: {fact_id}")
        conn.commit()
    return _row_to_fact(row)


def correct_fact(user_id, kunci, nilai, agen_id=None, kejadian_id=None, dsn=None):
    """Koreksi user: sama dengan tambah fakta `dikonfirmasi_user`."""
    return add_fact(
        user_id, kunci, nilai, "dikonfirmasi_user",
        agen_id=agen_id, kejadian_id=kejadian_id, dsn=dsn,
    )


def delete_fact(fact_id, user_id, dsn=None):
    """Hapus fakta dari profil: status `ditolak`, riwayat tetap ada.

    Penghapusan keras hanya lewat hapus akun (consent.account).
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE fakta_profil SET status = 'ditolak', berlaku_sampai = now() "
                "WHERE id = %s AND user_id = %s AND status IN ('berlaku', 'usulan') "
                "RETURNING *",
                (fact_id, user_id),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"fakta tidak ditemukan: {fact_id}")
        conn.commit()
    return _row_to_fact(row)
