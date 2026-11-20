"""Restore drill end to end (E-09, Gate 15).

Runs the drill against a scratch Postgres instance on this host:
base backup + WAL replay after a simulated disaster. This proves
the restore path locally; the live clean-host drill on the VPS is
still pending (E-02).
"""

import shutil
from pathlib import Path

import pytest

from puspa.backup.drill import (
    TARGET_SECONDS,
    render_drill_markdown,
    run_restore_drill,
)


def _pg_binaries_present() -> bool:
    bindir = Path("/usr/lib/postgresql/16/bin")
    if shutil.which("pg_config") is not None:
        return True
    return (bindir / "initdb").exists() and (bindir / "pg_basebackup").exists()


pytestmark = pytest.mark.skipif(
    not _pg_binaries_present(), reason="no local Postgres binaries for the drill"
)


def test_restore_drill_passes_within_target():
    # No work_root: the drill uses a fresh mkdtemp dir. A pytest
    # tmp_path has root-owned 0700 ancestors the postgres OS user
    # cannot traverse; the drill's own temp root is opened up for it.
    report = run_restore_drill()
    assert report["passed"] is True, report
    assert report["total_seconds"] < TARGET_SECONDS
    assert report["within_target"] is True
    assert "error" not in report
    names = {item["name"] for item in report["checks"]}
    assert "wal_segment_archived" in names
    assert "wal_chain_continuous" in names
    assert "vault_canary_before_backup" in names
    # The post-backup canary is the proof WAL replay works, not just
    # the base backup.
    assert "vault_canary_after_backup" in names
    assert "escrowed_key_unlocks_vault" in names
    assert "state_snapshot_hash" in names
    assert "recovery_completed" in names
    assert all(item["ok"] for item in report["checks"]), report["checks"]
    assert set(report["phases"]) == {
        "init_scratch_instance",
        "apply_migrations",
        "seed_before_backup",
        "snapshot_state_db",
        "base_backup",
        "seed_after_backup",
        "switch_wal_and_archive",
        "simulate_disaster",
        "restore_and_recover",
        "verify",
    }


def test_render_drill_markdown_marks_pass():
    report = run_restore_drill()
    text = render_drill_markdown(report)
    assert "PASS" in text
    assert "Total:" in text
    assert "vault_canary_after_backup" in text
