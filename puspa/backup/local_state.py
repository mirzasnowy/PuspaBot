"""Local-state snapshots and vault key escrow (E-09, plan section 5.4).

Two pieces of a restore that the Postgres dump does not cover:

1. ``state.db``: the per-profile SQLite session database
   (``<home>/profiles/<name>/state.db``). Postgres is the authority
   for task status, but the session transcript and search index live
   here, so a restore without it loses the session history. The
   snapshot uses the SQLite online backup API, so it is consistent
   even while the agent is running.

2. The vault master key (``PUSPA_VAULT_MASTER_KEY``): every user
   credential in the ``vault`` table is encrypted under keys derived
   from this one master key, and the key itself is never stored in
   the database. Losing the key means losing every user credential,
   even with a perfect database restore. The key is therefore
   escrowed to a file that lives SEPARATELY from the data backups:
   :func:`escrow_vault_key` refuses an escrow path inside the data
   backup directory, and :func:`check_key_separation` verifies the
   separation afterwards. The escrow file is mode 0600.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ..accounts.vault import derive_user_key
from ..memory.schema import resolve_dsn
from .backup import BackupError


class StateSnapshotError(BackupError):
    """The state.db snapshot could not be taken."""


class KeyEscrowError(BackupError):
    """The vault key escrow is invalid or violates separation."""


def sha256_file(path) -> str:
    """SHA-256 hex digest of a file's bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_state_db(*, profile=None, home=None, dest_dir, now=None) -> dict:
    """Take a consistent copy of a profile's ``state.db``.

    Returns ``{"path", "source", "sha256", "size"}``. The copy is made
    with the SQLite backup API against a read-only connection, so a
    running agent cannot corrupt the snapshot mid-write.
    """
    from ..sessions.home import state_db_path

    source = state_db_path(profile, home=home)
    if not source.exists():
        raise StateSnapshotError(f"state.db not found: {source}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(UTC)
    dest = dest_dir / f"state-{now.strftime('%Y%m%d-%H%M%S')}.db"
    src_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()
    return {
        "path": str(dest),
        "source": str(source),
        "sha256": sha256_file(dest),
        "size": dest.stat().st_size,
    }


def check_key_separation(escrow_path, data_backup_dir) -> tuple[bool, str]:
    """Check that the key escrow lives outside the data backups.

    The escrow must not be inside the data backup directory (a single
    lost/stolen backup directory must never contain both the encrypted
    data and the key that unlocks it) and must not be the same file as
    a data backup.
    """
    escrow = Path(escrow_path).expanduser().resolve()
    backup_root = Path(data_backup_dir).expanduser().resolve()
    if escrow == backup_root or backup_root in escrow.parents:
        return False, "escrow path is inside the data backup directory"
    return True, "escrow is separate from the data backups"


def escrow_vault_key(key, escrow_path, *, data_backup_dir=None, overwrite=False) -> dict:
    """Write the vault master key to a separate escrow file (mode 0600).

    ``key`` is the base64 urlsafe 32-byte master key value (the same
    value ``PUSPA_VAULT_MASTER_KEY`` holds). When ``data_backup_dir``
    is given, the escrow path is checked against it first and the
    write is refused on violation. An existing escrow file is never
    silently overwritten: a different key already escrowed means
    someone must rotate deliberately, not by accident.
    """
    try:
        raw = base64.urlsafe_b64decode(str(key).encode())
    except Exception as exc:
        raise KeyEscrowError("vault master key is not valid base64") from exc
    if len(raw) != 32:
        raise KeyEscrowError("vault master key must be 32 bytes")
    escrow = Path(escrow_path).expanduser()
    if data_backup_dir is not None:
        ok, reason = check_key_separation(escrow, data_backup_dir)
        if not ok:
            raise KeyEscrowError(reason)
    if escrow.exists() and not overwrite:
        existing = escrow.read_text(encoding="utf-8").strip()
        if existing != str(key).strip():
            raise KeyEscrowError("escrow file already holds a different key; refusing to overwrite")
        return {"path": str(escrow), "unchanged": True}
    escrow.parent.mkdir(parents=True, exist_ok=True)
    escrow.write_text(str(key).strip() + "\n", encoding="utf-8")
    os.chmod(escrow, 0o600)
    return {"path": str(escrow), "unchanged": False}


def verify_escrowed_key(escrow_path, dsn=None, *, limit: int = 10) -> dict:
    """Prove the escrowed key actually unlocks the vault rows.

    Reads the key from the escrow file, derives each sampled user's
    key, and decrypts that user's vault ciphertext. Returns
    ``{"checked", "ok", "failures"}``; an empty vault checks zero
    rows and reports ``ok=True`` with a note instead of failing.
    """
    from cryptography.fernet import Fernet

    escrow = Path(escrow_path).expanduser()
    if not escrow.exists():
        raise KeyEscrowError(f"escrow file not found: {escrow}")
    raw = escrow.read_text(encoding="utf-8").strip()
    try:
        master = base64.urlsafe_b64decode(raw.encode())
    except Exception as exc:
        raise KeyEscrowError("escrowed key is not valid base64") from exc
    if len(master) != 32:
        raise KeyEscrowError("escrowed key must be 32 bytes")
    import psycopg

    with psycopg.connect(resolve_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id, name, ciphertext FROM vault LIMIT %s", (limit,))
        rows = cur.fetchall()
    failures = []
    for user_id, name, ciphertext in rows:
        fernet = Fernet(base64.urlsafe_b64encode(derive_user_key(master, user_id)))
        try:
            fernet.decrypt(ciphertext.encode())
        except Exception:
            failures.append(name)
    return {
        "checked": len(rows),
        "ok": not failures,
        "failures": failures,
        "note": "vault empty, nothing to decrypt" if not rows else None,
    }
