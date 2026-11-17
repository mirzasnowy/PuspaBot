"""Akun dasar Puspa (definisi 2.13, CONTRACTS.md).

Pengguna (email + kata sandi Argon2id), sesi per perangkat (hanya hash
token yang disimpan), dan vault per pengguna (Fernet, kunci turunan
HKDF dari master key env PUSPA_VAULT_MASTER_KEY).
"""

from pathlib import Path

import psycopg

from .sessions import (
    SessionError,
    create_session,
    list_sessions,
    owner_of_token,
    revoke_all_sessions,
    revoke_session,
    validate_session,
)
from .users import (
    AuthError,
    DuplicateEmailError,
    authenticate,
    create_user,
    delete_user,
    resolve_dsn,
)
from .vault import VaultError, delete as vault_delete
from .vault import get as vault_get
from .vault import list_names as vault_list_names
from .vault import put as vault_put

__all__ = [
    "AuthError",
    "DuplicateEmailError",
    "SessionError",
    "VaultError",
    "authenticate",
    "create_schema",
    "create_session",
    "create_user",
    "delete_user",
    "list_sessions",
    "owner_of_token",
    "revoke_all_sessions",
    "revoke_session",
    "validate_session",
    "vault_delete",
    "vault_get",
    "vault_list_names",
    "vault_put",
]


def create_schema(dsn=None):
    """Jalankan schema.sql (CREATE TABLE IF NOT EXISTS)."""
    sql = (Path(__file__).parent / "schema.sql").read_text()
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        conn.execute(sql)
        conn.commit()
