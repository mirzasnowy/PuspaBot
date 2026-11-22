"""Cross-process single-writer lock: flock + Postgres advisory (E-07).

Proves the plan 5.2 semantics with two processes on one volume and
one database: a second writer for the same agent is refused at open
time, whether the collision is on the shared file (flock) or on the
shared database (advisory lock), and a crashed holder (SIGKILL)
releases both layers so a restart can open again.

Honest limit, recorded here and in ``docs/session-storage.md``:
there is no Docker on this host, so the literal two-container form
of the test cannot run live here. The subprocesses below are the
same proof at the process level — a container boundary adds
isolation, not a new lock semantic — and the container form is
CI / E-02 work.
"""

from __future__ import annotations

import errno
import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import psycopg
import pytest

from puspa.sessions import (
    SingleWriterError,
    open_persistence,
    open_persistence_reader,
)
from puspa.sessions import single_writer as single_writer_module
from puspa.sessions.single_writer import (
    PostgresAdvisoryLock,
    SingleWriterLock,
    StateDbFileLock,
    advisory_lock_key,
    default_agent_id,
)

ROOT = Path(__file__).resolve().parent.parent

_HOLDER_CODE = (
    "import sys, time\n"
    "from puspa.sessions import open_persistence\n"
    "gw = open_persistence(home=sys.argv[1], agent_id=sys.argv[2], "
    "dsn=sys.argv[3], advisory_lock=sys.argv[4] == '1')\n"
    "print('OPENED', flush=True)\n"
    "time.sleep(120)\n"
)

_BOGUS_DSN = "dbname=puspa_test_no_such_db user=root host=/var/run/postgresql"


def _spawn_holder(tmp_path, agent_id, test_dsn, advisory=True):
    """Start a child holding a gateway open; wait for its OPENED line."""
    env = dict(os.environ, PUSPA_TEST_DSN=test_dsn)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _HOLDER_CODE,
            str(tmp_path),
            agent_id,
            test_dsn,
            "1" if advisory else "0",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline()
    assert line.strip() == "OPENED", proc.stderr.read()
    return proc


@pytest.fixture
def _stop_holder():
    holders = []
    yield holders.append
    for proc in holders:
        proc.kill()
        proc.wait()


def test_advisory_lock_key_is_deterministic_and_scoped():
    assert advisory_lock_key("agent-1") == advisory_lock_key("agent-1")
    assert advisory_lock_key("agent-1") != advisory_lock_key("agent-2")
    assert default_agent_id(None) == "profile:default"
    assert default_agent_id("work") == "profile:work"


def test_file_lock_second_acquire_is_refused(tmp_path):
    db_path = tmp_path / "state.db"
    first = StateDbFileLock(db_path)
    first.acquire()
    assert first.held
    assert db_path.exists()
    second = StateDbFileLock(db_path)
    with pytest.raises(SingleWriterError):
        second.acquire()
    assert not second.held
    first.release()
    assert not first.held


def test_file_lock_windows_mode_uses_msvcrt_on_a_sidecar(tmp_path, monkeypatch):
    """Windows has no ``fcntl``: the module must import without it and
    take the lock with ``msvcrt.locking`` on a sidecar file, never on
    ``state.db`` itself (a Windows byte-range lock is mandatory and
    would block SQLite's own reads). Run here as a Windows-mode copy of
    the module with a fake ``msvcrt`` built on ``flock``, so the Linux
    lane covers the Windows branch too."""
    fcntl = pytest.importorskip("fcntl")
    lk_unlck, lk_nblck = 0, 2  # the real msvcrt constants
    calls: list[tuple[int, int]] = []

    def locking(fd, mode, nbytes):
        calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR)))
        if mode == lk_unlck:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OSError(errno.EACCES, "Permission denied") from exc

    fake_msvcrt = types.ModuleType("msvcrt")
    fake_msvcrt.__dict__.update(LK_NBLCK=lk_nblck, LK_UNLCK=lk_unlck, locking=locking)
    spec = importlib.util.spec_from_file_location(
        "puspa.sessions._single_writer_windows", single_writer_module.__file__
    )
    assert spec is not None and spec.loader is not None
    windows_copy = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "win32")
        patch.setitem(sys.modules, "msvcrt", fake_msvcrt)
        patch.setitem(sys.modules, "fcntl", None)  # `import fcntl` would raise
        spec.loader.exec_module(windows_copy)
    assert not hasattr(windows_copy, "fcntl")

    db_path = tmp_path / "state.db"
    first = windows_copy.StateDbFileLock(db_path)
    assert first.lock_path == tmp_path / "state.db.lock"
    first.acquire()
    assert first.held
    assert not db_path.exists(), "state.db itself must not be touched or locked"
    second = windows_copy.StateDbFileLock(db_path)
    with pytest.raises(SingleWriterError):
        second.acquire()
    assert not second.held
    first.release()
    second.acquire()
    assert second.held
    second.release()
    assert [mode for mode, _ in calls] == [lk_nblck, lk_nblck, lk_unlck, lk_nblck, lk_unlck]
    assert {pos for _, pos in calls} == {0}, "msvcrt locks from the current position: seek to 0"


