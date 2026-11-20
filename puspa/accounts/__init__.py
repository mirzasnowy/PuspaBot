"""Base Puspa accounts (definition 2.13, CONTRACTS.md).

Users (email + Argon2id password), one session per device (only the
token hash is stored), and a per-user vault (Fernet, key derived with
HKDF from the master key env PUSPA_VAULT_MASTER_KEY).
"""

from puspa.migrations import apply_migrations

from .user_sessions import (
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
from .vault import VaultError
from .vault import delete as vault_delete
from .vault import get as vault_get
from .vault import list_names as vault_list_names
from .vault import put as vault_put
from .vault import rekey_all as vault_rekey_all
from .vault import rotate_entry as vault_rotate_entry

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
    "vault_rekey_all",
    "vault_rotate_entry",
]


def create_schema(dsn=None):
    """Apply the account tables (migration 0001_core).

    The tables (users, sessions, vault, agents) come from the
    migration, not from a schema file: see puspa/migrations.
    Idempotent; safe to call before every test.
    """
    apply_migrations(resolve_dsn(dsn), target="0001")
