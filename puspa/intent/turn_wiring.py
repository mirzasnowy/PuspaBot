"""Turn-loop wiring for the Intent Engine (Kosmos K1).

:class:`IntentTurnAdvisor` is the seam between the intent signal
and the turn loop's act-vs-ask path: for one proposed tool call
it infers the intent distribution from the user message, the
profile, and the world model, then decides act or ask through
:func:`puspa.intent.act_ask.decide_act_or_ask`. The turn loop
(:class:`puspa.turns.loop.TurnLoop`, ``intent_advisor`` parameter)
consults it before dispatching a tool call to the registry: on
``act`` the call runs as before; on ``ask`` the call is NOT run
and the loop gets an ``ask`` payload (see :func:`ask_payload`)
telling the model to put the clarifying question to the user.
The ask itself goes through the existing G4 approval mechanism
when an :class:`~puspa.policy.approvals.ApprovalPolicy` is wired
in — this module builds no second approval system.

An advisor only gates tools it has an :class:`ActionSpec` for.
A tool with no spec fails open: the advisor defers to the
existing gates and the call runs exactly as it would without an
advisor. That keeps the wiring additive — wiring an advisor in
cannot newly block a tool nobody described — and the spec is
where the reversibility and stakes of each tool are stated, in
one place, instead of guessed per call. A spec may leave either
value blank (``None``) to derive it from the tool's effect data
instead (see :func:`puspa.intent.act_ask.derive_effect_factors`);
an explicit value is always an override and is never
second-guessed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from puspa.policy.approvals import ApprovalPolicy

from .act_ask import ActAskDecision, clarifying_question, decide_act_or_ask
from .distribution import IntentDistribution
from .inference import DeterministicInferrer, IntentContext, IntentInferrer, infer_intent

__all__ = [
    "ActionSpec",
    "IntentTurnAdvisor",
    "ask_payload",
]


@dataclass(frozen=True)
class ActionSpec:
    """What the advisor needs to know about one tool.

    ``reversible`` is whether the tool's effect can be undone;
    ``stakes`` is ``"low"`` or ``"high"``. Either may be left as
    ``None`` to derive it from the tool's effect data (the
    approvals-taxonomy kind of the tool name); an explicit value
    is an override. ``approval_tool_name`` overrides the tool
    name used when classifying the action for the G4 policy (a
    tool whose registry name hides its verb can be classified
    under its real action name). ``content_keys`` names
    tool-argument keys copied into the policy content, so the
    policy's auto-review sees the amount, recipient, or target
    (for example ``("amount",)`` for a payment tool).
    """

    reversible: bool | None = None
    stakes: str | None = None
    approval_tool_name: str | None = None
    content_keys: tuple[str, ...] = ()


class IntentTurnAdvisor:
    """Advise act or ask for one proposed tool call.

    Built with the profile (a plain ``key -> value`` dict from
    ``puspa.memory.profile.get_profile``), the world model (a
    :class:`~puspa.world.model.WorldModel`, its state dict, or
    None), the action specs, and optionally an approval policy
    and a custom inferrer. ``context_provider`` replaces the
    stored profile/world when given: a ``message -> IntentContext``
    callable, for callers whose profile or world changes between
    turns and want it read fresh each time. ``plan_provider`` is
    the recall-first seam: a ``message -> RecallPlan`` callable
    (see :func:`puspa.memory.recall_plan.recall_plan`) that is
    run inside :meth:`_context`, before intent inference, so a
    cold session recalls pinned items, semantic facts, linked
    episodes, and person models before any intent is read. The
    plan of the last call is kept on ``last_plan`` for the
    caller to inspect. The recalled profile fills in under the
    stored profile: a key the constructor was given wins over a
    recalled one.
    """

    def __init__(
        self,
        *,
        profile: dict[str, str] | None = None,
        world=None,
        inferrer: IntentInferrer | None = None,
        action_specs: dict[str, ActionSpec] | None = None,
        approval_policy: ApprovalPolicy | None = None,
        context_provider=None,
        plan_provider=None,
    ) -> None:
        self._profile = dict(profile) if profile else {}
        self._world = world
        self._inferrer = inferrer if inferrer is not None else DeterministicInferrer()
        self._specs = dict(action_specs) if action_specs else {}
        self._approval_policy = approval_policy
        self._context_provider = context_provider
        self._plan_provider = plan_provider
        self.last_plan = None

    @property
    def action_specs(self) -> dict[str, ActionSpec]:
        """The tools this advisor gates, by tool name."""
        return dict(self._specs)

    def _context(self, message: str) -> IntentContext:
        if self._context_provider is not None:
            return self._context_provider(message)
        if self._plan_provider is not None:
            plan = self._plan_provider(message)
            self.last_plan = plan
            recalled = plan.intent_context(world=self._world).profile
            return IntentContext(profile={**recalled, **self._profile}, world=self._world)
        return IntentContext(profile=dict(self._profile), world=self._world)

    def advise(self, message: str, tool_name: str, arguments: dict | None = None) -> ActAskDecision:
        """Decide act or ask for ``tool_name`` with ``arguments``.

        No spec for the tool -> ``act``, fail-open, with no
        distribution attached: the advisor did not consult intent
        at all, and the existing gates decide. Otherwise the
        distribution is inferred and the decision follows the
        act-vs-ask rule, with the approval policy consulted first
        when one is wired in.
        """
        spec = self._specs.get(tool_name)
        if spec is None:
            return ActAskDecision(
                outcome="act",
                reason=(
                    f"no action spec for tool {tool_name!r}: the intent advisor "
                    "defers to the existing gates"
                ),
                confidence=0.0,
                distribution=None,
            )
        context = self._context(message)
        distribution = infer_intent(message, context, inferrer=self._inferrer)
        content: dict = {}
        for key in spec.content_keys:
            if arguments is not None and key in arguments:
                content[key] = arguments[key]
        return decide_act_or_ask(
            distribution,
            reversible=spec.reversible,
            stakes=spec.stakes,
            approval_policy=self._approval_policy,
            tool_name=spec.approval_tool_name or tool_name,
            content=content or None,
        )

    def distribution_for(self, message: str) -> IntentDistribution:
        """Just the intent reading for ``message``, without a decision.

        For callers that want to show or log the distribution (the
        layered "why?" door) without gating an action on it.
        """
        return infer_intent(message, self._context(message), inferrer=self._inferrer)


def ask_payload(decision: ActAskDecision, *, tool_name: str) -> str:
    """The tool-result text the loop returns when the advisor says ask.

    A JSON object the model can read: the decision, the reason, the
    one clarifying question to put to the user, and the top intent
    reading when one was read. The tool itself was NOT run — the
    payload says so, so the model must not report its effect as done.
    """
    distribution = decision.distribution
    payload: dict = {
        "intent_decision": "ask",
        "tool": tool_name,
        "tool_was_run": False,
        "reason": decision.reason,
        "question": clarifying_question(decision, tool_name=tool_name),
    }
    if distribution is not None and distribution.desire.top is not None:
        payload["top_intent"] = distribution.desire.top.label
        payload["confidence"] = distribution.desire.confidence
    return json.dumps(payload, ensure_ascii=False)
