"""Encrypted backup + restore test (Gate 15, partial).

- Dump Postgres (``pg_dump`` custom format) -> Fernet encryption ->
  store in a directory OUTSIDE the database (off-VPS object storage
  in production; see ``docs/backup-restore-runbook.md``).
- 14-day retention (initial value); older backups are pruned
  automatically.
- Restore test to a clean database: ``restore_backup`` + row-count
  verification + ledger hash-chain verification. The first restore
  test ran during Phase 1; the monthly timed drill lives in
  ``puspa/backup/drill.py`` (E-09).
- Blobs (screenshots) are NOT backed up (30-day TTL, revision 7).
- The backup key comes only from env ``PUSPA_BACKUP_KEY`` (base64
  urlsafe, 32 bytes). There is no default key and it is never stored
  in the database.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet

from ..memory.schema import resolve_dsn

RETENTION_DAYS = 14
BACKUP_PREFIX = "puspa-"
BACKUP_SUFFIX = ".dump.enc"


class BackupError(Exception):
    """Backup/restore failed."""


def _backup_key():
    raw = os.environ.get("PUSPA_BACKUP_KEY")
    if not raw:
        raise BackupError("PUSPA_BACKUP_KEY is not set; backup refuses to run without a key")
    try:
        key = base64.urlsafe_b64decode(raw.encode())
    except Exception as exc:
        raise BackupError("PUSPA_BACKUP_KEY is not valid urlsafe base64") from exc
    if len(key) != 32:
        raise BackupError("PUSPA_BACKUP_KEY must be 32 bytes")
    return raw.encode()


def _fernet():
    return Fernet(_backup_key())


def _dsn_parts(dsn):
    """Split a `dbname=X user=Y host=Z` DSN into a dict (no password)."""
    parts = {}
    for token in (dsn or "").split():
        if "=" in token:
            key, value = token.split("=", 1)
            parts[key] = value
    return parts


def _pg_env(dsn):
    parts = _dsn_parts(dsn)
    env = dict(os.environ)
    if parts.get("host"):
        env["PGHOST"] = parts["host"]
    if parts.get("user"):
        env["PGUSER"] = parts["user"]
    if parts.get("port"):
        env["PGPORT"] = parts["port"]
    return env


def _db_name(dsn):
    return _dsn_parts(dsn).get("dbname", "puspa_test")


def create_backup(dsn=None, backup_dir=None, pg_dump_cmd="pg_dump", now=None):
    """Create an encrypted backup. Return dict {path, manifest, ...}."""
    dsn = resolve_dsn(dsn)
    backup_dir = Path(backup_dir or Path(__file__).resolve().parent.parent.parent / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(UTC)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    db = _db_name(dsn)
    path = backup_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [pg_dump_cmd, "-Fc", "-d", db, "-f", tmp_path],
            env=_pg_env(dsn),
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise BackupError(f"pg_dump failed: {proc.stderr.strip()[:500]}")
        plaintext = Path(tmp_path).read_bytes()
        ciphertext = _fernet().encrypt(plaintext)
        path.write_bytes(ciphertext)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    manifest = {
        "file": path.name,
        "database": db,
        "created_at": now.isoformat(),
        "encrypted_size": path.stat().st_size,
        "dump_size": len(plaintext),
    }
    (backup_dir / (path.name + ".json")).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    pruned = prune_backups(backup_dir)
    manifest["deleted_by_retention"] = pruned
    return {"path": str(path), "manifest": manifest}


def list_backups(backup_dir=None):
    """List backups (newest first) with their manifests."""
    backup_dir = Path(backup_dir or Path(__file__).resolve().parent.parent.parent / "backups")
    out = []
    if not backup_dir.exists():
        return out
    for path in sorted(backup_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"), reverse=True):
        manifest_path = Path(str(path) + ".json")
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {"file": path.name}
        )
        out.append({"path": str(path), "manifest": manifest})
    return out


def prune_backups(backup_dir=None, retention_days=RETENTION_DAYS, now=None):
    """Delete backups older than the retention window. Return deleted names."""
    backup_dir = Path(backup_dir or Path(__file__).resolve().parent.parent.parent / "backups")
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)
    deleted = []
    if not backup_dir.exists():
        return deleted
    for path in backup_dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
        match = re.search(r"(\d{8})-(\d{6})", path.name)
        if not match:
            continue
        stamp = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(
            tzinfo=UTC
        )
        if stamp < cutoff:
            path.unlink(missing_ok=True)
            Path(str(path) + ".json").unlink(missing_ok=True)
            deleted.append(path.name)
    return deleted


def restore_backup(backup_path, target_dsn, pg_restore_cmd="pg_restore"):
    """Restore a backup into the target database (must exist and be empty).

    Return dict {database, table}. The caller is responsible for
    creating the empty target database first (the restore test uses a
    clean database).
    """
    target_dsn = resolve_dsn(target_dsn)
    plaintext = _fernet().decrypt(Path(backup_path).read_bytes())
    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp.write(plaintext)
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            [pg_restore_cmd, "-d", _db_name(target_dsn), "--clean", "--if-exists", tmp_path],
            env=_pg_env(target_dsn),
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise BackupError(f"pg_restore failed: {proc.stderr.strip()[:500]}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    import psycopg

    with psycopg.connect(target_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        tables = [r[0] for r in cur.fetchall()]
    return {"database": _db_name(target_dsn), "table": tables}


def verify_restored_counts(target_dsn, tables):
    """Count rows per table in the restored database (content check)."""
    import psycopg

    out = {}
    with psycopg.connect(resolve_dsn(target_dsn)) as conn, conn.cursor() as cur:
        for table in tables:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            out[table] = cur.fetchone()[0]
    return out
