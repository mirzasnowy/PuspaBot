"""Migration package: the single source of truth for the schema.

``puspa.migrations.runner`` applies the numbered SQL files in
``puspa/migrations/versions/`` and records them in
``schema_migrations``. Every ``create_schema`` entry point in the
codebase delegates to it, so no Python code creates tables anymore
(the SQLite ``state.db`` schema in ``puspa/sessions`` is the one
exception: that database is rebuildable by design).
"""

from __future__ import annotations

from .runner import (
    Migration,
    MigrationError,
    apply_migrations,
    list_migrations,
    migration_status,
    resolve_dsn,
    rollback_migrations,
)

__all__ = [
    "Migration",
    "MigrationError",
    "apply_migrations",
    "list_migrations",
    "migration_status",
    "resolve_dsn",
    "rollback_migrations",
]
