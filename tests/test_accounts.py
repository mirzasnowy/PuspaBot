"""Tes akun dasar Puspa — definisi 2.13, CONTRACTS.md bagian "Akun dasar".

Memetakan ke gerbang: login (email + kata sandi Argon2id), sesi per
perangkat yang bisa dicabut, vault per pengguna yang terisolasi, dan
hapus akun (sesi + vault ikut terhapus, Buku Kerja tidak disentuh).
"""

import hashlib

import psycopg
import pytest

from puspa.accounts import (
    AuthError,
    DuplicateEmailError,
    SessionError,
    VaultError,
    authenticate,
    create_session,
    create_user,
    delete_user,
    list_sessions,
    revoke_all_sessions,
    revoke_session,
    validate_session,
    vault,
)

# --- Pengguna ---------------------------------------------------------------


def test_create_and_authenticate_ok(test_dsn):
    uid = create_user("  Alice@Example.COM ", "kata-sandi-rahasia", dsn=test_dsn)
    assert uid
    # Email dinormalisasi: login dengan bentuk lain tetap lolos.
    assert authenticate("alice@example.com", "kata-sandi-rahasia", dsn=test_dsn) == uid


def test_authenticate_wrong_password_raises(test_dsn):
    create_user("bob@example.com", "correct", dsn=test_dsn)
    with pytest.raises(AuthError):
        authenticate("bob@example.com", "wrong", dsn=test_dsn)


def test_authenticate_unknown_email_raises(test_dsn):
    with pytest.raises(AuthError):
        authenticate("tidak-ada@example.com", "apa-saja", dsn=test_dsn)


def test_duplicate_email_rejected(test_dsn):
    create_user("carol@example.com", "satu", dsn=test_dsn)
    with pytest.raises(DuplicateEmailError):
        create_user("carol@example.com", "dua", dsn=test_dsn)
    # Duplikat juga terdeteksi setelah normalisasi (beda kapital).
    with pytest.raises(DuplicateEmailError):
        create_user("CAROL@example.com", "tiga", dsn=test_dsn)


def test_password_never_stored_plaintext(test_dsn):
    create_user("dave@example.com", "rahasia-123", dsn=test_dsn)
    with psycopg.connect(test_dsn) as conn:
        stored = conn.execute(
            "SELECT password_hash FROM users WHERE email = 'dave@example.com'"
        ).fetchone()[0]
    assert stored != "rahasia-123"
    assert stored.startswith("$argon2id$")


# --- Sesi -------------------------------------------------------------------


def test_session_create_validate_revoke(test_dsn):
    uid = create_user("erin@example.com", "pw", dsn=test_dsn)
    token = create_session(uid, "laptop", dsn=test_dsn)
    assert validate_session(token, dsn=test_dsn) == uid
    revoke_session(token, dsn=test_dsn)
    with pytest.raises(SessionError):
        validate_session(token, dsn=test_dsn)


def test_session_invalid_token_raises(test_dsn):
    with pytest.raises(SessionError):
        validate_session("token-yang-tidak-pernah-ada", dsn=test_dsn)


def test_session_token_only_hash_stored(test_dsn):
    uid = create_user("frank@example.com", "pw", dsn=test_dsn)
    token = create_session(uid, "phone", dsn=test_dsn)
    with psycopg.connect(test_dsn) as conn:
        stored = conn.execute(
            "SELECT token_hash FROM sessions WHERE user_id = %s", (uid,)
        ).fetchone()[0]
    assert stored != token
    assert stored == hashlib.sha256(token.encode()).hexdigest()


def test_revoke_all_sessions(test_dsn):
    uid = create_user("grace@example.com", "pw", dsn=test_dsn)
    t1 = create_session(uid, "laptop", dsn=test_dsn)
    t2 = create_session(uid, "phone", dsn=test_dsn)
    revoke_all_sessions(uid, dsn=test_dsn)
    with pytest.raises(SessionError):
        validate_session(t1, dsn=test_dsn)
    with pytest.raises(SessionError):
        validate_session(t2, dsn=test_dsn)
    assert all(s["revoked"] for s in list_sessions(uid, dsn=test_dsn))


def test_list_sessions(test_dsn):
    uid = create_user("heidi@example.com", "pw", dsn=test_dsn)
    create_session(uid, "laptop", dsn=test_dsn)
    create_session(uid, "phone", dsn=test_dsn)
    sessions = list_sessions(uid, dsn=test_dsn)
    assert len(sessions) == 2
    assert {s["device_label"] for s in sessions} == {"laptop", "phone"}
    assert all(not s["revoked"] for s in sessions)


# --- Vault ------------------------------------------------------------------


def test_vault_put_get_delete(test_dsn, vault_master_key):
    uid = create_user("ivan@example.com", "pw", dsn=test_dsn)
    vault.put(uid, "gmail_token", "token-rahasia-123", dsn=test_dsn)
    assert vault.get(uid, "gmail_token", dsn=test_dsn) == "token-rahasia-123"
    assert vault.list_names(uid, dsn=test_dsn) == ["gmail_token"]
    vault.put(uid, "gmail_token", "token-baru", dsn=test_dsn)  # timpa
    assert vault.get(uid, "gmail_token", dsn=test_dsn) == "token-baru"
    vault.delete(uid, "gmail_token", dsn=test_dsn)
    with pytest.raises(KeyError):
        vault.get(uid, "gmail_token", dsn=test_dsn)
    assert vault.list_names(uid, dsn=test_dsn) == []


def test_vault_isolated_between_users(test_dsn, vault_master_key):
    a = create_user("judy@example.com", "pw", dsn=test_dsn)
    b = create_user("mallory@example.com", "pw", dsn=test_dsn)
    vault.put(a, "gmail_token", "milik-A", dsn=test_dsn)
    # Pengguna B tidak bisa membaca entri pengguna A (nama yang sama pun tidak).
    with pytest.raises(KeyError):
        vault.get(b, "gmail_token", dsn=test_dsn)
    assert vault.list_names(b, dsn=test_dsn) == []
    # Ciphertext di DB berbeda antar pengguna walau nilainya sama.
    vault.put(b, "gmail_token", "milik-A", dsn=test_dsn)
    with psycopg.connect(test_dsn) as conn:
        rows = conn.execute(
            "SELECT user_id, ciphertext FROM vault WHERE name = 'gmail_token'"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][1] != rows[1][1]


def test_vault_without_master_key_raises(test_dsn, monkeypatch):
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY", raising=False)
    uid = create_user("oscar@example.com", "pw", dsn=test_dsn)
    with pytest.raises(VaultError):
        vault.put(uid, "gmail_token", "x", dsn=test_dsn)
    with pytest.raises(VaultError):
        vault.get(uid, "gmail_token", dsn=test_dsn)


# --- Hapus akun --------------------------------------------------------------


def test_delete_user_removes_sessions_and_vault(test_dsn, vault_master_key):
    uid = create_user("peggy@example.com", "pw", dsn=test_dsn)
    token = create_session(uid, "laptop", dsn=test_dsn)
    vault.put(uid, "gmail_token", "tok", dsn=test_dsn)
    delete_user(uid, dsn=test_dsn)
    with pytest.raises(AuthError):
        authenticate("peggy@example.com", "pw", dsn=test_dsn)
    with pytest.raises(SessionError):
        validate_session(token, dsn=test_dsn)
    with psycopg.connect(test_dsn) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = %s", (uid,)).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM vault WHERE user_id = %s", (uid,)).fetchone()[0] == 0
        )
        assert conn.execute("SELECT COUNT(*) FROM users WHERE id = %s", (uid,)).fetchone()[0] == 0
