"""Causal Ledger wiring tests (Kosmos K4).

Pure in-memory tests, no database. Run with:
`pytest tests/test_causal_learning_wiring.py -q` (SOLO, no -n).

What is proven here:

- a loop without a ledger behaves exactly as before (no
  ledger, no change to predictions);
- the loop feeds the ledger: a misprediction proposes the
  observed outcome and contradicts the predicted one, "correct"
  feedback strengthens, "wrong" feedback without a correction
  only contradicts;
- a correction held as a manual-edit proposal never reaches
  the ledger, so it cannot leak back into predictions;
- the ledger influences predictions: `predict` falls back to
  the strongest active hypothesis when no correction exists,
  and a correction still wins over the ledger;
- the read path (`expect`) works on the loop and on the world
  model, and the TaskRunner / session brain share the loop's
  ledger with the model.
"""

from datetime import UTC, datetime

import pytest

from puspa.chassis.agent_session import AgentBrain
from puspa.memory.learning_loop import LearningLoop
from puspa.runtime.tasks import TaskRunner
from puspa.world.causal import CausalLedger
from puspa.world.model import WorldModel

T0 = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)


def _misprediction(task="check_pln", predicted=50000, observed=75000):
    return {
        "task": task,
        "predicted": predicted,
        "observed": observed,
        "error": "price moved",
        "context": {},
    }


def _ledger_loop():
    return LearningLoop(causal_ledger=CausalLedger())


# -- no ledger: unchanged behaviour ------------------------------------
def test_loop_without_ledger_is_unchanged():
    """Opt-in: a plain loop never touches causal hypotheses."""
    loop = LearningLoop()
    assert loop.causal_ledger is None
    assert loop.expect("check_pln") == []
    loop.record_misprediction(_misprediction())
    assert loop.predict("check_pln") == 75000  # correction, as before
    assert loop.predict("other_task") is None
    loop.record_feedback("other_task", "correct")
    assert loop.predict("other_task") is None


# -- the loop proposes / strengthens / contradicts ----------------------
def test_misprediction_proposes_observed_outcome():
    """Observed outcome proposed at 0.5, with the experience as evidence."""
    loop = _ledger_loop()
    loop.record_misprediction(_misprediction())
    rows = loop.expect("check_pln")
    assert len(rows) == 1
    row = rows[0]
    assert row["effect_value"] == 75000
    assert row["strength"] == 0.5
    assert row["status"] == "active"
    assert {"kind": "observation", "id": loop.experiences[0]["id"]} in row["evidence"]


def test_correct_feedback_strengthens_predicted_hypothesis():
    """A "correct" verdict is a consistent observation."""
    loop = _ledger_loop()
    loop.record_misprediction(_misprediction())
    loop.record_feedback("check_pln", "correct")
    rows = loop.expect("check_pln")
    assert len(rows) == 1
    assert rows[0]["strength"] == pytest.approx(0.6)
    assert rows[0]["supporting"] == 2


def test_misprediction_contradicts_predicted_hypothesis():
    """A later miss weakens the hypothesis the prediction came from."""
    loop = _ledger_loop()
    loop.record_misprediction(_misprediction())
    loop.record_feedback("check_pln", "correct")  # 0.5 -> 0.6
    loop.record_misprediction(_misprediction(predicted=75000, observed=80000))
    rows = loop.expect("check_pln")
    by_effect = {row["effect_value"]: row for row in rows}
    assert by_effect[75000]["strength"] == pytest.approx(0.4)
    assert by_effect[80000]["strength"] == 0.5
    assert rows[0]["effect_value"] == 80000  # strongest first


def test_repeated_contradiction_kills_hypothesis_through_the_loop():
    """Strength below 0.2 via the loop means contradicted, invisible."""
    loop = _ledger_loop()
    loop.record_misprediction(_misprediction())
    loop.record_feedback("check_pln", "correct")  # 0.6
    loop.record_misprediction(_misprediction(predicted=75000, observed=80000))  # 0.4
    loop.record_misprediction(_misprediction(predicted=75000, observed=80000))  # 0.2
    assert loop.causal_ledger.get(loop.expect("check_pln")[0]["id"]) is not None
    loop.record_misprediction(_misprediction(predicted=75000, observed=80000))  # 0.0
    rows = loop.expect("check_pln")
    assert [row["effect_value"] for row in rows] == [80000]
    history = loop.causal_ledger.list_all(include_terminal=True)
    dead = next(row for row in history if row["effect_value"] == 75000)
    assert dead["status"] == "contradicted"
    assert dead["valid_to"] is not None


def test_wrong_feedback_without_correction_only_contradicts():
    """No correction given: nothing new is proposed."""
    loop = _ledger_loop()
    loop.record_misprediction(_misprediction())
    loop.record_feedback("check_pln", "correct")  # 0.6
    loop.record_feedback("check_pln", "wrong")
    rows = loop.expect("check_pln")
    assert len(rows) == 1
    assert rows[0]["strength"] == pytest.approx(0.4)
    assert loop.predict("check_pln") == 75000  # correction still stands


