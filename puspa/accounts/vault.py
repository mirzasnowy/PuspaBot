"""Per-user vault for Puspa (definition 2.15, CONTRACTS.md).

Each user's connector tokens (Gmail, Drive, Calendar, ...) are
stored encrypted with a per-user key (envelope encryption):

    master key (32 bytes)
        -> HKDF-SHA256(master_key, info=user_id)
        -> per-user data key (32 bytes) -> Fernet

Master keys come from, in order (first key is the primary):

1. ``PUSPA_VAULT_MASTER_KEY`` env var: one base64url 32-byte key,
   or several comma-separated keys during a rotation (the new
   primary first, the old ones after it).
2. ``PUSPA_VAULT_MASTER_KEY_FILE`` env var: a path to a file with
   one base64url key per line. The file MUST have mode 0600 or
   stricter (no group/other permissions at all); a looser file is
   refused outright. This is the plan 6.8 rule: the master key
   lives outside plain config files, in a separately permissioned
   file (or a VPS secret manager that writes such a file), never
   next to the application config.

The master key is NEVER written to the database: only ciphertext
(plus user_id/name) is stored there. With no key configured
anywhere, every operation raises VaultError; there is no default
key.

Rotation without rewriting the vault (plan 6.8): decryption uses
``MultiFernet`` over all configured master keys, while encryption
always uses the primary. To rotate, put the new key first in the
key list (env or file) and keep the old key after it: every old
ciphertext still decrypts, and new writes use the new key. Entries
can then be re-encrypted under the primary lazily with
:func:`rotate_entry`, or all at once with :func:`rekey_all`; only
after every entry is re-encrypted may the old key be dropped from
the list.
"""

import base64
import os
import stat

