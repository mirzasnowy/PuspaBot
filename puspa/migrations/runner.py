"""Numbered SQL migration runner (E-08, plan section 5.3).

One migration tool, one source of truth for the schema: the numbered
``.sql`` files in ``puspa/migrations/versions/``. The file format is
dbmate-compatible (``-- migrate:up`` / ``-- migrate:down`` sections,
``schema_migrations`` table with a ``version`` primary key), so the
real dbmate binary can apply the same files later without changes.
This runner is the embedded implementation: it needs only psycopg
(already a runtime dependency), no new binary, no new package.

Rules the runner enforces:

- Migrations apply in version order, one transaction per migration.
  A failed migration rolls back only itself.
- A migration is recorded in ``schema_migrations`` only after its
  transaction commits. Re-running ``up`` is a no-op when everything
  is already applied.
- Rollback goes in reverse version order and needs an explicit
  target (``--to``): the version that stays applied. Every migration
  has a down section, or an explicit ``cannot be reversed`` note
  (rolling such a migration back raises instead of guessing).
- An applied version with no file on disk fails loudly: the files
  must never change under an applied migration. Never edit an
  applied migration; add a new numbered one instead.

Run as an operator (database owner / superuser), never as the agent:

    python -m puspa.migrations up
    python -m puspa.migrations status
    python -m puspa.migrations down --to 0006

The DSN comes from ``--dsn``, then ``PUSPA_DSN``, then
``PUSPA_TEST_DSN``, then the local test default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

from puspa.accounts.ports import resolve_dsn as _port_resolve_dsn

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

MIGRATIONS_DIR = Path(__file__).resolve().parent / "versions"

# The one piece of DDL this module is allowed to hold: the bookkeeping
# table itself. Every other CREATE TABLE lives in a migration file.
# (The no-runtime-DDL scanner test allowlists exactly this statement.)
SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

#: Marker a down section uses when the migration cannot be rolled
#: back. The reason follows the colon on the same line.
IRREVERSIBLE_MARKER = "cannot be reversed"


class MigrationError(ValueError):
    """A migration file, target, or recorded state is unusable."""


@dataclass(frozen=True)
class Migration:
    """One numbered migration file."""

    version: str
    name: str
    path: Path
    up_sql: str
    down_sql: str

    @property
    def irreversible(self) -> bool:
        return IRREVERSIBLE_MARKER in self.down_sql


def resolve_dsn(dsn: str | None = None) -> str:
    """Postgres DSN: argument, then env, then the local test default.

    Delegates to :func:`puspa.accounts.ports.resolve_dsn`, the one
    canonical resolution order in the tree.
    """
    return _port_resolve_dsn(dsn)


def list_migrations(directory: Path | None = None) -> list[Migration]:
    """All migration files in version order.

    Raises MigrationError on a duplicate version, a missing marker,
    or an empty up/down section.
    """
    directory = directory or MIGRATIONS_DIR
    files = sorted(directory.glob("*.sql"))
    seen: dict[str, Path] = {}
    migrations: list[Migration] = []
    for path in files:
        match = re.match(r"^(\d+)_([a-z0-9_]+)\.sql$", path.name)
        if not match:
            raise MigrationError(
                f"migration file {path.name} does not match "
                "'NNNN_name.sql' — rename it or remove it"
            )
        version = match.group(1)
        if version in seen:
            raise MigrationError(
                f"duplicate migration version {version}: {seen[version].name} and {path.name}"
            )
        seen[version] = path
        up_sql, down_sql = _split_sections(path)
        migrations.append(
            Migration(
                version=version,
                name=path.stem,
                path=path,
                up_sql=up_sql,
                down_sql=down_sql,
            )
        )
    return migrations


def _split_sections(path: Path) -> tuple[str, str]:
    """Split a migration file into its up and down SQL."""
    text = path.read_text(encoding="utf-8")
    up_marker = "-- migrate:up"
    down_marker = "-- migrate:down"
    if text.count(up_marker) != 1 or text.count(down_marker) != 1:
        raise MigrationError(
            f"{path.name}: expected exactly one '{up_marker}' and one '{down_marker}' marker"
        )
    _, _, after_up = text.partition(up_marker)
    up_sql, _, down_sql = after_up.partition(down_marker)
    up_sql = up_sql.strip()
    down_sql = down_sql.strip()
    if not up_sql:
        raise MigrationError(f"{path.name}: empty up section")
    if not down_sql:
        raise MigrationError(
            f"{path.name}: empty down section — write the rollback, or "
            f"an explicit '{IRREVERSIBLE_MARKER}: <reason>' note"
        )
    return up_sql, down_sql


def _applied_versions(conn: psycopg.Connection) -> dict[str, str]:
    """Applied versions from schema_migrations (creates the table first)."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_MIGRATIONS_DDL)
        cur.execute("SELECT version, applied_at::text FROM schema_migrations")
        rows = cur.fetchall()
    conn.commit()
    return {str(version): str(applied_at) for version, applied_at in rows}


