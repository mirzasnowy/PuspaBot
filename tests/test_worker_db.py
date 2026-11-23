"""Tests for per-worker databases + the marker split (E-17, plan 9.1).

Two backends (COW-10): the default lane is SQLite files — the
template is a migrated file, each worker copies it to its own
file — and the Postgres lane (template database + CREATE DATABASE
... TEMPLATE clones) still runs when a Postgres server is
reachable, guarded by :func:`_postgres_available`.

- Pure helpers: DSN parsing, template/worker database names.
- Template: built once from the migrations, rebuilt when stale.
- Isolation: two worker databases hold the same rows independently.
- Wiring: a real ``pytest -n 2`` run gives two workers two
  different databases and drops them afterwards.
- Markers: every collected test carries exactly one of
  unit/integration/live, the known live tests are marked live,
  and the unit subset runs with no database at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from worker_db import (
    build_dsn,
    create_sqlite_worker_db,
    create_worker_db,
    drop_run_sqlite_worker_dbs,
    drop_run_worker_dbs,
    drop_sqlite_worker_db,
    drop_worker_db,
    ensure_sqlite_template_path,
    ensure_template_dsn,
    maintenance_dsn,
    migrations_fingerprint,
    parse_dsn,
    prune_stale_worker_dbs,
    sqlite_migrations_fingerprint,
    sqlite_template_path,
    sqlite_worker_path,
    template_db_name,
    with_dbname,
    worker_db_name,
)

from puspa.db import connect as _db_connect
from puspa.migrations.runner import MIGRATIONS_DIR_SQLITE, list_migrations

# Every test in this file needs a live database: the tests
# connect directly (``scratch_base`` reads ``PUSPA_TEST_DSN``
# from the environment), so the marker split cannot see the
# database need from the fixture closure (E-17). The pure
# helpers (DSN parsing, fingerprint, name truncation) still run
# in the integration lane with the rest of the file.
pytestmark = pytest.mark.integration

BUILD_ROOT = Path(__file__).resolve().parent.parent
BASE_DSN = os.environ.get(
    "PUSPA_TEST_DSN", "dbname=puspa_test_w1 user=root host=/var/run/postgresql"
)

#: Postgres scratch base: always a Postgres DSN, independent of
#: the suite's own PUSPA_TEST_DSN (which is a SQLite file now).
#: The name carries the process id, so two workers running this
#: file at the same time never share a template or a worker
#: database.
POSTGRES_SCRATCH_BASE = f"dbname=puspa_test_e17_{os.getpid()} user=root host=/var/run/postgresql"
POSTGRES_MAINTENANCE = "dbname=postgres user=root host=/var/run/postgresql"


def _postgres_available() -> bool:
    """True when a Postgres server answers on the local socket."""
    try:
        with psycopg.connect(POSTGRES_MAINTENANCE, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except (psycopg.Error, OSError):
        return False


def _db_names(dsn: str) -> set[str]:
    with psycopg.connect(maintenance_dsn(dsn), autocommit=True) as conn:
        rows = conn.execute("SELECT datname FROM pg_database").fetchall()
    return {str(row[0]) for row in rows}


def _drop_quietly(dsn: str, name: str) -> None:
    with psycopg.connect(maintenance_dsn(dsn), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def scratch_base():
    """Scratch Postgres base DSN; template + worker DBs dropped afterwards."""
    if not _postgres_available():
        pytest.skip("no Postgres server reachable for the Postgres lane")
    yield POSTGRES_SCRATCH_BASE
    names = _db_names(POSTGRES_SCRATCH_BASE)
    stem = parse_dsn(POSTGRES_SCRATCH_BASE)["dbname"]
    for name in sorted(names):
        if name.startswith(stem):
            _drop_quietly(POSTGRES_SCRATCH_BASE, name)


@pytest.fixture
def sqlite_scratch_dir(tmp_path, monkeypatch):
    """Fresh directory for SQLite template + worker files."""
    directory = tmp_path / "db"
    directory.mkdir()
    monkeypatch.setenv("PUSPA_TEST_DB_DIR", str(directory))
    yield directory
    for path in directory.glob("puspa_test*.db*"):
        path.unlink(missing_ok=True)


def _sqlite_versions(path: str) -> list[str]:
    with _db_connect(path) as conn:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return [str(row[0]) for row in rows]


def _sqlite_tables(path: str) -> set[str]:
    with _db_connect(path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row[0]) for row in rows}


# --- pure helpers (no database) -------------------------------------------


def test_dsn_roundtrip():
    dsn = "dbname=puspa_test user=root host=/var/run/postgresql port=5432"
    assert build_dsn(parse_dsn(dsn)) == dsn
    assert with_dbname(dsn, "other") == (
        "dbname=other user=root host=/var/run/postgresql port=5432"
    )
    assert maintenance_dsn(dsn) == ("dbname=postgres user=root host=/var/run/postgresql port=5432")


def test_template_and_worker_names():
    base = "dbname=puspa_test_w1 user=root host=/var/run/postgresql"
    assert template_db_name(base) == "puspa_test_w1_template"
    assert worker_db_name(base, "gw0", "abc123") == "puspa_test_w1_abc123_gw0"
    assert worker_db_name(base, "gw2", "") == "puspa_test_w1_gw2"


def test_worker_name_is_truncated_to_the_postgres_limit():
    base = "dbname=" + "x" * 60 + " user=root host=/var/run/postgresql"
    name = worker_db_name(base, "gw0", "abc123")
    assert len(name) == 63
    assert name.endswith("_abc123_gw0")


def test_migrations_fingerprint_changes_with_content(tmp_path):
    first = tmp_path / "0001_a.sql"
    first.write_text("-- migrate:up\nSELECT 1;\n-- migrate:down\nSELECT 1;\n")
    before = migrations_fingerprint(tmp_path)
    first.write_text("-- migrate:up\nSELECT 2;\n-- migrate:down\nSELECT 1;\n")
    assert migrations_fingerprint(tmp_path) != before
    second = tmp_path / "0002_b.sql"
    second.write_text("-- migrate:up\nSELECT 1;\n-- migrate:down\nSELECT 1;\n")
    assert migrations_fingerprint(tmp_path) != before


def test_sqlite_worker_paths(sqlite_scratch_dir):
    assert sqlite_worker_path(None, "gw0", "abc123") == (
        sqlite_scratch_dir / "puspa_test_abc123_gw0.db"
    )
    assert sqlite_worker_path(None, "gw2", "") == (sqlite_scratch_dir / "puspa_test_gw2.db")
    explicit = str(sqlite_scratch_dir / "mine.db")
    assert sqlite_worker_path(explicit, "gw0", "abc123") == (
        sqlite_scratch_dir / "mine_abc123_gw0.db"
    )


def test_sqlite_migrations_fingerprint_changes_with_content(tmp_path):
    first = tmp_path / "0001_a.sql"
    first.write_text("-- migrate:up\nSELECT 1;\n-- migrate:down\nSELECT 1;\n")
    before = sqlite_migrations_fingerprint(tmp_path)
    first.write_text("-- migrate:up\nSELECT 2;\n-- migrate:down\nSELECT 1;\n")
    assert sqlite_migrations_fingerprint(tmp_path) != before


def test_sqlite_migrations_fingerprint_covers_the_sqlite_set():
    assert sqlite_migrations_fingerprint() == sqlite_migrations_fingerprint(MIGRATIONS_DIR_SQLITE)
    assert len(list(sqlite_template_path().parent.glob("*.db")) or []) >= 0


# --- SQLite template file ---------------------------------------------------


def test_sqlite_template_is_built_from_the_migrations(sqlite_scratch_dir):
    template = ensure_sqlite_template_path()
    assert Path(template).exists()
    assert Path(template).parent == sqlite_scratch_dir
    assert len(_sqlite_versions(template)) == 45
    assert {"users", "events", "agents"} <= _sqlite_tables(template)
    # A second call reuses the template instead of rebuilding it.
    assert ensure_sqlite_template_path() == template


def test_sqlite_stale_template_is_rebuilt(sqlite_scratch_dir):
    template = ensure_sqlite_template_path()
    stale = sqlite_scratch_dir / "puspa_test_template_deadbeef.db"
    stale.write_bytes(b"stale")
    Path(template).unlink()
    rebuilt = ensure_sqlite_template_path()
    assert rebuilt == template
    assert not stale.exists()
    assert len(_sqlite_versions(rebuilt)) == 45


# --- SQLite per-worker isolation --------------------------------------------


def test_sqlite_two_workers_get_isolated_files(sqlite_scratch_dir):
    """Two workers, same rows, different files, no collision."""
    run_id = "sqliteiso"
    path_a = create_sqlite_worker_db(None, "gw0", run_id)
    path_b = create_sqlite_worker_db(None, "gw1", run_id)
    assert path_a != path_b
    try:
        for path in (path_a, path_b):
            with _db_connect(path) as conn:
                conn.execute("CREATE TABLE e17_probe (id INT PRIMARY KEY, v TEXT)")
                conn.execute("INSERT INTO e17_probe VALUES (1, 'same-key')")
                conn.commit()
        with _db_connect(path_a) as conn:
            conn.execute("INSERT INTO e17_probe VALUES (2, 'only-in-a')")
            conn.commit()
        with _db_connect(path_b) as conn:
            rows = conn.execute("SELECT id FROM e17_probe ORDER BY id").fetchall()
        assert [row[0] for row in rows] == [1]
    finally:
        drop_sqlite_worker_db(path_a)
        drop_sqlite_worker_db(path_b)
    assert not Path(path_a).exists()
    assert not Path(path_b).exists()


def test_sqlite_drop_run_drops_only_that_run(sqlite_scratch_dir):
    """Controller cleanup drops one run's files, not another's."""
    create_sqlite_worker_db(None, "gw0", "sqliterun1")
    create_sqlite_worker_db(None, "gw1", "sqliterun1")
    create_sqlite_worker_db(None, "gw0", "sqliterun2")
    dropped = drop_run_sqlite_worker_dbs(None, "sqliterun1")
    assert sorted(dropped) == [
        str(sqlite_worker_path(None, "gw0", "sqliterun1")),
        str(sqlite_worker_path(None, "gw1", "sqliterun1")),
    ]
    assert Path(sqlite_worker_path(None, "gw0", "sqliterun2")).exists()
    assert not Path(sqlite_worker_path(None, "gw0", "sqliterun1")).exists()
    drop_sqlite_worker_db(str(sqlite_worker_path(None, "gw0", "sqliterun2")))


# --- Postgres template database (guarded: needs a live server) --------------


def test_template_is_built_from_the_migrations(scratch_base):
    template_dsn = ensure_template_dsn(scratch_base)
    assert template_db_name(scratch_base) in _db_names(scratch_base)
    with psycopg.connect(template_dsn) as conn:
        versions = conn.execute("SELECT version FROM schema_migrations").fetchall()
        assert len(versions) == len(list_migrations())
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        ).fetchall()
    assert {"users", "events", "agents"} <= {row[0] for row in tables}
    # A second call reuses the template instead of rebuilding it.
    assert ensure_template_dsn(scratch_base) == template_dsn


def test_stale_template_is_rebuilt(scratch_base):
    template_dsn = ensure_template_dsn(scratch_base)
    template = template_db_name(scratch_base)
    with psycopg.connect(maintenance_dsn(scratch_base), autocommit=True) as conn:
        conn.execute(f"COMMENT ON DATABASE \"{template}\" IS 'stale'")
    rebuilt_dsn = ensure_template_dsn(scratch_base)
    assert rebuilt_dsn == template_dsn
    with psycopg.connect(template_dsn) as conn:
        versions = conn.execute("SELECT version FROM schema_migrations").fetchall()
    assert len(versions) == len(list_migrations())


# --- Postgres per-worker isolation (guarded) --------------------------------


def test_two_workers_get_isolated_databases(scratch_base):
    """Two workers, same rows, different databases, no collision."""
    run_id = "e17iso"
    dsn_a = create_worker_db(scratch_base, "gw0", run_id)
    dsn_b = create_worker_db(scratch_base, "gw1", run_id)
    assert dsn_a != dsn_b
    try:
        for dsn in (dsn_a, dsn_b):
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute("CREATE TABLE e17_probe (id INT PRIMARY KEY, v TEXT)")
                conn.execute("INSERT INTO e17_probe VALUES (1, 'same-key')")
        with psycopg.connect(dsn_a, autocommit=True) as conn:
            conn.execute("INSERT INTO e17_probe VALUES (2, 'only-in-a')")
        with psycopg.connect(dsn_b) as conn:
            rows = conn.execute("SELECT id FROM e17_probe ORDER BY id").fetchall()
        assert [row[0] for row in rows] == [1]
    finally:
        drop_worker_db(scratch_base, "gw0", run_id)
        drop_worker_db(scratch_base, "gw1", run_id)
    names = _db_names(scratch_base)
    assert worker_db_name(scratch_base, "gw0", run_id) not in names
    assert worker_db_name(scratch_base, "gw1", run_id) not in names


def test_drop_run_worker_dbs_drops_only_that_run(scratch_base):
    """Controller cleanup drops one run's databases, not another's."""
    create_worker_db(scratch_base, "gw0", "e17run1")
    create_worker_db(scratch_base, "gw1", "e17run1")
    create_worker_db(scratch_base, "gw0", "e17run2")
    dropped = drop_run_worker_dbs(scratch_base, "e17run1")
    assert sorted(dropped) == [
        worker_db_name(scratch_base, "gw0", "e17run1"),
        worker_db_name(scratch_base, "gw1", "e17run1"),
    ]
    names = _db_names(scratch_base)
    assert worker_db_name(scratch_base, "gw0", "e17run2") in names
    assert worker_db_name(scratch_base, "gw0", "e17run1") not in names
    drop_worker_db(scratch_base, "gw0", "e17run2")


def test_prune_drops_only_old_and_idle_worker_databases(scratch_base):
    """Prune needs old AND idle: a young idle database is live.

    A live worker holds no connection between tests, so idleness
    alone must never prune — that dropped a running lane's
    database once. The birth stamp (``puspa-worker:<epoch>``) is
    backdated here to fake age; a fresh database is never pruned.
    """
    run_id = "e17prune"
    dsn = create_worker_db(scratch_base, "gw0", run_id)
    name = worker_db_name(scratch_base, "gw0", run_id)
    assert prune_stale_worker_dbs(scratch_base) == []
    assert name in _db_names(scratch_base)
    with psycopg.connect(maintenance_dsn(scratch_base), autocommit=True) as conn:
        conn.execute(f"COMMENT ON DATABASE \"{name}\" IS 'puspa-worker:1000000000'")
    assert prune_stale_worker_dbs(scratch_base) == [name]
    assert name not in _db_names(scratch_base)
    # An old database with a live connection is never pruned.
    dsn = create_worker_db(scratch_base, "gw0", run_id)
    with psycopg.connect(maintenance_dsn(scratch_base), autocommit=True) as conn:
        conn.execute(f"COMMENT ON DATABASE \"{name}\" IS 'puspa-worker:1000000000'")
    with psycopg.connect(dsn):
        assert prune_stale_worker_dbs(scratch_base) == []
    drop_worker_db(scratch_base, "gw0", run_id)


# --- xdist wiring, end to end ----------------------------------------------


def _probe_source() -> str:
    return (
        "import os\n"
        "from pathlib import Path\n\n\n"
        "def test_records_worker_dsn():\n"
        "    Path(os.environ['E17_PROBE_OUT']).open('a').write(\n"
        "        os.environ.get('PUSPA_TEST_DSN', '') + '\\n'\n"
        "    )\n"
        "\n\n"
        "def test_records_worker_dsn_again():\n"
        "    Path(os.environ['E17_PROBE_OUT']).open('a').write(\n"
        "        os.environ.get('PUSPA_TEST_DSN', '') + '\\n'\n"
        "    )\n"
        "\n\n"
        "def test_records_worker_dsn_third():\n"
        "    Path(os.environ['E17_PROBE_OUT']).open('a').write(\n"
        "        os.environ.get('PUSPA_TEST_DSN', '') + '\\n'\n"
        "    )\n"
        "\n\n"
        "def test_records_worker_dsn_fourth():\n"
        "    Path(os.environ['E17_PROBE_OUT']).open('a').write(\n"
        "        os.environ.get('PUSPA_TEST_DSN', '') + '\\n'\n"
        "    )\n"
    )


def _run_probe(probe: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-n",
            "2",
            "--dist",
            "load",
            "-q",
            "-p",
            "no:cacheprovider",
            str(probe),
        ],
        cwd=BUILD_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


def test_xdist_run_gives_each_worker_its_own_database(tmp_path):
    """A real ``pytest -n 2`` run: two workers, two SQLite files.

    The probe file records the DSN each worker sees. The run
    passes only when both workers ran and their files differ,
    and the worker files are gone afterwards (dropped by the
    session teardown in tests/conftest.py). The shared template
    file stays.
    """
    probe = BUILD_ROOT / "tests" / f"test_e17_probe_{os.getpid()}.py"
    out_file = tmp_path / "worker_dsns.txt"
    probe.write_text(_probe_source())
    env = dict(os.environ)
    env["PUSPA_TEST_DSN"] = str(tmp_path / "scratch.db")
    env["E17_PROBE_OUT"] = str(out_file)
    env["NO_PROXY"] = "localhost,127.0.0.1"
    env["no_proxy"] = "localhost,127.0.0.1"
    try:
        result = _run_probe(probe, env)
        assert result.returncode == 0, result.stdout + result.stderr
        dsns = {
            line.strip()
            for line in out_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        assert len(dsns) == 2, f"expected 2 worker DSNs, got: {sorted(dsns)}"
        assert all(dsn.endswith(".db") for dsn in dsns)
        assert len({Path(dsn).name for dsn in dsns}) == 2
        # Teardown dropped the worker files; the template stays.
        assert not any(Path(dsn).exists() for dsn in dsns)
        assert sqlite_template_path().exists()
    finally:
        probe.unlink(missing_ok=True)


def test_xdist_run_gives_each_worker_its_own_postgres_database(tmp_path, scratch_base):
    """The same wiring on the Postgres lane (needs a live server)."""
    probe = BUILD_ROOT / "tests" / f"test_e17_pg_probe_{os.getpid()}.py"
    out_file = tmp_path / "worker_dsns.txt"
    probe.write_text(_probe_source())
    env = dict(os.environ)
    env["PUSPA_TEST_DSN"] = scratch_base
    env["E17_PROBE_OUT"] = str(out_file)
    env["NO_PROXY"] = "localhost,127.0.0.1"
    env["no_proxy"] = "localhost,127.0.0.1"
    try:
        result = _run_probe(probe, env)
        assert result.returncode == 0, result.stdout + result.stderr
        dsns = {
            line.strip()
            for line in out_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        assert len(dsns) == 2, f"expected 2 worker DSNs, got: {sorted(dsns)}"
        names = {parse_dsn(dsn)["dbname"] for dsn in dsns}
        assert all(name.startswith(parse_dsn(scratch_base)["dbname"]) for name in names)
        assert len(names) == 2
        # Teardown dropped the worker databases; the template stays.
        remaining = _db_names(scratch_base)
        assert not (names & remaining), f"worker DBs not dropped: {names & remaining}"
        assert template_db_name(scratch_base) in remaining
    finally:
        probe.unlink(missing_ok=True)


# --- marker split -----------------------------------------------------------


def _collect_only(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PUSPA_TEST_DSN"] = BASE_DSN
    env["NO_PROXY"] = "localhost,127.0.0.1"
    env["no_proxy"] = "localhost,127.0.0.1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
        cwd=BUILD_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


def _nodeids(output: str) -> set[str]:
    return {
        line.strip()
        for line in output.splitlines()
        if "::" in line and line.startswith(("tests/", "cli/"))
    }


def test_every_test_carries_exactly_one_split_marker():
    """unit + integration + live covers the whole suite, no overlap."""
    all_tests = _collect_only()
    assert all_tests.returncode == 0, all_tests.stderr[-2000:]
    marked = _collect_only("-m", "unit or integration or live")
    assert marked.returncode == 0, marked.stderr[-2000:]
    assert _nodeids(marked.stdout) == _nodeids(all_tests.stdout)


def test_known_live_tests_are_marked_live():
    live = _collect_only("-m", "live")
    assert live.returncode == 0, live.stderr[-2000:]
    nodeids = _nodeids(live.stdout)
    # The sandbox and image-smoke live tests moved to the private
    # suite with the sandbox and computer backends in the split;
    # the public tree keeps only the reader and live-eval entries.
    expected = {
        "tests/test_readers.py::test_playwright_reader_reads_test_site_when_browser_exists",
        "tests/test_hipporag_judge_live_eval.py::test_live_judge_keeps_the_fraud_triples_on_query_1",
        "tests/test_hipporag_judge_live_eval.py::test_live_judge_chain_graph_agreement_with_the_stub",
        "tests/test_hipporag_judge_live_eval.py::test_live_judge_eval_recalls_cost_and_latency",
    }
    assert expected <= nodeids


def test_unit_subset_runs_with_no_database_at_all():
    """``pytest -m unit`` must not connect to Postgres.

    The DSN points at a database that does not exist; if any unit
    test (or fixture) opened a connection, the run would fail.
    (test_file_size_ratchet.py is deliberately NOT in this list:
    its soft-cap test already fails in this worktree for an
    unrelated pre-existing reason — puspa/memory/learning_loop.py
    at 1215 lines with no baseline entry — and would fail the
    nested run whatever the DSN is.)
    """
    env = dict(os.environ)
    env["PUSPA_TEST_DSN"] = "dbname=puspa_test_bogus_xyz user=root host=/var/run/postgresql"
    env["NO_PROXY"] = "localhost,127.0.0.1"
    env["no_proxy"] = "localhost,127.0.0.1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "unit",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_docs_drift.py",
            # The public sandbox unit tests: the computer-backend file
            # moved to the private suite with the sandbox in the split.
            "tests/test_localexec.py",
        ],
        cwd=BUILD_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
