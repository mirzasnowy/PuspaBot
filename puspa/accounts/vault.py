"""Vault per pengguna Puspa (definisi 2.15, CONTRACTS.md).

Menyimpan token konektor (Gmail, Drive, Calendar, ...) tiap pengguna
terenkripsi dengan kunci per pengguna (envelope encryption):

    kunci master (env PUSPA_VAULT_MASTER_KEY, 32 byte)
        -> HKDF-SHA256(master_key, info=user_id)
        -> kunci enkripsi data per pengguna (32 byte) -> Fernet

Kunci master HANYA dari env PUSPA_VAULT_MASTER_KEY (base64 urlsafe,
32 byte) dan TIDAK PERNAH ditulis ke database — di database hanya ada
ciphertext (dan user_id/nama). Kalau env tidak ada (atau bukan 32 byte),
semua operasi raise VaultError — tidak pernah dibuat kunci default.
"""

import base64
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .users import _connect


class VaultError(Exception):
    """Vault tidak bisa dipakai (mis. master key tidak diset / tidak valid)."""


def _master_key():
    raw = os.environ.get("PUSPA_VAULT_MASTER_KEY")
    if not raw:
        raise VaultError(
            "PUSPA_VAULT_MASTER_KEY tidak diset; vault menolak bekerja "
            "tanpa master key (tidak ada kunci default)"
        )
    try:
        key = base64.urlsafe_b64decode(raw.encode())
    except Exception:
        raise VaultError("PUSPA_VAULT_MASTER_KEY bukan base64 urlsafe yang valid")
    if len(key) != 32:
        raise VaultError("PUSPA_VAULT_MASTER_KEY harus 32 byte")
    return key


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


def _fernet(user_id):
    return Fernet(base64.urlsafe_b64encode(derive_user_key(_master_key(), user_id)))


def put(user_id, name, value, dsn=None):
    """Simpan (atau timpa) nilai terenkripsi dengan nama `name`."""
    if not isinstance(value, str):
        raise ValueError("value vault harus string")
    ciphertext = _fernet(user_id).encrypt(value.encode()).decode()
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO vault (user_id, name, ciphertext) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, name) DO UPDATE "
                "SET ciphertext = EXCLUDED.ciphertext, diubah = now()",
                (user_id, name, ciphertext),
            )
        conn.commit()


def get(user_id, name, dsn=None):
    """Ambil nilai terdekripsi. Raise KeyError kalau nama tidak ada
    untuk pengguna ini (termasuk kalau nama itu milik pengguna lain)."""
    _master_key()  # fail-closed: tanpa master key, tolak sebelum menyentuh DB
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
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
    _master_key()  # fail-closed: tanpa master key, tolak sebelum menyentuh DB
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM vault WHERE user_id = %s AND name = %s",
                (user_id, name),
            )
        conn.commit()


def list_names(user_id, dsn=None):
    """Daftar nama entri vault milik pengguna, terurut."""
    _master_key()  # fail-closed: tanpa master key, tolak sebelum menyentuh DB
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM vault WHERE user_id = %s ORDER BY name",
                (user_id,),
            )
            rows = cur.fetchall()
    return [r[0] for r in rows]
