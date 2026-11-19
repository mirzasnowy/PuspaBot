"""Tests for other-agent goal models (Kosmos K5).

All in-memory tests are deterministic: no database, no network,
no randomness, no LLM call. A goal model is built only from the
user's own observed interactions with a contact: goals they asked
for or agreed to, replies, delays, channels. Predictions are
scored claims with evidence, never certainties, and confidence
falls with sparse or stale evidence. The Postgres round-trip
tests live in `tests/test_agent_store.py`.
"""

import pytest

from puspa.world import WorldModel, plan
from puspa.world.agents import (
    AgentGoalModel,
    AgentGoalStore,
    KeywordGoalInferrer,
    check_user_involved,
)
from puspa.world.causal import CausalLedger


def _interaction(
    contact_id,
    *,
    day=0,
    replied=True,
    channel="whatsapp",
    delay=None,
    asked_for=None,
    agreed_to=None,
    declined=None,
    text=None,
    user_involved=True,
    participants=None,
    source=None,
):
    """One observed interaction between the user and a contact."""
    interaction = {
        "contact_id": contact_id,
        "day": day,
        "replied": replied,
        "channel": channel,
        "user_involved": user_involved,
    }
    if delay is not None:
        interaction["reply_delay_hours"] = delay
    if asked_for is not None:
        interaction["asked_for"] = asked_for
    if agreed_to is not None:
        interaction["agreed_to"] = agreed_to
    if declined is not None:
        interaction["declined"] = declined
    if text is not None:
        interaction["text"] = text
    if participants is not None:
        interaction["participants"] = participants
    if source is not None:
        interaction["source"] = source
    return interaction


def _sari_store(days=3):
    """Sari: replies every time, asks for a review every time."""
    store = AgentGoalStore()
    for day in range(days):
        store.observe(
            _interaction(
                "sari", day=day, delay=2.0, asked_for=["review_work"], agreed_to=["review_work"]
            )
        )
    return store


# -- fail-open -----------------------------------------------------------


def test_unknown_contact_predicts_nothing():
    store = AgentGoalStore()
    assert store.predict("nobody") == []
    assert store.get("nobody") is None
    model = WorldModel()
    forecast = model.predict_agent("nobody")
    assert forecast["known"] is False
    assert forecast["predictions"] == []
    assert forecast["reply_likely"] is None


def test_model_with_no_observations_predicts_nothing():
    model = AgentGoalModel(contact_id="sari")
    assert model.predict() == []
    assert model.reply_rate is None
    assert model.preferred_channel is None
    assert model.active_goals == []


# -- lifecycle: build -> predict -> update -> decay ----------------------


def test_model_observe_returns_labels_touched():
    model = AgentGoalModel(contact_id="sari")
    touched = model.observe(
        _interaction("sari", day=0, asked_for=["review_work"], agreed_to=["send_document"])
    )
    assert touched == ["review_work", "send_document"]


def test_goal_lifecycle_strength_and_evidence():
    store = AgentGoalStore()
    model = store.observe(_interaction("sari", day=0, asked_for=["review_work"]))
    assert isinstance(model, AgentGoalModel)
    goal = store.get("sari").goals["review_work"]
    assert goal.strength == pytest.approx(0.5)
    assert goal.supporting == 1
    assert goal.status == "active"
    # A second supporting observation strengthens by 0.1.
    store.observe(_interaction("sari", day=1, agreed_to=["review_work"]))
    assert goal.strength == pytest.approx(0.6)
    assert goal.supporting == 2
    # A refusal weakens twice as fast.
    store.observe(_interaction("sari", day=2, declined=["review_work"]))
    assert goal.strength == pytest.approx(0.4)
    assert goal.contradicting == 1


def test_predict_returns_scored_claims_with_evidence():
    store = _sari_store(days=3)
    predictions = store.predict("sari", {"goal": "review_work"}, day=3)
    kinds = {item.kind: item for item in predictions}
    assert set(kinds) == {"reply", "channel", "next_action", "agreement"}
    reply = kinds["reply"]
    assert reply.label == "will_reply"
    assert reply.confidence == pytest.approx(1.0)
    assert reply.basis.startswith("reply rate 1.00 over 3 observed")
    assert reply.evidence
    assert kinds["channel"].label == "whatsapp"
    assert kinds["next_action"].label == "review_work"
    agreement = kinds["agreement"]
    # Strength 0.5 + 2*0.1 (asked) + 3*0.1 (agreed) = 1.0, capped.
    assert agreement.confidence == pytest.approx(1.0)


