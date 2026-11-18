"""Sesi per perangkat Puspa (definisi 2.13, CONTRACTS.md).

Token sesi dibuat dengan secrets.token_urlsafe(32); yang disimpan di
database HANYA sha256-nya, jadi bocornya isi tabel sesi tidak memberikan
token yang bisa dipakai. Sesi bisa dicabut satu per satu atau sekaligus
(logout semua perangkat).
"""

import hashlib
import secrets

from .users import _connect


class SessionError(Exception):
    """Token sesi tidak valid atau sudah dicabut."""


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id, device_label, dsn=None):
    """Buat sesi baru untuk pengguna; kembalikan token mentah (str).

    Token mentah HANYA dikembalikan sekali di sini — pemanggil wajib
    menyimpannya dengan aman; database hanya menyimpan hash-nya.
    """
    token = secrets.token_urlsafe(32)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (user_id, token_hash, device_label) VALUES (%s, %s, %s)",
                (user_id, _token_hash(token), device_label),
            )
        conn.commit()
    return token


def validate_session(token, dsn=None):
    """Periksa token; kembalikan user_id (str UUID) kalau sesi masih berlaku.

    Raise SessionError kalau token tidak dikenal atau sudah dicabut.
    Sesi yang berhasil divalidasi memperbarui `terakhir_dilihat`.
    """
    token_h = _token_hash(token)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM sessions WHERE token_hash = %s AND NOT revoked",
                (token_h,),
            )
            row = cur.fetchone()
            if row is None:
                raise SessionError("sesi tidak valid atau sudah dicabut")
            cur.execute(
                "UPDATE sessions SET last_seen_at = now() WHERE token_hash = %s",
                (token_h,),
            )
        conn.commit()
    return str(row[0])


def owner_of_token(token, dsn=None):
    """Kembalikan user_id pemilik token TANPA efek samping.

    Sama seperti `validate_session` tapi TIDAK memperbarui
    `terakhir_dilihat`: dipakai `puspa.accounts.access.resolve_session_as`
    supaya token B yang dipakai A dan ditolak tidak meninggalkan jejak
    (perubahan timestamp) di sesi B. Raise SessionError kalau token
    tidak dikenal atau sudah dicabut.
    """
    token_h = _token_hash(token)
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_id FROM sessions WHERE token_hash = %s AND NOT revoked",
            (token_h,),
        )
        row = cur.fetchone()
    if row is None:
        raise SessionError("sesi tidak valid atau sudah dicabut")
    return str(row[0])


def revoke_session(token, dsn=None):
    """Cabut satu sesi (logout perangkat itu). Idempotent."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET revoked = true WHERE token_hash = %s",
                (_token_hash(token),),
            )
        conn.commit()


def revoke_all_sessions(user_id, dsn=None):
    """Cabut semua sesi milik pengguna (logout semua perangkat)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET revoked = true WHERE user_id = %s",
                (user_id,),
            )
        conn.commit()


def list_sessions(user_id, dsn=None):
    """Daftar semua sesi milik pengguna (termasuk yang sudah dicabut,
    dengan flag `dicabut`), terbaru dulu."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, device_label, created_at, last_seen_at, revoked "
            "FROM sessions WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(r[0]),
            "device_label": r[1],
            "created_at": r[2],
            "last_seen_at": r[3],
            "revoked": r[4],
        }
        for r in rows
    ]
