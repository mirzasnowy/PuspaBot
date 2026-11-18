"""Intent Engine tests (K1): distribution properties + deterministic inference.

No live provider, no network, no database: the inferrer reads
plain profile dicts and an in-memory WorldModel, so these tests
never touch Postgres.
"""

from __future__ import annotations

import pytest

from puspa.intent import (
    IntentContext,
    IntentDistribution,
    IntentLevel,
    build_llm_prompt,
    infer_intent,
)
from puspa.world.model import WorldModel


def _level(name, scored):
    return IntentLevel.from_scores(name, scored)


# ---------------------------------------------------------------------------
# Distribution properties
# ---------------------------------------------------------------------------


def test_weights_sum_to_one_per_level():
    level = _level(
        "desire", [("rest", 3.0, ("tired",), "message"), ("eat", 1.0, ("hungry",), "message")]
    )
    assert level.total_weight == pytest.approx(1.0)
    assert level.top.label == "rest"
    assert level.top.weight == pytest.approx(0.75)
    assert level.hypotheses[1].weight == pytest.approx(0.25)


def test_single_hypothesis_takes_the_whole_level():
    level = _level("goal", [("sleep well", 0.5, ("sleep",), "profile")])
    assert level.total_weight == pytest.approx(1.0)
    assert level.confidence == pytest.approx(1.0)


def test_empty_level_is_honest_not_invented():
    level = IntentLevel("values")
    assert level.empty
    assert level.top is None
    assert level.confidence == 0.0
    assert level.total_weight == 0.0
    assert level.conflicting is False
    assert level.to_dict()["empty"] is True
    assert level.to_dict()["hypotheses"] == []


def test_zero_scores_build_an_empty_level():
    level = _level("desire", [("rest", 0.0, (), "message")])
    assert level.empty


def test_unnormalized_direct_construction_raises():
    from puspa.intent import IntentHypothesis

    with pytest.raises(ValueError, match="sum to"):
        IntentLevel("desire", (IntentHypothesis("rest", 0.5), IntentHypothesis("eat", 0.2)))


def test_unknown_level_raises():
    with pytest.raises(ValueError, match="unknown intent level"):
        IntentLevel("mood")


def test_conflict_is_top_two_within_margin():
    close = _level(
        "desire",
        [
            ("buy the laptop", 1.0, ("buy", "laptop"), "profile"),
            ("buy a laptop bag", 1.0, ("buy", "laptop"), "profile"),
        ],
    )
    assert close.conflicting
    assert close.hypotheses[0].weight == pytest.approx(0.5)
    far = _level(
        "desire", [("rest", 3.0, ("tired",), "message"), ("eat", 1.0, ("hungry",), "message")]
    )
    assert not far.conflicting
    single = _level("desire", [("rest", 1.0, ("tired",), "message")])
    assert not single.conflicting


def test_distribution_reports_empty_and_conflicting():
    empty = IntentDistribution(message="hello")
    assert empty.is_empty
    assert empty.confidence == 0.0
    assert not empty.conflicting
    assert empty.to_dict()["empty"] is True
    full = IntentDistribution(
        desire=_level("desire", [("rest", 1.0, ("tired",), "message")]),
        goal=_level("goal", [("sleep well", 1.0, ("sleep",), "profile")]),
        values=_level("values", [("health", 1.0, ("health",), "profile")]),
        message="I'm so tired",
    )
    assert not full.is_empty
    assert full.confidence == pytest.approx(1.0)
    assert full.level("goal").top.label == "sleep well"
    with pytest.raises(ValueError, match="unknown intent level"):
        full.level("mood")


# ---------------------------------------------------------------------------
# Deterministic inference
# ---------------------------------------------------------------------------


def test_doc_example_one_message_read_at_all_three_levels():
    """ "I'm so tired", read at all three levels at once (kosmos-design B)."""
    context = IntentContext(
        profile={"goal": "sleep well", "value": "health"},
    )
    distribution = infer_intent("I'm so tired", context)
    assert distribution.desire.top.label == "rest"
    assert distribution.desire.top.source == "message"
    assert distribution.goal.top.label == "sleep well"
    assert distribution.goal.top.source == "profile"
    assert distribution.values.top.label == "health"
    assert distribution.values.top.source == "profile"
    assert distribution.values.top.evidence == ("health",)
    assert not distribution.is_empty


def test_empty_context_and_plain_message_reads_nothing():
    distribution = infer_intent("hello there, how are you")
    assert distribution.is_empty
    assert distribution.desire.empty
    assert distribution.goal.empty
    assert distribution.values.empty


def test_identity_profile_facts_are_not_intent():
    context = IntentContext(profile={"name": "Nero", "address": "Jakarta"})
    distribution = infer_intent("buy the laptop", context)
    # Only the message's own "buy" signal reads; the name/address
    # never become hypotheses.
    assert distribution.desire.top.label == "buy"
    assert distribution.goal.empty
    assert distribution.values.empty
    labels = [h.label for h in distribution.desire.hypotheses]
    assert "Nero" not in labels
    assert "Jakarta" not in labels


