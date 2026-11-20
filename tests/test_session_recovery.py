"""Recovery from corrupt state + housekeeping + spawner env (G26).

Fail-closed is the rule: a corrupt ``state.db`` is never deleted,
truncated, or overwritten automatically. Recovery is inspection
first, then a documented restore.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from puspa.sessions import (
    CorruptDatabaseError,
    ProfileError,
    maintenance,
    open_persistence,
    open_persistence_reader,
    recover_report,
)
from puspa.sessions.spawner import child_env, home_from_env, require_home_in_env


@pytest.mark.integration
def test_garbage_file_fails_closed(tmp_path):
    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"this is not a sqlite database at all")
    with pytest.raises(CorruptDatabaseError):
        open_persistence(home=tmp_path)
    # The file is still there, untouched, for the operator to recover.
    assert db_path.read_bytes() == b"this is not a sqlite database at all"


def test_header_only_garbage_is_corrupt(tmp_path):
    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    report = recover_report(db_path)
    assert report["status"] == "corrupt"
    assert report["problems"]
    assert any("do NOT delete" in step for step in report["steps"])


@pytest.mark.integration
def test_recover_report_states(tmp_path):
    missing = recover_report(tmp_path / "state.db")
    assert missing["status"] == "missing"
    empty_path = tmp_path / "empty.db"
    empty_path.write_bytes(b"")
    assert recover_report(empty_path)["status"] == "empty"
    garbage_path = tmp_path / "garbage.db"
    garbage_path.write_bytes(b"garbage")
    assert recover_report(garbage_path)["status"] == "corrupt"
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("healthy")
        gateway.append_message(session["id"], "user", "PLN bill")
    healthy = recover_report(tmp_path / "state.db")
    assert healthy["status"] == "healthy"
    assert healthy["steps"] == ["no action needed"]


def test_recover_report_never_modifies_the_file(tmp_path):
    db_path = tmp_path / "state.db"
    db_path.write_bytes(b"garbage bytes here")
    before = db_path.read_bytes()
    recover_report(db_path)
    assert db_path.read_bytes() == before


@pytest.mark.integration
def test_maintenance_report_on_healthy_db(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("healthy")
        gateway.append_message(session["id"], "user", "PLN bill reminder")
        report = maintenance(gateway.store)
        assert report["ok"]
        assert report["problems"] == []
        assert report["fts"]["ok"]
        assert report["counts"] == {
            "sessions": 1,
            "messages": 1,
            "session_lineage": 0,
            "model_config": 0,
        }
        assert gateway.store.get_state_meta("last_maintenance") is not None


@pytest.mark.integration
def test_maintenance_rebuilds_drifted_fts(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("drift")
        gateway.append_message(session["id"], "user", "PLN bill reminder")
        gateway.store._conn.execute("DELETE FROM messages_fts WHERE rowid = 1")
        gateway.store._conn.commit()
        report = maintenance(gateway.store, rebuild_index=True)
        assert report["ok"]
        assert report["fts_rebuilt"]


@pytest.mark.integration
def test_maintenance_with_vacuum(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("vacuum")
        gateway.append_message(session["id"], "user", "hello")
        report = maintenance(gateway.store, vacuum=True)
        assert report["ok"] and report["vacuumed"]
        assert len(gateway.search_messages("hello")) == 1


def test_reader_on_corrupt_db_fails_closed(tmp_path):
    (tmp_path / "state.db").write_bytes(b"not sqlite at all")
    with pytest.raises(CorruptDatabaseError):
        open_persistence_reader(home=tmp_path)


def test_child_env_always_sets_puspa_home(tmp_path):
    env = child_env(home=tmp_path, base_env={"PATH": "/usr/bin", "OTHER": "x"})
    assert env["PUSPA_HOME"] == str(tmp_path)
    assert env["PATH"] == "/usr/bin"
    assert env["OTHER"] == "x"


def test_require_home_in_env_enforces_propagation():
    with pytest.raises(ProfileError):
        require_home_in_env({})
    with pytest.raises(ProfileError):
        require_home_in_env({"PUSPA_HOME": ""})
    assert require_home_in_env({"PUSPA_HOME": "/tmp/home"}) == "/tmp/home"


def test_home_from_env_returns_none_when_dropped():
    assert home_from_env({}) is None
    assert home_from_env({"PUSPA_HOME": "/tmp/home"}) == "/tmp/home"


def test_child_process_sees_the_propagated_home(tmp_path):
    # A child spawned without PUSPA_HOME would fall back to the
    # default profile; with child_env it resolves to the same home.
    env = child_env(home=tmp_path, base_env={"PATH": "/usr/bin"})
    require_home_in_env(env)
    code = "import os; print(os.environ.get('PUSPA_HOME', 'MISSING'))"
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == str(tmp_path)
