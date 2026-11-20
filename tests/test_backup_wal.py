"""WAL archiving, state.db snapshots, and vault key escrow (E-09)."""

import base64
import os
import sqlite3
from pathlib import Path

import pytest

from puspa.accounts import vault
from puspa.backup.local_state import (
    KeyEscrowError,
    StateSnapshotError,
    check_key_separation,
    escrow_vault_key,
    sha256_file,
    snapshot_state_db,
    verify_escrowed_key,
)
from puspa.backup.wal import (
    WalError,
    archive_command,
    check_archiving,
    pgbackrest_config,
    verify_wal_chain,
    walg_env,
)


def _segment(timeline=1, log=0, segment=1):
    return f"{timeline:08X}{log:08X}{segment:08X}"


# --- archive_command / backend configs -------------------------------------


def test_archive_command_pgbackrest():
    assert archive_command("pgbackrest") == "pgbackrest --stanza=puspa archive-push %p"
    assert archive_command("pgbackrest", stanza="prod") == (
        "pgbackrest --stanza=prod archive-push %p"
    )


def test_archive_command_walg():
    assert archive_command("wal-g") == "wal-g wal-push %p"


def test_archive_command_local_needs_dir_and_guards_rearchive(tmp_path):
    with pytest.raises(WalError):
        archive_command("local")
    cmd = archive_command("local", archive_dir=tmp_path)
    assert str(tmp_path) in cmd and "test ! -f" in cmd and "%p" in cmd and "%f" in cmd


def test_archive_command_unknown_backend():
    with pytest.raises(WalError):
        archive_command("rsync")


def test_pgbackrest_config_s3_is_encrypted_and_off_host():
    cfg = pgbackrest_config(
        s3_bucket="puspa-backups",
        s3_endpoint="https://s3.example.invalid",
        s3_region="sgp1",
    )
    assert "repo1-type=s3" in cfg
    assert "repo1-s3-bucket=puspa-backups" in cfg
    assert "repo1-s3-endpoint=https://s3.example.invalid" in cfg
    assert "repo1-cipher-type=aes-256-cbc" in cfg
    assert "[puspa]" in cfg


def test_pgbackrest_config_local_path_is_dev_only():
    cfg = pgbackrest_config(repo_path="/tmp/pgbackrest")
    assert "repo1-path=/tmp/pgbackrest" in cfg
    assert "repo1-type=s3" not in cfg


def test_walg_env_has_no_secrets():
    env = walg_env(
        s3_prefix="s3://puspa-backups/wal",
        s3_endpoint="https://s3.example.invalid",
        s3_region="sgp1",
    )
    assert env["WALG_S3_PREFIX"] == "s3://puspa-backups/wal"
    assert env["AWS_ENDPOINT"] == "https://s3.example.invalid"
    assert "AWS_ACCESS_KEY_ID" not in env and "AWS_SECRET_ACCESS_KEY" not in env


# --- check_archiving --------------------------------------------------------


def test_check_archiving_reports_unconfigured_cluster(test_dsn):
    # The shared dev cluster has archive_mode off: the report must say
    # so instead of raising, so the caller can decide.
    status = check_archiving(test_dsn)
    assert status["configured"] is False
    assert status["archive_mode"] == "off"
    assert status["healthy"] is False
    assert status["failed_count"] == 0


# --- verify_wal_chain -------------------------------------------------------


def test_verify_wal_chain_clean(tmp_path):
    for n in (1, 2, 3):
        (tmp_path / _segment(segment=n)).write_bytes(b"x")
    result = verify_wal_chain(tmp_path)
    assert result["ok"] is True
    assert result["segment_count"] == 3
    assert result["gaps"] == []


def test_verify_wal_chain_finds_gap(tmp_path):
    (tmp_path / _segment(segment=1)).write_bytes(b"x")
    (tmp_path / _segment(segment=3)).write_bytes(b"x")
    result = verify_wal_chain(tmp_path)
    assert result["ok"] is False
    assert result["gaps"] == [{"after": _segment(segment=1), "expected": _segment(segment=2)}]


def test_verify_wal_chain_rollover_to_next_log(tmp_path):
    (tmp_path / _segment(segment=255)).write_bytes(b"x")
    (tmp_path / _segment(log=1, segment=0)).write_bytes(b"x")
    assert verify_wal_chain(tmp_path)["ok"] is True


def test_verify_wal_chain_timeline_switch_is_not_a_gap(tmp_path):
    (tmp_path / _segment(timeline=1, segment=5)).write_bytes(b"x")
    (tmp_path / _segment(timeline=2, segment=0)).write_bytes(b"x")
    assert verify_wal_chain(tmp_path)["ok"] is True


