"""Durable revocation lists for permission tokens (E-11, plan section 6.6).

The in-memory revocation set in :mod:`puspa.policy.tokens` dies with
the process that holds it: a restarted Gate forgets every revocation,
and a token refreshed in another process never sees it. This module
is the durable replacement, backed by migration 0020
(``token_revocations`` + ``agent_revocations``):

- :meth:`RevocationList.revoke_jti`: revoke one token by its ``jti``.
- :meth:`RevocationList.revoke_all_for_agent`: revoke-all for one
  agent. Every token of that agent issued at or before the recorded
  instant is dead — including tokens refreshed from a pre-stop parent,
  because a refresh re-verifies the parent token first (see
  :func:`puspa.policy.tokens.verify_token` with a revocation list).
- :meth:`RevocationList.is_revoked_payload`: the check the Gate runs
  on every call, for a verified token payload.

Writes run on the operator connection (the table owner): the agent
role has SELECT only on both tables (migration 0020), so an agent
that is stopped cannot delete its own revocation. Reads may run as
a read-only role through ``read_role`` (``puspa_app`` in production):
the Gate only ever reads these tables. Tests and operator tools use
the default (no role switch, the DSN user reads and writes).
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from puspa.migrations import apply_migrations

__all__ = ["RevocationError", "RevocationList"]

#: Default DSN (peer auth as the OS user, no password).
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: Migration that creates the revocation tables.
REVOCATION_MIGRATION = "0020"

_ROLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


class RevocationError(Exception):
    """A revocation could not be recorded or read."""


class RevocationList:
    """Durable per-token and per-agent revocation lists.

    ``dsn`` must be an operator connection (the table owner) for
    the write methods; reads work on any connection that can SELECT
    the two tables. Pass ``read_role="puspa_app"`` when the caller
    runs in the agent process: reads then run as the read-only
    application role, so the agent process never needs the owner
    connection just to check a token.
    """

    def __init__(self, dsn: str | None = None, *, read_role: str | None = None):
        if read_role is not None and not _ROLE_NAME_RE.match(read_role):
            raise RevocationError(f"invalid role name: {read_role!r}")
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
        self.read_role = read_role

    def create_schema(self) -> None:
        """Apply migrations up to 0020 (operator step, as the owner)."""
        apply_migrations(self.dsn, target=REVOCATION_MIGRATION)

    # -- connections ----------------------------------------------------

    def _connect(self, *, for_read: bool = False):
        conn = psycopg.connect(self.dsn, row_factory=dict_row)
        if for_read and self.read_role is not None:
            with conn.cursor() as cur:
                cur.execute(f"SET ROLE {self.read_role}")
        return conn

    # -- writes (operator only) -----------------------------------------

    def revoke_jti(self, jti: str, agent_id: str, *, reason: str = "") -> datetime:
        """Revoke one token by its ``jti``. Idempotent: revoking the
        same ``jti`` twice records it once. Returns the instant the
        revocation was recorded."""
        if not jti or not isinstance(jti, str):
            raise RevocationError("jti must be a non-empty string")
        if not agent_id or not isinstance(agent_id, str):
            raise RevocationError("agent_id must be a non-empty string")
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO token_revocations (jti, agent_id, reason) "
                    "VALUES (%s, %s, %s) ON CONFLICT (jti) DO NOTHING "
                    "RETURNING revoked_at",
                    (jti, agent_id, reason),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        "SELECT revoked_at FROM token_revocations WHERE jti = %s",
                        (jti,),
                    )
                    row = cur.fetchone()
                conn.commit()
        except psycopg.errors.UndefinedTable as exc:
            raise RevocationError(
                "token_revocations table is missing; run RevocationList.create_schema() first"
            ) from exc
        if row is None:  # cannot happen; last defence
            raise RevocationError(f"revocation for jti {jti!r} vanished after insert")
        return row["revoked_at"]

    def revoke_all_for_agent(self, agent_id: str, *, actor: str, reason: str) -> datetime:
        """Revoke every token of one agent issued up to now.

        Upserts the agent's row in ``agent_revocations`` with the
        current instant; that instant is the cutoff
        (:meth:`is_revoked_payload` treats any token of this agent
        with ``iat`` at or before it as revoked). Re-stopping an
        agent moves the cutoff forward to the new instant, so tokens
        issued between the two stops die as well. Returns the cutoff.
        """
        if not agent_id or not isinstance(agent_id, str):
            raise RevocationError("agent_id must be a non-empty string")
        if not actor:
            raise RevocationError("actor must be a non-empty string")
        if not reason:
            raise RevocationError("reason must be a non-empty string")
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_revocations (agent_id, reason, actor) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (agent_id) DO UPDATE SET "
                    "revoked_at = EXCLUDED.revoked_at, "
                    "reason = EXCLUDED.reason, actor = EXCLUDED.actor "
                    "RETURNING revoked_at",
                    (agent_id, reason, actor),
                )
                row = cur.fetchone()
                conn.commit()
        except psycopg.errors.UndefinedTable as exc:
            raise RevocationError(
                "agent_revocations table is missing; run RevocationList.create_schema() first"
            ) from exc
        if row is None:  # cannot happen; last defence
            raise RevocationError(f"revoke-all for agent {agent_id!r} vanished after upsert")
        return row["revoked_at"]

    # -- reads (Gate path: every call) ----------------------------------

    def agent_revoked_at(self, agent_id: str) -> datetime | None:
        """The revoke-all cutoff for one agent, or None if never stopped."""
        if not agent_id:
            return None
        try:
            with self._connect(for_read=True) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT revoked_at FROM agent_revocations WHERE agent_id = %s",
                    (agent_id,),
                )
                row = cur.fetchone()
        except psycopg.errors.UndefinedTable as exc:
            raise RevocationError(
                "agent_revocations table is missing; run RevocationList.create_schema() first"
            ) from exc
        return row["revoked_at"] if row is not None else None

    def is_revoked_jti(self, jti: str) -> bool:
        """True when this ``jti`` is listed as individually revoked."""
        if not jti:
            return False
        try:
            with self._connect(for_read=True) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM token_revocations WHERE jti = %s",
                    (jti,),
                )
                return cur.fetchone() is not None
        except psycopg.errors.UndefinedTable as exc:
            raise RevocationError(
                "token_revocations table is missing; run RevocationList.create_schema() first"
            ) from exc

    def is_revoked_payload(self, payload: dict) -> bool:
        """True when a verified token payload is revoked.

        Either its ``jti`` is individually listed, or its agent has
        a revoke-all cutoff at or after the token's ``iat``. A token
        with no usable ``iat`` under an active agent cutoff is
        treated as revoked (fail-closed): without an issue time
        there is no way to prove it was issued after the stop.
        """
        if not isinstance(payload, dict):
            return True
        jti = payload.get("jti")
        if isinstance(jti, str) and jti and self.is_revoked_jti(jti):
            return True
        agent_id = payload.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            return False
        cutoff = self.agent_revoked_at(agent_id)
        if cutoff is None:
            return False
        iat = payload.get("iat")
        if not isinstance(iat, int) or isinstance(iat, bool):
            return True
        return iat <= int(cutoff.astimezone(UTC).timestamp())
