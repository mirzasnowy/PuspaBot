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
   is checked before the score: a conflict is the more specific
   diagnosis, and any conflicting level necessarily has a top
   weight below the act bar anyway (with weights summing to 1, a
   top-two gap under the conflict margin caps the top below
   0.575).
5. Otherwise the decision is COMPUTED: ``score = confidence x
   reversibility x stakes`` (:func:`compute_act_score`), and the
   action runs only when ``score >= ACT_THRESHOLD`` (the clear
   bar, 0.6). A score below the bar asks, and the reason names
   the factor that failed it, so the "why?" door always has an
   answer. Reversibility and stakes come from the caller when
   given, and are otherwise derived from the action's effect
   data (:func:`derive_effect_factors`): the approvals-taxonomy
   kind of the tool name.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from puspa.policy.approvals import (
    ApprovalDecision,
    ApprovalPolicy,
    classify_action,
)

from .distribution import CLEAR_CONFIDENCE, IntentDistribution

__all__ = [
    "ACT_THRESHOLD",
    "ActAskDecision",
    "EffectFactors",
    "clarifying_question",
    "compute_act_score",
    "decide_act_or_ask",
    "derive_effect_factors",
]

#: Stakes values :func:`decide_act_or_ask` accepts.
STAKES_VALUES = ("low", "high")

#: The score at or above which an action runs: the same bar as a
#: clear intent reading (:data:`CLEAR_CONFIDENCE`). A computed
#: score below it asks.
ACT_THRESHOLD = CLEAR_CONFIDENCE

#: Action kinds whose effects cannot be undone once they leave
#: the agent (the approvals taxonomy's always-ask kinds). An
#: action of one of these kinds is irreversible and high-stakes.
_IRREVERSIBLE_KINDS = frozenset(
    {"delete", "send_to_other", "share", "post", "pay", "change_credentials"}
)


@dataclass(frozen=True)
class EffectFactors:
    """Reversibility and stakes derived from an action's effect data.

    ``reversible`` is whether the effect can be undone;
    ``stakes`` is ``"low"`` or ``"high"``. ``note`` is one plain
    sentence saying where the values came from, so a derived
    decision can be explained instead of asserted.
    """

    reversible: bool
    stakes: str
    note: str


def derive_effect_factors(tool_name: str | None, content: dict | None = None) -> EffectFactors:
    """Derive reversibility and stakes from the action's effect data.

    The derivation reads the approvals-taxonomy kind of the tool
    name (:func:`puspa.policy.approvals.classify_action`): kinds
    whose effects leave the agent's control (delete, send, share,
    post, pay, credential change) are irreversible and
    high-stakes; a skill install is reversible but always asks, so
    it is high-stakes too. An unrecognized action fails closed to
    asking — nothing is derived as safe from a name the taxonomy
    does not know. ``content`` (the tool arguments) is accepted
    for future derivation inputs (amount, scope); today the kind
    alone decides, because an amount without a money-moving kind
    proves nothing (a price check carries an amount and moves no
    money), and the taxonomy's payment recognition already covers
    money-moving names.
    """
    classification = classify_action(tool_name or "")
    kind = classification.kind
    if kind in _IRREVERSIBLE_KINDS:
        return EffectFactors(
            reversible=False,
            stakes="high",
            note=f"action kind {kind!r} is irreversible and high-stakes",
        )
    if kind == "install":
        return EffectFactors(
            reversible=True,
            stakes="high",
            note="skill install always asks",
        )
    return EffectFactors(
        reversible=False,
        stakes="high",
        note="unrecognized action: failing closed to asking",
    )


