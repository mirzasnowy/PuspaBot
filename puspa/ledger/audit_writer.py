"""Audit writer (K12): ledger writes through the application role only.

The agent never writes to the audit log as the table owner. Every
agent-side write goes through :class:`AuditWriter`, a :class:`Ledger
<puspa.ledger.ledger.Ledger>` bound to the ``puspa_app`` role (``SET
ROLE`` right after connecting). Postgres itself enforces what that
role may do — INSERT and SELECT on ``events``, nothing else (see
migration 0001_core) — so an agent that tries to UPDATE or
DELETE a log row is refused by the database with
``InsufficientPrivilege``, whatever the application code does.

:func:`ensure_audit_role` creates the role when it is missing (an
operator step, run as a superuser); the schema grants apply only
when the role already exists, so the role must exist before
``create_schema`` runs. The test suite's shared fixture does this
once per session; the function exists for standalone setups.
"""

from __future__ import annotations

import os

from puspa.db import connect as _db_connect, errors as pg_errors

from .ledger import APP_ROLE, DEFAULT_DSN, Ledger

__all__ = ["AuditWriter", "ensure_audit_role"]


def ensure_audit_role(dsn: str | None = None) -> None:
    """Create the ``puspa_app`` role (NOLOGIN) when it is missing.

    Must run as a superuser (the DSN user owns the database).
    Idempotent: a second call is a no-op.
    """
    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    with _db_connect(dsn) as conn:
        conn.execute(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN "
            "CREATE ROLE puspa_app NOLOGIN; "
            "END IF; END $$"
        )
        conn.commit()


class AuditWriter:
    """Append-only writer to the audit log, as the agent role.

    Wraps a :class:`Ledger` bound to ``puspa_app``. Appends and
    reads behave exactly like the plain ledger; the difference is
    the connection: every statement runs as the application role,
    so UPDATE/DELETE on ``events`` (and on the ``kill_switch``
    flag table) fail in Postgres before they can touch a row.
    Schema setup (:meth:`create_schema`) still runs as the DSN
    user — that is an operator step, never an agent step.
    """

    def __init__(self, dsn: str | None = None, *, role: str = APP_ROLE):
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
        self.role = role
        self._ledger = Ledger(self.dsn, role=role)

    def create_schema(self) -> None:
        """Create the ledger schema (operator step, as the owner)."""
        Ledger(self.dsn).create_schema()

    def append_event(self, **kwargs) -> dict:
        """Append one event as the application role."""
        return self._ledger.append_event(**kwargs)

    def events(self, task_id: str, event_type: str | None = None) -> list[dict]:
        """Read one task's events as the application role."""
        return self._ledger.events(task_id, event_type=event_type)

    def verify_chain(self, task_id: str) -> tuple[bool, list[str]]:
        """Verify one task's hash chain as the application role."""
        return self._ledger.verify_chain(task_id)

    def probe_tamper(self, event_id: str) -> dict[str, bool]:
        """Try to tamper with one log row as the application role.

        Attempts UPDATE and DELETE on ``event_id`` and reports
        what Postgres did: both must be ``True`` (blocked with
        ``InsufficientPrivilege``). A ``False`` means the role
        could modify the log — the deployment's grants are wrong
        and the audit log is not outside the agent's reach. The
        probe never changes a row: each attempt runs in its own
        transaction (re-``SET ROLE`` first, because a rollback
        resets the role to the session user) and is rolled back
        afterwards, so even a misconfigured grant that let an
        attempt through leaves the row as it was.
        """
        attempts = (
            ("update_blocked", "UPDATE events SET content = '{}'::jsonb WHERE id = %s"),
            ("delete_blocked", "DELETE FROM events WHERE id = %s"),
        )
        blocked: dict[str, bool] = {name: False for name, _ in attempts}
        with _db_connect(self.dsn) as conn, conn.cursor() as cur:
            for name, statement in attempts:
                cur.execute(f"SET ROLE {self.role}")
                try:
                    cur.execute(statement, (event_id,))
                except pg_errors.InsufficientPrivilege:
                    blocked[name] = True
                finally:
                    conn.rollback()
        return blocked
