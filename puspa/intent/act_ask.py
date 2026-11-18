"""Act-vs-ask from the intent distribution (Kosmos K1 + K2 signal).

This module is the intent SIGNAL feeding the act-vs-ask rule — it
is not a second approval system. The approval rule itself (K2,
folded into the G4 approvals taxonomy and the G5 gateway) already
decides which action kinds always ask; this module only adds what
K2's formula needs from intent: how confident the reading is, and
whether it conflicts. The ask itself always goes through the
existing :class:`puspa.policy.approvals.ApprovalPolicy` — when one
is wired in, the decision it returns is attached to the outcome,
and the caller presents the question through that mechanism.

Decision order in :func:`decide_act_or_ask`:

1. The existing approval policy, when wired in and the action is
   a recognized taxonomy kind: a policy ``ask`` / ``deny`` /
   ``escalate_to_human`` wins over any intent signal (the policy
   is the standing rule; intent never overrides it). A policy
   ``allow`` (a usable tiered answer) lets the intent check run.
   An unrecognized action kind is not a policy matter — the
   intent check decides, and the policy is untouched.
2. No intent read anywhere -> ``act``, fail-open: with no signal
   at all the distribution changes nothing, and the existing
   behaviour (policy, gates) stands.
3. Intent read at goal/values but not at desire -> ``ask``: the
   immediate want is unread, so acting would be a guess.
4. Desire level conflicting (top two too close) -> ``ask``. This
   is checked before low confidence: a conflict is the more
   specific diagnosis, and any conflicting level necessarily has
   a top weight below the clear bar anyway (with weights summing
   to 1, a top-two gap under the conflict margin caps the top
   below 0.575).
5. Desire top weight below :data:`CLEAR_CONFIDENCE` -> ``ask``.
6. Irreversible action, or high stakes -> ``ask`` (the K2 shape:
   confidence x reversibility x stakes, all three must pass).
7. Otherwise -> ``act``: clear, reversible, low-stakes intent.
"""

from __future__ import annotations

from dataclasses import dataclass

from puspa.policy.approvals import (
    ApprovalDecision,
    ApprovalPolicy,
    classify_action,
)

from .distribution import CLEAR_CONFIDENCE, IntentDistribution

__all__ = [
    "ActAskDecision",
    "clarifying_question",
    "decide_act_or_ask",
]

#: Stakes values :func:`decide_act_or_ask` accepts.
STAKES_VALUES = ("low", "high")


@dataclass(frozen=True)
class ActAskDecision:
    """The verdict for one proposed action.

    ``outcome`` is ``"act"`` or ``"ask"``. ``reason`` is one plain
    sentence naming the deciding factor. ``confidence`` is the
    desire level's top weight (0.0 when nothing was read).
    ``approval`` is the G4 policy decision, set only when the ask
    (or the allow that preceded it) came from the wired-in
    approval policy. ``distribution`` is the intent reading the
    verdict was based on, or None when no inference ran (no action
    spec — see :mod:`puspa.intent.turn_wiring`).
    """

    outcome: str
    reason: str
    confidence: float
    approval: ApprovalDecision | None = None
    distribution: IntentDistribution | None = None