def test_confidence_falls_with_sparse_evidence():
    store = AgentGoalStore()
    store.observe(_interaction("sari", day=0, asked_for=["review_work"]))
    predictions = store.predict("sari", {"goal": "review_work"}, day=0)
    agreement = next(item for item in predictions if item.kind == "agreement")
    # One observation: strength 0.5 * evidence factor 1/3 * fresh 1.0,
    # rounded to 4 decimals by the prediction.
    assert agreement.confidence == 0.1667
    reply = next(item for item in predictions if item.kind == "reply")
    assert reply.confidence == 0.3333


def test_confidence_falls_with_stale_evidence():
    store = _sari_store(days=3)
    predictions = store.predict("sari", {"goal": "review_work"}, day=3 + 100)
    agreement = next(item for item in predictions if item.kind == "agreement")
    # Strength 1.0, full evidence, but 100 days stale: factor 0.25.
    assert agreement.confidence == pytest.approx(0.25)


def test_decay_halves_stale_goals():
    store = _sari_store(days=3)
    assert store.decay_all(3) == {}
    decayed = store.decay_all(3 + 91)
    assert decayed == {"sari": ["review_work"]}
    goal = store.get("sari").goals["review_work"]
    assert goal.status == "stale"
    assert goal.strength == pytest.approx(0.5)
    # A fresh observation revives the stale goal.
    store.observe(_interaction("sari", day=3 + 91, asked_for=["review_work"]))
    assert goal.status == "active"
    assert goal.strength == pytest.approx(0.6)


def test_dropped_goal_stays_history_and_restarts_fresh():
    store = AgentGoalStore()
    store.observe(_interaction("sari", day=0, asked_for=["review_work"]))
    store.observe(_interaction("sari", day=1, declined=["review_work"]))
    store.observe(_interaction("sari", day=2, declined=["review_work"]))
    goal = store.get("sari").goals["review_work"]
    assert goal.strength == pytest.approx(0.1)
    assert goal.status == "dropped"
    assert goal.confidence(2) == 0.0
    # The dropped goal is not a prediction: no next-action claim.
    kinds = {item.kind for item in store.predict("sari", day=2)}
    assert "next_action" not in kinds
    # Asking again starts the goal fresh at 0.5.
    store.observe(_interaction("sari", day=3, asked_for=["review_work"]))
    goal = store.get("sari").goals["review_work"]
    assert goal.status == "active"
    assert goal.strength == pytest.approx(0.5)
    assert goal.supporting == 1


def test_contradiction_never_creates_a_goal():
    store = AgentGoalStore()
    store.observe(_interaction("sari", day=0, declined=["review_work"]))
    assert store.get("sari").goals == {}


def test_predict_never_mutates_the_model():
    store = _sari_store(days=3)
    before = store.get("sari").to_dict()
    store.predict("sari", {"goal": "review_work"}, day=3)
    after = store.get("sari").to_dict()
    before.pop("predictions")
    after.pop("predictions")
    assert before == after


# -- scoring predictions against outcomes -------------------------------


def test_prediction_accuracy_against_scripted_history():
    """Sari always agrees to reviews: predictions score right."""
    store = _sari_store(days=5)
    predictions = store.predict("sari", {"goal": "review_work"}, day=5)
    agreement = next(item for item in predictions if item.kind == "agreement")
    scored = store.score_prediction("sari", agreement.id, True)
    assert scored is not None
    assert scored.outcome is True
    assert scored.correct is True
    model = store.get("sari")
    assert model.accuracy == pytest.approx(1.0)
    assert model.stats == {"predictions": 1, "correct": 1}
    # Scoring the same prediction twice is refused.
    assert store.score_prediction("sari", agreement.id, True) is None
    # An unknown prediction id scores nothing.
    assert store.score_prediction("sari", "no-such-id", True) is None
    assert store.score_prediction("nobody", agreement.id, True) is None


def test_wrong_prediction_scores_wrong_and_weakens_the_goal():
    store = _sari_store(days=3)
    predictions = store.predict("sari", {"goal": "review_work"}, day=3)
    agreement = next(item for item in predictions if item.kind == "agreement")
    strength_before = store.get("sari").goals["review_work"].strength
    scored = store.score_prediction("sari", agreement.id, False)
    assert scored.correct is False
    assert store.get("sari").accuracy == pytest.approx(0.0)
    assert store.get("sari").goals["review_work"].strength == pytest.approx(strength_before - 0.2)