def test_wrong_feedback_with_correction_proposes_correction():
    """A "wrong" verdict with a correction swaps the hypothesis."""
    loop = _ledger_loop()
    loop.record_misprediction(_misprediction())
    loop.record_feedback("check_pln", "wrong", correction="bill 80000")
    rows = loop.expect("check_pln")
    by_effect = {row["effect_value"]: row for row in rows}
    assert by_effect[75000]["strength"] == pytest.approx(0.3)
    assert by_effect["bill 80000"]["strength"] == 0.5
    assert loop.predict("check_pln") == "bill 80000"


def test_manual_edit_conflict_never_reaches_the_ledger():
    """A held proposal must not leak back into predictions via the ledger."""
    loop = _ledger_loop()
    loop.note_manual_edit("check_pln", "user version")
    result = loop.record_feedback("check_pln", "wrong", correction="bill 75000")
    assert result["status"] == "proposed"
    assert result["conflicts_with_manual_edit"] is True
    assert loop.expect("check_pln") == []
    assert loop.predict("check_pln", default="bill 50000") == "bill 50000"


# -- the ledger influences predictions ----------------------------------
def test_predict_falls_back_to_the_ledger_when_no_correction_exists():
    """A ledger-only hypothesis answers predict; a correction wins."""
    ledger = CausalLedger()
    ledger.propose("check_pln", "bill 75000")
    loop = LearningLoop(causal_ledger=ledger)
    assert loop.predict("check_pln") == "bill 75000"
    loop.record_misprediction(_misprediction(predicted=75000, observed=80000))
    assert loop.predict("check_pln") == 80000  # correction wins over ledger


def test_condition_and_ledger_event_evidence_flow_through():
    """Condition names the claim; a ledger event id becomes evidence."""
    loop = _ledger_loop()
    loop.record_misprediction(
        _misprediction(),
        condition="peak_hours",
        evidence_event_id="evt-123",
    )
    assert loop.expect("check_pln") == []
    rows = loop.expect("check_pln", "peak_hours")
    assert len(rows) == 1
    assert rows[0]["condition"] == "peak_hours"
    assert {"kind": "ledger_event", "id": "evt-123"} in rows[0]["evidence"]
    assert any(link["kind"] == "observation" for link in rows[0]["evidence"])


def test_expired_hypotheses_are_invisible_through_the_loop():
    """The loop's read path never returns an expired hypothesis."""
    ledger = CausalLedger()
    ledger.propose("check_pln", "bill 75000", now=T0, ttl_days=1)
    loop = LearningLoop(causal_ledger=ledger)
    assert loop.expect("check_pln") == []
    assert loop.predict("check_pln") is None


# -- world model read path and sharing ----------------------------------
def test_world_model_expect_needs_a_ledger():
    """No ledger attached: the read path is empty, never an error."""
    model = WorldModel()
    assert model.causal_ledger is None
    assert model.expect("check_pln") == []


def test_world_model_expect_reads_the_shared_ledger():
    """The model answers "what do we expect if X?" from its ledger."""
    ledger = CausalLedger()
    ledger.propose("attempt_task", "success")
    ledger.propose("attempt_task", "failure")
    ledger.observe("attempt_task", "success", consistent=True)
    model = WorldModel()
    model.causal_ledger = ledger
    rows = model.expect("attempt_task")
    assert [row["effect_value"] for row in rows] == ["success", "failure"]


def test_task_runner_shares_the_loop_ledger_with_the_model():
    """TaskRunner wires loop -> model, like it does for mispredictions."""
    loop = _ledger_loop()
    loop.record_misprediction(_misprediction())
    model = WorldModel()
    TaskRunner(world_model=model, learning_loop=loop)
    assert model.causal_ledger is loop.causal_ledger
    assert [row["effect_value"] for row in model.expect("check_pln")] == [75000]


def test_task_runner_without_ledger_leaves_model_alone():
    """A loop without a ledger must not wipe a ledger the model has."""
    ledger = CausalLedger()
    ledger.propose("check_pln", "bill 75000")
    model = WorldModel()
    model.causal_ledger = ledger
    TaskRunner(world_model=model, learning_loop=LearningLoop())
    assert model.causal_ledger is ledger
    assert model.expect("check_pln")[0]["effect_value"] == "bill 75000"


def test_session_brain_shares_the_loop_ledger_with_the_model():
    """AgentBrain wires the session loop's ledger into its model."""
    loop = _ledger_loop()
    loop.record_misprediction(_misprediction())
    brain = AgentBrain("user-1", "agent-1", learning_loop=loop)
    assert brain.world_model.causal_ledger is loop.causal_ledger
    assert [row["effect_value"] for row in brain.world_model.expect("check_pln")] == [75000]
    plain = AgentBrain("user-1", "agent-1")
    assert plain.world_model.causal_ledger is None
    assert plain.world_model.expect("check_pln") == []
