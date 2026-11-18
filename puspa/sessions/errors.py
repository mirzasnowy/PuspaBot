"""Errors for the session persistence package (G26).

Every failure mode a caller has to distinguish gets its own type so
call sites can fail closed on exactly the right condition instead of
matching on message text.
"""

from __future__ import annotations


class SessionError(Exception):
    """Base class for all session persistence errors."""


class SingleWriterError(SessionError):
    """A second writer was opened for a profile that already has one.

    Only the profile gateway writes the canonical session database;
    every other client goes through the gateway API (or opens a
    read-only reader). See ``puspa.sessions.gateway``.
    """


class LiveHomeError(SessionError):
    """An operation would touch the real live home directory.

    Tests must run against a temporary home (``PUSPA_HOME`` pointed at
    a ``tmp_path``). The live home (``~/.puspa``) is only touched when
    the caller passes ``allow_live_home=True`` and the environment
    escape hatch ``PUSPA_ALLOW_LIVE_HOME=1`` is set.
    """


class CorruptDatabaseError(SessionError):
    """A state database file exists but is not a readable SQLite database.

    Fail-closed: the file is never deleted, truncated, or overwritten
    automatically. Follow ``docs/state-db-recovery.md`` to recover
    from a backup or rebuild from the ledger.
    """


class ProfileError(SessionError):
    """An invalid profile name, or a missing profile/home value."""


class WriteContentionError(SessionError):
    """A write could not acquire the database lock in time.

    The writer retries inside ``busy_timeout`` first; this is raised
    only when SQLite still reports the database as locked, so the
    caller knows the write did not happen and can retry later.
    """


class SessionNotFoundError(SessionError):
    """A referenced session id does not exist in this profile."""
