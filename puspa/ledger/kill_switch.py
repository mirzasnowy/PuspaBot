"""Kill switch (K12): one flag that stops the agent, outside its reach.

The flag is a single row in the ``kill_switch`` table (see
migration 0001_core). The enforcement is in Postgres, not in a
promise:

- The application role (``puspa_app`` — the only role the agent's
  connections ever run as) has SELECT on the table and nothing else.
  An agent attempt to INSERT, UPDATE, or DELETE the flag is refused
  by Postgres with ``InsufficientPrivilege``.
- Only the table owner (the operator connection) can arm or disarm
  the flag, through :meth:`KillSwitch.activate` /
  :meth:`KillSwitch.deactivate`. Those are explicit operator calls:
  the agent is never handed a ``KillSwitch`` bound to the owner
  connection, and there is no agent-side path that flips the flag.

When the flag is active, the turn loop
(:class:`puspa.harness.loop.CodingHarness`) and the scheduler
(:mod:`puspa.scheduler.scheduler`) refuse to start any new action:
the harness returns a ``killed`` outcome before the first attempt,
and each later attempt re-checks the flag, so a switch flipped
mid-run stops the run before the next attempt. The scheduler's
``tick`` / ``run_trigger`` / ``run_missed`` run nothing while the
flag is active.

Every transition is recorded in the ledger (the audit log) using
existing event types only — the closed set of 31 revision-7 types
is untouched:

- activation -> ``task_stopped`` with ``content["kill_switch"] ==
  "activated"`` (the agent stops);
- deactivation -> ``user_message`` with ``content["kill_switch"] ==
  "deactivated"`` (a person tells the system it may run again).

The flag flip happens before the ledger write, on purpose: if the
ledger is unreachable, the agent still stops (fail-closed, safe
direction) and :meth:`KillSwitch.activate` raises
:class:`KillSwitchError` so the operator knows the record is
missing.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from puspa.db import connect as _db_connect, errors as pg_errors
from puspa.db import dict_row

if TYPE_CHECKING:
    from .ledger import Ledger

#: Application role: the only role agent connections run as.
APP_ROLE = "puspa_app"

#: Default DSN (peer auth as the OS user, no password).
DEFAULT_DSN = None

#: The single flag row id.
FLAG_ID = 1

#: Ledger event type for an activation (the agent stops).
ACTIVATION_EVENT_TYPE = "task_stopped"

#: Ledger event type for a deactivation (a person resumes the agent).
DEACTIVATION_EVENT_TYPE = "user_message"


class KillSwitchError(Exception):
    """The kill switch could not be read or changed."""


class KillSwitchActive(KillSwitchError):
    """The kill switch is active: no new agent action may start."""


def _row_to_state(row: dict | None) -> dict | None:
    """One ``kill_switch`` row -> plain state dict, or None."""
    if row is None:
        return None
    return {
        "active": bool(row["active"]),
        "actor": row["actor"],
        "reason": row["reason"],
        "updated_at": row["updated_at"],
    }


class KillSwitch:
    """The kill-switch flag, read and flipped by the operator.

    ``dsn`` must be an operator connection (the table owner): the
    agent role cannot UPDATE the table at all, so a ``KillSwitch``
    built on the agent DSN can only read the flag, never flip it.
    """

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)

    def _connect(self):
        return _db_connect(self.dsn, row_factory=dict_row)

    def create_schema(self) -> None:
        """Create the ledger schema (which includes the flag table).

        Operator step, not an agent step: it runs the full
        migration 0001_core as the table owner.
        """
        # Lazy import: this module is imported by ``puspa.ledger``
        # at package import time, and ``ledger`` must not import
        # this module back.
        from .ledger import Ledger

        Ledger(self.dsn).create_schema()

    def state(self) -> dict | None:
        """The current flag row, or None when no flag was ever set.

        Raises :class:`KillSwitchError` when the table itself is
        missing: an unknown flag state must never read as "off".
        """
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT * FROM kill_switch WHERE id = %s", (FLAG_ID,))
                return _row_to_state(cur.fetchone())
        except pg_errors.UndefinedTable as exc:
            raise KillSwitchError(
                "kill_switch table is missing; run KillSwitch.create_schema() "
                "before reading the flag"
            ) from exc

    def is_active(self) -> bool:
        """True when the flag is set (no row yet means off)."""
        current = self.state()
        return bool(current and current["active"])

    def check(self) -> None:
        """Raise :class:`KillSwitchActive` when the flag is set.

        The exception carries who armed the flag, why, and when,
        so the stop is explainable at the point it happens.
        """
        current = self.state()
        if current and current["active"]:
            detail = ""
            if current["actor"] or current["reason"]:
                detail = (
                    f" (armed by {current['actor']}: {current['reason']})"
                    if current["actor"] and current["reason"]
                    else f" (armed by {current['actor'] or current['reason']})"
                )
            when = f", at {current['updated_at'].isoformat()}" if current["updated_at"] else ""
            raise KillSwitchActive(f"kill switch is active{detail}{when}")

    def activate(
        self,
        *,
        actor: str,
        reason: str,
        ledger: Ledger,
        task_id: str,
        agent_id: str,
    ) -> dict:
        """Arm the flag and record the activation in the ledger.

        The flag flips first: if the ledger write then fails, the
        agent is still stopped and :class:`KillSwitchError` is
        raised so the missing record is not silent. ``ledger`` is
        required — an activation without a record is refused by
        this API (pass a ledger bound to an operator connection).
        """
        if not actor:
            raise KillSwitchError("actor must be a non-empty string")
        if not reason:
            raise KillSwitchError("reason must be a non-empty string")
        state = self._set_flag(True, actor=actor, reason=reason)
        try:
            ledger.append_event(
                task_id=task_id,
                agent_id=agent_id,
                event_type=ACTIVATION_EVENT_TYPE,
                content={
                    "kill_switch": "activated",
                    "actor": actor,
                    "reason": reason,
                },
            )
        except Exception as exc:
            raise KillSwitchError(
                "kill switch is ACTIVE but the activation could not be "
                f"recorded in the ledger: {exc}"
            ) from exc
        return state

    def deactivate(
        self,
        *,
        actor: str,
        reason: str,
        ledger: Ledger,
        task_id: str,
        agent_id: str,
    ) -> dict:
        """Disarm the flag and record the deactivation in the ledger.

        Explicit operator path only: there is no agent-side call
        that reaches this method, and the agent role cannot UPDATE
        the flag row directly either (see the module docstring).
        Like :meth:`activate`, the flag flips first and a failed
        ledger write raises :class:`KillSwitchError`.
        """
        if not actor:
            raise KillSwitchError("actor must be a non-empty string")
        if not reason:
            raise KillSwitchError("reason must be a non-empty string")
        state = self._set_flag(False, actor=actor, reason=reason)
        try:
            ledger.append_event(
                task_id=task_id,
                agent_id=agent_id,
                event_type=DEACTIVATION_EVENT_TYPE,
                content={
                    "kill_switch": "deactivated",
                    "actor": actor,
                    "reason": reason,
                },
            )
        except Exception as exc:
            raise KillSwitchError(
                "kill switch is INACTIVE but the deactivation could not "
                f"be recorded in the ledger: {exc}"
            ) from exc
        return state

    def _set_flag(self, active: bool, *, actor: str, reason: str) -> dict:
        """Upsert the single flag row; returns the new state.

        Raises :class:`KillSwitchError` when the write is refused
        (for example, an agent-role connection trying to flip the
        flag gets ``InsufficientPrivilege`` from Postgres).
        """
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO kill_switch (id, active, actor, reason, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "active = EXCLUDED.active, actor = EXCLUDED.actor, "
                        "reason = EXCLUDED.reason, updated_at = EXCLUDED.updated_at "
                        "RETURNING *",
                        (FLAG_ID, active, actor, reason, datetime.now(UTC)),
                    )
                    row = cur.fetchone()
                conn.commit()
        except pg_errors.UndefinedTable as exc:
            raise KillSwitchError(
                "kill_switch table is missing; run KillSwitch.create_schema() "
                "before flipping the flag"
            ) from exc
        except pg_errors.InsufficientPrivilege as exc:
            raise KillSwitchError(
                "this connection may not flip the kill switch "
                "(the agent role has SELECT only on the flag table)"
            ) from exc
        state = _row_to_state(row)
        if state is None:  # cannot happen; last defence
            raise KillSwitchError("kill_switch row vanished after upsert")
        return state


_ROLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def is_kill_switch_active(kill_switch: Any | None) -> bool:
    """True when ``kill_switch`` says the agent must stop.

    Accepts the shapes the harness and scheduler are handed:

    - ``None`` -> False (no switch wired in: behaviour unchanged);
    - a callable ``() -> bool`` -> its return value;
    - an object with ``is_active()`` (a :class:`KillSwitch`, or a
      test stub) -> its return value;
    - an object with a boolean ``active`` attribute -> its value.

    Anything else is a programming error and raises ``TypeError``
    instead of silently reading as "off": a mis-wired switch must
    never look like a clear one.
    """
    if kill_switch is None:
        return False
    if callable(kill_switch):
        return bool(kill_switch())
    is_active = getattr(kill_switch, "is_active", None)
    if callable(is_active):
        return bool(is_active())
    if hasattr(kill_switch, "active"):
        return bool(kill_switch.active)
    raise TypeError(
        "kill_switch must be None, a callable, or an object with "
        f"is_active() or .active; got {type(kill_switch).__name__}"
    )
