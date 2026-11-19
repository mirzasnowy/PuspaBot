"""VPS validation + machine-replacement drill (E-02, plan section 4.2).

Covers what is buildable WITHOUT a VPS:

- the health checks in ``puspa.teams.vps`` (Postgres tables + RLS
  against the configured test database, HTTP probes against a local server,
  Docker precondition, offline deploy-file validation, allowlist
  probe judging);
- the one-command rebuild script (``scripts/vps-rebuild.sh``):
  syntax, dry-run output (steps in order, exit 0, nothing
  executed), and content rules (role root created before the core
  schema runs, no credentials stored);
- the backup-restore procedure end to end on the configured test
  database: create an encrypted backup, restore it into a scratch database,
  verify the tables and row counts match. The same commands run on
  the VPS; only the host differs.

The LIVE checks (``docker compose up``, sandbox tests inside a
container, allowlist probing against the real egress path, restore
on a real VPS) cannot run here — this VM has a ``docker`` binary
but no compose plugin and no daemon, and the Contabo VPS has not
been bought yet. They are recorded as
pending in ``docs/vps-validation.md``, not faked here.
"""

import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from puspa.backup.backup import create_backup, restore_backup, verify_restored_counts
from puspa.teams.vps import (
    all_passed,
    check_docker_available,
    check_http,
    check_postgres,
    classify_allowlist_probe,
    format_report,
    run_health_checks,
    validate_deploy_files,
)

BUILD_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = BUILD_ROOT / "scripts" / "vps-rebuild.sh"
W2_DSN = os.environ.get("PUSPA_TEST_DSN", "")


def _dbname(dsn: str) -> str:
    """Database name from a libpq DSN string.

    The central suite runs this file against ``puspa_test`` while a
    worker runs it against ``puspa_test_w2``; the name must come from
    the DSN, never hardcoded, or the manifest assertion fails on the
    other database (same trap as the K11 backup-test fix).
    """
    for token in dsn.split():
        if token.startswith("dbname="):
            return token[len("dbname="):]
    raise ValueError(f"no dbname in DSN: {dsn!r}")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/manifest.json":
            body = b'{"name": "Puspa"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # silence the test server
        pass


@pytest.fixture(scope="module")
def local_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_check_http_ok(local_server):
    result = check_http(f"{local_server}/manifest.json")
    assert result.passed
    assert "HTTP 200" in result.detail


def test_check_http_404_fails(local_server):
    result = check_http(f"{local_server}/missing")
    assert not result.passed
    assert "404" in result.detail


def test_check_http_refused_fails():
    result = check_http("http://127.0.0.1:1/manifest.json", timeout=2.0)
    assert not result.passed
    assert "unreachable" in result.detail


@pytest.mark.skipif(not W2_DSN, reason="PUSPA_TEST_DSN not set")
def test_check_postgres_w2():
    results = check_postgres(W2_DSN)
    by_name = {result.name: result for result in results}
    assert by_name["postgres.connect"].passed, by_name["postgres.connect"].detail
    assert by_name["postgres.tables"].passed, by_name["postgres.tables"].detail
    assert by_name["postgres.rls"].passed, by_name["postgres.rls"].detail
    assert all_passed(results)


def test_check_postgres_bad_dsn_fails():
    results = check_postgres("dbname=does_not_exist user=root host=/var/run/postgresql")
    assert results
    assert not all_passed(results)
    assert "cannot connect" in results[0].detail


