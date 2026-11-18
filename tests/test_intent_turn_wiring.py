"""Intent Engine tests (K1): act-vs-ask wiring + turn-loop run tests.

No live provider, no network, no database: decisions are made
from plain profile dicts and an in-memory WorldModel, and the
turn loop runs with injected fake model calls.
"""

from __future__ import annotations

import json

import pytest

from puspa.intent import (
    ActionSpec,
    IntentTurnAdvisor,
    ask_payload,
    clarifying_question,
    decide_act_or_ask,
)
from puspa.intent.distribution import IntentLevel
from puspa.policy.approvals import ApprovalPolicy, ApprovalRule
from puspa.turns.config import TurnConfig
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply


def _level(name, scored):
    return IntentLevel.from_scores(name, scored)


def _distribution(desire_scored, goal_scored=None, values_scored=None):
    from puspa.intent import IntentDistribution

    return IntentDistribution(
        desire=_level("desire", desire_scored),
        goal=_level("goal", goal_scored or []),
        values=_level("values", values_scored or []),
        message="test",
    )


# ---------------------------------------------------------------------------
# decide_act_or_ask
# ---------------------------------------------------------------------------


def test_clear_reversible_low_stakes_intent_acts():
    distribution = _distribution([("buy the laptop", 1.0, ("buy", "laptop"), "profile")])
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    assert decision.outcome == "act"
    assert decision.confidence == pytest.approx(1.0)
    assert decision.approval is None


def test_low_confidence_intent_asks():
    # Weights 0.5 / 0.3 / 0.2: top below the clear bar, top-two gap
    # 0.2 so the level is not conflicting — this is low confidence,
    # not a conflict.
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
    assert "unclear" in decision.reason


def test_conflicting_intent_asks_even_when_top_is_strong():
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


def test_irreversible_action_asks_despite_clear_intent():
    distribution = _distribution([("buy the laptop", 1.0, ("buy", "laptop"), "profile")])
    decision = decide_act_or_ask(distribution, reversible=False, stakes="low")
    assert decision.outcome == "ask"
    assert "irreversible" in decision.reason


def test_high_stakes_action_asks_despite_clear_intent():
    distribution = _distribution([("buy the laptop", 1.0, ("buy", "laptop"), "profile")])
    decision = decide_act_or_ask(distribution, reversible=True, stakes="high")
    assert decision.outcome == "ask"
    assert "high" in decision.reason


def test_unknown_stakes_raises():
    distribution = _distribution([("buy the laptop", 1.0, ("buy", "laptop"), "profile")])
    with pytest.raises(ValueError, match="stakes must be"):
        decide_act_or_ask(distribution, reversible=True, stakes="medium")


def test_no_intent_read_anywhere_fails_open_to_act():
    from puspa.intent import IntentDistribution

    distribution = IntentDistribution(message="do it")
    assert distribution.is_empty
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    assert decision.outcome == "act"
    assert "failing open" in decision.reason


def test_goal_and_values_without_a_desire_asks():
    distribution = _distribution(
        [],
        goal_scored=[("sleep well", 1.0, ("sleep",), "profile")],
        values_scored=[("health", 1.0, ("health",), "profile")],
    )
    assert distribution.desire.empty
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    assert decision.outcome == "ask"
    assert "desire could not be read" in decision.reason


def test_policy_deny_wins_over_clear_intent():
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


def test_policy_allow_lets_the_intent_check_run():
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


def test_unrecognized_tool_kind_skips_the_policy():
    policy = ApprovalPolicy()
    distribution = _distribution([("buy the laptop", 1.0, ("buy", "laptop"), "profile")])
    decision = decide_act_or_ask(
        distribution,
        reversible=True,
        stakes="low",
        approval_policy=policy,
        tool_name="search_prices",
    )
    assert decision.outcome == "act"
    assert decision.approval is None


def test_clarifying_question_names_the_two_conflicting_readings():
    distribution = _distribution(
        [
            ("buy the laptop", 1.0, ("buy", "laptop"), "profile"),
            ("buy a laptop bag", 1.0, ("buy", "laptop"), "profile"),
        ]
    )
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    question = clarifying_question(decision, tool_name="search_prices")
    assert "buy a laptop bag" in question
    assert "buy the laptop" in question


def test_clarifying_question_for_low_confidence_names_the_reading():
    distribution = _distribution(
        [
            ("first reading", 5.0, (), "profile"),
            ("second reading", 3.0, (), "profile"),
            ("third reading", 2.0, (), "profile"),
        ]
    )
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    question = clarifying_question(decision)
    assert "Did you mean" in question
    assert "first reading" in question


def test_clarifying_question_for_an_act_decision_raises():
    distribution = _distribution([("buy the laptop", 1.0, ("buy", "laptop"), "profile")])
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    with pytest.raises(ValueError, match="only for an 'ask' decision"):
        clarifying_question(decision)


