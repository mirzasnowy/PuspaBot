"""Local backup port: a real local export of the Postgres database.

This module is the public side of the backup boundary. The hosted
service implements server WAL backup + ``pgbackrest`` behind the
same port name; the local install implements it as a plain
``pg_dump`` of its own Postgres database into a local file.

The implementation is honest about failure: when ``pg_dump`` is
missing or exits non-zero, :func:`create_backup` raises
:class:`BackupError` (reused from :mod:`puspa.backup.local_state`)
instead of returning a path to a partial or empty file, and a
partial file is removed. Nothing here fakes a successful backup.

The SQLite snapshot path (``local_state.snapshot_state_db``) stays
the way a per-profile ``state.db`` is preserved; it becomes the
local backup implementation when the SQLite migration lands.
Until then, the Postgres dump is the local backup.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from puspa.accounts.ports import resolve_dsn

from .local_state import BackupError

__all__ = ["BackupError", "create_backup", "default_backup_dir"]


def default_backup_dir() -> Path:
    """Where local backups go: ``PUSPA_BACKUP_DIR`` when set, else
    ``~/.local/share/puspa/backups``."""
    env_dir = os.environ.get("PUSPA_BACKUP_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return Path.home() / ".local" / "share" / "puspa" / "backups"


def create_backup(dsn=None, backup_dir=None, now=None) -> dict:
    """Dump the local Postgres database to a file; return its details.

    Returns ``{"path", "created_at", "size_bytes", "format"}`` with
    ``"format"`` always ``"pg_dump"`` (plain SQL, restorable with
    ``psql``). ``backup_dir`` defaults to
    :func:`default_backup_dir`; ``now`` defaults to the current UTC
    time and only sets the file name. Raises :class:`BackupError`
    when ``pg_dump`` is missing, when the dump fails, or when the
    dump file ends up empty — a failed backup is never reported
    as a path.
    """
    resolved = resolve_dsn(dsn)
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        raise BackupError("pg_dump not found on PATH: cannot back up the database")
    dest_dir = Path(backup_dir).expanduser() if backup_dir is not None else default_backup_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(UTC)
    dest = dest_dir / f"backup-{now.strftime('%Y%m%d-%H%M%S')}.sql"
    with dest.open("wb") as handle:
        proc = subprocess.run(
            [pg_dump, "--dbname", resolved],
            stdout=handle,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
        )
    if proc.returncode != 0:
        dest.unlink(missing_ok=True)
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise BackupError(f"pg_dump failed (exit {proc.returncode}): {detail}"[:500])
    size = dest.stat().st_size
    if size == 0:
        dest.unlink(missing_ok=True)
        raise BackupError("pg_dump produced an empty backup file")
    return {
        "path": str(dest),
        "created_at": now.isoformat(),
        "size_bytes": size,
        "format": "pg_dump",
    }