def test_will_not_reply_prediction_scores_on_silence():
    store = AgentGoalStore()
    for day in range(3):
        store.observe(_interaction("budi", day=day, replied=False))
    predictions = store.predict("budi", day=3)
    reply = next(item for item in predictions if item.kind == "reply")
    assert reply.label == "will_not_reply"
    scored = store.score_prediction("budi", reply.id, False)
    assert scored.correct is True
    assert store.get("budi").accuracy == pytest.approx(1.0)


# -- privacy boundary -----------------------------------------------------


@pytest.mark.parametrize(
    "interaction",
    [
        _interaction("sari", user_involved=False, asked_for=["review_work"]),
        _interaction("sari", participants=["sari", "budi"], asked_for=["review_work"]),
        _interaction("sari", source="third_party_thread", asked_for=["review_work"]),
    ],
)
def test_third_party_only_interaction_is_refused(interaction):
    with pytest.raises(ValueError, match="third-party-only"):
        check_user_involved(interaction)
    store = AgentGoalStore()
    with pytest.raises(ValueError, match="third-party-only"):
        store.observe(interaction)
    assert store.get("sari") is None
    assert len(store) == 0


def test_world_model_ingest_refuses_third_party_and_records_it():
    model = WorldModel()
    model.ingest_task_result(
        {
            "task": "message_contact",
            "success": True,
            "result": {
                "day": 5,
                "contact_id": "sari",
                "channel": "whatsapp",
                "replied": True,
                "asked_for": ["review_work"],
                "user_involved": False,
            },
        }
    )
    assert model.agent_model("sari") is None
    assert len(model.agent_refusals) == 1
    assert model.agent_refusals[0]["contact_id"] == "sari"
    # The contact belief still counted the interaction itself; the
    # goal model is what was refused, not the reply observation.
    assert "sari" in model._contact_beliefs


# -- world model wiring ---------------------------------------------------


def test_world_model_builds_goal_model_from_task_results():
    model = WorldModel()
    for day in range(3):
        model.ingest_task_result(
            {
                "task": "message_contact",
                "success": True,
                "result": {
                    "day": day,
                    "contact_id": "sari",
                    "channel": "whatsapp",
                    "replied": True,
                    "reply_delay_hours": 2.0,
                    "asked_for": ["review_work"],
                },
            }
        )
    # The contact belief and the goal model agree, from the same
    # observed interactions — the goal model is built on the
    # contact belief, not a parallel store.
    assert model._contact_beliefs["sari"].reply_rate == pytest.approx(1.0)
    assert model._contact_beliefs["sari"].preferred_channel == "whatsapp"
    forecast = model.predict_agent("sari", {"goal": "review_work"})
    assert forecast["known"] is True
    assert forecast["reply_likely"] is True
    assert forecast["reply_rate"] == pytest.approx(1.0)
    assert forecast["preferred_channel"] == "whatsapp"
    assert forecast["mean_reply_delay_hours"] == pytest.approx(2.0)
    kinds = {item["kind"] for item in forecast["predictions"]}
    assert kinds == {"reply", "channel", "next_action", "agreement"}


def test_world_model_state_round_trip_keeps_goal_models():
    model = WorldModel()
    model.ingest_task_result(
        {
            "task": "message_contact",
            "success": True,
            "result": {
                "day": 0,
                "contact_id": "sari",
                "channel": "whatsapp",
                "replied": True,
                "asked_for": ["review_work"],
            },
        }
    )
    restored = WorldModel()
    restored._restore_state(model.state)
    assert restored.agent_model("sari") is not None
    assert restored.agent_model("sari").goals["review_work"].strength == pytest.approx(0.5)
    assert restored.predict_agent("sari")["known"] is True


