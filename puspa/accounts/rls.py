"""Helper Row Level Security (RLS) Puspa — definisi 2.15.

Satu-satunya tempat kode aplikasi memasang pengguna saat ini pada
koneksi Postgres. Policy RLS di ``puspa/accounts/schema.sql`` membaca
variabel sesi ``app.current_user_id``; modul ini yang menuliskannya,
supaya kode aplikasi lain tidak menulis SQL mentah di mana-mana.

Pemakaian (di dalam satu transaksi — ``SET LOCAL`` hanya hidup sampai
transaksi selesai, lalu otomatis kembali kosong):

    with psycopg.connect(dsn) as conn:
        with rls.as_user(conn, user_id):
            ... query sebagai pengguna itu (peran puspa_app) ...

Atau manual:

    rls.set_current_user(conn, user_id)
    ...
    rls.clear_current_user(conn)

CATATAN PENTING — dua operasi TIDAK bisa lewat helper ini:
  - Login/registrasi (``create_user``, ``authenticate``,
    ``validate_session``): sebelum sesi divalidasi, user_id belum
    diketahui (ayam-telur). Operasi itu jalan sebagai pemilik tabel
    (bypass RLS — lihat keputusan "tanpa FORCE" di schema.sql).
    Alurnya: validasi sesi sebagai pemilik -> dapat user_id -> buka
    koneksi peran app -> ``as_user(conn, user_id)`` untuk semua
    query berikutnya.
  - Koneksi HARUS berjalan sebagai peran yang dikenai RLS
    (``puspa_app``). Pemilik tabel/superuser melewati RLS begitu saja
    (itu sifat Postgres, bukan bug) — jadi tes RLS memakai
    ``SET ROLE puspa_app``; lihat tests/test_rls.py.
"""

from __future__ import annotations

import contextlib
import uuid

#: Nama variabel sesi Postgres yang dibaca policy RLS.
SETTING_NAME = "app.current_user_id"


class RlsError(Exception):
    """Tidak ada pengguna saat ini yang dipasang padahal dibutuhkan."""


def _validate_user_id(user_id) -> str:
    """User_id harus UUID yang valid; kembalikan bentuk string kanonis.

    Validasi di sini (bukan hanya parameter SQL) supaya nilai yang
    masuk ke SET LOCAL selalu UUID — tidak ada string sembarang yang
    bisa lolos ke policy.
    """
    if user_id is None:
        raise ValueError("user_id tidak boleh None")
    try:
        return str(uuid.UUID(str(user_id)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"user_id bukan UUID yang valid: {user_id!r}") from exc


def set_current_user(conn, user_id) -> str:
    """Pasang pengguna saat ini pada koneksi ini (transaction-local).

    Memakai ``set_config(..., true)`` = SET LOCAL: nilai hilang sendiri
    saat transaksi berakhir (commit/rollback), jadi tidak ada risiko
    pengguna A bocor ke transaksi berikutnya di koneksi yang sama.
    Kembalikan user_id (string kanonis).
    """
    user_id = _validate_user_id(user_id)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_config(%s, %s, true)",
            (SETTING_NAME, user_id),
        )
    return user_id


def clear_current_user(conn) -> None:
    """Kosongkan pengguna saat ini pada koneksi ini (transaction-local)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT set_config(%s, '', true)",
            (SETTING_NAME,),
        )


def current_user_id(conn) -> str:
    """Baca pengguna saat ini pada koneksi ini; '' kalau belum dipasang.

    ``current_setting(..., true)`` mengembalikan NULL (bukan '') kalau
    variabel belum diset — di-coalesce ke '' supaya konsisten dengan
    ``set_current_user``/``clear_current_user`` dan policy RLS.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(current_setting(%s, true), '')", (SETTING_NAME,)
        )
        row = cur.fetchone()
    return row[0] if row else ""


def require_current_user(conn) -> str:
    """Kembalikan pengguna saat ini; raise RlsError kalau belum dipasang.

    Fail-closed di sisi aplikasi: kode yang lupa memasang pengguna
    gagal keras sebelum query, bukan diam-diam membaca data orang lain
    (di DB pun policy akan mengembalikan kosong — dua lapis).
    """
    user_id = current_user_id(conn)
    if not user_id:
        raise RlsError(
            "app.current_user_id belum dipasang pada koneksi ini; "
            "pakai rls.as_user(conn, user_id) dulu"
        )
    return user_id


@contextlib.contextmanager
def as_user(conn, user_id):
    """Context manager: jalankan blok sebagai pengguna `user_id`.

    Memasang ``app.current_user_id`` (SET LOCAL) lalu mengosongkannya
    lagi saat keluar. Harus dipakai di dalam transaksi; kalau dipakai
    di luar transaksi eksplisit, psycopg menjalankan SET LOCAL di
    transaksi implisit yang berakhir di commit berikutnya — tetap aman
    karena nilainya tidak bertahan melewati commit.
    """
    user_id = set_current_user(conn, user_id)
    try:
        yield user_id
    finally:
        try:
            clear_current_user(conn)
        except Exception:
            # Keluar dari context manager tidak boleh menutupi error
            # asli; dan kalau transaksi sudah selesai/rollback,
            # SET LOCAL sudah hilang sendiri.
            pass
