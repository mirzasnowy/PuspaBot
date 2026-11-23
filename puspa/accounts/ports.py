"""Local account ports: DSN resolution and the single-operator session store.

This module is the public side of the accounts boundary. It defines
the two ports the hosted service implements against its own user
registry and that the local install implements against its own
Postgres database:

- :func:`resolve_dsn` — the canonical DSN resolution (explicit
  argument, then ``PUSPA_DSN`` / ``PUSPA_TEST_DSN``, then the local
  peer-auth default). Every other ``resolve_dsn`` copy in the public
  tree delegates to this one, so there is exactly one resolution
  order to keep correct.
- the session store — :func:`create_user`, :func:`delete_user`,
  :func:`create_session`, :func:`validate_session`,
  :func:`list_sessions`, :func:`revoke_session`,
  :func:`revoke_all_sessions`, :func:`register_agent`,
  :func:`owner_of`, :func:`owner_of_token`,
  :func:`create_agent_schema`, :func:`create_account_schema`.

The local implementations operate on the database behind
:func:`puspa.db.connect` (SQLite by default, Postgres when the DSN
says so) through :func:`resolve_dsn` (the same test database the
rest of the suite uses).

Safety properties kept from the original implementation:

- Passwords are never stored raw, only as Argon2id hashes; email
  is normalized (strip + lower) and unique.
- Only the SHA-256 of a session token is stored, never the token
  itself, so a leaked sessions table yields no usable token.
- ``delete_user`` removes the user row, their sessions, and their
  vault, but never touches the append-only ledger (``events``).
- ``register_agent`` is idempotent for the same owner and refuses
  with :class:`AccessDeniedError` when an agent already belongs to
  someone else, so an agent cannot be hijacked by re-registering it.
"""

from __future__ import annotations

import hashlib
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from puspa import db
from puspa.db import errors as pg_errors

#: The hosted Postgres default. The live default is the SQLite file
#: (see :func:`puspa.db.resolve_dsn`); this stays as the explicit
#: hosted DSN for deployments that still point at Postgres.
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

__all__ = [
    "DEFAULT_DSN",
    "AccessDeniedError",
    "AuthError",
    "DuplicateEmailError",
    "SessionError",
    "authenticate",
    "create_account_schema",
    "create_agent_schema",
    "create_session",
    "create_user",
    "delete_user",
    "list_sessions",
    "owner_of",
    "owner_of_token",
    "register_agent",
    "resolve_dsn",
    "revoke_all_sessions",
    "revoke_session",
    "revoke_session_by_id",
    "validate_session",
]


class AuthError(Exception):
    """Unknown email or wrong password."""


class DuplicateEmailError(Exception):
    """Email already registered."""


class SessionError(Exception):
    """Session token invalid or revoked."""


class AccessDeniedError(Exception):
    """Reader is not the owner of the data. Always used for ownership
    refusals — never replaced with KeyError/SessionError, so callers
    can tell "data exists but is not yours" from "data does not
    exist"."""


_PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=1)


def resolve_dsn(dsn: str | None = None) -> str:
    """DSN resolution (D-SQLite-2): explicit argument, then env, then default.

    Delegates to :func:`puspa.db.resolve_dsn`, the one canonical
    resolution order in the tree. A Postgres DSN passed explicitly
    or through the environment is returned unchanged.
    """
    return db.resolve_dsn(dsn)


def _connect(dsn=None):
    return db.connect(resolve_dsn(dsn))


def _normalize_email(email):
    if not isinstance(email, str):
        raise ValueError("email must be a string")
    return email.strip().lower()


def create_user(email, password, dsn=None):
    """Create a user; return the user id (str UUID).

    Raise DuplicateEmailError when the (normalized) email already
    exists.
    """
    email_n = _normalize_email(email)
    if not email_n:
        raise ValueError("email is empty")
    if not password:
        raise ValueError("password is empty")
    password_hash = _PASSWORD_HASHER.hash(password)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                    (email_n, password_hash),
                )
                user_id = cur.fetchone()[0]
            except pg_errors.UniqueViolation as err:
                raise DuplicateEmailError(f"email already registered: {email_n}") from err
        conn.commit()
    return str(user_id)


def authenticate(email, password, dsn=None):
    """Check email + password; return the user id (str UUID).

    Raise AuthError for an unknown email or a wrong password. The
    message is deliberately the same for both, so it never leaks
    which one was wrong.
    """
    email_n = _normalize_email(email)
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, password_hash FROM users WHERE email = %s",
            (email_n,),
        )
        row = cur.fetchone()
    if row is None:
        raise AuthError("email or password is wrong")
    user_id, password_hash = row
    try:
        _PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHash) as err:
        raise AuthError("email or password is wrong") from err
    return str(user_id)