def test_verify_wal_chain_missing_dir(tmp_path):
    with pytest.raises(WalError):
        verify_wal_chain(tmp_path / "nope")


def test_verify_wal_chain_ignores_backup_files(tmp_path):
    (tmp_path / _segment(segment=1)).write_bytes(b"x")
    (tmp_path / "000000010000000000000001.00000028.backup").write_bytes(b"x")
    result = verify_wal_chain(tmp_path)
    assert result["backup_files"] == ["000000010000000000000001.00000028.backup"]
    assert result["segment_count"] == 1


# --- state.db snapshot ------------------------------------------------------


def _make_state_db(home: Path):
    profile_dir = home / "profiles" / "default"
    profile_dir.mkdir(parents=True)
    src = profile_dir / "state.db"
    with sqlite3.connect(src) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO notes (body) VALUES ('hello')")
        conn.commit()
    return src


def test_snapshot_state_db_round_trip(tmp_path):
    home = tmp_path / "home"
    src = _make_state_db(home)
    result = snapshot_state_db(profile="default", home=home, dest_dir=tmp_path / "snaps")
    dest = Path(result["path"])
    assert dest.exists()
    assert result["source"] == str(src)
    assert result["sha256"] == sha256_file(dest)
    assert result["size"] == dest.stat().st_size
    with sqlite3.connect(dest) as conn:
        rows = conn.execute("SELECT body FROM notes").fetchall()
    assert rows == [("hello",)]


def test_snapshot_state_db_missing_source(tmp_path):
    with pytest.raises(StateSnapshotError):
        snapshot_state_db(profile="default", home=tmp_path / "empty", dest_dir=tmp_path)


# --- vault key escrow -------------------------------------------------------


def _good_key():
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def test_escrow_rejects_bad_keys(tmp_path):
    with pytest.raises(KeyEscrowError):
        escrow_vault_key("not-base64!!", tmp_path / "k")
    with pytest.raises(KeyEscrowError):
        escrow_vault_key(base64.urlsafe_b64encode(b"short").decode(), tmp_path / "k")


def test_escrow_refuses_path_inside_data_backups(tmp_path):
    data_dir = tmp_path / "backups"
    data_dir.mkdir()
    with pytest.raises(KeyEscrowError):
        escrow_vault_key(_good_key(), data_dir / "vault-master.key", data_backup_dir=data_dir)
    ok, _reason = check_key_separation(data_dir / "vault-master.key", data_dir)
    assert ok is False


def test_escrow_writes_mode_0600_and_is_idempotent(tmp_path):
    key = _good_key()
    escrow = tmp_path / "escrow" / "vault-master.key"
    result = escrow_vault_key(key, escrow, data_backup_dir=tmp_path / "backups")
    assert Path(result["path"]).exists()
    assert oct(Path(result["path"]).stat().st_mode & 0o777) == "0o600"
    assert escrow.read_text(encoding="utf-8").strip() == key
    again = escrow_vault_key(key, escrow, data_backup_dir=tmp_path / "backups")
    assert again["unchanged"] is True
    ok, _reason = check_key_separation(escrow, tmp_path / "backups")
    assert ok is True


def test_escrow_refuses_overwriting_a_different_key(tmp_path):
    escrow = tmp_path / "vault-master.key"
    escrow_vault_key(_good_key(), escrow)
    with pytest.raises(KeyEscrowError):
        escrow_vault_key(_good_key(), escrow)


def test_verify_escrowed_key_unlocks_vault(make_user, test_dsn, vault_master_key, tmp_path):
    user = make_user()
    vault.put(user["user_id"], "gmail-token", "secret-token", dsn=test_dsn)
    escrow = tmp_path / "vault-master.key"
    escrow_vault_key(vault_master_key, escrow)
    result = verify_escrowed_key(escrow, test_dsn)
    assert result["checked"] == 1
    assert result["ok"] is True
    assert result["failures"] == []


def test_verify_escrowed_key_wrong_key_fails(make_user, test_dsn, vault_master_key, tmp_path):
    user = make_user()
    vault.put(user["user_id"], "gmail-token", "secret-token", dsn=test_dsn)
    escrow = tmp_path / "vault-master.key"
    escrow_vault_key(_good_key(), escrow)
    result = verify_escrowed_key(escrow, test_dsn)
    assert result["ok"] is False
    assert result["failures"] == ["gmail-token"]


def test_verify_escrowed_key_empty_vault(test_dsn, vault_master_key, tmp_path):
    escrow = tmp_path / "vault-master.key"
    escrow_vault_key(vault_master_key, escrow)
    result = verify_escrowed_key(escrow, test_dsn)
    assert result["checked"] == 0
    assert result["ok"] is True
    assert result["note"] is not None
