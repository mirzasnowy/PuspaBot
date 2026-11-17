"""Pengguna Puspa — akun dasar (definisi 2.13, CONTRACTS.md).

Email + kata sandi; kata sandi tidak pernah disimpan mentah, hanya hash
Argon2id. Email dinormalisasi (strip + lower) dan unik.
"""

import os

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from psycopg import errors as pg_errors

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"


class AuthError(Exception):
    """Email tidak dikenal atau kata sandi salah."""


class DuplicateEmailError(Exception):
    """Email sudah terdaftar."""


_PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=1)


def resolve_dsn(dsn=None):
    """DSN Postgres: argumen eksplisit, lalu env PUSPA_DSN / PUSPA_TEST_DSN,
    lalu default peer-auth lokal."""
    return dsn or os.environ.get("PUSPA_DSN") or os.environ.get("PUSPA_TEST_DSN") or DEFAULT_DSN


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn))


def _normalize_email(email):
    if not isinstance(email, str):
        raise ValueError("email harus string")
    return email.strip().lower()


def create_user(email, password, dsn=None):
    """Buat pengguna baru; kembalikan user_id (str UUID).

    Raise DuplicateEmailError kalau email (setelah normalisasi) sudah ada.
    """
    email_n = _normalize_email(email)
    if not email_n:
        raise ValueError("email kosong")
    if not password:
        raise ValueError("kata sandi kosong")
    password_hash = _PASSWORD_HASHER.hash(password)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO pengguna (email, password_hash) VALUES (%s, %s) RETURNING id",
                    (email_n, password_hash),
                )
                user_id = cur.fetchone()[0]
            except pg_errors.UniqueViolation:
                raise DuplicateEmailError(f"email sudah terdaftar: {email_n}")
        conn.commit()
    return str(user_id)


def authenticate(email, password, dsn=None):
    """Periksa email + kata sandi; kembalikan user_id (str UUID).

    Raise AuthError kalau email tidak dikenal atau kata sandi salah.
    Pesan kesalahan sengaja sama untuk keduanya (tidak membocorkan
    mana yang salah).
    """
    email_n = _normalize_email(email)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, password_hash FROM pengguna WHERE email = %s",
                (email_n,),
            )
            row = cur.fetchone()
    if row is None:
        raise AuthError("email atau password salah")
    user_id, password_hash = row
    try:
        _PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        raise AuthError("email atau password salah")
    return str(user_id)


def delete_user(user_id, dsn=None):
    """Hapus pengguna: baris pengguna + semua sesi + seluruh vault.

    Kejadian di Buku Kerja TIDAK dihapus (audit append-only) — tabel
    `kejadian` memang tidak disentuh di sini.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM vault WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM sesi WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM pengguna WHERE id = %s", (user_id,))
        conn.commit()