def compute_act_score(
    confidence: float, *, reversible: bool, stakes: str
) -> tuple[float, dict[str, float]]:
    """The computed act score: confidence x reversibility x stakes.

    Each factor is 0..1: confidence is the desire level's top
    weight, reversibility is 1 for a reversible action and 0 for
    an irreversible one, stakes is 1 for low stakes and 0 for
    high stakes. The product is 0 whenever any factor fails, and
    equals the confidence when all three pass. Returns the score
    and the three factors, so the decision can show its working.
    """
    factors = {
        "confidence": confidence,
        "reversibility": 1.0 if reversible else 0.0,
        "stakes": 1.0 if stakes == "low" else 0.0,
    }
    score = factors["confidence"] * factors["reversibility"] * factors["stakes"]
    return score, factors


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
    spec — see :mod:`puspa.intent.turn_wiring`). ``score`` is the
    computed act score (confidence x reversibility x stakes), and
    ``factors`` is the three factors it was computed from, so the
    "why?" door can show the working instead of asserting the
    verdict. A decision that short-circuited before scoring (the
    approval policy, no intent read, an unread desire, a
    conflict) still carries the score and factors that were
    computed from what was known — the score says what the
    numbers said, the reason says why the verdict differs. A
    decision built without consulting intent at all (no action
    spec) keeps the defaults (score 0.0, no factors): nothing was
    computed for it.
    """

    outcome: str
    reason: str
    confidence: float
    approval: ApprovalDecision | None = None
    distribution: IntentDistribution | None = None
    score: float = 0.0
    factors: dict[str, float] = field(default_factory=dict)


def decide_act_or_ask(
    distribution: IntentDistribution,
    *,
    reversible: bool | None = None,
    stakes: str | None = None,
    approval_policy: ApprovalPolicy | None = None,
    tool_name: str | None = None,
    content: dict | None = None,
    conversation_id: str | None = None,
    from_checkin: bool = False,
) -> ActAskDecision:
    """Decide act or ask for one proposed action from its intent reading.

    ``reversible`` and ``stakes`` describe the proposed action.
    Either may be left as ``None`` to derive it from the action's
    effect data (:func:`derive_effect_factors`); an explicit value
    is always an override and is never second-guessed. ``approval_policy``
    + ``tool_name`` wire the existing G4 policy in: with both given,
    a recognized action kind is decided by the policy first, and
    only an ``allow`` reaches the intent check below.
    """
    if stakes is not None and stakes not in STAKES_VALUES:
        raise ValueError(f"stakes must be one of {STAKES_VALUES}, not {stakes!r}")
    if reversible is None or stakes is None:
        derived = derive_effect_factors(tool_name, content)
        if reversible is None:
            reversible = derived.reversible
        if stakes is None:
            stakes = derived.stakes
    confidence = distribution.confidence
    score, factors = compute_act_score(confidence, reversible=reversible, stakes=stakes)
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
                    score=score,
                    factors=factors,
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
            score=score,
            factors=factors,
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
            score=score,
            factors=factors,
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
            score=score,
            factors=factors,
        )
    if score >= ACT_THRESHOLD:
        return ActAskDecision(
            outcome="act",
            reason=(
                f"clear intent ({top.label!r}, weight {top.weight:.2f}), reversible, "
                f"low stakes (score {score:.2f})"
            ),
            confidence=top.weight,
            approval=approval,
            distribution=distribution,
            score=score,
            factors=factors,
        )
    failing: list[str] = []
    if factors["confidence"] < ACT_THRESHOLD:
        failing.append(
            f"intent is unclear: the strongest reading ({top.label!r}) "
            f"has weight {top.weight:.2f}, below {ACT_THRESHOLD}"
        )
    if not reversible:
        failing.append(
            f"the action is irreversible, so the intent ({top.label!r}) needs confirming"
        )
    if stakes != "low":
        failing.append(f"the stakes are high, so the intent ({top.label!r}) needs confirming")
    return ActAskDecision(
        outcome="ask",
        reason=f"score {score:.2f} is under the {ACT_THRESHOLD:.2f} bar: " + "; ".join(failing),
        confidence=top.weight,
        approval=approval,
        distribution=distribution,
        score=score,
        factors=factors,
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