from puspa.db import connect as _db_connect
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: Fallback DSN when no DSN is passed and no env var is set (same
#: convention as the rest of the codebase).
DEFAULT_DSN = None


def _connect(dsn=None):
    """Open a Postgres connection (split Step 1: local copy).

    The vault used to take this helper from the multi-user accounts
    module, which moved private with the hosted accounts. The
    resolution order is unchanged: explicit DSN, then ``PUSPA_DSN``,
    then ``PUSPA_TEST_DSN``, then the local default.
    """
    resolved = dsn or os.environ.get("PUSPA_DSN") or os.environ.get("PUSPA_TEST_DSN") or DEFAULT_DSN
    return _db_connect(resolved)


__all__ = [
    "VaultError",
    "delete",
    "derive_user_key",
    "get",
    "list_names",
    "put",
    "rekey_all",
    "rotate_entry",
]

#: Env var with the master key (or comma-separated keys, primary first).
MASTER_KEY_ENV_VAR = "PUSPA_VAULT_MASTER_KEY"

#: Env var with the path to a key file (one base64url key per line).
MASTER_KEY_FILE_ENV_VAR = "PUSPA_VAULT_MASTER_KEY_FILE"


class VaultError(Exception):
    """Vault tidak bisa dipakai (mis. master key tidak diset / tidak valid)."""


def _decode_key(raw: str, *, source: str) -> bytes:
    """Decode one base64url 32-byte master key, or raise VaultError."""
    try:
        key = base64.urlsafe_b64decode(raw.strip().encode())
    except Exception as err:
        raise VaultError(f"vault master key from {source} is not valid base64 urlsafe") from err
    if len(key) != 32:
        raise VaultError(f"vault master key from {source} must be 32 bytes")
    return key


def _keys_from_env() -> list[bytes]:
    """Master keys from ``PUSPA_VAULT_MASTER_KEY`` (comma-separated)."""
    raw = os.environ.get(MASTER_KEY_ENV_VAR)
    if not raw:
        return []
    return [_decode_key(part, source=MASTER_KEY_ENV_VAR) for part in raw.split(",") if part.strip()]


def _keys_from_file() -> list[bytes]:
    """Master keys from the ``PUSPA_VAULT_MASTER_KEY_FILE`` key file.

    The file must exist and have mode 0600 or stricter (no group or
    other permission bits at all); anything looser is refused, so a
    key file copied with default permissions can never silently
    become the vault's trust anchor. One base64url key per line;
    blank lines are ignored.
    """
    path = os.environ.get(MASTER_KEY_FILE_ENV_VAR)
    if not path:
        return []
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError as err:
        raise VaultError(f"vault master key file {path!r} cannot be read: {err}") from err
    if mode & 0o077:
        raise VaultError(
            f"vault master key file {path!r} has mode {mode:04o}; "
            "refusing: it must be 0600 or stricter (no group/other access)"
        )
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as err:
        raise VaultError(f"vault master key file {path!r} cannot be read: {err}") from err
    keys = [_decode_key(line, source=MASTER_KEY_FILE_ENV_VAR) for line in lines if line.strip()]
    if not keys:
        raise VaultError(f"vault master key file {path!r} holds no key")
    return keys


def _master_keys() -> list[bytes]:
    """All configured master keys, primary first, deduplicated.

    Env keys come first, then key-file keys. Raises VaultError when
    no key is configured anywhere (fail-closed, no default key).
    """
    keys: list[bytes] = []
    for key in _keys_from_env() + _keys_from_file():
        if key not in keys:
            keys.append(key)
    if not keys:
        raise VaultError(
            "PUSPA_VAULT_MASTER_KEY / PUSPA_VAULT_MASTER_KEY_FILE not set; "
            "vault refuses to work without a master key (there is no default key)"
        )
    return keys


def _master_key():
    """The primary master key (first configured key)."""
    return _master_keys()[0]


def derive_user_key(master_key: bytes, user_id) -> bytes:
    """Turunkan kunci enkripsi data per pengguna dari kunci master.

    Envelope encryption (definisi 2.15): kunci master (dari env, tidak
    pernah disimpan di database) -> HKDF-SHA256 dengan info=user_id ->
    kunci 32 byte untuk Fernet pengguna itu. Turunan ini deterministik:
    master key + user_id yang sama selalu menghasilkan kunci yang sama,
    jadi ciphertext lama tetap bisa didekripsi selama master key tidak
    berubah; pengguna berbeda (atau master key berbeda) mendapat kunci
    berbeda.

    JANGAN ubah parameter turunan ini (SHA256, length 32, salt=None,
    info=str(user_id).encode()): mengubahnya membuat semua ciphertext
    vault yang sudah ada tidak bisa didekripsi.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=str(user_id).encode(),
    )
    return hkdf.derive(master_key)


def _fernet(user_id, *, primary_only: bool = False):
    """Fernet for a user: MultiFernet over all master keys, or primary-only.

    Decryption tries every configured master key in order, so
    ciphertext written under a rotated-out key still decrypts.
    Encryption always uses the primary key (``primary_only=True``),
    so new writes converge on the current key. With a single key
    configured, the plain single-key Fernet is returned.
    """
    keys = _master_keys()
    ferns = [Fernet(base64.urlsafe_b64encode(derive_user_key(key, user_id))) for key in keys]
    if primary_only or len(ferns) == 1:
        return ferns[0]
    return MultiFernet(ferns)


def put(user_id, name, value, dsn=None):
    """Simpan (atau timpa) nilai terenkripsi dengan nama `name`.

    Encrypted under the primary master key only.
    """
    if not isinstance(value, str):
        raise ValueError("value vault harus string")
    ciphertext = _fernet(user_id, primary_only=True).encrypt(value.encode()).decode()
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO vault (user_id, name, ciphertext) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, name) DO UPDATE "
                "SET ciphertext = EXCLUDED.ciphertext, updated_at = now()",
                (user_id, name, ciphertext),
            )
        conn.commit()


def get(user_id, name, dsn=None):
    """Ambil nilai terdekripsi. Raise KeyError kalau nama tidak ada
    untuk pengguna ini (termasuk kalau nama itu milik pengguna lain).

    Decrypts with any configured master key (MultiFernet), so
    entries written before a rotation still read.
    """
    _master_keys()  # fail-closed: tanpa master key, tolak sebelum menyentuh DB
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ciphertext FROM vault WHERE user_id = %s AND name = %s",
            (user_id, name),
        )
        row = cur.fetchone()
    if row is None:
        raise KeyError(name)
    return _fernet(user_id).decrypt(row[0].encode()).decode()


def delete(user_id, name, dsn=None):
    """Hapus entri vault. Idempotent (tidak error kalau tidak ada)."""
    _master_keys()  # fail-closed: tanpa master key, tolak sebelum menyentuh DB
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM vault WHERE user_id = %s AND name = %s",
                (user_id, name),
            )
        conn.commit()


def list_names(user_id, dsn=None):
    """Daftar nama entri vault milik pengguna, terurut."""
    _master_keys()  # fail-closed: tanpa master key, tolak sebelum menyentuh DB
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT name FROM vault WHERE user_id = %s ORDER BY name",
            (user_id,),
        )
        rows = cur.fetchall()
    return [r[0] for r in rows]


def rotate_entry(user_id, name, dsn=None) -> bool:
    """Re-encrypt one entry under the primary master key.

    Returns True when the entry was re-encrypted, False when it was
    already under the primary key (nothing to do). Raises KeyError
    when the entry does not exist and InvalidToken when no
    configured key can decrypt it (loud: a lost key must never
    silently drop a credential).
    """
    _master_keys()  # fail-closed before touching the DB
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ciphertext FROM vault WHERE user_id = %s AND name = %s",
            (user_id, name),
        )
        row = cur.fetchone()
    if row is None:
        raise KeyError(name)
    ciphertext = row[0]
    try:
        # Already under the primary key: no rewrite needed.
        _fernet(user_id, primary_only=True).decrypt(ciphertext.encode())
        return False
    except InvalidToken:
        pass
    value = _fernet(user_id).decrypt(ciphertext.encode()).decode()
    new_ciphertext = _fernet(user_id, primary_only=True).encrypt(value.encode()).decode()
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE vault SET ciphertext = %s, updated_at = now() "
                "WHERE user_id = %s AND name = %s",
                (new_ciphertext, user_id, name),
            )
        conn.commit()
    return True


def rekey_all(dsn=None) -> int:
    """Re-encrypt every vault entry under the primary master key.

    Returns the number of entries actually re-encrypted (entries
    already under the primary key are skipped). Run this after a
    rotation, before dropping the old key from the key list: once
    it returns 0 on a second run, no ciphertext depends on the old
    key anymore. Raises InvalidToken when an entry cannot be
    decrypted with any configured key.
    """
    _master_keys()  # fail-closed before touching the DB
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id, name FROM vault ORDER BY user_id, name")
        entries = cur.fetchall()
    rekeyed = 0
    for user_id, name in entries:
        if rotate_entry(user_id, name, dsn=dsn):
            rekeyed += 1
    return rekeyed
