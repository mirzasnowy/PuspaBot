"""Single entry points for session persistence (G26).

``open_persistence()`` returns the profile gateway — the one writer.
``open_persistence_reader()`` returns a read-only store. There is no
other supported way to open ``state.db``.
"""

from __future__ import annotations

from .gateway import SessionGateway
from .home import guard_not_live, resolve_home, state_db_path
from .store import SessionStore


def open_persistence(
    profile=None,
    *,
    home=None,
    allow_live_home=False,
    busy_timeout_ms=5000,
    dsn=None,
    agent_id=None,
    advisory_lock=True,
) -> SessionGateway:
    """Open the single writer (gateway) for a profile.

    Creates the profile home and ``state.db`` on first use. Raises
    :class:`SingleWriterError` when a gateway for this profile is
    already open, when another process holds the ``flock`` on
    ``state.db``, or when another process or container holds the
    Postgres advisory lock for this agent (or Postgres cannot be
    reached to verify the claim — fail-closed, unless
    ``advisory_lock=False``); :class:`LiveHomeError` for the live
    home without the explicit opt-in; and
    :class:`CorruptDatabaseError` when the existing ``state.db`` is
    unreadable (fail-closed). ``agent_id`` defaults to
    ``profile:<name>``; pass the real agent id when it is known.
    """
    return SessionGateway.open(
        profile,
        home=home,
        allow_live_home=allow_live_home,
        busy_timeout_ms=busy_timeout_ms,
        dsn=dsn,
        agent_id=agent_id,
        advisory_lock=advisory_lock,
    )


def open_persistence_reader(
    profile=None, *, home=None, allow_live_home=False, busy_timeout_ms=5000
) -> SessionStore:
    """Open a read-only reader over a profile's session database.

    Any number of readers may be open at once; a reader physically
    cannot write (SQLite ``mode=ro``). Raises
    :class:`CorruptDatabaseError` when the database is unreadable,
    and :class:`FileNotFoundError` when the database file does not
    exist yet (readers never create it — open the writer first).
    """
    root = resolve_home(profile, home=home)
    guard_not_live(root, allow_live_home=allow_live_home)
    db_path = state_db_path(profile, home=home)
    if not db_path.exists():
        raise FileNotFoundError(
            f"no session database at {db_path} yet; open the writer "
            "(open_persistence()) first to create it"
        )
    return SessionStore(db_path, read_only=True, busy_timeout_ms=busy_timeout_ms)
