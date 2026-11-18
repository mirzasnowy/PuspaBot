"""Session persistence (G26): single-writer authority, FTS5, lineage.

One SQLite database per profile (``<home>/state.db``) holds session
metadata, the full message history, model configuration, compression
lineage, and an FTS5 full-text index. Only the profile gateway
writes; everyone else reads through a read-only reader or the
gateway API. See ``docs/session-storage.md``.
"""

from __future__ import annotations

from .assembly import open_persistence, open_persistence_reader
from .errors import (
    CorruptDatabaseError,
    LiveHomeError,
    ProfileError,
    SessionError,
    SessionNotFoundError,
    SingleWriterError,
    WriteContentionError,
)
from .gateway import SessionGateway
from .home import (
    default_home,
    is_live_home,
    override_home,
    resolve_home,
    state_db_path,
)
from .housekeeping import maintenance, recover_report
from .lineage import adopt_compression_result, record_compression
from .spawner import child_env, require_home_in_env
from .store import SessionStore

__all__ = [
    "CorruptDatabaseError",
    "LiveHomeError",
    "ProfileError",
    "SessionError",
    "SessionGateway",
    "SessionNotFoundError",
    "SessionStore",
    "SingleWriterError",
    "WriteContentionError",
    "adopt_compression_result",
    "child_env",
    "default_home",
    "is_live_home",
    "maintenance",
    "open_persistence",
    "open_persistence_reader",
    "override_home",
    "record_compression",
    "recover_report",
    "require_home_in_env",
    "resolve_home",
    "state_db_path",
]