def test_inference_is_deterministic():
    context = IntentContext(profile={"goal": "save money for a house", "value": "frugality"})
    first = infer_intent("I want to save money this month", context)
    second = infer_intent("I want to save money this month", context)
    assert first.to_dict() == second.to_dict()


def test_stronger_overlap_ranks_first_and_weights_sum_to_one():
    context = IntentContext(
        profile={
            "goal": "save money for a house",
            "goal_2": "save money",
        }
    )
    distribution = infer_intent("I want to save money", context)
    goal = distribution.goal
    assert goal.top.label == "save money"
    assert goal.top.weight > goal.hypotheses[1].weight
    assert goal.total_weight == pytest.approx(1.0)


def test_inferred_profile_facts_never_reach_inference():
    # Only stated/confirmed facts live in a profile dict from
    # get_profile; this test pins the inference side: a key that
    # does not name an intent contributes nothing even when its
    # value overlaps the message.
    context = IntentContext(profile={"hobby": "buying laptops"})
    distribution = infer_intent("buy the laptop", context)
    labels = [h.label for h in distribution.desire.hypotheses]
    assert "buying laptops" not in labels


def test_world_bill_belief_reads_as_a_desire():
    model = WorldModel()
    model.ingest_graph(
        [{"id": "pln", "kind": "bill", "attributes": {"amount": 150000, "due_day": 20}}]
    )
    distribution = infer_intent("pay my PLN bill", IntentContext(world=model))
    assert distribution.desire.top.label == "pay bill pln"
    assert distribution.desire.top.source == "world"
    assert "pln" in distribution.desire.top.evidence


def test_world_bill_belief_accepts_a_state_dict_too():
    model = WorldModel()
    model.ingest_graph(
        [{"id": "pln", "kind": "bill", "attributes": {"amount": 150000, "due_day": 20}}]
    )
    distribution = infer_intent("pay my PLN bill", IntentContext(world=model.state))
    assert distribution.desire.top.label == "pay bill pln"


def test_paid_bill_is_not_a_desire():
    model = WorldModel()
    model.ingest_graph(
        [{"id": "pln", "kind": "bill", "attributes": {"amount": 150000, "due_day": 20}}]
    )
    model.update(
        {
            "action": {"type": "pay_bill", "bill_id": "pln"},
            "observed": {"success": True, "status": "paid"},
        }
    )
    distribution = infer_intent("pay my PLN bill", IntentContext(world=model))
    labels = [h.label for h in distribution.desire.hypotheses]
    assert "pay bill pln" not in labels


def test_world_habit_belief_reads_as_a_goal():
    model = WorldModel()
    for day in (1, 2, 3):
        model.ingest_task_result(
            {
                "task": "check_price",
                "success": True,
                "result": {"day": day, "hour": 9, "weekday": 1},
            }
        )
    distribution = infer_intent("check prices as usual", IntentContext(world=model))
    assert distribution.goal.top.label == "keep up check price"
    assert distribution.goal.top.source == "world"


def test_world_price_belief_reads_as_a_desire():
    model = WorldModel()
    model.ingest_graph(
        [
            {
                "id": "laptop-x",
                "kind": "product",
                "attributes": {"price": 12000000, "store": "tokopedia"},
            }
        ]
    )
    distribution = infer_intent("what is the laptop price on tokopedia", IntentContext(world=model))
    assert distribution.desire.top.label == "check price of laptop-x at tokopedia"
    assert distribution.desire.top.source == "world"


def test_message_with_no_signal_and_no_context_stays_empty():
    distribution = infer_intent("do it", IntentContext(profile={}, world=None))
    assert distribution.is_empty


def test_inferrer_is_pluggable():
    class ShoutingInferrer:
        def infer(self, message, context):
            return IntentDistribution(
                desire=_level("desire", [("shout", 1.0, (), "message")]),
                message=message,
                source="shouting-test-double",
            )

    distribution = infer_intent("hello", IntentContext(), inferrer=ShoutingInferrer())
    assert distribution.source == "shouting-test-double"
    assert distribution.desire.top.label == "shout"


def test_default_inferrer_is_the_deterministic_one():
    distribution = infer_intent("I'm so tired", IntentContext())
    assert distribution.source == "deterministic"
    assert distribution.desire.top.label == "rest"


def test_non_string_message_raises():
    with pytest.raises(ValueError, match="message must be a string"):
        infer_intent(42, IntentContext())


def test_llm_prompt_states_the_json_contract_and_never_invents():
    context = IntentContext(
        profile={"goal": "save money for a house"},
        world=None,
    )
    prompt = build_llm_prompt("I want to save money", context)
    assert "I want to save money" in prompt
    assert "save money for a house" in prompt
    assert "never invent a hypothesis" in prompt
    assert "sum to 1" in prompt
    assert '"desire"' in prompt and '"goal"' in prompt and '"values"' in prompt