def decide_act_or_ask(
    distribution: IntentDistribution,
    *,
    reversible: bool,
    stakes: str,
    approval_policy: ApprovalPolicy | None = None,
    tool_name: str | None = None,
    content: dict | None = None,
    conversation_id: str | None = None,
    from_checkin: bool = False,
) -> ActAskDecision:
    """Decide act or ask for one proposed action from its intent reading.

    ``reversible`` and ``stakes`` describe the proposed action
    (from the action spec — see :mod:`puspa.intent.turn_wiring`).
    ``approval_policy`` + ``tool_name`` wire the existing G4 policy
    in: with both given, a recognized action kind is decided by
    the policy first, and only an ``allow`` reaches the intent
    check below.
    """
    if stakes not in STAKES_VALUES:
        raise ValueError(f"stakes must be one of {STAKES_VALUES}, not {stakes!r}")
    confidence = distribution.confidence
    approval: ApprovalDecision | None = None
    if approval_policy is not None and tool_name is not None:
        classification = classify_action(tool_name)
        if classification.kind is not None:
            approval = approval_policy.decide(
                tool_name=tool_name,
                content=content,
                conversation_id=conversation_id,
                from_checkin=from_checkin,
            )
            if approval.outcome != "allow":
                return ActAskDecision(
                    outcome="ask",
                    reason=f"approval policy: {approval.reason}",
                    confidence=confidence,
                    approval=approval,
                    distribution=distribution,
                )
    if distribution.is_empty:
        return ActAskDecision(
            outcome="act",
            reason=(
                "no intent could be read from the message, profile, or world state; "
                "failing open to the existing behaviour (the approval policy and "
                "gates still apply)"
            ),
            confidence=0.0,
            approval=approval,
            distribution=distribution,
        )
    if distribution.desire.empty:
        return ActAskDecision(
            outcome="ask",
            reason=(
                "the current desire could not be read: longer-term intent is present "
                "but the immediate want is unclear"
            ),
            confidence=0.0,
            approval=approval,
            distribution=distribution,
        )
    top = distribution.desire.top
    assert top is not None  # desire is non-empty here, so top exists
    if distribution.desire.conflicting:
        second = distribution.desire.hypotheses[1]
        return ActAskDecision(
            outcome="ask",
            reason=(
                f"intent conflicts: {top.label!r} and {second.label!r} are too close to separate"
            ),
            confidence=top.weight,
            approval=approval,
            distribution=distribution,
        )
    if top.weight < CLEAR_CONFIDENCE:
        return ActAskDecision(
            outcome="ask",
            reason=(
                f"intent is unclear: the strongest reading ({top.label!r}) "
                f"has weight {top.weight:.2f}, below {CLEAR_CONFIDENCE}"
            ),
            confidence=top.weight,
            approval=approval,
            distribution=distribution,
        )
    if not reversible:
        return ActAskDecision(
            outcome="ask",
            reason=f"the action is irreversible, so the intent ({top.label!r}) needs confirming",
            confidence=top.weight,
            approval=approval,
            distribution=distribution,
        )
    if stakes == "high":
        return ActAskDecision(
            outcome="ask",
            reason=f"the stakes are high, so the intent ({top.label!r}) needs confirming",
            confidence=top.weight,
            approval=approval,
            distribution=distribution,
        )
    return ActAskDecision(
        outcome="act",
        reason=(f"clear intent ({top.label!r}, weight {top.weight:.2f}), reversible, low stakes"),
        confidence=top.weight,
        approval=approval,
        distribution=distribution,
    )


def clarifying_question(decision: ActAskDecision, *, tool_name: str | None = None) -> str:
    """One calm question for an ``ask`` decision.

    The question names what was read (or that nothing was read),
    so the user answers one thing instead of re-stating the whole
    request. An ``act`` decision has no question to ask; asking
    anyway would be a bug in the caller, so this raises
    ``ValueError`` for it.
    """
    if decision.outcome != "ask":
        raise ValueError("clarifying_question is only for an 'ask' decision")
    distribution = decision.distribution
    action = f" before I {tool_name}" if tool_name else ""
    if distribution is None or distribution.is_empty:
        return f"Could you say a little more about what you want{action}?"
    desire = distribution.desire
    if desire.empty:
        goal_top = distribution.goal.top
        values_top = distribution.values.top
        known = (
            goal_top.label
            if goal_top is not None
            else (values_top.label if values_top is not None else None)
        )
        if known is not None:
            return (
                f"I can see this is about {known}, but I am not sure what you "
                f"want right now{action} — what should I do?"
            )
        return f"What would you like me to do{action}?"
    if desire.conflicting:
        first, second = desire.hypotheses[0], desire.hypotheses[1]
        return f"Did you mean {first.label}, or {second.label}{action}?"
    top = desire.top
    assert top is not None  # desire is non-empty here, so top exists
    if "unclear" in decision.reason or "below" in decision.reason:
        return f"Did you mean {top.label}{action}?"
    return f"Shall I go ahead with {top.label}{action}?"
