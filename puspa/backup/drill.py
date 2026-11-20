"""Monthly restore drill with a stopwatch (E-09, plan section 5.4).

Gate 15 requires a restore to a clean host within 30 minutes. The
live clean-host drill waits for the validation VPS (E-02); what this
module proves here, on this host, is the full restore path against a
scratch Postgres instance:

1. Start a scratch instance with WAL archiving on (local ``cp``
   backend: it proves the archive/replay path, not off-host storage).
2. Apply the migration runner schema, seed canary data (a user, two
   vault entries, two ledger events, a profile fact).
3. Take a base backup with ``pg_basebackup``.
4. Write more canary data *after* the base backup.
5. Switch WAL and wait until the segment is archived.
6. Simulate disaster: stop the instance hard and delete its data
   directory.
7. Restore the base backup, replay WAL to the latest point, promote.
8. Verify: both canaries decrypt (the post-backup one proves WAL
   replay, not just the base backup), both ledger hash chains verify,
   the escrowed vault key unlocks the vault, the state.db snapshot
   hash matches.

Every phase is timed; the report records the total against the
30-minute target and is written as JSON next to a Markdown rendering.
Run it directly::

    python -m puspa.backup.drill [--work-root DIR] [--keep] [--report PATH]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pwd
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .local_state import escrow_vault_key, sha256_file, snapshot_state_db
from .wal import check_archiving, verify_wal_chain

#: Gate 15 target: restore to a clean host within 30 minutes.
TARGET_SECONDS = 30 * 60

#: Scratch database name inside the drill instance.
DRILL_DB = "puspa_drill"

#: How long to wait for WAL archiving / recovery before giving up.
ARCHIVE_TIMEOUT_SECONDS = 60
RECOVERY_TIMEOUT_SECONDS = 120
READY_TIMEOUT_SECONDS = 60


class DrillError(Exception):
    """A drill step failed."""


def _pg_bindir() -> Path:
    """Directory holding the Postgres binaries."""
    pg_config = shutil.which("pg_config")
    if pg_config:
        proc = subprocess.run([pg_config, "--bindir"], capture_output=True, text=True, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip())
    return Path("/usr/lib/postgresql/16/bin")


def _postgres_user() -> str | None:
    try:
        pwd.getpwnam("postgres")
    except KeyError:
        return None
    return "postgres"


def _run(cmd, *, timeout=300, **kwargs) -> subprocess.CompletedProcess:
    """Run a Postgres helper binary, as the postgres user when root.

    The scratch instance must be owned by the postgres OS user (a
    cluster refuses to run as root). When this process is root, the
    binaries run through ``runuser``; otherwise they run directly
    under the current user (which then owns the scratch files).
    """
    full = [str(c) for c in cmd]
    if os.geteuid() == 0 and _postgres_user() is not None:
        full = ["runuser", "-u", "postgres", "--", *full]
    return subprocess.run(
        full, check=False, capture_output=True, text=True, timeout=timeout, **kwargs
    )


def _chown_tree(path: Path, user: str) -> None:
    info = pwd.getpwnam(user)
    for root, dirs, files in os.walk(path):
        os.chown(root, info.pw_uid, info.pw_gid)
        for name in dirs + files:
            os.chown(Path(root) / name, info.pw_uid, info.pw_gid)


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ScratchPostgres:
    """A throwaway Postgres instance with WAL archiving enabled."""

    def __init__(self, root: Path, port: int):
        self.root = Path(root)
        self.port = port
        self.data_dir = self.root / "data"
        self.archive_dir = self.root / "wal-archive"
        self.log_file = self.root / "server.log"
        self.bindir = _pg_bindir()

    def setup(self) -> None:
        """initdb + configure + start. Raises DrillError on failure."""
        for directory in (self.root, self.data_dir, self.archive_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0 and _postgres_user():
            _chown_tree(self.root, "postgres")
        proc = _run([self.bindir / "initdb", "-D", self.data_dir, "-E", "UTF8", "--auth=trust"])
        if proc.returncode != 0:
            raise DrillError(f"initdb failed: {proc.stderr.strip()[:500]}")
        conf = self.data_dir / "postgresql.conf"
        with conf.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n# --- restore-drill settings (scratch instance only) ---\n"
                f"port = {self.port}\n"
                "listen_addresses = '127.0.0.1'\n"
                f"unix_socket_directories = '{self.root}'\n"
                "wal_level = replica\n"
                "archive_mode = on\n"
                f"archive_command = 'test ! -f {self.archive_dir}/%f"
                f" && cp %p {self.archive_dir}/%f'\n"
                "max_connections = 20\n"
                "shared_buffers = 32MB\n"
                "fsync = off\n"
                "synchronous_commit = off\n"
            )
        proc = _run(
            [
                self.bindir / "pg_ctl",
                "-D",
                self.data_dir,
                "-l",
                self.log_file,
                "start",
            ],
            timeout=READY_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            raise DrillError(f"pg_ctl start failed: {proc.stderr.strip()[:500]}")
        self._wait_ready()

    def _wait_ready(self, timeout: int = READY_TIMEOUT_SECONDS) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            proc = _run(
                [self.bindir / "pg_isready", "-h", "127.0.0.1", "-p", self.port],
                timeout=10,
            )
            if proc.returncode == 0:
                return
            time.sleep(0.5)
        raise DrillError("scratch Postgres did not become ready in time")

    def dsn(self, dbname: str = DRILL_DB) -> str:
        return f"dbname={dbname} user=postgres host=127.0.0.1 port={self.port}"

    def stop(self, mode: str = "fast") -> None:
        _run(
            [self.bindir / "pg_ctl", "-D", self.data_dir, "-m", mode, "stop"],
            timeout=60,
        )

    def destroy_data(self) -> None:
        """Delete the data directory: the simulated disaster."""
        shutil.rmtree(self.data_dir)


@contextmanager
def _phase(report: dict, name: str):
    started = time.monotonic()
    report["phases"][name] = {"seconds": None}
    try:
        yield
    finally:
        report["phases"][name]["seconds"] = round(time.monotonic() - started, 2)


def _connect(dsn: str):
    import psycopg

    return psycopg.connect(dsn, autocommit=True)


def _wait_for_file(path: Path, timeout: int) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.5)
    return False


def _wait_out_of_recovery(dsn: str, timeout: int = RECOVERY_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with _connect(dsn) as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_is_in_recovery()")
                if not cur.fetchone()[0]:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def run_restore_drill(
    *, work_root=None, keep: bool = False, target_seconds: int = TARGET_SECONDS
) -> dict:
    """Run the full restore drill and return the timed report.

    ``work_root`` defaults to a fresh temp dir, removed afterwards
    unless ``keep`` is set. The report's ``passed`` is True only when
    every check passed and the total stayed within the target.
    """
    started_at = datetime.now(UTC)
    report: dict = {
        "drill": "restore-to-scratch-instance",
        "started_at": started_at.isoformat(),
        "target_seconds": target_seconds,
        "phases": {},
        "checks": [],
        "paths": {},
        "notes": [
            (
                "Local drill only: the live clean-host drill on the VPS is "
                "still pending (E-02). This proves the restore path "
                "(base backup + WAL replay + vault key escrow + state.db "
                "snapshot) against a scratch instance on this host."
            )
        ],
    }

    def check(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    root = Path(work_root) if work_root else Path(tempfile.mkdtemp(prefix="puspa-drill-"))
    root.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0 and _postgres_user():
        # The scratch instance runs as the postgres OS user, so it
        # must be able to traverse into (and own files under) the
        # work root. mkdtemp creates 0700 root-owned dirs; open the
        # work root itself up and hand the tree to postgres.
        os.chmod(root, 0o755)
        _chown_tree(root, "postgres")
    instance = ScratchPostgres(root / "instance", _free_port())
    old_vault_key = os.environ.get("PUSPA_VAULT_MASTER_KEY")
    error: str | None = None
    try:
        with _phase(report, "init_scratch_instance"):
            instance.setup()
        with _phase(report, "apply_migrations"):
            with _connect(instance.dsn("postgres")) as conn, conn.cursor() as cur:
                cur.execute(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='puspa_app') "
                    "THEN CREATE ROLE puspa_app NOLOGIN; END IF; END $$"
                )
                cur.execute(f'CREATE DATABASE "{DRILL_DB}" OWNER postgres')
            from puspa.migrations.runner import apply_migrations

            applied = apply_migrations(instance.dsn())
            check("migrations_applied", len(applied) > 0, f"{len(applied)} migrations")
        vault_key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        os.environ["PUSPA_VAULT_MASTER_KEY"] = vault_key
        escrow_path = root / "key-escrow" / "vault-master.key"
        base_dir = root / "base-backup"
        with _phase(report, "seed_before_backup"):
            from puspa.accounts import vault
            from puspa.accounts.users import create_user
            from puspa.ledger import Ledger
            from puspa.memory import profile
            from puspa.runtime.agents import ensure_puspa_agent

            user_id = create_user(
                "drill@example.invalid", "drill-password-hash", dsn=instance.dsn()
            )
            agent_id = ensure_puspa_agent(user_id, dsn=instance.dsn())
            vault.put(user_id, "drill-canary-before", "secret-before", dsn=instance.dsn())
            task_before = "22222222-2222-2222-2222-222222222222"
            Ledger(instance.dsn()).append_event(
                task_id=task_before,
                agent_id=agent_id,
                event_type="task_created",
                content={"task": "drill canary before base backup"},
            )
            profile.add_fact(user_id, "name", "Drill", "user_stated", dsn=instance.dsn())
            escrow = escrow_vault_key(vault_key, escrow_path, data_backup_dir=base_dir)
            report["paths"]["key_escrow"] = escrow["path"]
        with _phase(report, "snapshot_state_db"):
            fake_home = root / "fake-home" / "profiles" / "default"
            fake_home.mkdir(parents=True, exist_ok=True)
            # Build the fake state.db through the real sessions
            # gateway, not raw SQLite DDL: the gateway creates the
            # real state.db schema (puspa/sessions owns that DDL),
            # so the snapshot copies a genuine session database.
            from puspa.sessions import open_persistence

            gateway = open_persistence(
                "default",
                home=root / "fake-home",
                agent_id="drill-canary",
                advisory_lock=False,
            )
            gateway.create_session("drill canary")
            gateway.close()
            snapshot = snapshot_state_db(
                profile="default",
                home=root / "fake-home",
                dest_dir=root / "state-snapshot",
            )
            report["paths"]["state_snapshot"] = snapshot["path"]
            check(
                "state_snapshot_hash",
                sha256_file(snapshot["path"]) == snapshot["sha256"],
                snapshot["sha256"][:16],
            )
        with _phase(report, "base_backup"):
            base_dir.mkdir(parents=True, exist_ok=True)
            if os.geteuid() == 0 and _postgres_user():
                _chown_tree(root, "postgres")
            proc = _run(
                [
                    _pg_bindir() / "pg_basebackup",
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(instance.port),
                    "-U",
                    "postgres",
                    "-D",
                    str(base_dir),
                    "-X",
                    "stream",
                    "-c",
                    "fast",
                ],
                timeout=300,
            )
            if proc.returncode != 0:
                raise DrillError(f"pg_basebackup failed: {proc.stderr.strip()[:500]}")
            report["paths"]["base_backup"] = str(base_dir)
            check("base_backup_taken", (base_dir / "PG_VERSION").exists(), str(base_dir))
        with _phase(report, "seed_after_backup"):
            from puspa.accounts import vault
            from puspa.ledger import Ledger

            vault.put(user_id, "drill-canary-after", "secret-after", dsn=instance.dsn())
            task_after = "33333333-3333-3333-3333-333333333333"
            Ledger(instance.dsn()).append_event(
                task_id=task_after,
                agent_id=agent_id,
                event_type="task_created",
                content={"task": "drill canary after base backup"},
            )
        with _phase(report, "switch_wal_and_archive"):
            with _connect(instance.dsn()) as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_walfile_name(pg_switch_wal())")
                switched = cur.fetchone()[0]
            archived = _wait_for_file(instance.archive_dir / switched, ARCHIVE_TIMEOUT_SECONDS)
            check("wal_segment_archived", archived, switched)
            archiving = check_archiving(instance.dsn())
            report["wal_archiving"] = archiving
            check("wal_archiving_healthy", archiving["healthy"], str(archiving))
            chain = verify_wal_chain(instance.archive_dir)
            report["wal_chain"] = {
                "segment_count": chain["segment_count"],
                "gaps": chain["gaps"],
            }
            check("wal_chain_continuous", chain["ok"], f"{chain['segment_count']} segments")
        with _phase(report, "simulate_disaster"):
            instance.stop(mode="immediate")
            instance.destroy_data()
            check("data_dir_destroyed", not instance.data_dir.exists(), str(instance.data_dir))
        with _phase(report, "restore_and_recover"):
            shutil.copytree(base_dir, instance.data_dir)
            if os.geteuid() == 0 and _postgres_user():
                _chown_tree(instance.data_dir, "postgres")
            # copytree restores the directory as 0755; Postgres
            # refuses to start unless the data dir is 0700 or 0750.
            os.chmod(instance.data_dir, 0o700)
            for stale in ("postmaster.pid", "postmaster.opts"):
                (instance.data_dir / stale).unlink(missing_ok=True)
            (instance.data_dir / "recovery.signal").write_text("", encoding="utf-8")
            conf = instance.data_dir / "postgresql.conf"
            with conf.open("a", encoding="utf-8") as handle:
                archive = str(instance.archive_dir).replace("'", "''")
                handle.write(f"\nrestore_command = 'cp {archive}/%f %p'\n")
            proc = _run(
                [
                    _pg_bindir() / "pg_ctl",
                    "-D",
                    instance.data_dir,
                    "-l",
                    instance.log_file,
                    "start",
                ],
                timeout=READY_TIMEOUT_SECONDS,
            )
            if proc.returncode != 0:
                raise DrillError(f"pg_ctl start after restore failed: {proc.stderr[:500]}")
            recovered = _wait_out_of_recovery(instance.dsn())
            check("recovery_completed", recovered, "replayed WAL to latest")
        with _phase(report, "verify"):
            from puspa.accounts import vault
            from puspa.ledger import Ledger

            from .local_state import verify_escrowed_key

            before = vault.get(user_id, "drill-canary-before", dsn=instance.dsn())
            check("vault_canary_before_backup", before == "secret-before", before)
            after = vault.get(user_id, "drill-canary-after", dsn=instance.dsn())
            check(
                "vault_canary_after_backup",
                after == "secret-after",
                "post-backup write survived via WAL replay",
            )
            for task_id in (task_before, task_after):
                ok, problems = Ledger(instance.dsn()).verify_chain(task_id)
                check(f"ledger_chain_{task_id[:8]}", ok, "; ".join(problems))
            escrow_check = verify_escrowed_key(escrow_path, instance.dsn())
            check(
                "escrowed_key_unlocks_vault",
                escrow_check["ok"] and escrow_check["checked"] >= 2,
                f"checked {escrow_check['checked']} rows",
            )
    except Exception as exc:  # the report must carry any failure
        error = f"{type(exc).__name__}: {exc}"
        report["error"] = error
    finally:
        instance.stop()
        if old_vault_key is None:
            os.environ.pop("PUSPA_VAULT_MASTER_KEY", None)
        else:
            os.environ["PUSPA_VAULT_MASTER_KEY"] = old_vault_key
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
        else:
            report["paths"]["work_root"] = str(root)
    finished_at = datetime.now(UTC)
    report["finished_at"] = finished_at.isoformat()
    total = (finished_at - started_at).total_seconds()
    report["total_seconds"] = round(total, 2)
    report["within_target"] = total <= target_seconds
    report["passed"] = (
        error is None and all(c["ok"] for c in report["checks"]) and report["within_target"]
    )
    return report


def write_drill_report(report: dict, path) -> str:
    """Write the drill report as JSON; return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return str(path)


