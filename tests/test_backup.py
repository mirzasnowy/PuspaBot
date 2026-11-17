"""Tes Cadangan terenkripsi + restore (C126, gerbang 15 parsial)."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

from puspa.backup import (
    BackupError,
    create_backup,
    list_backups,
    prune_backups,
    restore_backup,
    verify_restored_counts,
)
from puspa.ledger import Ledger
from puspa.memory import profile

RESTORE_DSN = "dbname=puspa_test_restore user=root host=/var/run/postgresql"


def _create_db_restore():
    with psycopg.connect("dbname=postgres user=root host=/var/run/postgresql", autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS puspa_test_restore")
        conn.execute("CREATE DATABASE puspa_test_restore OWNER root")


def _delete_db_restore():
    with psycopg.connect("dbname=postgres user=root host=/var/run/postgresql", autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS puspa_test_restore")


def test_backup_without_key_rejected(tmp_path, test_dsn, monkeypatch):
    monkeypatch.delenv("PUSPA_BACKUP_KEY", raising=False)
    with pytest.raises(BackupError):
        create_backup(dsn=test_dsn, backup_dir=tmp_path)


def test_create_and_backup_items(make_user, test_dsn, backup_key, tmp_path):
    u = make_user()
    profile.add_fact(u["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    result = create_backup(dsn=test_dsn, backup_dir=tmp_path)
    path = Path(result["path"])
    assert path.exists() and path.stat().st_size > 0
    assert (path.parent / (path.name + ".json")).exists()
    assert result["manifest"]["database"] == "puspa_test"
    assert result["manifest"]["encrypted_size"] > 0
    items = list_backups(tmp_path)
    assert len(items) == 1 and items[0]["path"] == str(path)


def test_retention_14_day_deletes_older_ones(backup_key, tmp_path):
    old = datetime.now(timezone.utc) - timedelta(days=15)
    (tmp_path / "puspa-20200101-000000.dump.enc").write_bytes(b"x")
    new = tmp_path / f"puspa-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.dump.enc"
    new.write_bytes(b"x")
    deleted = prune_backups(tmp_path, now=datetime.now(timezone.utc))
    assert deleted == ["puspa-20200101-000000.dump.enc"]
    assert new.exists()


def test_restore_to_clean_db_and_verify(make_user, test_dsn, backup_key, tmp_path, ledger):
    """Uji restore pertama (dijalankan nyata di Fase 1)."""
    u = make_user()
    profile.add_fact(u["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    task_id = "11111111-1111-1111-1111-111111111111"
    ledger.append_event(
        task_id=task_id, agent_id=u["agent_id"], event_type="task_created",
        content={"tasks": "uji restore"},
    )
    result = create_backup(dsn=test_dsn, backup_dir=tmp_path)
    _create_db_restore()
    try:
        restored = restore_backup(result["path"], RESTORE_DSN)
        assert "events" in restored["table"]
        assert "profile_facts" in restored["table"]
        assert "users" in restored["table"]
        counts = verify_restored_counts(RESTORE_DSN, ["users", "profile_facts", "events"])
        assert counts["users"] >= 1
        assert counts["profile_facts"] == 1
        assert counts["events"] == 1
        # Rantai hash Buku Kerja tetap utuh sesudah restore.
        ok, problems = Ledger(RESTORE_DSN).verify_chain(task_id)
        assert ok, problems
    finally:
        _delete_db_restore()
