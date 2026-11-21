"""Computed act-or-ask rule (Kosmos K2): score, derivation, gateway wiring.

No live provider, no network, no database: the score is computed
from plain intent distributions, the effect factors are derived
from the approvals taxonomy, and the gateway wiring is exercised
with a fake computer call. Only modules with a clean import chain
on the scrubbed main are imported here (no turn loop, no conftest
fixtures), so these tests run with --noconftest.
"""

from __future__ import annotations

import pytest

from puspa.intent import (
    IntentDistribution,
    IntentTurnAdvisor,
)
from puspa.intent.act_ask import (
    ACT_THRESHOLD,
    compute_act_score,
    decide_act_or_ask,
    derive_effect_factors,
)
from puspa.intent.distribution import IntentLevel
from puspa.intent.turn_wiring import ActionSpec
from puspa.policy.approvals import ApprovalPolicy, ApprovalRule
from puspa.policy.gateway import ActionContext, Gateway, GatewayDenied


def _level(name, scored):
    return IntentLevel.from_scores(name, scored)


def _distribution(desire_scored, goal_scored=None, values_scored=None):
    return IntentDistribution(
        desire=_level("desire", desire_scored),
        goal=_level("goal", goal_scored or []),
        values=_level("values", values_scored or []),
        message="test",
    )


def _clear_distribution():
    return _distribution([("buy the laptop", 1.0, ("buy", "laptop"), "profile")])


# ---------------------------------------------------------------------------
# The computed score
# ---------------------------------------------------------------------------


def test_clear_reversible_low_stakes_acts_with_full_score():
    decision = decide_act_or_ask(_clear_distribution(), reversible=True, stakes="low")
    assert decision.outcome == "act"
    assert decision.score == pytest.approx(1.0)
    assert decision.factors == {
        "confidence": pytest.approx(1.0),
        "reversibility": 1.0,
        "stakes": 1.0,
    }


def test_score_is_the_confidence_when_all_factors_pass():
    distribution = _distribution(
        [
            ("buy the laptop", 8.0, ("buy", "laptop"), "profile"),
            ("buy a laptop bag", 2.0, ("buy", "laptop"), "profile"),
        ]
    )
    assert not distribution.desire.conflicting
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    assert decision.outcome == "act"
    assert decision.score == pytest.approx(0.8)
    assert decision.factors["confidence"] == pytest.approx(0.8)


def test_threshold_edge_at_the_clear_bar():
    # Top weight exactly at the bar acts; just below it asks.
    at_bar = _distribution(
        [("first reading", 6.0, (), "profile"), ("second reading", 4.0, (), "profile")]
    )
    assert at_bar.desire.top.weight == pytest.approx(ACT_THRESHOLD)
    assert decide_act_or_ask(at_bar, reversible=True, stakes="low").outcome == "act"
    below_bar = _distribution(
        [("first reading", 5.9, (), "profile"), ("second reading", 4.1, (), "profile")]
    )
    assert below_bar.desire.top.weight == pytest.approx(0.59)
    assert decide_act_or_ask(below_bar, reversible=True, stakes="low").outcome == "ask"


def test_low_confidence_asks_naming_unclear():
    distribution = _distribution(
        [
            ("first reading", 5.0, (), "profile"),
            ("second reading", 3.0, (), "profile"),
            ("third reading", 2.0, (), "profile"),
        ]
    )
    assert not distribution.desire.conflicting
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    assert decision.outcome == "ask"
    assert decision.score == pytest.approx(0.5)
    assert "unclear" in decision.reason


def test_irreversible_asks_with_zero_score():
    decision = decide_act_or_ask(_clear_distribution(), reversible=False, stakes="low")
    assert decision.outcome == "ask"
    assert decision.score == pytest.approx(0.0)
    assert decision.factors["reversibility"] == 0.0
    assert "irreversible" in decision.reason


def test_high_stakes_asks_with_zero_score():
    decision = decide_act_or_ask(_clear_distribution(), reversible=True, stakes="high")
    assert decision.outcome == "ask"
    assert decision.score == pytest.approx(0.0)
    assert decision.factors["stakes"] == 0.0
    assert "high" in decision.reason


def test_score_reason_names_every_failing_factor():
    # Low confidence AND irreversible AND high stakes: all three
    # factors fail, and the reason names all three.
    distribution = _distribution(
        [
            ("first reading", 5.0, (), "profile"),
            ("second reading", 3.0, (), "profile"),
            ("third reading", 2.0, (), "profile"),
        ]
    )
    decision = decide_act_or_ask(distribution, reversible=False, stakes="high")
    assert decision.outcome == "ask"
    assert "unclear" in decision.reason
    assert "irreversible" in decision.reason
    assert "high" in decision.reason