def test_ask_payload_says_the_tool_was_not_run():
    distribution = _distribution(
        [
            ("buy the laptop", 1.0, ("buy", "laptop"), "profile"),
            ("buy a laptop bag", 1.0, ("buy", "laptop"), "profile"),
        ]
    )
    decision = decide_act_or_ask(distribution, reversible=True, stakes="low")
    payload = json.loads(ask_payload(decision, tool_name="search_prices"))
    assert payload["intent_decision"] == "ask"
    assert payload["tool_was_run"] is False
    assert payload["tool"] == "search_prices"
    assert payload.get("question")
    assert "top_intent" in payload


# ---------------------------------------------------------------------------
# IntentTurnAdvisor
# ---------------------------------------------------------------------------

SPECS = {"search_prices": ActionSpec(reversible=True, stakes="low")}


def test_same_message_different_profile_different_outcome():
    """The run test: one message, two profiles, act vs ask.

    The two preferences share the same keyword set ("buy the
    laptop" vs "buy a laptop"), so the same message matches both
    equally: one recorded preference reads clearly, two read as
    a conflict.
    """
    message = "buy the laptop"
    clear_profile = {"preference": "buy the laptop"}
    conflicted_profile = {
        "preference": "buy the laptop",
        "preference_2": "buy a laptop",
    }
    clear = IntentTurnAdvisor(profile=clear_profile, action_specs=SPECS)
    conflicted = IntentTurnAdvisor(profile=conflicted_profile, action_specs=SPECS)
    clear_decision = clear.advise(message, "search_prices", {"query": "laptop"})
    conflicted_decision = conflicted.advise(message, "search_prices", {"query": "laptop"})
    assert clear_decision.outcome == "act"
    assert clear_decision.distribution.desire.top.label == "buy the laptop"
    assert conflicted_decision.outcome == "ask"
    assert conflicted_decision.distribution.desire.conflicting


def test_same_message_different_world_different_outcome():
    """The run test, world edition: one unpaid bill reads clearly,
    two unpaid bills read as a conflict ("which bill?")."""
    from puspa.world.model import WorldModel

    specs = {"check_bill": ActionSpec(reversible=True, stakes="low")}

    def world_with_bills(*bill_ids):
        model = WorldModel()
        model.ingest_graph(
            [
                {
                    "id": bill_id,
                    "kind": "bill",
                    "attributes": {"amount": 150000, "due_day": 20},
                }
                for bill_id in bill_ids
            ]
        )
        return model

    one_bill = IntentTurnAdvisor(world=world_with_bills("pln"), action_specs=specs)
    two_bill = IntentTurnAdvisor(world=world_with_bills("pln", "pdam"), action_specs=specs)
    one = one_bill.advise("pay the bill", "check_bill", {"bill_id": "pln"})
    two = two_bill.advise("pay the bill", "check_bill", {"bill_id": "pln"})
    assert one.outcome == "act"
    assert one.distribution.desire.top.label == "pay bill pln"
    assert two.outcome == "ask"
    assert two.distribution.desire.conflicting
    assert "conflicts" in two.reason


def test_advisor_with_no_spec_fails_open():
    advisor = IntentTurnAdvisor(profile={"preference": "buy the laptop"}, action_specs={})
    decision = advisor.advise("buy the laptop", "unknown_tool", {})
    assert decision.outcome == "act"
    assert decision.distribution is None
    assert "defers to the existing gates" in decision.reason


def test_advisor_with_no_intent_read_fails_open_to_act():
    advisor = IntentTurnAdvisor(profile={}, world=None, action_specs=SPECS)
    decision = advisor.advise("do it", "search_prices", {"query": "x"})
    assert decision.outcome == "act"
    assert decision.distribution is not None
    assert decision.distribution.is_empty


def test_advisor_distribution_for_shows_the_reading_without_deciding():
    advisor = IntentTurnAdvisor(profile={"goal": "sleep well"}, action_specs=SPECS)
    distribution = advisor.distribution_for("I'm so tired")
    assert distribution.desire.top.label == "rest"
    assert distribution.goal.top.label == "sleep well"


# ---------------------------------------------------------------------------
# TurnLoop wiring: real runs with a fake model
# ---------------------------------------------------------------------------


def _tool_reply(name, arguments=None):
    return ModelReply(
        text="",
        tool_calls=[{"id": "call-1", "name": name, "arguments": arguments or {}}],
        input_tokens=10,
        output_tokens=5,
    )


def _text_reply(text):
    return ModelReply(text=text, input_tokens=10, output_tokens=5)


def _run_loop_with_advisor(advisor, message="buy the laptop"):
    calls = {"count": 0, "registry": []}

    def model_call(entry, wire_messages, tools):
        calls["count"] += 1
        if calls["count"] == 1:
            return _tool_reply("search_prices", {"query": "laptop"})
        return _text_reply("Here is what I found.")

    def registry(name, arguments):
        calls["registry"].append((name, arguments))
        return {"results": ["laptop-x at tokopedia: Rp12.000.000"]}

    loop = TurnLoop(
        model_call=model_call,
        tool_call=registry,
        config=TurnConfig(max_iterations=10),
        intent_advisor=advisor,
    )
    result = loop.run_turn([InternalMessage.user(message)])
    return result, calls