def test_check_docker_available_matches_reality():
    """The Docker check reports what this host really has.

    Three honest states: no binary at all, a binary without a
    working ``docker compose`` (this VM after a replacement:
    docker 29.1.3 with no compose plugin and no daemon), and
    a working docker + compose. The check must match the state
    it finds, never claim a compose step the host cannot run.
    """
    result = check_docker_available()
    docker = shutil.which("docker")
    if docker is None:
        assert not result.passed
        assert "not installed" in result.detail
        return
    proc = subprocess.run(
        [docker, "compose", "version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        assert result.passed
    else:
        assert not result.passed
        assert "docker compose version" in result.detail


def test_validate_deploy_files_clean():
    results = validate_deploy_files(BUILD_ROOT)
    assert all_passed(results), format_report(results)
    assert [result.name for result in results] == [
        "deploy.dockerfile",
        "deploy.compose",
    ]


def test_classify_allowlist_probe():
    allowed_url = "https://api.openai.com/v1/models"
    blocked_url = "https://169.254.169.254/latest/meta-data/"
    other_blocked = "https://example.com/"
    assert classify_allowlist_probe(allowed_url, True).passed
    assert classify_allowlist_probe(blocked_url, False).passed
    assert classify_allowlist_probe(other_blocked, False).passed
    # Mismatches fail either way: an allowed URL that does not fetch
    # means the egress path is broken; a blocked URL that fetches
    # means the allowlist is not enforced.
    assert not classify_allowlist_probe(allowed_url, False).passed
    assert not classify_allowlist_probe(blocked_url, True).passed
    assert "not enforced" in classify_allowlist_probe(blocked_url, True).detail


def test_format_report_and_all_passed(local_server):
    results = [check_http(f"{local_server}/manifest.json")]
    assert all_passed(results)
    report = format_report(results)
    assert "PASS http.dashboard" in report
    assert "SUMMARY 1/1 checks passed" in report
    failing = [check_http(f"{local_server}/missing")]
    assert not all_passed(failing)
    assert "FAIL http.dashboard" in format_report(failing)


@pytest.mark.skipif(not W2_DSN, reason="PUSPA_TEST_DSN not set")
def test_run_health_checks_smoke(local_server):
    results = run_health_checks(W2_DSN, f"{local_server}/manifest.json", BUILD_ROOT)
    by_name = {result.name: result for result in results}
    assert by_name["postgres.connect"].passed
    assert by_name["postgres.tables"].passed
    assert by_name["postgres.rls"].passed
    assert by_name["http.dashboard"].passed
    assert by_name["deploy.dockerfile"].passed
    assert by_name["deploy.compose"].passed
    # Docker is not installed on this VM: the check must report that
    # honestly instead of passing or crashing the whole run.
    assert not by_name["docker.available"].passed


def test_rebuild_script_syntax():
    assert SCRIPT.is_file()
    proc = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


def test_rebuild_script_dry_run(tmp_path):
    target = tmp_path / "puspa"
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run", "--target-dir", str(target)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    # Dry-run must not create anything.
    assert not target.exists()
    assert not (tmp_path / "vps-rebuild.log").exists()
    steps = [
        "Install system packages",
        "Start PostgreSQL cluster",
        "Create role root",
        "Fetch Puspa tree",
        "Create Python virtualenv",
        "Create databases",
        "Apply core schema",
        "Apply ledger + accounts schemas",
        "Verify RLS",
        "Verify Docker and Compose",
        "Offline deploy validation",
    ]
    positions = [proc.stdout.index(step) for step in steps]
    assert positions == sorted(positions), proc.stdout
    assert "Puspa VPS rebuild finished" in proc.stdout


def test_rebuild_script_role_before_schema():
    text = SCRIPT.read_text(encoding="utf-8")
    # Order of the step CALLS in main(): the role step must run before
    # the schema step (the schema connects as root).
    role_pos = text.index('step "Create role root')
    schema_pos = text.index('step "Apply core schema')
    assert role_pos < schema_pos
    # The role step must be the guarded create (re-runnable), and the
    # schema must run from the real tree path, never via a /tmp copy.
    assert "CREATE ROLE root LOGIN SUPERUSER" in text
    assert "/tmp/" not in text


def test_rebuild_script_stores_no_credentials():
    text = SCRIPT.read_text(encoding="utf-8").lower()
    assert "password" not in text
    assert "puspa_api_key=" not in text
    assert "secret" not in text
    assert "peer auth" in text


@pytest.mark.skipif(not W2_DSN, reason="PUSPA_TEST_DSN not set")
def test_backup_restore_round_trip_on_w2(tmp_path, monkeypatch):
    import psycopg

    monkeypatch.setenv("PGUSER", "root")
    key = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh4="
    monkeypatch.setenv("PUSPA_BACKUP_KEY", key)
    backup_dir = tmp_path / "backups"
    created = create_backup(dsn=W2_DSN, backup_dir=backup_dir)
    backup_path = Path(created["path"])
    assert backup_path.is_file()
    assert created["manifest"]["database"] == _dbname(W2_DSN)

    scratch_name = f"{_dbname(W2_DSN)}_restore"
    scratch_dsn = f"dbname={scratch_name} user=root host=/var/run/postgresql"
    with psycopg.connect(W2_DSN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {scratch_name}")
        conn.execute(f"CREATE DATABASE {scratch_name} OWNER root")
    try:
        restored = restore_backup(str(backup_path), scratch_dsn)
        for table in ("users", "sessions", "vault", "events", "kill_switch"):
            assert table in restored["table"], restored["table"]
        counts = verify_restored_counts(scratch_dsn, restored["table"])
        with psycopg.connect(W2_DSN) as conn, conn.cursor() as cur:
            for table in ("users", "sessions", "vault", "events", "kill_switch"):
                cur.execute(f"SELECT count(*) FROM {table}")
                assert counts[table] == cur.fetchone()[0]
    finally:
        with psycopg.connect(W2_DSN, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {scratch_name}")