def test_compute_act_score_is_a_plain_product():
    score, _factors = compute_act_score(0.8, reversible=True, stakes="low")
    assert score == pytest.approx(0.8)
    score, _ = compute_act_score(0.8, reversible=False, stakes="low")
    assert score == pytest.approx(0.0)
    score, _ = compute_act_score(0.8, reversible=True, stakes="high")
    assert score == pytest.approx(0.0)


def test_unknown_stakes_still_raises():
    with pytest.raises(ValueError, match="stakes must be"):
        decide_act_or_ask(_clear_distribution(), reversible=True, stakes="medium")


def test_no_intent_read_fails_open_with_zero_score():
    distribution = IntentDistribution(message="do it")
    assert distribution.is_empty
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    assert decision.outcome == "act"
    assert decision.score == pytest.approx(0.0)
    assert "failing open" in decision.reason


def test_conflicting_intent_asks():
    distribution = _distribution(
        [
            ("buy the laptop", 1.0, ("buy", "laptop"), "profile"),
            ("buy a laptop bag", 1.0, ("buy", "laptop"), "profile"),
        ]
    )
    assert distribution.desire.conflicting
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    assert decision.outcome == "ask"
    assert "conflicts" in decision.reason


# ---------------------------------------------------------------------------
# Deriving reversibility and stakes from effect data
# ---------------------------------------------------------------------------


def test_derive_effect_factors_known_kinds():
    send = derive_effect_factors("gmail.send")
    assert send.reversible is False
    assert send.stakes == "high"
    delete = derive_effect_factors("delete_file")
    assert delete.reversible is False
    assert delete.stakes == "high"
    pay = derive_effect_factors("pay_bill")
    assert pay.reversible is False
    assert pay.stakes == "high"
    install = derive_effect_factors("install_skill")
    assert install.reversible is True
    assert install.stakes == "high"


def test_derive_effect_factors_unknown_tool_fails_closed():
    derived = derive_effect_factors("search_prices")
    assert derived.reversible is False
    assert derived.stakes == "high"
    assert "failing closed" in derived.note


def test_derived_labels_decide_when_not_given():
    decision = decide_act_or_ask(_clear_distribution(), tool_name="gmail.send")
    assert decision.outcome == "ask"
    assert "irreversible" in decision.reason


def test_explicit_labels_override_derivation():
    # A send tool with explicit reversible + low stakes acts: the
    # caller's labels win over what the taxonomy would derive.
    decision = decide_act_or_ask(
        _clear_distribution(), reversible=True, stakes="low", tool_name="gmail.send"
    )
    assert decision.outcome == "act"
    assert decision.score == pytest.approx(1.0)


def test_none_labels_with_unknown_tool_fail_closed():
    decision = decide_act_or_ask(_clear_distribution(), tool_name="search_prices")
    assert decision.outcome == "ask"


# ---------------------------------------------------------------------------
# The approval policy still wins (G4 wiring preserved)
# ---------------------------------------------------------------------------


def test_policy_deny_wins_over_a_passing_score():
    policy = ApprovalPolicy()
    policy.add_rule(ApprovalRule(kind="pay", owner="admin", mode="members_self_serve"))
    distribution = _distribution([("pay the bill", 1.0, ("pay", "bill"), "profile")])
    decision = decide_act_or_ask(
        distribution,
        reversible=True,
        stakes="low",
        approval_policy=policy,
        tool_name="pay_bill",
        content={"amount": 150000},
    )
    assert decision.outcome == "ask"
    assert decision.approval is not None
    assert decision.approval.outcome == "deny"
    assert "approval policy" in decision.reason


def test_policy_allow_lets_the_computed_score_run():
    policy = ApprovalPolicy()
    policy.record_answer("pay", "always")
    distribution = _distribution([("pay the bill", 1.0, ("pay", "bill"), "profile")])
    decision = decide_act_or_ask(
        distribution,
        reversible=True,
        stakes="low",
        approval_policy=policy,
        tool_name="pay_bill",
        content={"amount": 150000},
    )
    assert decision.outcome == "act"
    assert decision.approval is not None
    assert decision.approval.outcome == "allow"
    assert decision.score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# The advisor: blank spec labels derive, explicit labels stand
# ---------------------------------------------------------------------------

CLEAR_PROFILE = {"preference": "buy the laptop"}


def test_advisor_derives_labels_from_blank_spec():
    advisor = IntentTurnAdvisor(profile=CLEAR_PROFILE, action_specs={"gmail.send": ActionSpec()})
    decision = advisor.advise("buy the laptop", "gmail.send", {})
    assert decision.outcome == "ask"
    assert "irreversible" in decision.reason


def test_advisor_explicit_spec_labels_stand():
    advisor = IntentTurnAdvisor(
        profile=CLEAR_PROFILE,
        action_specs={"gmail.send": ActionSpec(reversible=True, stakes="low")},
    )
    decision = advisor.advise("buy the laptop", "gmail.send", {})
    assert decision.outcome == "act"


