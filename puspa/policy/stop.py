"""Stop one agent, as a single timed package (E-11, plan section 6.6).

"Stop" means three things, done in order and measured with a
stopwatch (target: under 5 seconds for N subagents):

1. **Revoke-all.** Write a revoke-all row for the agent into the
   durable revocation list
   (:class:`puspa.policy.revocations.RevocationList`). From that
   instant on, every live token of the agent is refused by the Gate,
   at approval request and again at execution.
2. **Stop token refresh.** A refresh re-issues a child token from a
   parent token, and issuing a child re-verifies the parent — which
   now fails against the revocation list. When the caller supplies
   a ``probe_token`` (a live pre-stop token), the stop proves the
   refresh path is dead by re-verifying it: it must fail. Without
   a probe token the revoke-all cutoff itself is the guarantee, and
   the report says so instead of claiming a proof it did not run.
3. **Interrupt all subagents.** Ask the lifecycle to cancel every
   live child of this agent. Cancel is cooperative: a child that is
   already at a boundary finishes as CANCELLED at once, a running
   child becomes CANCEL_REQUESTED and finishes as CANCELLED at its
   next boundary. Either way no further tool call runs for it.

The stop is per-agent. It does not touch the global kill switch
(:class:`puspa.ledger.kill_switch.KillSwitch`): stopping one agent
must not stop every agent. Optionally the stop is recorded in the
ledger as a ``task_stopped`` event (existing type, no new type).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .revocations import RevocationList
from .tokens import TokenError, verify_token

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from puspa.ledger.ledger import Ledger

__all__ = ["StopError", "StopReport", "stop_agent"]

#: Stopwatch target for the whole package, in seconds (plan 6.6).
STOP_TARGET_SECONDS = 5.0


class StopError(Exception):
    """The stop could not be carried out or proved."""


@dataclass
class StopReport:
    """What one stop did, and how long it took (gate-11 evidence)."""

    agent_id: str
    actor: str
    reason: str
    cutoff: datetime
    """Revoke-all instant: tokens of this agent issued at or before it are dead."""
    revoked_jtis: list[str] = field(default_factory=list)
    """Individual jtis revoked by this call (empty for a pure revoke-all)."""
    refresh_blocked: bool = False
    """True when the refresh path was proved dead (probe refused) or
    when no probe was supplied and the cutoff is the guarantee."""
    refresh_proof: str = ""
    """How refresh blocking was established: ``probe-refused`` (a
    pre-stop token was re-verified and refused), ``cutoff-only``
    (no probe token supplied), or ``probe-missing``."""
    interrupted_subagents: list[dict[str, Any]] = field(default_factory=list)
    """One entry per live child found: subagent id + state after cancel."""
    lifecycle_wired: bool = False
    """False when no lifecycle was supplied, so no child could be
    interrupted (there may still be none running)."""
    ledger_recorded: bool = False
    elapsed_seconds: float = 0.0


def stop_agent(
    *,
    agent_id: str,
    revocations: RevocationList,
    actor: str,
    reason: str,
    lifecycle: Any | None = None,
    public_key: Ed25519PublicKey | None = None,
    probe_token: str | None = None,
    revoke_jtis: list[str] | None = None,
    ledger: Ledger | None = None,
    task_id: str | None = None,
) -> StopReport:
    """Stop one agent: revoke-all, stop refresh, interrupt children.

    ``revocations`` is the durable list (operator connection).
    ``lifecycle`` is a :class:`puspa.subagents.lifecycle.SubagentLifecycle`
    (or any object with ``cancel_all_for_agent(agent_id)``); None
    means no live children can be reached from here. ``probe_token``
    is a live pre-stop token of this agent, used to prove the refresh
    path is dead; ``public_key`` must accompany it. ``ledger`` +
    ``task_id`` record the stop as a ``task_stopped`` event.

    Returns a :class:`StopReport` with the measured elapsed time.
    Raises :class:`StopError` when the stop itself cannot be done
    (bad arguments, unwritable revocation list, a lifecycle that
    cannot cancel, a probe token that still verifies, or a ledger
    write that fails after the agent was already stopped).
    """
    started = time.perf_counter()
    if not agent_id:
        raise StopError("agent_id must be a non-empty string")
    if not actor:
        raise StopError("actor must be a non-empty string")
    if not reason:
        raise StopError("reason must be a non-empty string")
    if probe_token is not None and public_key is None:
        raise StopError("a probe_token needs its public_key to verify against")
    if lifecycle is not None and not hasattr(lifecycle, "cancel_all_for_agent"):
        raise StopError("lifecycle cannot interrupt children: it has no cancel_all_for_agent")

    # 1. Revoke-all (plus any individually named jtis), first: the
    # agent is dead from this instant even if a later step fails.
    cutoff = revocations.revoke_all_for_agent(agent_id, actor=actor, reason=reason)
    revoked_jtis: list[str] = []
    for jti in revoke_jtis or []:
        revocations.revoke_jti(jti, agent_id, reason=reason)
        revoked_jtis.append(jti)

    # 2. Stop token refresh: prove it with the probe token, or say
    # plainly that only the cutoff guarantees it.
    if probe_token is not None:
        # Guarded above: a probe token without its public key is refused.
        assert public_key is not None
        try:
            verify_token(probe_token, public_key, revocations=revocations)
        except TokenError:
            refresh_blocked = True
            refresh_proof = "probe-refused"
        else:
            raise StopError(
                "refresh is NOT stopped: a pre-stop token still verifies after revoke-all"
            )
    else:
        refresh_blocked = True
        refresh_proof = "cutoff-only"

    # 3. Interrupt all live subagents of this agent.
    interrupted: list[dict[str, Any]] = []
    if lifecycle is not None:
        interrupted = list(lifecycle.cancel_all_for_agent(agent_id))

    report = StopReport(
        agent_id=agent_id,
        actor=actor,
        reason=reason,
        cutoff=cutoff,
        revoked_jtis=revoked_jtis,
        refresh_blocked=refresh_blocked,
        refresh_proof=refresh_proof,
        interrupted_subagents=interrupted,
        lifecycle_wired=lifecycle is not None,
    )

    if ledger is not None and task_id is not None:
        try:
            ledger.append_event(
                task_id=task_id,
                agent_id=agent_id,
                event_type="task_stopped",
                content={
                    "stop": "agent_stopped",
                    "actor": actor,
                    "reason": reason,
                    "interrupted_subagents": len(interrupted),
                },
            )
        except Exception as exc:
            report.elapsed_seconds = time.perf_counter() - started
            raise StopError(
                f"agent IS stopped but the stop could not be recorded in the ledger: {exc}"
            ) from exc
        report.ledger_recorded = True

    report.elapsed_seconds = time.perf_counter() - started
    return report
