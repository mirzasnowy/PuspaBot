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


def _buat_db_restore():
    with psycopg.connect("dbname=postgres user=root host=/var/run/postgresql", autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS puspa_test_restore")
        conn.execute("CREATE DATABASE puspa_test_restore OWNER root")


def _hapus_db_restore():
    with psycopg.connect("dbname=postgres user=root host=/var/run/postgresql", autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS puspa_test_restore")


def test_cadangan_tanpa_kunci_ditolak(tmp_path, test_dsn, monkeypatch):
    monkeypatch.delenv("PUSPA_BACKUP_KEY", raising=False)
    with pytest.raises(BackupError):
        create_backup(dsn=test_dsn, backup_dir=tmp_path)


def test_buat_dan_daftar_cadangan(make_user, test_dsn, backup_key, tmp_path):
    u = make_user()
    profile.add_fact(u["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    hasil = create_backup(dsn=test_dsn, backup_dir=tmp_path)
    path = Path(hasil["path"])
    assert path.exists() and path.stat().st_size > 0
    assert (path.parent / (path.name + ".json")).exists()
    assert hasil["manifest"]["database"] == "puspa_test"
    assert hasil["manifest"]["ukuran_terenkripsi"] > 0
    daftar = list_backups(tmp_path)
    assert len(daftar) == 1 and daftar[0]["path"] == str(path)


def test_retensi_14_hari_menghapus_yang_tua(backup_key, tmp_path):
    lama = datetime.now(timezone.utc) - timedelta(days=15)
    (tmp_path / "puspa-20200101-000000.dump.enc").write_bytes(b"x")
    baru = tmp_path / f"puspa-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.dump.enc"
    baru.write_bytes(b"x")
    dihapus = prune_backups(tmp_path, now=datetime.now(timezone.utc))
    assert dihapus == ["puspa-20200101-000000.dump.enc"]
    assert baru.exists()


def test_restore_ke_db_bersih_dan_verifikasi(make_user, test_dsn, backup_key, tmp_path, ledger):
    """Uji restore pertama (dijalankan nyata di Fase 1)."""
    u = make_user()
    profile.add_fact(u["user_id"], "nama", "Nero", "diucapkan_user", dsn=test_dsn)
    tugas_id = "11111111-1111-1111-1111-111111111111"
    ledger.append_event(
        tugas_id=tugas_id, agen_id=u["agen_id"], jenis="tugas_dibuat",
        isi={"tugas": "uji restore"},
    )
    hasil = create_backup(dsn=test_dsn, backup_dir=tmp_path)
    _buat_db_restore()
    try:
        restored = restore_backup(hasil["path"], RESTORE_DSN)
        assert "kejadian" in restored["tabel"]
        assert "fakta_profil" in restored["tabel"]
        assert "pengguna" in restored["tabel"]
        counts = verify_restored_counts(RESTORE_DSN, ["pengguna", "fakta_profil", "kejadian"])
        assert counts["pengguna"] >= 1
        assert counts["fakta_profil"] == 1
        assert counts["kejadian"] == 1
        # Rantai hash Buku Kerja tetap utuh sesudah restore.
        ok, masalah = Ledger(RESTORE_DSN).verify_chain(tugas_id)
        assert ok, masalah
    finally:
        _hapus_db_restore()
