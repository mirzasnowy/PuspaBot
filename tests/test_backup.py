"""Tes Cadangan terenkripsi + restore (C126, gerbang 15 parsial)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

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


def _db_name(dsn: str) -> str:
    """The dbname inside a DSN string (default ``puspa_test``)."""
    return str(conninfo_to_dict(dsn).get("dbname") or "puspa_test")


def _restore_dsn(dsn: str) -> str:
    """The restore DSN for a test DSN: its database + ``_restore``.

    Per-DSN on purpose: two worktrees run their suites against
    different databases (``puspa_test_w1`` / ``puspa_test_w2``),
    so a shared ``puspa_test_restore`` database would race.
    """
    info = conninfo_to_dict(dsn)
    return make_conninfo(
        dbname=_db_name(dsn) + "_restore",
        user=info.get("user"),
        host=info.get("host"),
    )


def _create_db_restore(dsn: str) -> None:
    with psycopg.connect(
        "dbname=postgres user=root host=/var/run/postgresql", autocommit=True
    ) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{_db_name(dsn) + "_restore"}"')
        conn.execute(f'CREATE DATABASE "{_db_name(dsn) + "_restore"}" OWNER root')


def _delete_db_restore(dsn: str) -> None:
    with psycopg.connect(
        "dbname=postgres user=root host=/var/run/postgresql", autocommit=True
    ) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{_db_name(dsn) + "_restore"}"')


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
    assert result["manifest"]["database"] == _db_name(test_dsn)
    assert result["manifest"]["encrypted_size"] > 0
    items = list_backups(tmp_path)
    assert len(items) == 1 and items[0]["path"] == str(path)


def test_retention_14_day_deletes_older_ones(backup_key, tmp_path):
    datetime.now(UTC) - timedelta(days=15)
    (tmp_path / "puspa-20200101-000000.dump.enc").write_bytes(b"x")
    new = tmp_path / f"puspa-{datetime.now(UTC):%Y%m%d-%H%M%S}.dump.enc"
    new.write_bytes(b"x")
    deleted = prune_backups(tmp_path, now=datetime.now(UTC))
    assert deleted == ["puspa-20200101-000000.dump.enc"]
    assert new.exists()


def test_restore_to_clean_db_and_verify(make_user, test_dsn, backup_key, tmp_path, ledger):
    """Uji restore pertama (dijalankan nyata di Fase 1)."""
    u = make_user()
    profile.add_fact(u["user_id"], "name", "Nero", "user_stated", dsn=test_dsn)
    task_id = "11111111-1111-1111-1111-111111111111"
    ledger.append_event(
        task_id=task_id,
        agent_id=u["agent_id"],
        event_type="task_created",
        content={"tasks": "uji restore"},
    )
    result = create_backup(dsn=test_dsn, backup_dir=tmp_path)
    restore_dsn = _restore_dsn(test_dsn)
    _create_db_restore(test_dsn)
    try:
        restored = restore_backup(result["path"], restore_dsn)
        assert "events" in restored["table"]
        assert "profile_facts" in restored["table"]
        assert "users" in restored["table"]
        counts = verify_restored_counts(restore_dsn, ["users", "profile_facts", "events"])
        assert counts["users"] >= 1
        assert counts["profile_facts"] == 1
        assert counts["events"] == 1
        # Rantai hash Buku Kerja tetap utuh sesudah restore.
        ok, problems = Ledger(restore_dsn).verify_chain(task_id)
        assert ok, problems
    finally:
        _delete_db_restore(test_dsn)