def test_advisory_lock_second_acquire_is_refused(test_dsn):
    first = PostgresAdvisoryLock(test_dsn, "agent-e07-a")
    first.acquire()
    assert first.held
    other = PostgresAdvisoryLock(test_dsn, "agent-e07-b")
    other.acquire()
    assert other.held
    second = PostgresAdvisoryLock(test_dsn, "agent-e07-a")
    with pytest.raises(SingleWriterError):
        second.acquire()
    assert not second.held
    first.release()
    other.release()


def test_advisory_lock_unreachable_database_fails_closed():
    lock = PostgresAdvisoryLock(_BOGUS_DSN, "agent-e07-c")
    with pytest.raises(SingleWriterError):
        lock.acquire()


def test_failed_advisory_acquire_releases_the_file_lock(test_dsn, tmp_path):
    db_path = tmp_path / "state.db"
    holder = psycopg.connect(test_dsn, autocommit=True)
    assert holder.execute(
        "SELECT pg_try_advisory_lock(%s)",
        (advisory_lock_key("agent-e07-d"),),
    ).fetchone()[0]
    lock = SingleWriterLock(db_path, "agent-e07-d", dsn=test_dsn)
    with pytest.raises(SingleWriterError):
        lock.acquire()
    assert not lock.file_held and not lock.advisory_held, (
        "A refused open must leave nothing held: the flock taken first\n"
        "    is released again when the advisory lock fails."
    )
    holder.close()


def test_second_process_same_volume_is_refused(test_dsn, tmp_path, _stop_holder):
    """Two processes, one volume, advisory layer disabled: the flock
    alone refuses the second writer."""
    holder = _spawn_holder(tmp_path, agent_id="agent-e07-f", test_dsn=test_dsn, advisory=False)
    _stop_holder(holder)
    with pytest.raises(SingleWriterError):
        open_persistence(home=tmp_path, agent_id="agent-e07-f", advisory_lock=False)


def test_second_process_same_agent_different_volume_is_refused(test_dsn, tmp_path, _stop_holder):
    """Two processes, different volumes, same agent and database:
    the flock cannot see this collision (different files), so the
    advisory lock is the layer that refuses the second writer."""
    home_a = tmp_path / "a"
    home_b = tmp_path / "b"
    holder = _spawn_holder(home_a, agent_id="agent-e07-g", test_dsn=test_dsn)
    _stop_holder(holder)
    with pytest.raises(SingleWriterError):
        open_persistence(home=home_b, agent_id="agent-e07-g", dsn=test_dsn)


def test_second_process_same_volume_and_agent_is_refused(test_dsn, tmp_path, _stop_holder):
    """Both layers on: a second writer for the same agent on the same
    volume is refused at open time."""
    holder = _spawn_holder(tmp_path, agent_id="agent-e07-h", test_dsn=test_dsn)
    _stop_holder(holder)
    with pytest.raises(SingleWriterError):
        open_persistence(home=tmp_path, agent_id="agent-e07-h", dsn=test_dsn)


def test_crashed_holder_releases_both_layers(test_dsn, tmp_path, _stop_holder):
    """SIGKILL the holder: the OS releases the flock and Postgres
    releases the session advisory lock, so a restart can open."""
    holder = _spawn_holder(tmp_path, agent_id="agent-e07-i", test_dsn=test_dsn)
    _stop_holder(holder)
    holder.kill()
    holder.wait()
    gateway = open_persistence(home=tmp_path, agent_id="agent-e07-i", dsn=test_dsn)
    session = gateway.create_session("after crash")
    assert gateway.get_session(session["id"])["title"] == "after crash"
    gateway.close()


def test_gateway_close_releases_the_advisory_lock(test_dsn, tmp_path):
    gateway = open_persistence(home=tmp_path, agent_id="agent-e07-j", dsn=test_dsn)
    assert gateway.advisory_locked
    assert gateway.agent_id == "agent-e07-j"
    gateway.close()
    assert not gateway.advisory_locked
    reopened = open_persistence(home=tmp_path, agent_id="agent-e07-j", dsn=test_dsn)
    assert reopened.advisory_locked
    reopened.close()


def test_gateway_open_with_unreachable_postgres_fails_closed(tmp_path):
    with pytest.raises(SingleWriterError):
        open_persistence(home=tmp_path, agent_id="agent-e07-k", dsn=_BOGUS_DSN)
    gateway = open_persistence(home=tmp_path, agent_id="agent-e07-k", advisory_lock=False)
    assert not gateway.advisory_locked
    session = gateway.create_session("local only")
    assert gateway.get_session(session["id"])["title"] == "local only"
    gateway.close()


def test_reader_is_unaffected_by_writer_locks(test_dsn, tmp_path):
    gateway = open_persistence(home=tmp_path, agent_id="agent-e07-l", dsn=test_dsn)
    session = gateway.create_session("shared")
    gateway.append_message(session["id"], "user", "hello")
    reader = open_persistence_reader(home=tmp_path)
    assert len(reader.list_sessions()) == 1
    gateway.close()