def delete_user(user_id, dsn=None):
    """Delete a user: the user row + all sessions + the whole vault.

    Ledger events are NOT deleted (append-only audit) — the
    ``events`` table is never touched here.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM vault WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id, device_label, dsn=None):
    """Create a session for a user; return the raw token (str).

    The raw token is returned exactly once here — the caller must
    store it safely; the database only keeps its hash.
    """
    token = secrets.token_urlsafe(32)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (user_id, token_hash, device_label) VALUES (%s, %s, %s)",
                (user_id, _token_hash(token), device_label),
            )
        conn.commit()
    return token


def validate_session(token, dsn=None):
    """Check a token; return the user id (str UUID) while it is valid.

    Raise SessionError when the token is unknown or revoked. A
    session that validates successfully refreshes ``last_seen_at``.
    """
    token_h = _token_hash(token)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM sessions WHERE token_hash = %s AND NOT revoked",
                (token_h,),
            )
            row = cur.fetchone()
            if row is None:
                raise SessionError("session is invalid or revoked")
            cur.execute(
                "UPDATE sessions SET last_seen_at = now() WHERE token_hash = %s",
                (token_h,),
            )
        conn.commit()
    return str(row[0])


def owner_of_token(token, dsn=None):
    """Return the user id owning a token, WITHOUT side effects.

    Same as :func:`validate_session` but does NOT refresh
    ``last_seen_at``: use this for ownership checks, so a token
    presented by the wrong user leaves no trace on the owner's
    session. Raise SessionError when the token is unknown or
    revoked.
    """
    token_h = _token_hash(token)
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT user_id FROM sessions WHERE token_hash = %s AND NOT revoked",
            (token_h,),
        )
        row = cur.fetchone()
    if row is None:
        raise SessionError("session is invalid or revoked")
    return str(row[0])


def revoke_session(token, dsn=None):
    """Revoke one session (log out that device). Idempotent."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET revoked = true WHERE token_hash = %s",
                (_token_hash(token),),
            )
        conn.commit()


def revoke_session_by_id(session_id, user_id, dsn=None):
    """Revoke one session by its row id. Returns True when a
    session of this user was revoked, False when no such session
    exists. An unknown id and another user's id read the same
    (False), so one user's ids cannot probe another user's
    sessions. Idempotent: revoking an already-revoked session
    still returns True. A malformed id raises the database's
    error, which the caller maps to "no such session"."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET revoked = true WHERE id = %s AND user_id = %s",
                (session_id, user_id),
            )
            revoked = cur.rowcount > 0
        conn.commit()
    return revoked


def revoke_all_sessions(user_id, dsn=None):
    """Revoke all sessions of a user (log out every device)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET revoked = true WHERE user_id = %s",
                (user_id,),
            )
        conn.commit()


def list_sessions(user_id, dsn=None):
    """List all sessions of a user (revoked ones included, with the
    ``revoked`` flag), newest first."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, device_label, created_at, last_seen_at, revoked "
            "FROM sessions WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(r[0]),
            "device_label": r[1],
            "created_at": r[2],
            "last_seen_at": r[3],
            "revoked": r[4],
        }
        for r in rows
    ]


def create_agent_schema(dsn=None):
    """Apply the agent registry table (migration 0001_core).

    The ``agents`` table comes from the migration, not from a schema
    file. Idempotent; safe to call before every test.
    """
    from puspa.migrations import apply_migrations

    apply_migrations(resolve_dsn(dsn), target="0001")


def create_account_schema(dsn=None):
    """Apply the account tables (migration 0001_core).

    The tables (users, sessions, vault, agents) come from the
    migration, not from a schema file. Idempotent; safe to call
    before every test.
    """
    from puspa.migrations import apply_migrations

    apply_migrations(resolve_dsn(dsn), target="0001")


def register_agent(agent_id, user_id, dsn=None):
    """Register an agent to its owner. Idempotent when the agent is
    already registered to the same user.

    Raise AccessDeniedError when the agent id is already registered
    to a DIFFERENT user (an agent cannot be hijacked or moved by
    re-registering it). Raise ValueError for an empty agent_id or
    user_id.
    """
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("agent_id must be a non-empty string")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id must be a non-empty string")
    agent_id = agent_id.strip()
    user_id = user_id.strip()
    with db.connect(resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM agents WHERE agent_id = %s", (agent_id,))
            row = cur.fetchone()
            if row is not None:
                if str(row[0]) != user_id:
                    raise AccessDeniedError(
                        f"agent {agent_id!r} is already registered to another user"
                    )
                return  # already registered to the same owner: no-op
            cur.execute(
                "INSERT INTO agents (agent_id, user_id) VALUES (%s, %s)",
                (agent_id, user_id),
            )
        conn.commit()


def owner_of(agent_id, dsn=None):
    """Return the user id (str UUID) owning an agent, or None when
    the agent is not registered. None means the owner is unknown,
    which owned readers treat as fail-closed (never as owned by
    anyone)."""
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None
    with db.connect(resolve_dsn(dsn)) as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id FROM agents WHERE agent_id = %s", (agent_id.strip(),))
        row = cur.fetchone()
    return str(row[0]) if row is not None else None