def render_drill_markdown(report: dict) -> str:
    """Render the drill report as a Markdown log entry."""
    lines = [
        f"## Drill: {report['started_at']}",
        "",
        f"- Result: **{'PASS' if report['passed'] else 'FAIL'}**",
        f"- Total: {report['total_seconds']}s (target: {report['target_seconds']}s)",
        f"- Within target: {'yes' if report['within_target'] else 'NO'}",
        "",
        "### Phases",
        "",
        "| Phase | Seconds |",
        "| --- | ---: |",
    ]
    for name, phase in report["phases"].items():
        lines.append(f"| {name} | {phase['seconds']} |")
    lines += ["", "### Checks", ""]
    for item in report["checks"]:
        mark = "PASS" if item["ok"] else "FAIL"
        lines.append(
            f"- [{mark}] {item['name']}" + (f" ({item['detail']})" if item["detail"] else "")
        )
    if report.get("error"):
        lines += ["", f"Error: {report['error']}"]
    for note in report.get("notes", []):
        lines += ["", f"Note: {note}"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    """CLI entry point: ``python -m puspa.backup.drill``."""
    parser = argparse.ArgumentParser(description="Run the Puspa restore drill.")
    parser.add_argument("--work-root", default=None, help="Keep work files here")
    parser.add_argument("--keep", action="store_true", help="Keep the scratch files")
    parser.add_argument("--report", default=None, help="Write the JSON report here")
    args = parser.parse_args(argv)
    report = run_restore_drill(work_root=args.work_root, keep=args.keep)
    if args.report:
        print(write_drill_report(report, args.report))
    print(render_drill_markdown(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