def test_advisor_with_no_spec_still_fails_open():
    advisor = IntentTurnAdvisor(profile=CLEAR_PROFILE, action_specs={})
    decision = advisor.advise("buy the laptop", "unknown_tool", {})
    assert decision.outcome == "act"
    assert decision.distribution is None


# ---------------------------------------------------------------------------
# The gateway: the advisor may only escalate to ask (G5 wiring)
# ---------------------------------------------------------------------------

ALLOW_ALL = {"deny": [], "allow": ["true"]}


def _computer_context():
    return ActionContext(
        tool_name="browser.click",
        page_url="https://example.com",
        page_host="example.com",
    )


def _asking_advisor():
    # A blank spec for a browser tool derives irreversible +
    # high-stakes, so the advisor asks on a clear intent.
    return IntentTurnAdvisor(profile=CLEAR_PROFILE, action_specs={"browser.click": ActionSpec()})


def _acting_advisor():
    return IntentTurnAdvisor(
        profile=CLEAR_PROFILE,
        action_specs={"browser.click": ActionSpec(reversible=True, stakes="low")},
    )


def test_gateway_advisor_ask_denies_and_never_calls_the_computer():
    gateway = Gateway(ALLOW_ALL, intent_advisor=_asking_advisor())
    calls: list = []

    def computer(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ok"}

    with pytest.raises(GatewayDenied, match="intent advisor asked"):
        gateway.execute(_computer_context(), computer, message="buy the laptop")
    assert calls == []


def test_gateway_advisor_ask_carries_the_calm_question():
    gateway = Gateway(ALLOW_ALL, intent_advisor=_asking_advisor())
    decision = gateway.decide(_computer_context(), message="buy the laptop")
    assert decision.allowed is False
    assert decision.outcome == "deny"
    assert "Shall I go ahead" in decision.reason


def test_gateway_advisor_act_allows():
    gateway = Gateway(ALLOW_ALL, intent_advisor=_acting_advisor())
    calls: list = []

    def computer(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ok"}

    result = gateway.execute(_computer_context(), computer, message="buy the laptop")
    assert result == {"status": "ok"}
    assert len(calls) == 1


def test_gateway_deny_wins_over_advisor_act():
    calls: list = []

    class _RecordingAdvisor:
        def __init__(self, decision):
            self.decision = decision

        def advise(self, message, tool_name, arguments=None):
            calls.append((message, tool_name, arguments))
            return self.decision

    acting = decide_act_or_ask(_clear_distribution(), reversible=True, stakes="low")
    gateway = Gateway(
        {"deny": ['tool.name == "browser.click"'], "allow": ["true"]},
        intent_advisor=_RecordingAdvisor(acting),
    )
    decision = gateway.decide(_computer_context(), message="buy the laptop")
    assert decision.allowed is False
    assert decision.outcome == "deny"
    assert calls == []  # the advisor is never consulted on a deny


def test_gateway_without_message_skips_the_advisor():
    calls: list = []

    class _RecordingAdvisor:
        def __init__(self, decision):
            self.decision = decision

        def advise(self, message, tool_name, arguments=None):
            calls.append((message, tool_name, arguments))
            return self.decision

    asking = decide_act_or_ask(_clear_distribution(), reversible=False, stakes="low")
    gateway = Gateway(ALLOW_ALL, intent_advisor=_RecordingAdvisor(asking))
    decision = gateway.decide(_computer_context())
    assert decision.allowed is True
    assert calls == []


def test_gateway_without_advisor_behaves_as_before():
    gateway = Gateway(ALLOW_ALL)
    decision = gateway.decide(_computer_context(), message="buy the laptop")
    assert decision.allowed is True
    assert decision.outcome == "allow"


def test_gateway_advisor_ask_in_dry_run_forwards_but_records_deny():
    gateway = Gateway(ALLOW_ALL, dry_run=True, intent_advisor=_asking_advisor())
    decision = gateway.decide(_computer_context(), message="buy the laptop")
    assert decision.allowed is True
    assert decision.outcome == "deny"
    assert decision.forwarded is True
    assert decision.dry_run is True


def test_gateway_advisor_failure_fails_closed():
    class _FailingAdvisor:
        def advise(self, message, tool_name, arguments=None):
            raise RuntimeError("advisor crashed")

    gateway = Gateway(ALLOW_ALL, intent_advisor=_FailingAdvisor())
    decision = gateway.decide(_computer_context(), message="buy the laptop")
    assert decision.allowed is False
    assert decision.outcome == "deny"
    assert "intent advisor failed" in decision.reason


def test_ask_payload_carries_the_computed_score():
    # The turn loop's ask payload (the "why?" door) keeps working
    # with the computed decision.
    from puspa.intent.turn_wiring import ask_payload

    decision = decide_act_or_ask(_clear_distribution(), reversible=False, stakes="low")
    assert decision.outcome == "ask"
    payload = ask_payload(decision, tool_name="delete_file")
    assert '"tool_was_run": false' in payload
    assert "delete_file" in payload
