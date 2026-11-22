"""Cross-process single-writer lock for one agent (E-07, plan 5.2).

The process-wide gateway registry (G26) cannot see other processes,
so a second container sharing the same volume — or the same Postgres
database — could open a second writer for the same agent. This module
adds the two layers plan 5.2 requires, both taken when a session is
opened and both released when the gateway closes (or when the
process dies, which is what makes a crash safe instead of sticky):

1. ``flock`` on ``state.db`` itself (``StateDbFileLock``). An
   exclusive non-blocking lock on the shared file: a second process
   on the same volume fails at open time. The lock lives on an open
   file description, so the operating system releases it when the
   holder process exits for any reason, including ``kill -9``.
   Windows has no ``flock`` (and no ``fcntl`` module), so there the
   same lock is a one-byte ``msvcrt.locking`` lock on a sidecar file
   ``state.db.lock`` next to the database. It is a sidecar on
   purpose: a Windows byte-range lock is mandatory, so locking
   ``state.db`` itself would stop SQLite from reading the locked
   bytes. The semantics are the same: non-blocking, refused on
   contention, released when the holder exits.
2. A Postgres session-level advisory lock per ``agent_id``
   (``PostgresAdvisoryLock``). The lock key is a deterministic
   64-bit hash of the agent id, taken with
   ``pg_try_advisory_lock`` on a dedicated connection held for the
   gateway's lifetime. A second holder — same agent, different
   volume or host, same database — is refused. The database
   releases a session-level advisory lock when the connection
   drops, so a crashed holder never blocks a restart.

``SingleWriterLock`` composes the two: the file lock is taken first,
then the advisory lock; if the advisory lock fails, the file lock is
released again so a failed open leaves nothing held. Both layers
report contention as :class:`SingleWriterError` — the second process
is refused, never queued — and a database that cannot be reached at
all fails closed the same way (a writer that cannot prove it is the
single writer does not open).

In beta there is one container per user, so the lock normally never
contends; a refusal there is a misconfiguration alarm (two
containers pointed at the same agent), exactly as plan 5.2 intends.

Honest limits (see also ``docs/session-storage.md``):

- The file lock only works where the shared volume honours
  ``flock`` (a local or shared-host volume). It does not span hosts
  whose filesystem ignores advisory file locks; the Postgres layer
  is the one that spans hosts, as long as both share one database.
- The two-container test (two containers on one volume, the second
  refused) cannot run live here — this host has no Docker — so the
  tests prove the same semantics with two processes on one volume
  and one database, and the container form is CI / E-02 work.
"""

from __future__ import annotations

import errno
import hashlib
import os
import sys
from pathlib import Path

import psycopg

from puspa.accounts.ports import resolve_dsn as _port_resolve_dsn

from .errors import SingleWriterError

# ``fcntl`` is a Unix-only module: importing it on Windows raises
# ModuleNotFoundError, which used to make every ``puspa`` command die
# at import time there (even ``puspa --help``). The platform check
# is a plain ``sys.platform`` comparison so type checkers can follow it.
if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        """Take a one-byte exclusive lock at offset 0 without blocking.

        ``msvcrt.locking`` locks from the current position, so seek
        first. Raises ``OSError`` (``EACCES``) when another holder
        has the lock.
        """
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        """Unlock explicitly: Windows frees locks of a closed handle
        only after a delay, so a quick restart could still be refused."""
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # closing the descriptor releases the lock anyway

else:
    import fcntl

    def _try_lock(fd: int) -> None:
        """Take an exclusive ``flock`` without blocking. Raises
        ``OSError`` (``EAGAIN``/``EACCES``) when another holder has it."""
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        """Nothing to do: ``flock`` is released when the descriptor closes."""


#: errno values that mean "somebody else holds the lock" (as opposed
#: to a real I/O failure): ``flock`` reports EAGAIN or EACCES,
#: ``msvcrt.locking`` reports EACCES (EDEADLK after retries).
_CONTENTION_ERRNOS = (errno.EACCES, errno.EAGAIN, errno.EDEADLK)

#: True where the file lock must live on a sidecar file (see the
#: module docstring); evaluated once so tests can build a Windows-mode
#: copy of this module.
_USE_SIDECAR = sys.platform == "win32"

__all__ = [
    "DEFAULT_DSN",
    "PostgresAdvisoryLock",
    "SingleWriterLock",
    "StateDbFileLock",
    "advisory_lock_key",
    "default_agent_id",
    "resolve_dsn",
]

#: Default Postgres DSN (peer auth as the OS user, no password).
#: Same default as the ledger and the accounts package.
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: Namespace mixed into the advisory-lock key so this lock can never
#: collide with another advisory-lock user in the same database.
_LOCK_NAMESPACE = "puspa:single-writer:"

#: Timeout (seconds) for the dedicated advisory-lock connection.
#: Opening a writer must fail fast, never hang a startup.
_CONNECT_TIMEOUT_SECONDS = 5


def resolve_dsn(dsn: str | None = None) -> str:
    """Postgres DSN: explicit argument, then ``PUSPA_DSN`` /
    ``PUSPA_TEST_DSN`` env, then the local peer-auth default.

    Delegates to :func:`puspa.accounts.ports.resolve_dsn`, the one
    canonical resolution order in the tree.
    """
    return _port_resolve_dsn(dsn)


def default_agent_id(profile: str | None) -> str:
    """Agent id a gateway stands for when the caller passes none.

    The gateway only knows the profile, so the default is the
    profile name. Callers that know the real agent id (one agent per
    user in beta) should pass it explicitly via ``agent_id``.
    """
    return f"profile:{profile or 'default'}"


