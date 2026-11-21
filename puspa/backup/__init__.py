"""Local state snapshots for the self-host product.

The vault key escrow and state-database snapshots a single user runs
locally. Server backups, WAL archiving, and restore drills live
elsewhere.
"""

from __future__ import annotations

from .local_state import (
    BackupError,
    KeyEscrowError,
    StateSnapshotError,
    check_key_separation,
    escrow_vault_key,
    sha256_file,
    snapshot_state_db,
    verify_escrowed_key,
)

__all__ = [
    "BackupError",
    "KeyEscrowError",
    "StateSnapshotError",
    "check_key_separation",
    "escrow_vault_key",
    "sha256_file",
    "snapshot_state_db",
    "verify_escrowed_key",
]