def test_loop_acts_when_intent_is_clear_and_runs_the_tool():
    advisor = IntentTurnAdvisor(profile={"preference": "buy the laptop"}, action_specs=SPECS)
    result, calls = _run_loop_with_advisor(advisor)
    assert result.status == "completed"
    assert calls["registry"] == [("search_prices", {"query": "laptop"})]
    assert len(result.intent_decisions) == 1
    assert result.intent_decisions[0].outcome == "act"
    assert result.text == "Here is what I found."


def test_loop_asks_when_intent_conflicts_and_does_not_run_the_tool():
    advisor = IntentTurnAdvisor(
        profile={"preference": "buy the laptop", "preference_2": "buy a laptop"},
        action_specs=SPECS,
    )
    result, calls = _run_loop_with_advisor(advisor)
    assert result.status == "completed"
    assert calls["registry"] == [], "a conflicting intent must not run the tool"
    assert len(result.intent_decisions) == 1
    assert result.intent_decisions[0].outcome == "ask"
    # The model got the ask payload as the tool result, not a real result.
    tool_results = [
        block.get("content", "")
        for message in result.messages
        for block in message.blocks
        if block.get("type") == "tool_result"
    ]
    assert len(tool_results) == 1
    payload = json.loads(tool_results[0])
    assert payload["intent_decision"] == "ask"
    assert payload["tool_was_run"] is False
    assert "buy a laptop" in payload["question"]
    assert "buy the laptop" in payload["question"]
    assert result.text == "Here is what I found."


def test_loop_ask_event_is_recorded_on_the_loop_sink():
    advisor = IntentTurnAdvisor(
        profile={"preference": "buy the laptop", "preference_2": "buy a laptop"},
        action_specs=SPECS,
    )
    calls = {"count": 0}

    def model_call(entry, wire_messages, tools):
        calls["count"] += 1
        if calls["count"] == 1:
            return _tool_reply("search_prices", {"query": "laptop"})
        return _text_reply("Which one did you mean?")

    loop = TurnLoop(
        model_call=model_call,
        tool_call=lambda name, arguments: {"results": []},
        config=TurnConfig(max_iterations=10),
        intent_advisor=advisor,
    )
    result = loop.run_turn([InternalMessage.user("buy the laptop")])
    assert result.status == "completed"
    events = loop.event_memory()
    ask_events = [event for event in events if event["type"] == "intent_ask"]
    assert len(ask_events) == 1
    assert ask_events[0]["content"]["tool"] == "search_prices"
    assert "conflicts" in ask_events[0]["content"]["reason"]


def test_loop_without_an_advisor_behaves_exactly_as_before():
    calls = {"registry": []}

    def model_call(entry, wire_messages, tools):
        return (
            _tool_reply("search_prices", {"query": "laptop"})
            if len(calls["registry"]) == 0
            else _text_reply("done")
        )

    loop = TurnLoop(
        model_call=model_call,
        tool_call=lambda name, arguments: calls["registry"].append(name) or {"ok": True},
        config=TurnConfig(max_iterations=10),
    )
    result = loop.run_turn([InternalMessage.user("buy the laptop")])
    assert result.status == "completed"
    assert calls["registry"] == ["search_prices"]
    assert result.intent_decisions == []


def test_loop_fails_open_when_no_intent_can_be_read():
    advisor = IntentTurnAdvisor(profile={}, world=None, action_specs=SPECS)
    result, calls = _run_loop_with_advisor(advisor, message="do it")
    assert result.status == "completed"
    assert calls["registry"] == [("search_prices", {"query": "laptop"})]
    assert result.intent_decisions[0].outcome == "act"
    assert "failing open" in result.intent_decisions[0].reason


def test_loop_fails_open_when_the_advisor_raises():
    class BrokenAdvisor:
        def advise(self, message, tool_name, arguments=None):
            raise RuntimeError("advisor is broken")

    calls = {"registry": []}

    def model_call(entry, wire_messages, tools):
        return (
            _tool_reply("search_prices", {"query": "laptop"})
            if not calls["registry"]
            else _text_reply("done")
        )

    loop = TurnLoop(
        model_call=model_call,
        tool_call=lambda name, arguments: calls["registry"].append(name) or {"ok": True},
        config=TurnConfig(max_iterations=10),
        intent_advisor=BrokenAdvisor(),
    )
    result = loop.run_turn([InternalMessage.user("buy the laptop")])
    assert result.status == "completed"
    assert calls["registry"] == ["search_prices"]
    assert result.intent_decisions == []


def test_loop_never_advises_intercepted_tools():
    advisor = IntentTurnAdvisor(
        profile={"preference": "buy the laptop", "preference_2": "buy a laptop"},
        action_specs={"todo": ActionSpec(reversible=True, stakes="low")},
    )

    def model_call(entry, wire_messages, tools):
        return _tool_reply("todo", {"action": "list"})

    loop = TurnLoop(
        model_call=model_call,
        config=TurnConfig(max_iterations=3),
        intent_advisor=advisor,
    )
    result = loop.run_turn([InternalMessage.user("buy the laptop")])
    # The intercepted todo ran (the loop keeps looping on tool
    # results until the budget ends); the advisor was never asked,
    # so no intent decision was recorded.
    assert result.intent_decisions == []