def advisory_lock_key(agent_id: str) -> int:
    """Deterministic signed 64-bit advisory-lock key for an agent id.

    First 8 bytes of the MD5 of the namespaced agent id, read as an
    unsigned big-endian integer and folded into Postgres ``bigint``
    (signed 64-bit) range. Two agent ids map to different keys with
    overwhelming probability; the same agent id always maps to the
    same key in every process and container.
    """
    digest = hashlib.md5(f"{_LOCK_NAMESPACE}{agent_id}".encode(), usedforsecurity=False).digest()
    key = int.from_bytes(digest[:8], "big")
    if key >= 2**63:
        key -= 2**64
    return key


class StateDbFileLock:
    """Exclusive non-blocking ``flock`` on the session database file.

    Held for the gateway's lifetime; released on ``release()`` (or
    when the process dies). Readers never take this lock.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._fd: int | None = None

    @property
    def lock_path(self) -> Path:
        """The file the lock is taken on: ``state.db`` itself, except
        on Windows where it is the sidecar ``state.db.lock``."""
        if _USE_SIDECAR:
            return self.db_path.with_name(self.db_path.name + ".lock")
        return self.db_path

    def acquire(self) -> None:
        """Take the lock, or raise :class:`SingleWriterError`.

        The file is created (never truncated) when it does not exist
        yet, so acquiring the lock on a first open is safe.
        """
        if self._fd is not None:
            return
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _try_lock(fd)
        except OSError as exc:
            os.close(fd)
            if exc.errno in _CONTENTION_ERRNOS:
                raise SingleWriterError(
                    f"another process already holds the writer lock on "
                    f"{self.db_path}; refusing to open a second writer "
                    "(close the other process, or open a read-only reader)"
                ) from exc
            raise
        self._fd = fd

    def release(self) -> None:
        """Release the lock. Idempotent."""
        fd, self._fd = self._fd, None
        if fd is not None:
            _unlock(fd)
            os.close(fd)

    @property
    def held(self) -> bool:
        """True while this object holds the file lock."""
        return self._fd is not None

    def __enter__(self) -> StateDbFileLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class PostgresAdvisoryLock:
    """Session-level Postgres advisory lock for one agent id.

    Holds one dedicated connection for the lock's lifetime and takes
    the lock with ``pg_try_advisory_lock`` (never waits: contention
    is a refusal, not a queue). Closing the connection releases the
    lock, so a crashed holder frees it automatically.
    """

    def __init__(self, dsn: str, agent_id: str) -> None:
        self.dsn = dsn
        self.agent_id = agent_id
        self.key = advisory_lock_key(agent_id)
        self._conn: psycopg.Connection | None = None

    def acquire(self) -> None:
        """Take the advisory lock, or raise :class:`SingleWriterError`.

        A database that cannot be reached fails closed with the same
        error: a writer that cannot prove it is the single writer
        does not open.
        """
        if self._conn is not None:
            return
        try:
            conn = psycopg.connect(self.dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS)
            conn.autocommit = True
        except psycopg.Error as exc:
            raise SingleWriterError(
                f"cannot verify the single-writer claim for agent "
                f"{self.agent_id!r}: Postgres is unreachable ({exc}); "
                "refusing to open the writer"
            ) from exc
        try:
            row = conn.execute("SELECT pg_try_advisory_lock(%s)", (self.key,)).fetchone()
            held = bool(row and row[0])
        except psycopg.Error as exc:
            conn.close()
            raise SingleWriterError(
                f"cannot verify the single-writer claim for agent "
                f"{self.agent_id!r}: advisory lock query failed ({exc})"
            ) from exc
        if not held:
            conn.close()
            raise SingleWriterError(
                f"agent {self.agent_id!r} already has a live writer "
                "(another process or container holds its Postgres "
                "advisory lock); refusing to open a second writer"
            )
        self._conn = conn

    def release(self) -> None:
        """Release the lock and close the connection. Idempotent."""
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.execute("SELECT pg_advisory_unlock(%s)", (self.key,))
            except psycopg.Error:
                pass
            finally:
                conn.close()

    @property
    def held(self) -> bool:
        """True while this object holds the advisory lock."""
        return self._conn is not None

    def __enter__(self) -> PostgresAdvisoryLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class SingleWriterLock:
    """Both E-07 layers together: file lock, then advisory lock.

    Acquire order is file first, advisory second; a failed advisory
    acquire releases the file lock again, so a refused open leaves
    nothing held. Release order is advisory first, file second.
    """

    def __init__(
        self,
        db_path: Path,
        agent_id: str,
        *,
        dsn: str | None = None,
        advisory: bool = True,
    ) -> None:
        self.db_path = Path(db_path)
        self.agent_id = agent_id
        self.advisory = advisory
        self._file_lock = StateDbFileLock(self.db_path)
        self._advisory_lock = PostgresAdvisoryLock(resolve_dsn(dsn), agent_id) if advisory else None

    def acquire(self) -> None:
        """Take both layers (or just the file layer when the advisory
        layer is disabled). Raises :class:`SingleWriterError` on
        contention, leaving nothing held."""
        self._file_lock.acquire()
        if self._advisory_lock is not None:
            try:
                self._advisory_lock.acquire()
            except SingleWriterError:
                self._file_lock.release()
                raise

    def release(self) -> None:
        """Release both layers. Idempotent."""
        if self._advisory_lock is not None:
            self._advisory_lock.release()
        self._file_lock.release()

    @property
    def file_held(self) -> bool:
        """True while the file lock is held."""
        return self._file_lock.held

    @property
    def advisory_held(self) -> bool:
        """True while the advisory lock is held (False when the
        advisory layer is disabled)."""
        return self._advisory_lock is not None and self._advisory_lock.held

    def __enter__(self) -> SingleWriterLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
