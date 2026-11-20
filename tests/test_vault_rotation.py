"""Vault key rotation with MultiFernet, and the key-file source
(plan 6.8, E-12).

Rotation without rewriting the vault: decryption tries every
configured master key (MultiFernet), encryption always uses the
primary. A new primary is added first in the key list; old entries
keep decrypting, and :func:`rotate_entry` / :func:`rekey_all`
re-encrypt them under the primary so the old key can be dropped.

Every key here is generated fresh inside the test
(``os.urandom``); no literal key value appears in this file. The
key-file tests write their own temp files with explicit modes.
"""

from __future__ import annotations

import base64
import os
import uuid

import pytest
from cryptography.fernet import InvalidToken

from puspa.accounts import create_user, vault
from puspa.accounts.vault import VaultError, rekey_all, rotate_entry


def _b64key(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


def _two_keys(monkeypatch):
    """Two fresh keys; returns (old_b64, new_b64)."""
    return _b64key(os.urandom(32)), _b64key(os.urandom(32))


def _write_key_file(path, keys, mode=0o600):
    path.write_text("\n".join(keys) + "\n", encoding="utf-8")
    os.chmod(path, mode)
    return path


# --- MultiFernet rotation -------------------------------------------------


def test_old_ciphertext_reads_after_adding_new_primary(test_dsn, monkeypatch):
    old, new = _two_keys(monkeypatch)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", old)
    uid = create_user("vault-rot-a@example.com", "pw", dsn=test_dsn)
    vault.put(uid, "gmail_token", "token-A", dsn=test_dsn)
    # Rotate: new primary first, old key kept for decryption.
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", f"{new},{old}")
    assert vault.get(uid, "gmail_token", dsn=test_dsn) == "token-A"


def test_new_writes_use_primary_only(test_dsn, monkeypatch):
    old, new = _two_keys(monkeypatch)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", old)
    uid = create_user("vault-rot-b@example.com", "pw", dsn=test_dsn)
    vault.put(uid, "gmail_token", "token-A", dsn=test_dsn)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", f"{new},{old}")
    vault.put(uid, "drive_token", "token-B", dsn=test_dsn)
    # Drop the old key: the new entry still reads, the old one does not.
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", new)
    assert vault.get(uid, "drive_token", dsn=test_dsn) == "token-B"
    with pytest.raises(InvalidToken):
        vault.get(uid, "gmail_token", dsn=test_dsn)


def test_rotate_entry_rekeys_under_primary(test_dsn, monkeypatch):
    old, new = _two_keys(monkeypatch)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", old)
    uid = create_user("vault-rot-c@example.com", "pw", dsn=test_dsn)
    vault.put(uid, "gmail_token", "token-A", dsn=test_dsn)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", f"{new},{old}")
    assert rotate_entry(uid, "gmail_token", dsn=test_dsn) is True
    assert rotate_entry(uid, "gmail_token", dsn=test_dsn) is False
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", new)
    assert vault.get(uid, "gmail_token", dsn=test_dsn) == "token-A"


def test_rotate_entry_missing_name_raises_key_error(test_dsn, monkeypatch):
    old, _new = _two_keys(monkeypatch)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", old)
    uid = create_user("vault-rot-d@example.com", "pw", dsn=test_dsn)
    with pytest.raises(KeyError):
        rotate_entry(uid, "no-such-name", dsn=test_dsn)


def test_rekey_all_returns_count_and_second_run_is_zero(test_dsn, monkeypatch):
    old, new = _two_keys(monkeypatch)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", old)
    uid = create_user("vault-rot-e@example.com", "pw", dsn=test_dsn)
    vault.put(uid, "a_token", "A", dsn=test_dsn)
    vault.put(uid, "b_token", "B", dsn=test_dsn)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", f"{new},{old}")
    assert rekey_all(dsn=test_dsn) == 2
    assert rekey_all(dsn=test_dsn) == 0
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", new)
    assert vault.get(uid, "a_token", dsn=test_dsn) == "A"
    assert vault.get(uid, "b_token", dsn=test_dsn) == "B"


def test_rekey_all_empty_vault_returns_zero(test_dsn, monkeypatch):
    old, _new = _two_keys(monkeypatch)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", old)
    assert rekey_all(dsn=test_dsn) == 0


# --- Key file source ------------------------------------------------------


def test_key_file_mode_0600_accepted(test_dsn, tmp_path, monkeypatch):
    old, _new = _two_keys(monkeypatch)
    key_file = _write_key_file(tmp_path / "vault.key", [old], mode=0o600)
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY", raising=False)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY_FILE", str(key_file))
    uid = create_user("vault-file-a@example.com", "pw", dsn=test_dsn)
    vault.put(uid, "gmail_token", "token-A", dsn=test_dsn)
    assert vault.get(uid, "gmail_token", dsn=test_dsn) == "token-A"


def test_key_file_mode_0644_refused(test_dsn, tmp_path, monkeypatch):
    old, _new = _two_keys(monkeypatch)
    key_file = _write_key_file(tmp_path / "vault.key", [old], mode=0o644)
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY", raising=False)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY_FILE", str(key_file))
    uid = uuid.uuid4()
    with pytest.raises(VaultError, match="0600"):
        vault.put(uid, "gmail_token", "token-A", dsn=test_dsn)


def test_key_file_mode_0640_refused(test_dsn, tmp_path, monkeypatch):
    old, _new = _two_keys(monkeypatch)
    key_file = _write_key_file(tmp_path / "vault.key", [old], mode=0o640)
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY", raising=False)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY_FILE", str(key_file))
    with pytest.raises(VaultError, match="0600"):
        vault.get(uuid.uuid4(), "gmail_token", dsn=test_dsn)


def test_key_file_missing_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY", raising=False)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY_FILE", str(tmp_path / "nope.key"))
    with pytest.raises(VaultError):
        vault.get(uuid.uuid4(), "gmail_token")


def test_key_file_rotation_with_env_primary(test_dsn, tmp_path, monkeypatch):
    old, new = _two_keys(monkeypatch)
    key_file = _write_key_file(tmp_path / "vault.key", [old], mode=0o600)
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY", raising=False)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY_FILE", str(key_file))
    uid = create_user("vault-file-b@example.com", "pw", dsn=test_dsn)
    vault.put(uid, "gmail_token", "token-A", dsn=test_dsn)
    # New primary from env, old key from the file: old entries read.
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY", new)
    assert vault.get(uid, "gmail_token", dsn=test_dsn) == "token-A"
    assert rotate_entry(uid, "gmail_token", dsn=test_dsn) is True
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY_FILE", raising=False)
    assert vault.get(uid, "gmail_token", dsn=test_dsn) == "token-A"


def test_no_key_anywhere_still_fails_closed(monkeypatch):
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY_FILE", raising=False)
    with pytest.raises(VaultError):
        vault.get(uuid.uuid4(), "gmail_token")


def test_key_file_blank_lines_ignored(test_dsn, tmp_path, monkeypatch):
    old, _new = _two_keys(monkeypatch)
    key_file = tmp_path / "vault.key"
    key_file.write_text(f"\n{old}\n\n", encoding="utf-8")
    os.chmod(key_file, 0o600)
    monkeypatch.delenv("PUSPA_VAULT_MASTER_KEY", raising=False)
    monkeypatch.setenv("PUSPA_VAULT_MASTER_KEY_FILE", str(key_file))
    uid = create_user("vault-file-c@example.com", "pw", dsn=test_dsn)
    vault.put(uid, "gmail_token", "token-A", dsn=test_dsn)
    assert vault.get(uid, "gmail_token", dsn=test_dsn) == "token-A"
