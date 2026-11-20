"""Host path for subagent children (G25).

This is the seam to the host path Puspa already has — the lifecycle
does NOT invent a parallel spawn path. A child is accounted for by
the existing :class:`puspa.runtime.subagents.SubagentPool` (one
level only, child token a subset of the parent token, pool caps),
its cost rolls up into the parent
:class:`puspa.runtime.budget.BudgetTracker`, its finish notifies
memory through an injected callback, its stop hooks fire serially,
and its pool entry is cleaned up at finish. Events go to the same
``sink(event_type, content)`` shape the harness uses.

Two things this module deliberately does NOT do:

- It does not mint signed tokens. The child token here is a
  payload dict (a subset of the parent payload) validated by
  :func:`puspa.policy.tokens.is_subset`; signing it into a real
  token (``issue_child``) needs the runtime signing key, which the
  lifecycle does not hold. The pool only ever sees payload dicts.
- It does not run a model. The child body is a caller-supplied
  ``child_fn``; wiring a :class:`puspa.harness.CodingHarness` run
  behind that callable (with iteration boundaries) is a follow-up.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from puspa.policy.tokens import is_subset
from puspa.runtime.budget import (
    CEILING_RP_PER_TASK,
    STEP_MAX_SUBAGENT,
    BudgetExhausted,
    BudgetTracker,
)
from puspa.runtime.subagents import KLASS_PERMISSION, Subagent, SubagentError, SubagentPool

from .hooks import dispatch_subagent_stop
from .records import ChildRecord

__all__ = [
    "HostSession",
    "build_child_token_payload",
    "build_parent_token_payload",
]

#: Child token lifetime cap (seconds from launch). E-11: minutes,
#: not hours — a child token must die soon after its work, so a
#: stop takes effect at the next expiry even if a revocation write
#: is delayed. Matches DEFAULT_TOKEN_TTL_SECONDS in policy/tokens.
CHILD_TOKEN_TTL_S = 300
#: Child token cost ceiling cap (tokens).
CHILD_TOKEN_MAX = 20000


def build_child_token_payload(
    *,
    parent_token: dict | None,
    user_id: str,
    agent_id: str,
    subagent_id: str,
    tool_names: frozenset[str],
    task_id: str,
) -> dict:
    """Build the child token payload as a subset of the parent.

    With a parent token, the child keeps the parent's user, agent,
    domains, and connectors, narrows ``tools`` to the child toolset,
    narrows the budget (at most ``STEP_MAX_SUBAGENT`` steps, at most
    ``CHILD_TOKEN_MAX`` tokens, at most the parent rupiah ceiling),
    and expires no later than the parent. Without a parent token, a
    minimal synthetic parent is assumed and the child payload stands
    alone with the same shape (documented in ``lifecycle.launch``).
    """
    if parent_token is not None:
        parent_budget = parent_token.get("budget") or {}
        budget = {
            "step_max": min(STEP_MAX_SUBAGENT, int(parent_budget.get("step_max", 0) or 0)),
            "token_max": min(CHILD_TOKEN_MAX, int(parent_budget.get("token_max", 0) or 0)),
            "rupiah_max": min(CEILING_RP_PER_TASK, int(parent_budget.get("rupiah_max", 0) or 0)),
        }
        expires = int(parent_token.get("exp", 0) or 0)
        domains = list(parent_token.get("domains") or [])
        connectors = dict(parent_token.get("connectors") or {})
    else:
        budget = {
            "step_max": STEP_MAX_SUBAGENT,
            "token_max": CHILD_TOKEN_MAX,
            "rupiah_max": CEILING_RP_PER_TASK,
        }
        expires = 0
        domains = []
        connectors = {}
    now = int(time.time())
    child_exp = min(expires, now + CHILD_TOKEN_TTL_S) if expires else now + CHILD_TOKEN_TTL_S
    return {
        "user_id": user_id,
        "agent_id": agent_id,
        "subagent_id": subagent_id,
        "jti": uuid.uuid4().hex,
        "tools": sorted(tool_names),
        "domains": domains,
        "connectors": connectors,
        "budget": budget,
        "exp": child_exp,
        "iat": now,
        "task_id": task_id,
    }


def build_parent_token_payload(
    *,
    user_id: str,
    agent_id: str,
    tool_names: frozenset[str],
    task_id: str,
) -> dict:
    """Minimal synthetic parent payload (used only without a real token)."""
    now = int(time.time())
    return {
        "user_id": user_id,
        "agent_id": agent_id,
        "subagent_id": None,
        "jti": uuid.uuid4().hex,
        "tools": sorted(tool_names),
        "domains": [],
        "connectors": {},
        "budget": {
            "step_max": STEP_MAX_SUBAGENT,
            "token_max": CHILD_TOKEN_MAX,
            "rupiah_max": CEILING_RP_PER_TASK,
        },
        "exp": now + CHILD_TOKEN_TTL_S,
        "iat": now,
        "task_id": task_id,
    }


class HostSession:
    """The host path a child runs through: pool, budget, hooks, cleanup.

    ``sink`` takes ``(event_type, content)`` like the harness sink.
    ``memory_notify`` takes one payload dict describing the finished
    child (id, agent, task, state, result hash, cost); it is
    best-effort — a failing notifier is recorded, never fatal.
    ``stop_hooks`` are ``(event, payload)`` callables fired serially
    at finish. ``budget`` is the parent budget the child cost rolls
    up into.
    """

    def __init__(
        self,
        *,
        pool: SubagentPool | None = None,
        budget: BudgetTracker | None = None,
        sink: Callable[[str, dict[str, Any]], None] | None = None,
        memory_notify: Callable[[dict[str, Any]], None] | None = None,
        stop_hooks: list[Callable[..., Any]] | None = None,
    ) -> None:
        self.pool = pool if pool is not None else SubagentPool()
        self.budget = budget if budget is not None else BudgetTracker()
        self.sink = sink if sink is not None else (lambda _event_type, _content: None)
        self.memory_notify = memory_notify
        self.stop_hooks = list(stop_hooks) if stop_hooks is not None else []

    # -- spawn ----------------------------------------------------------

    def spawn(
        self,
        record: ChildRecord,
        *,
        parent_token: dict | None,
        child_token_payload: dict,
        permission_class: str = "Pelaksana",
    ) -> Subagent:
        """Account for the child in the pool (subset + one level + caps).

        The parent payload is the verified parent token when given,
        else the minimal synthetic parent built from the child
        toolset. A pool refusal (non-subset token, bad permission
        class, nested subagent) raises here, before the child runs.
        """
        if permission_class not in KLASS_PERMISSION:
            raise SubagentError(f"permission_class must be one of {KLASS_PERMISSION}")
        agent_token = parent_token
        if agent_token is None:
            agent_token = build_parent_token_payload(
                user_id=child_token_payload["user_id"],
                agent_id=record.agent_id,
                tool_names=frozenset(child_token_payload["tools"]),
                task_id=record.task_id,
            )
        if not is_subset(child_token_payload, agent_token):
            raise SubagentError("child token is not a subset of the parent token")
        try:
            return self.pool.spawn(
                agent_id=record.agent_id,
                task_id=record.task_id,
                permission_class=permission_class,
                agent_token=agent_token,
                subagent_token=child_token_payload,
            )
        except SubagentError:
            raise
        except Exception as exc:  # pool misuse is a host error, not a child failure
            raise SubagentError(str(exc)) from exc

    # -- finish ---------------------------------------------------------

    def finish(
        self,
        record: ChildRecord,
        *,
        parent_tools_restored: bool,
    ) -> dict[str, Any]:
        """Run the shared finish path for a terminal child, in order.

        1. Memory notification (best-effort, recorded).
        2. Serial ``subagent_stop`` hooks (isolated, recorded).
        3. Pool cleanup (the pool entry completes, freeing a slot).
        4. Cost rollup into the parent budget (best-effort: an
           exhausted parent budget is recorded, not fatal — the
           child's own cost is already on the record).

        Returns the finish report. The parent-tools restore check
        itself runs in the lifecycle (it owns the toolsets); the
        outcome arrives as ``parent_tools_restored``.
        """
        payload = {
            "subagent_id": record.subagent_id,
            "agent_id": record.agent_id,
            "task_id": record.task_id,
            "state": record.state.value,
            "result_hash": record.result.result_hash if record.result else "",
            "cost_rp": record.cost_rp,
        }
        memory_notified = False
        memory_error = ""
        if self.memory_notify is not None:
            try:
                self.memory_notify(payload)
                memory_notified = True
            except Exception as exc:
                memory_error = str(exc)
        hook_report = dispatch_subagent_stop(self.stop_hooks, dict(payload))
        pool_completed = False
        if record.pool_subagent_id:
            pool_completed = self.pool.complete(record.pool_subagent_id) is not None
        cost_rollup_rp = 0
        cost_rollup_error = ""
        if record.cost_rp:
            try:
                self.budget.record_cost(record.cost_rp)
                cost_rollup_rp = record.cost_rp
            except BudgetExhausted as exc:
                cost_rollup_error = str(exc)
        report = {
            "subagent_id": record.subagent_id,
            "state": record.state.value,
            "memory_notified": memory_notified,
            "memory_error": memory_error,
            "stop_hooks": hook_report,
            "pool_completed": pool_completed,
            "cost_rollup_rp": cost_rollup_rp,
            "cost_rollup_error": cost_rollup_error,
            "parent_tools_restored": parent_tools_restored,
        }
        self.sink("subagent_finished", report)
        return report