def test_score_agent_prediction_through_world_model():
    model = WorldModel()
    model.observe_agent_interaction(_interaction("sari", day=0, asked_for=["review_work"]))
    model.observe_agent_interaction(_interaction("sari", day=1, asked_for=["review_work"]))
    model.observe_agent_interaction(_interaction("sari", day=2, asked_for=["review_work"]))
    forecast = model.predict_agent("sari", {"goal": "review_work"})
    agreement = next(item for item in forecast["predictions"] if item["kind"] == "agreement")
    scored = model.score_agent_prediction("sari", agreement["id"], True)
    assert scored is not None
    assert scored["correct"] is True
    assert model.agent_model("sari").accuracy == pytest.approx(1.0)
    assert model.score_agent_prediction("sari", "no-such-id", True) is None
    assert model.score_agent_prediction("nobody", agreement["id"], True) is None


def test_causal_expectations_attach_as_evidence():
    model = WorldModel()
    model.causal_ledger = CausalLedger()
    model.causal_ledger.propose("contact:sari", "replied")
    model.observe_agent_interaction(_interaction("sari", day=0, asked_for=["review_work"]))
    forecast = model.predict_agent("sari")
    assert len(forecast["causal_expectations"]) == 1
    assert forecast["causal_expectations"][0]["cause"] == "contact:sari"
    reply = next(item for item in forecast["predictions"] if item["kind"] == "reply")
    assert any(link["kind"] == "causal" for link in reply["evidence"])


def test_decay_stale_agent_models_through_world_model():
    model = WorldModel()
    model.observe_agent_interaction(_interaction("sari", day=0, asked_for=["review_work"]))
    assert model.decay_stale_agent_models(10) == {}
    assert model.decay_stale_agent_models(91) == {"sari": ["review_work"]}
    assert model.agent_model("sari").goals["review_work"].status == "stale"


# -- keyword goal inferrer -------------------------------------------------


def test_keyword_inferrer_maps_text_to_goals():
    inferrer = KeywordGoalInferrer()
    assert inferrer.infer("please review my PR and send feedback") == [
        "review_work",
        "send_document",
        "give_feedback",
    ]
    assert inferrer.infer("hello, how are you?") == []
    assert inferrer.infer("") == []


def test_free_text_interaction_uses_the_inferrer():
    store = AgentGoalStore()
    store.observe(_interaction("sari", day=0, text="could you review my draft?"))
    assert "review_work" in store.get("sari").goals


# -- planner caller ---------------------------------------------------------


def test_planner_message_contact_accounts_for_reaction():
    model = WorldModel()
    for day in range(3):
        model.ingest_task_result(
            {
                "task": "message_contact",
                "success": True,
                "result": {
                    "day": day,
                    "contact_id": "sari",
                    "channel": "whatsapp",
                    "replied": True,
                    "asked_for": ["review_work"],
                    "agreed_to": ["review_work"],
                },
            }
        )
    planned = plan(model, {"goal": "message_contact", "contact_id": "sari", "ask": "review_work"})
    assert planned["feasible"] is True
    assert planned["steps"][0]["action"]["channel"] == "whatsapp"
    assert planned["predicted_outcome"]["reply_likely"] is True


def test_planner_message_contact_unknown_contact_is_infeasible():
    model = WorldModel()
    planned = plan(model, {"goal": "message_contact", "contact_id": "nobody"})
    assert planned["feasible"] is False
    assert planned["reason"] == "unknown_contact"


def test_planner_message_contact_agreement_unlikely_is_infeasible():
    model = WorldModel()
    # One weak observation: the goal is known but the agreement
    # confidence (0.5 * 1/3) is below the 0.5 bar.
    model.ingest_task_result(
        {
            "task": "message_contact",
            "success": True,
            "result": {
                "day": 0,
                "contact_id": "sari",
                "channel": "whatsapp",
                "replied": True,
                "asked_for": ["review_work"],
            },
        }
    )
    planned = plan(model, {"goal": "message_contact", "contact_id": "sari", "ask": "review_work"})
    assert planned["feasible"] is False
    assert planned["reason"] == "agreement_unlikely"


# -- serialization -----------------------------------------------------------


def test_store_serialization_round_trip():
    store = _sari_store(days=3)
    predictions = store.predict("sari", {"goal": "review_work"}, day=3)
    agreement = next(item for item in predictions if item.kind == "agreement")
    store.score_prediction("sari", agreement.id, True)
    rebuilt = AgentGoalStore.from_dict(store.to_dict())
    model = rebuilt.get("sari")
    assert model is not None
    assert model.goals["review_work"].strength == pytest.approx(1.0)
    assert model.accuracy == pytest.approx(1.0)
    assert model.predictions[0].label == "will_reply"
