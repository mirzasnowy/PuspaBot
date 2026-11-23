"""Per-worker test databases (E-17, plan section 9.1).

The suite used to share one Postgres database (``puspa_test``), which
is why the full suite could only run SOLO: two processes truncated
each other's tables. This module replaces the shared database with
one database per pytest-xdist worker, built from a template:

- ``ensure_template_dsn`` builds the template database once: an
  empty database with every migration applied (``puspa.migrations``,
  the one schema path) plus ``synchronous_commit = off``. The
  template is keyed by a fingerprint of the migration files, so a
  migration change rebuilds it automatically. Creation is
  serialized with a Postgres advisory lock, so two workers (or two
  worktrees) racing to build it cannot corrupt it.
- ``create_worker_db`` clones the template with
  ``CREATE DATABASE ... TEMPLATE`` — a file-level copy, seconds at
  most — and returns a DSN pointing at the clone. No two workers
  ever share a database, so isolation is total by construction.
- ``drop_worker_db`` removes a worker database at session end.

The base database name comes from ``PUSPA_TEST_DSN`` (or the
peer-auth default), so the existing two-worktree mode composes:
worktree ``build-w1`` (base ``puspa_test_w1``) gets worker
databases ``puspa_test_w1_<run>_gw0`` ..., worktree ``build-w2``
gets ``puspa_test_w2_<run>_gw*``. A SOLO run (no ``-n``) does not
touch this module at all and keeps using the base database
directly, exactly as before.

The template is never written to by a test: workers connect only
to their own clone. A stale template (fingerprint mismatch) is
dropped and rebuilt under the lock.

Two ``pytest -n`` runs in the SAME worktree at the same time get
different run ids (one per controller invocation), so their
worker databases never collide.

SQLite mode (COW-10, the default): the same shape, with files
instead of databases. ``ensure_sqlite_template_path`` builds a
migrated template FILE once (``puspa_test_template_<fp>.db`` in
``PUSPA_TEST_DB_DIR`` or the system temp dir), keyed by a
fingerprint of ``versions_sqlite/``; creation is serialized with
a file lock (``fcntl.flock``). ``create_sqlite_worker_db``
copies that file to a per-worker path (``<stem>_<run>_<worker>.db``)
and returns the path as the DSN. A SOLO run gets its own copy
too (``<stem>_<run>_solo.db``), dropped at session end. The
template file is never written to by a test.

Honest limits:

- ``fsync`` is a cluster-wide setting, so this module cannot turn
  it off for one database; it sets ``synchronous_commit = off``
  per database instead (the durable-write shortcut that is safe
  for throwaway test data). A fully tmpfs cluster with ``fsync``
  off is a separate, opt-in setup: ``scripts/test_postgres_tmpfs.sh``
  starts a throwaway Postgres on /dev/shm for timing runs.
- Worker databases are dropped twice: each worker drops its own
  at session end (best effort), and the controller drops every
  database of the run in its own ``pytest_unconfigure`` — that
  second drop is the deterministic one, because a worker's own
  teardown sometimes never runs (xdist may SIGTERM a slow
  worker first). A crashed run leaves its worker databases
  behind; drop them by hand (``DROP DATABASE
  puspa_test_*_gw*``) or with ``scripts/setup_test_db.py
  --prune-stale``, which only drops databases older than 2 hours
  with no live connections (idleness alone is never enough: a
  live worker holds no connection between tests).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import psycopg

from puspa.migrations import apply_migrations
from puspa.migrations.runner import MIGRATIONS_DIR, MIGRATIONS_DIR_SQLITE

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: Default SQLite test database: a file in the system temp dir.
#: The Postgres DSN above stays the default for the hosted lane
#: (``PUSPA_TEST_DSN`` set to a Postgres DSN); the SQLite path
#: below is the default when no DSN is set at all.
DEFAULT_SQLITE_PATH = str(Path(tempfile.gettempdir()) / "puspa_test.db")

#: Longest database name Postgres accepts.
MAX_DB_NAME_LEN = 63

#: Template databases carry this fingerprint in their comment:
#: ``puspa-template:<sha256 of the migration files>``.
TEMPLATE_COMMENT_PREFIX = "puspa-template:"


def parse_dsn(dsn: str) -> dict[str, str]:
    """Split a libpq ``key=value`` DSN string into its parts."""
    parts: dict[str, str] = {}
    for item in dsn.split():
        if "=" in item:
            key, _, value = item.partition("=")
            parts[key] = value
    return parts


def build_dsn(parts: dict[str, str]) -> str:
    """Join DSN parts back into a libpq ``key=value`` string."""
    return " ".join(f"{key}={value}" for key, value in parts.items())


def with_dbname(dsn: str, name: str) -> str:
    """The same DSN, pointed at a different database."""
    parts = parse_dsn(dsn)
    parts["dbname"] = name
    return build_dsn(parts)


def maintenance_dsn(dsn: str) -> str:
    """The same connection, pointed at the maintenance database."""
    return with_dbname(dsn, "postgres")


def resolve_base_dsn(dsn: str | None = None) -> str:
    """Base DSN: argument, then env ``PUSPA_TEST_DSN``, then default."""
    return dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)


def template_db_name(base_dsn: str | None = None) -> str:
    """Template database name for a base DSN."""
    base = parse_dsn(resolve_base_dsn(base_dsn)).get("dbname", "puspa_test")
    return (base + "_template")[:MAX_DB_NAME_LEN]


def worker_db_name(base_dsn: str | None = None, worker_id: str = "gw0", run_id: str = "") -> str:
    """Worker database name: base + run id + xdist worker id.

    The run id keeps two concurrent runs in the same worktree from
    sharing a worker database. The name is truncated to the
    Postgres limit, keeping the run/worker suffix intact.
    """
    base = parse_dsn(resolve_base_dsn(base_dsn)).get("dbname", "puspa_test")
    suffix = f"_{run_id}_{worker_id}" if run_id else f"_{worker_id}"
    return base[: MAX_DB_NAME_LEN - len(suffix)] + suffix


def migrations_fingerprint(directory: Path | None = None) -> str:
    """SHA-256 over the sorted migration file names + contents.

    Any migration change (added, removed, edited) changes the
    fingerprint, so the template is rebuilt on the next run.
    """
    digest = hashlib.sha256()
    for path in sorted((directory or MIGRATIONS_DIR).glob("*.sql")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _database_comment(conn: psycopg.Connection, name: str) -> str | None:
    """The comment on a database, or None.

    Database comments live in the shared catalog
    ``pg_shdescription``, NOT in ``pg_description`` — the
    ``obj_description(oid, 'pg_database')`` call returns NULL for
    databases even when a comment is set, which silently disabled
    the template fingerprint check (the template was rebuilt on
    every call) and the worker birth stamp.
    """
    row = conn.execute(
        "SELECT description FROM pg_shdescription "
        "WHERE objoid = (SELECT oid FROM pg_database WHERE datname = %s)",
        (name,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def _database_exists(conn: psycopg.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
    return row is not None


def _advisory_lock(conn: psycopg.Connection, key: str) -> None:
    conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (key,))


def _advisory_unlock(conn: psycopg.Connection, key: str) -> None:
    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))


def _ensure_template_locked(conn: psycopg.Connection, base: str) -> str:
    """Template build, assuming the caller holds the advisory lock."""
    name = template_db_name(base)
    template_dsn = with_dbname(base, name)
    expected = TEMPLATE_COMMENT_PREFIX + migrations_fingerprint()
    if _database_exists(conn, name) and _database_comment(conn, name) == expected:
        return template_dsn
    conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(name)} WITH (FORCE)")
    owner = parse_dsn(base).get("user")
    owner_sql = f" OWNER {_quote_ident(owner)}" if owner else ""
    conn.execute(f"CREATE DATABASE {_quote_ident(name)}{owner_sql}")
    apply_migrations(dsn=template_dsn)
    conn.execute(f"ALTER DATABASE {_quote_ident(name)} SET synchronous_commit = off")
    conn.execute(f"COMMENT ON DATABASE {_quote_ident(name)} IS '{expected}'")
    return template_dsn


def ensure_template_dsn(base_dsn: str | None = None) -> str:
    """Create (or reuse) the template database; return its DSN.

    The template holds the fully migrated schema and nothing else.
    Rebuilding is automatic when the migration fingerprint changes.
    Safe to call concurrently: creation runs under one advisory
    lock per template name.
    """
    base = resolve_base_dsn(base_dsn)
    name = template_db_name(base)
    lock_key = f"puspa-template:{name}"
    with psycopg.connect(maintenance_dsn(base), autocommit=True) as conn:
        _advisory_lock(conn, lock_key)
        try:
            return _ensure_template_locked(conn, base)
        finally:
            _advisory_unlock(conn, lock_key)


def create_worker_db(base_dsn: str | None = None, worker_id: str = "gw0", run_id: str = "") -> str:
    """Clone the template into a fresh per-worker database.

    Returns a DSN pointing at the clone. The whole step (template
    check + clone) runs under the template advisory lock, so a
    clone can never race a template rebuild: ``CREATE DATABASE
    ... TEMPLATE`` refuses a template with open connections.
    """
    base = resolve_base_dsn(base_dsn)
    template = template_db_name(base)
    name = worker_db_name(base, worker_id, run_id)
    worker_dsn = with_dbname(base, name)
    lock_key = f"puspa-template:{template}"
    with psycopg.connect(maintenance_dsn(base), autocommit=True) as conn:
        _advisory_lock(conn, lock_key)
        try:
            _ensure_template_locked(conn, base)
            conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(name)} WITH (FORCE)")
            conn.execute(f"CREATE DATABASE {_quote_ident(name)} TEMPLATE {_quote_ident(template)}")
            conn.execute(f"ALTER DATABASE {_quote_ident(name)} SET synchronous_commit = off")
            # Birth stamp for the stale-pruner: a worker database is
            # only prunable once it is old, never merely idle (a
            # live worker holds no connection between tests).
            conn.execute(
                f"COMMENT ON DATABASE {_quote_ident(name)} IS 'puspa-worker:{int(time.time())}'"
            )
        finally:
            _advisory_unlock(conn, lock_key)
    return worker_dsn


def drop_worker_db(base_dsn: str | None = None, worker_id: str = "gw0", run_id: str = "") -> None:
    """Drop one worker database (best effort at session end)."""
    base = resolve_base_dsn(base_dsn)
    name = worker_db_name(base, worker_id, run_id)
    with psycopg.connect(maintenance_dsn(base), autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(name)} WITH (FORCE)")


def drop_run_worker_dbs(base_dsn: str | None = None, run_id: str = "") -> list[str]:
    """Drop every worker database of one run (controller cleanup).

    The controller calls this in ``pytest_unconfigure``, which
    runs after xdist tore the workers down — no worker is alive
    to reconnect, so dropping the whole run's databases is safe.
    This is the deterministic cleanup: a worker's own
    ``pytest_unconfigure`` drop is best-effort and sometimes
    never runs (the controller may SIGTERM a slow worker first),
    which used to leak one database per run.
    """
    base = resolve_base_dsn(base_dsn)
    stem = parse_dsn(base).get("dbname", "puspa_test")
    pattern = stem.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
    pattern += "_" + run_id + "_gw%"
    dropped: list[str] = []
    with psycopg.connect(maintenance_dsn(base), autocommit=True) as conn:
        rows = conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s ESCAPE '\\'",
            (pattern,),
        ).fetchall()
        for (name,) in rows:
            conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(str(name))} WITH (FORCE)")
            dropped.append(str(name))
    return dropped


def prune_stale_worker_dbs(base_dsn: str | None = None, max_age_seconds: float = 7200) -> list[str]:
    """Drop worker databases from crashed runs (old AND idle only).

    A worker database is prunable only when BOTH hold: it is
    older than ``max_age_seconds`` (default 2 hours — no lane run
    lasts that long) and it has no live backends. Idleness alone
    is NOT enough: a live worker holds no connection between
    tests, so pruning an idle-but-young database would drop a
    database a running lane is about to reconnect to.
    Databases without a ``puspa-worker:`` birth stamp (created by
    an older version of this module) are treated as stale when
    idle: they predate the stamp, so they are at least as old as
    the module version that introduced it.
    """
    base = resolve_base_dsn(base_dsn)
    stem = parse_dsn(base).get("dbname", "puspa_test")
    pattern = stem.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
    pattern += "\\_%\\_gw%"
    dropped: list[str] = []
    now = time.time()
    with psycopg.connect(maintenance_dsn(base), autocommit=True) as conn:
        rows = conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s ESCAPE '\\'",
            (pattern,),
        ).fetchall()
        for (name,) in rows:
            busy = conn.execute(
                "SELECT 1 FROM pg_stat_activity WHERE datname = %s LIMIT 1",
                (name,),
            ).fetchone()
            if busy is not None:
                continue
            born = _worker_birth(_database_comment(conn, str(name)))
            if born is not None and now - born < max_age_seconds:
                continue
            conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(str(name))} WITH (FORCE)")
            dropped.append(str(name))
    return dropped


def _worker_birth(comment: str | None) -> float | None:
    """Birth epoch from a ``puspa-worker:<epoch>`` comment, or None."""
    if not comment or not comment.startswith("puspa-worker:"):
        return None
    try:
        return float(comment.split(":", 1)[1])
    except ValueError:
        return None


def new_run_id() -> str:
    """A fresh run id: unique per pytest invocation."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# SQLite mode (COW-10): one file per worker, copied from a template file.
