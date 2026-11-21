"""Session persistence (G26, E-07): single-writer authority, FTS5, lineage.

One SQLite database per profile (``<home>/state.db``) holds session
metadata, the full message history, model configuration, compression
lineage, and an FTS5 full-text index. Only the profile gateway
writes; everyone else reads through a read-only reader or the
gateway API. The writer claim is cross-process (E-07): an exclusive
``flock`` on ``state.db`` plus a Postgres advisory lock per agent id,
both taken at open time. See ``docs/session-storage.md``.
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
from .export import (
    ARCHIVE_FORMAT,
    ARCHIVE_SCHEMA_VERSION,
    ConversationArchiveError,
    export_conversations,
    import_conversations,
    plan_import_conversations,
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
from .single_writer import (
    PostgresAdvisoryLock,
    SingleWriterLock,
    StateDbFileLock,
    advisory_lock_key,
    default_agent_id,
    resolve_dsn,
)
from .spawner import child_env, require_home_in_env
from .store import SessionStore

__all__ = [
    "ARCHIVE_FORMAT",
    "ARCHIVE_SCHEMA_VERSION",
    "ConversationArchiveError",
    "CorruptDatabaseError",
    "LiveHomeError",
    "PostgresAdvisoryLock",
    "ProfileError",
    "SessionError",
    "SessionGateway",
    "SessionNotFoundError",
    "SessionStore",
    "SingleWriterError",
    "SingleWriterLock",
    "StateDbFileLock",
    "WriteContentionError",
    "adopt_compression_result",
    "advisory_lock_key",
    "child_env",
    "default_agent_id",
    "default_home",
    "export_conversations",
    "import_conversations",
    "is_live_home",
    "maintenance",
    "open_persistence",
    "open_persistence_reader",
    "override_home",
    "plan_import_conversations",
    "record_compression",
    "recover_report",
    "require_home_in_env",
    "resolve_dsn",
    "resolve_home",
    "state_db_path",
]