def _check_target(migrations: list[Migration], target: str | None) -> None:
    if target is not None and target not in {m.version for m in migrations}:
        known = ", ".join(m.version for m in migrations)
        raise MigrationError(f"unknown migration target {target!r}; known versions: {known}")


def apply_migrations(dsn: str | None = None, target: str | None = None) -> list[str]:
    """Apply every pending migration up to ``target`` (or all of them).

    Returns the versions applied by this call, in order. A second call
    is a no-op. Each migration runs in its own transaction.
    """
    migrations = list_migrations()
    _check_target(migrations, target)
    applied: list[str] = []
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        recorded = _applied_versions(conn)
        unknown = sorted(set(recorded) - {m.version for m in migrations})
        if unknown:
            raise MigrationError(
                "schema_migrations holds versions with no file on disk: "
                + ", ".join(unknown)
                + " — the migration files must never change under an "
                "applied migration"
            )
        for migration in migrations:
            if migration.version in recorded:
                continue
            if target is not None and migration.version > target:
                break
            with conn.cursor() as cur:
                cur.execute(migration.up_sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (migration.version,),
                )
            conn.commit()
            applied.append(migration.version)
    return applied


def rollback_migrations(dsn: str | None = None, target: str = "") -> list[str]:
    """Roll back every applied migration after ``target`` (exclusive).

    ``target`` is the version that stays applied; pass ``""`` to roll
    back everything. Returns the versions rolled back, newest first.
    Raises MigrationError on an irreversible migration instead of
    rolling it back.
    """
    migrations = list_migrations()
    if target != "":
        _check_target(migrations, target)
    rolled_back: list[str] = []
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        recorded = _applied_versions(conn)
        for migration in reversed(migrations):
            if migration.version <= target:
                break
            if migration.version not in recorded:
                continue
            if migration.irreversible:
                raise MigrationError(
                    f"migration {migration.version} ({migration.name}) "
                    "cannot be rolled back: "
                    f"{_irreversible_reason(migration.down_sql)}"
                )
            with conn.cursor() as cur:
                cur.execute(migration.down_sql)
                cur.execute(
                    "DELETE FROM schema_migrations WHERE version = %s",
                    (migration.version,),
                )
            conn.commit()
            rolled_back.append(migration.version)
    return rolled_back


def _irreversible_reason(down_sql: str) -> str:
    for line in down_sql.splitlines():
        if IRREVERSIBLE_MARKER in line:
            return line.split(IRREVERSIBLE_MARKER, 1)[1].strip(" :")
    return "no reason recorded"


def migration_status(dsn: str | None = None) -> list[dict[str, str | bool]]:
    """One row per migration file: version, name, applied or not."""
    migrations = list_migrations()
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        recorded = _applied_versions(conn)
    return [
        {
            "version": m.version,
            "name": m.name,
            "applied": m.version in recorded,
            "applied_at": recorded.get(m.version, ""),
        }
        for m in migrations
    ]
