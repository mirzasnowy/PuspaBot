"""Tes envelope encryption vault Puspa — definisi 2.15.

Kunci master (env, tidak pernah di database) -> HKDF-SHA256(info=user_id)
-> kunci enkripsi data per pengguna -> Fernet. Semua kunci di sini dibuat
acak di dalam tes (os.urandom); tidak ada nilai kunci harfiah di file ini.

Catatan: get() oleh pengguna yang salah tetap KeyError sudah diuji di
tests/test_accounts.py (test_vault_isolated_between_users); tidak
didplikasi di sini.
"""

import base64
import os
import uuid

import psycopg
import pytest
from cryptography.fernet import Fernet, InvalidToken

from puspa.accounts import create_user
from puspa.accounts import vault
from puspa.accounts.vault import derive_user_key


def _fernet_for(master_key: bytes, user_id):
    return Fernet(base64.urlsafe_b64encode(derive_user_key(master_key, user_id)))


# --- Turunan kunci ----------------------------------------------------------

def test_derive_user_key_deterministic():
    master = os.urandom(32)
    uid = uuid.uuid4()
    assert derive_user_key(master, uid) == derive_user_key(master, uid)
    assert len(derive_user_key(master, uid)) == 32


def test_derive_user_key_differs_per_user():
    master = os.urandom(32)
    assert derive_user_key(master, uuid.uuid4()) != derive_user_key(
        master, uuid.uuid4()
    )


def test_derive_user_key_differs_per_master_key():
    uid = uuid.uuid4()
    assert derive_user_key(os.urandom(32), uid) != derive_user_key(
        os.urandom(32), uid
    )


def test_derive_user_key_accepts_string_user_id():
    # user_id dari DB adalah UUID; turunan memakai str(user_id), jadi
    # bentuk string dan bentuk UUID dari id yang sama harus sama.
    master = os.urandom(32)
    uid = uuid.uuid4()
    assert derive_user_key(master, uid) == derive_user_key(master, str(uid))


# --- Ciphertext lintas pengguna ---------------------------------------------

def test_ciphertext_user_a_not_decryptable_with_user_b_key(
    test_dsn, vault_master_key
):
    a = create_user("vault-a@example.com", "pw", dsn=test_dsn)
    b = create_user("vault-b@example.com", "pw", dsn=test_dsn)
    vault.put(a, "gmail_token", "token-milik-A", dsn=test_dsn)
    with psycopg.connect(test_dsn) as conn:
        ciphertext = conn.execute(
            "SELECT ciphertext FROM vault WHERE user_id = %s AND name = %s",
            (a, "gmail_token"),
        ).fetchone()[0]
    master = base64.urlsafe_b64decode(vault_master_key.encode())
    # Kunci B gagal dekripsi (InvalidToken), bukan data salah yang lolos.
    with pytest.raises(InvalidToken):
        _fernet_for(master, b).decrypt(ciphertext.encode())
    # Sanity: kunci A sendiri tetap bisa mendekripsi ciphertext yang sama.
    assert _fernet_for(master, a).decrypt(ciphertext.encode()) == b"token-milik-A"


def test_vault_put_uses_derive_user_key(test_dsn, vault_master_key):
    # Ciphertext yang ditulis vault.put bisa didekripsi dengan Fernet yang
    # dibangun dari derive_user_key — bukti _fernet memakai fungsi itu.
    uid = create_user("vault-c@example.com", "pw", dsn=test_dsn)
    vault.put(uid, "drive_token", "token-drive", dsn=test_dsn)
    with psycopg.connect(test_dsn) as conn:
        ciphertext = conn.execute(
            "SELECT ciphertext FROM vault WHERE user_id = %s AND name = %s",
            (uid, "drive_token"),
        ).fetchone()[0]
    master = base64.urlsafe_b64decode(vault_master_key.encode())
    assert _fernet_for(master, uid).decrypt(ciphertext.encode()) == b"token-drive"


# --- Kunci master tidak pernah di database ----------------------------------

def test_master_key_never_in_database(test_dsn, vault_master_key):
    a = create_user("vault-d@example.com", "pw", dsn=test_dsn)
    b = create_user("vault-e@example.com", "pw", dsn=test_dsn)
    vault.put(a, "gmail_token", "token-A", dsn=test_dsn)
    vault.put(a, "drive_token", "token-A-lain", dsn=test_dsn)
    vault.put(b, "gmail_token", "token-B", dsn=test_dsn)
    master_raw = base64.urlsafe_b64decode(vault_master_key.encode())
    with psycopg.connect(test_dsn) as conn:
        rows = conn.execute("SELECT * FROM vault").fetchall()
        columns = [d.name for d in conn.execute("SELECT * FROM vault LIMIT 0").description]
    assert rows, "tabel vault harus berisi entri yang baru ditulis"
    # Tidak ada kolom bernama master/kunci.
    assert not any("master" in c.lower() or "kunci" in c.lower() for c in columns)
    # Tidak ada nilai di tabel yang memuat bytes kunci master (mentah
    # maupun bentuk base64-nya yang dipakai di env).
    blob = b"\n".join(str(v).encode() for row in rows for v in row)
    assert master_raw not in blob
    assert vault_master_key.encode() not in blob
