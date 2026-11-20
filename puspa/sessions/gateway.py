"""The profile gateway: the single writer for a profile (G26, E-07).

Only the gateway writes the canonical session database. The rule is
enforced in code, not just in documentation:

- ``SessionStore`` refuses a writable open without a writer token,
  and only this module mints tokens.
- ``SessionGateway.open()`` refuses a second live gateway for the
  same database file while the first is still open (process-wide
  registry). A second writer gets :class:`SingleWriterError`, never
  a second connection.
- Readers are separate objects (``open_persistence_reader``) opened
  with SQLite ``mode=ro``: they physically cannot write, however
  many of them exist.

The registry is process-wide only, so E-07 adds the cross-process
layers from ``puspa.sessions.single_writer`` at open time: an
exclusive ``flock`` on ``state.db`` (second process on the same
volume is refused) and a Postgres session-level advisory lock per
``agent_id`` (second process or container on the same database is
refused, even on a different volume). Both are held for the
gateway's lifetime and released on close — or by the OS / the
database when the holder process dies. See
``docs/session-storage.md``.

Every other client — workers, subagents, tools — reaches session
state through the gateway object (or a reader), never by opening
``state.db`` itself.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .errors import SingleWriterError
from .home import guard_not_live, resolve_home, state_db_path
from .single_writer import SingleWriterLock, default_agent_id
from .store import SessionStore, _WriterToken

#: Live gateways by resolved database path (process-wide registry).
_ACTIVE: dict[str, SessionGateway] = {}
_LOCK = threading.Lock()


class SessionGateway:
    """The single writer for one profile's session database."""

    def __init__(
        self, store: SessionStore, *, db_path, profile, writer_lock=None, agent_id=None
    ) -> None:
        self._store = store
        self.db_path = Path(db_path)
        self.profile = profile
        self._writer_lock = writer_lock
        self.agent_id = agent_id

    # -- opening ---------------------------------------------------------

    @classmethod
    def open(
        cls,
        profile=None,
        *,
        home=None,
        allow_live_home=False,
        busy_timeout_ms=5000,
        dsn=None,
        agent_id=None,
        advisory_lock=True,
    ):
        """Open (or create) the gateway for a profile.

        Raises :class:`SingleWriterError` when a gateway for the same
        database file is already open in this process, when another
        process already holds the ``flock`` on ``state.db``, or when
        another process or container already holds the Postgres
        advisory lock for this agent — and also when Postgres cannot
        be reached to verify the claim (fail-closed, unless
        ``advisory_lock=False``). ``agent_id`` defaults to
        ``profile:<name>``; pass the real agent id when it is known.
        ``dsn`` defaults through ``PUSPA_DSN`` / ``PUSPA_TEST_DSN``
        to the local peer-auth default. Raises :class:`LiveHomeError`
        when the resolved home is the live home without the explicit
        opt-in.
        """
        root = resolve_home(profile, home=home)
        guard_not_live(root, allow_live_home=allow_live_home)
        db_path = state_db_path(profile, home=home)
        key = str(db_path.resolve()) if db_path.exists() else str(db_path.absolute())
        resolved_agent_id = agent_id or default_agent_id(profile)
        with _LOCK:
            active = _ACTIVE.get(key)
            if active is not None and active._store._conn is not None:
                raise SingleWriterError(
                    f"profile {profile or 'default'} already has a live "
                    f"session gateway for {db_path}; close it first, or "
                    "use open_persistence_reader() for read access"
                )
            db_path.parent.mkdir(parents=True, exist_ok=True)
            writer_lock = SingleWriterLock(
                db_path, resolved_agent_id, dsn=dsn, advisory=advisory_lock
            )
            writer_lock.acquire()
            try:
                store = SessionStore(
                    db_path,
                    busy_timeout_ms=busy_timeout_ms,
                    _writer_token=_WriterToken(),
                )
            except Exception:
                writer_lock.release()
                raise
            gateway = cls(
                store,
                db_path=db_path,
                profile=profile,
                writer_lock=writer_lock,
                agent_id=resolved_agent_id,
            )
            _ACTIVE[key] = gateway
            return gateway

    def close(self) -> None:
        """Close the writer and release the single-writer claim.

        Idempotent. The store closes first, then both cross-process
        lock layers are released, so no other process can open the
        database while this gateway still holds a connection to it.
        """
        key = str(self.db_path.resolve() if self.db_path.exists() else self.db_path.absolute())
        with _LOCK:
            _ACTIVE.pop(key, None)
        self._store.close()
        writer_lock, self._writer_lock = self._writer_lock, None
        if writer_lock is not None:
            writer_lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @property
    def advisory_locked(self) -> bool:
        """True while this gateway holds the Postgres advisory lock
        (False when the advisory layer was disabled at open)."""
        return self._writer_lock is not None and self._writer_lock.advisory_held

    @property
    def store(self) -> SessionStore:
        """The underlying store (for gateway-internal use)."""
        return self._store

    @classmethod
    def is_writer_active(cls, profile=None, *, home=None) -> bool:
        """True when a gateway for this profile is open in this process."""
        db_path = state_db_path(profile, home=home)
        key = str(db_path.resolve()) if db_path.exists() else str(db_path.absolute())
        with _LOCK:
            active = _ACTIVE.get(key)
            return active is not None and active._store._conn is not None

    def reader(self) -> SessionStore:
        """Open a read-only reader over this profile's database."""
        return SessionStore(self.db_path, read_only=True)

    # -- session API (delegates to the store) ---------------------------

    def create_session(self, title="", **kwargs):
        """Create a session; see ``SessionStore.create_session``."""
        return self._store.create_session(title, **kwargs)

    def append_message(self, session_id, role, content):
        """Append one message to a session."""
        return self._store.append_message(session_id, role, content)

    def append_messages(self, session_id, messages):
        """Append several messages atomically."""
        return self._store.append_messages(session_id, messages)

    def get_session(self, session_id, **kwargs):
        """Return a session record, optionally with its messages."""
        return self._store.get_session(session_id, **kwargs)

    def list_sessions(self, **kwargs):
        """Sessions newest first."""
        return self._store.list_sessions(**kwargs)

    def search_messages(self, query, **kwargs):
        """BM25-ranked messages matching ``query`` (best first)."""
        return self._store.search_messages(query, **kwargs)

    def set_model_config(self, key, value):
        """Store one model-config value."""
        self._store.set_model_config(key, value)

    def get_model_config(self, key, default=None):
        """Return one model-config value, or ``default``."""
        return self._store.get_model_config(key, default)

    def create_child_session(self, parent_session_id, **kwargs):
        """Birth a child session from a parent (see store method)."""
        return self._store.create_child_session(parent_session_id, **kwargs)

    def lineage_chain(self, session_id):
        """Walk a session's ancestry: [self, parent, ...]."""
        return self._store.lineage_chain(session_id)

    def children(self, session_id):
        """Direct child sessions of ``session_id``."""
        return self._store.children(session_id)
