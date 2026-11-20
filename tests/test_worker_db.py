"""Tests for per-worker databases + the marker split (E-17, plan 9.1).

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
    create_worker_db,
    drop_run_worker_dbs,
    drop_worker_db,
    ensure_template_dsn,
    maintenance_dsn,
    migrations_fingerprint,
    parse_dsn,
    prune_stale_worker_dbs,
    template_db_name,
    with_dbname,
    worker_db_name,
)

from puspa.migrations.runner import list_migrations

# Every test in this file needs a live Postgres server: the tests
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

#: Base DSN for the scratch databases this file creates. The name
#: carries the process id, so two workers running this file at the
#: same time never share a template or a worker database.
SCRATCH_BASE = f"dbname=puspa_test_e17_{os.getpid()} " + " ".join(
    f"{key}={value}" for key, value in parse_dsn(BASE_DSN).items() if key != "dbname"
)


def _db_names(dsn: str) -> set[str]:
    with psycopg.connect(maintenance_dsn(dsn), autocommit=True) as conn:
        rows = conn.execute("SELECT datname FROM pg_database").fetchall()
    return {str(row[0]) for row in rows}


def _drop_quietly(dsn: str, name: str) -> None:
    with psycopg.connect(maintenance_dsn(dsn), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def scratch_base():
    """Scratch base DSN; template + worker DBs dropped afterwards."""
    yield SCRATCH_BASE
    names = _db_names(SCRATCH_BASE)
    stem = parse_dsn(SCRATCH_BASE)["dbname"]
    for name in sorted(names):
        if name.startswith(stem):
            _drop_quietly(SCRATCH_BASE, name)


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


# --- template database ------------------------------------------------------


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


# --- per-worker isolation ---------------------------------------------------


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


def test_xdist_run_gives_each_worker_its_own_database(tmp_path, scratch_base):
    """A real ``pytest -n 2`` run: two workers, two databases.

    The probe file records the DSN each worker sees. The run
    passes only when both workers ran and their databases differ,
    and the worker databases are gone afterwards (dropped by the
    session teardown in tests/conftest.py).
    """
    probe = BUILD_ROOT / "tests" / f"test_e17_probe_{os.getpid()}.py"
    out_file = tmp_path / "worker_dsns.txt"
    probe.write_text(
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
    env = dict(os.environ)
    env["PUSPA_TEST_DSN"] = scratch_base
    env["E17_PROBE_OUT"] = str(out_file)
    env["NO_PROXY"] = "localhost,127.0.0.1"
    env["no_proxy"] = "localhost,127.0.0.1"
    try:
        result = subprocess.run(
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
    expected = {
        "tests/test_sandbox.py::test_run_print_ok_in_container",
        "tests/test_sandbox.py::test_failed_run_code_still_returns_result_not_unavailable",
        "tests/test_sandbox.py::test_run_network_to_domain_outside_allowlist_rejected",
        "tests/test_sandbox.py::test_run_cannot_write_to_host",
        "tests/test_image_smoke.py::test_image_smoke_live",
        "tests/test_readers.py::test_playwright_reader_reads_test_site_when_browser_exists",
        "tests/test_hipporag_judge_live_eval.py::test_live_judge_keeps_the_fraud_triples_on_query_1",
        "tests/test_hipporag_judge_live_eval.py::test_live_judge_chain_graph_agreement_with_the_stub",
        "tests/test_hipporag_judge_live_eval.py::test_live_judge_eval_recalls_cost_and_latency",
    }
    assert expected <= nodeids
    docker = {n for n in nodeids if n.endswith("[docker]")}
    assert docker, "no docker-param backend test is marked live"
    assert all("[docker]" in n for n in docker)


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
            "tests/test_computer_backends.py",
        ],
        cwd=BUILD_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
