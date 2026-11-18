"""Persistence tests for the learning loop corrections (no database).

The serialization round trip runs without Postgres: a loop with
mispredictions and user feedback is serialized, a fresh loop is
built from the serialized state, and the restored loop must
predict and propose exactly like the original. The Postgres round
trip lives in `tests/test_loop_persistence_db.py` (the coordinator
runs it; it needs the shared test database).
Run this file with `pytest tests/test_loop_persistence.py -q`.
"""

from puspa.memory.learning_loop import LearningLoop
from puspa.memory.loop_store import deserialize_loop, serialize_loop


def _grounded_loop():
    """Loop with two mispredictions, one feedback correction, and
    two experiences that carry no correction."""
    loop = LearningLoop()
    loop.record_misprediction(
        {
            "task": "watch_price",
            "predicted": 10000,
            "observed": 12000,
            "error": "price moved",
            "context": {"store": "S1"},
        }
    )
    loop.record_misprediction(
        {
            "task": "watch_price",
            "predicted": 12000,
            "observed": 11500,
            "error": "price moved again",
            "context": {"store": "S1"},
        }
    )
    loop.record_feedback("pay_bill", "wrong", correction="paid 75000")
    loop.record_feedback("check_bill", "correct")
    loop.record_feedback("track_order", "wrong")
    return loop


def test_serialize_deserialize_round_trip_keeps_corrections():
    """A fresh loop built from serialized state predicts the same."""
    loop = _grounded_loop()
    restored = deserialize_loop(serialize_loop(loop))
    assert restored is not loop
    assert restored.predict("watch_price") == 11500
    assert restored.predict("pay_bill") == "paid 75000"
    assert restored.predict("check_bill") is None
    assert restored.predict("track_order") is None
    assert restored.correction_for("watch_price") == loop.correction_for(
        "watch_price"
    )
    assert restored.correction_for("pay_bill") == loop.correction_for("pay_bill")
    assert restored.correction_for("check_bill") is None
    assert restored.correction_for("track_order") is None


def test_restore_rebuilds_gaps_so_proposals_match():
    """The restored loop proposes the same next task as the original."""
    loop = _grounded_loop()
    restored = deserialize_loop(serialize_loop(loop))
    assert restored.propose_next_task() == loop.propose_next_task()
    assert restored.propose_next_task()["task"] == "watch_price"
    assert restored.propose_next_task()["gap"]["mispredictions"] == 2


def test_only_correction_carrying_experiences_are_serialized():
    """Positive feedback and correction-less gaps are not stored."""
    loop = _grounded_loop()
    state = serialize_loop(loop)
    kept_ids = {exp["id"] for exp in state["experiences"]}
    # 2 mispredictions + 1 applied feedback correction = 3 kept.
    assert len(state["experiences"]) == 3
    assert len(loop.experiences) == 5
    for correction in state["corrections"].values():
        for experience_id in correction["experience_ids"]:
            assert experience_id in kept_ids


def test_loop_state_restore_round_trip():
    """loop_state / restore_loop_state is exact for predictions."""
    loop = _grounded_loop()
    fresh = LearningLoop()
    assert fresh.predict("watch_price") is None
    fresh.restore_loop_state(loop.loop_state())
    assert fresh.predict("watch_price") == 11500
    assert fresh.predict("pay_bill") == "paid 75000"
    assert fresh.propose_next_task()["task"] == "watch_price"
    # The restored loop is a copy: later learning in one loop does
    # not leak into the other.
    fresh.record_misprediction(
        {
            "task": "watch_price",
            "predicted": 11500,
            "observed": 11000,
            "error": "price moved",
            "context": {"store": "S1"},
        }
    )
    assert fresh.predict("watch_price") == 11000
    assert loop.predict("watch_price") == 11500


def test_restore_rejects_a_non_dict_state():
    """A bad snapshot fails loudly instead of half-restoring."""
    loop = LearningLoop()
    try:
        loop.restore_loop_state("not a dict")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    assert loop.corrections == {}