# ---------------------------------------------------------------------------


def sqlite_db_dir() -> Path:
    """Directory for SQLite test files (env override, else tmp)."""
    return Path(os.environ.get("PUSPA_TEST_DB_DIR", tempfile.gettempdir()))


def sqlite_migrations_fingerprint(directory: Path | None = None) -> str:
    """SHA-256 over the sorted SQLite migration file names + contents."""
    digest = hashlib.sha256()
    for path in sorted((directory or MIGRATIONS_DIR_SQLITE).glob("*.sql")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def sqlite_template_path() -> Path:
    """Template file path for the current SQLite migrations."""
    fingerprint = sqlite_migrations_fingerprint()[:16]
    return sqlite_db_dir() / f"puspa_test_template_{fingerprint}.db"


def _sqlite_file_lock():
    """Exclusive lock file guarding template builds (best effort).

    Returns a context manager holding an ``fcntl.flock`` lock, or a
    no-op when ``fcntl`` is unavailable (non-POSIX hosts).
    """
    lock_path = sqlite_db_dir() / "puspa_test_template.lock"
    try:
        import fcntl
    except ImportError:
        return contextlib.nullcontext()

    @contextlib.contextmanager
    def _locked():
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    return _locked()


def _copy_sqlite_file(source: Path, target: Path) -> None:
    """Copy a SQLite file plus its WAL sidecars, when present."""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists():
            shutil.copyfile(sidecar, Path(str(target) + suffix))


def _remove_sqlite_file(path: Path) -> None:
    """Remove a SQLite file plus its WAL sidecars, when present."""
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        with contextlib.suppress(FileNotFoundError):
            candidate.unlink()


def ensure_sqlite_template_path() -> str:
    """Create (or reuse) the migrated template file; return its path.

    The template holds the fully migrated schema and nothing else.
    Rebuilding is automatic when the SQLite migration fingerprint
    changes. Safe to call concurrently: creation runs under one
    file lock per template directory.
    """
    template = sqlite_template_path()
    with _sqlite_file_lock():
        if template.exists():
            return str(template)
        for stale in sqlite_db_dir().glob("puspa_test_template_*.db"):
            _remove_sqlite_file(stale)
        apply_migrations(dsn=str(template))
    return str(template)


def sqlite_worker_path(
    base_dsn: str | None = None, worker_id: str = "gw0", run_id: str = ""
) -> Path:
    """Worker file path: stem from the base DSN + run id + worker id.

    A bare base DSN (or none) uses the ``puspa_test`` stem in the
    test directory; an explicit SQLite path keeps its own stem
    and directory, so workers never write to the named file.
    """
    if base_dsn:
        base_path = Path(base_dsn)
        directory = base_path.parent
        stem = base_path.stem
    else:
        directory = sqlite_db_dir()
        stem = "puspa_test"
    suffix = f"_{run_id}_{worker_id}" if run_id else f"_{worker_id}"
    return directory / f"{stem}{suffix}.db"


def create_sqlite_worker_db(
    base_dsn: str | None = None, worker_id: str = "gw0", run_id: str = ""
) -> str:
    """Copy the template file into a fresh per-worker file.

    Returns the file path (the DSN). No two workers ever share a
    file, so isolation is total by construction.
    """
    template = ensure_sqlite_template_path()
    target = sqlite_worker_path(base_dsn, worker_id, run_id)
    _remove_sqlite_file(target)
    _copy_sqlite_file(Path(template), target)
    return str(target)


def drop_sqlite_worker_db(path: str) -> None:
    """Remove one worker file (best effort at session end)."""
    _remove_sqlite_file(Path(path))


def drop_run_sqlite_worker_dbs(base_dsn: str | None = None, run_id: str = "") -> list[str]:
    """Remove every worker file of one run (controller cleanup)."""
    target = sqlite_worker_path(base_dsn, "gw*", run_id)
    dropped: list[str] = []
    for path in target.parent.glob(target.name):
        if path.suffix != ".db":
            continue
        _remove_sqlite_file(path)
        dropped.append(str(path))
    return dropped
