"""Stop everything for one user (P-02, plan section 12.7).

The red stop-all button in the PWA halts everything the agent
is doing *for the user who pressed it*: every agent the user
owns is stopped through the E-11 stop package
(:func:`puspa.policy.stop_agent` — revoke-all for the agent,
token refresh proved dead, live subagents interrupted), and
every live subagent task in the PWA pool for those agents is
stopped too. One ledger ``task_stopped`` event per agent is
recorded under a single shared stop-all task id, so the whole
press is one auditable action in the audit log.

What this is NOT: it does not touch the global kill switch
(:class:`puspa.ledger.kill_switch.KillSwitch`). That flag stops
every agent for every user and only the operator may flip it;
a user pressing stop-all must not stop anyone else's agent.
It also does not delete anything: stopping is reversible by
starting again, which is the safe direction for a panic button.

The stop is timed (target: under 5 seconds, the plan 6.6
target the E-11 package already measures); the report carries
the measured elapsed time, per agent, so the button can show
what actually stopped instead of claiming it.

Pure orchestration over existing machinery: no new ledger
event type (``task_stopped`` already exists), no new
revocation mechanism (the durable ``RevocationList``).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..evidence.proof import user_agent_ids
from ..policy.revocations import RevocationList
from ..policy.stop import StopError, stop_agent

if TYPE_CHECKING:
    from ..ledger import Ledger
    from ..runtime.subagents import SubagentPool
    from ..subagents.lifecycle import SubagentLifecycle

__all__ = ["STOP_ALL_TARGET_SECONDS", "StopAllReport", "stop_all_for_user"]

#: Stopwatch target for the whole press, in seconds (plan 6.6).
STOP_ALL_TARGET_SECONDS = 5.0

#: URL of the audit trail the stop-all press is recorded in.
AUDIT_TRAIL_URL = "/api/proof/audit-trail"


@dataclass
class StopAllReport:
    """What one stop-all press did, and how long it took."""

    user_id: str
    actor: str
    reason: str
    task_id: str
    """Shared task id for the press: every per-agent ledger event
    of this press hangs off this one id, so the press reads as
    one action in the audit log."""
    agents_stopped: list[dict[str, Any]] = field(default_factory=list)
    """One entry per agent stopped: agent id, subagents
    interrupted, whether token refresh is blocked, and the
    elapsed seconds of that agent's stop."""
    subagents_stopped: int = 0
    """Live subagents stopped in the PWA pool, across agents."""
    pool_wired: bool = False
    """False when no pool was supplied, so no pool task could be
    reached from here (there may still be none running)."""
    lifecycle_wired: bool = False
    """False when no lifecycle was supplied, so no live child
    could be interrupted through it."""
    ledger_recorded: bool = False
    elapsed_seconds: float = 0.0
    stopped_at: str = ""

    def to_dict(self) -> dict:
        """JSON-safe dict of this report."""
        return {
            "user_id": self.user_id,
            "actor": self.actor,
            "reason": self.reason,
            "task_id": self.task_id,
            "agents_stopped": self.agents_stopped,
            "subagents_stopped": self.subagents_stopped,
            "pool_wired": self.pool_wired,
            "lifecycle_wired": self.lifecycle_wired,
            "ledger_recorded": self.ledger_recorded,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "stopped_at": self.stopped_at,
            "audit_trail_url": AUDIT_TRAIL_URL,
        }


def stop_all_for_user(
    user_id: str,
    *,
    dsn=None,
    revocations: RevocationList | None = None,
    lifecycle: SubagentLifecycle | None = None,
    pool: SubagentPool | None = None,
    ledger: Ledger | None = None,
    actor: str = "user",
    reason: str = "stop all requested from the PWA",
) -> StopAllReport:
    """Stop everything for ``user_id``: all their agents, all live work.

    ``revocations`` is the durable list (operator connection);
    one is built on ``dsn`` when not supplied. ``lifecycle``
    is a :class:`puspa.subagents.lifecycle.SubagentLifecycle`
    (or any object with ``cancel_all_for_agent``); ``pool`` is
    the PWA :class:`puspa.runtime.subagents.SubagentPool`.
    ``ledger`` records one ``task_stopped`` event per agent
    under the shared stop-all task id.

    Returns a :class:`StopAllReport`. A user with no agents
    stops nothing and records nothing: there is no agent to
    audit. Raises :class:`StopError` when an agent cannot be
    stopped or its stop cannot be recorded; agents stopped
    before the failure stay stopped (the report is lost with
    the raise, so callers that need a partial record should
    catch and read the ledger instead).
    """
    started = time.perf_counter()
    if not user_id:
        raise StopError("user_id must be a non-empty string")
    if not actor:
        raise StopError("actor must be a non-empty string")
    if not reason:
        raise StopError("reason must be a non-empty string")
    if lifecycle is not None and not hasattr(lifecycle, "cancel_all_for_agent"):
        raise StopError("lifecycle cannot interrupt children: it has no cancel_all_for_agent")

    agent_ids = user_agent_ids(user_id, dsn=dsn)
    revocation_list = revocations if revocations is not None else RevocationList(dsn)
    task_id = f"stop-all-{uuid.uuid4().hex[:12]}"
    stopped_at = datetime.now(UTC).isoformat()

    report = StopAllReport(
        user_id=user_id,
        actor=actor,
        reason=reason,
        task_id=task_id,
        pool_wired=pool is not None,
        lifecycle_wired=lifecycle is not None,
        stopped_at=stopped_at,
    )
    if not agent_ids:
        report.elapsed_seconds = time.perf_counter() - started
        return report

    ledger_wanted = ledger is not None
    for agent_id in agent_ids:
        agent_report = stop_agent(
            agent_id=agent_id,
            revocations=revocation_list,
            actor=actor,
            reason=reason,
            lifecycle=lifecycle,
            ledger=ledger if ledger_wanted else None,
            task_id=task_id if ledger_wanted else None,
        )
        report.agents_stopped.append(
            {
                "agent_id": agent_id,
                "interrupted_subagents": len(agent_report.interrupted_subagents),
                "refresh_blocked": agent_report.refresh_blocked,
                "elapsed_seconds": round(agent_report.elapsed_seconds, 3),
            }
        )
        if ledger_wanted:
            report.ledger_recorded = True
        if pool is not None:
            pool_result = pool.stop_all_for_agent(agent_id)
            report.subagents_stopped += pool_result["stopped"]

    report.elapsed_seconds = time.perf_counter() - started
    return report
