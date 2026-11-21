"""Prediction and surprise (Kosmos K19): predict, compare, learn.

No live provider, no network, no database: the learner is
in-memory, the writer and the learning loop are fakes (or a
fresh in-memory loop).
"""

from __future__ import annotations

import pytest

from puspa.memory.learning_loop import LearningLoop
from puspa.world.prediction_surprise import (
    PREDICTION_SURPRISE_OUTCOME_TYPE,
    PredictionSurprise,
    surprise_of,
)

ACTION = {"type": "check_price", "product_id": "rice-5kg", "store": "tokopedia"}


def _learner(**over):
    kwargs = {"learning_loop": LearningLoop()}
    kwargs.update(over)
    return PredictionSurprise(**kwargs)


def _record(learner, *, predicted=None, confidence=0.8, task="check rice price"):
    return learner.record_prediction(
        task=task,
        action=dict(ACTION),
        predicted={"price": 100000} if predicted is None else predicted,
        confidence=confidence,
        user_id="user-1",
        task_id="task-1",
    )


def test_seeded_prediction_error_updates_model_and_is_recorded():
    """Done criterion: a seeded wrong prediction updates the model
    and the update is recorded.

    The forecast says the price is 100000; reality says 120000.
    After observing, the next prediction for the same action must
    return the observed 120000 with lowered confidence, the
    learning loop must hold the correction, and a ledger event
    of type "prediction_surprise" must have been written.
    """
    written = []

    def fake_writer(**kwargs):
        written.append(kwargs)
        return {"outcome_id": "evt-surprise-1", **kwargs}

    loop = LearningLoop()
    learner = _learner(record_outcome=fake_writer, learning_loop=loop)
    assert learner.predict_for(ACTION) is None
    record = _record(learner)
    result = learner.observe(record.prediction_id, {"price": 120000})

    assert result.correct is False
    assert result.surprise == pytest.approx((120000 - 100000) / 120000)
    assert result.updated_confidence < 0.8
    assert result.corrected_prediction == {"price": 120000}

    # The model updated: the next prediction differs.
    prior = learner.predict_for(ACTION)
    assert prior is not None
    assert prior["outcome"] == {"price": 120000}
    assert prior["confidence"] == pytest.approx(result.updated_confidence)

    # The learning loop learned the correction too.
    correction = loop.correction_for("check rice price")
    assert correction is not None
    assert correction["observed"] == {"price": 120000}

    # The update is recorded as a ledger event.
    assert len(written) == 1
    event = written[0]
    assert event["outcome_type"] == PREDICTION_SURPRISE_OUTCOME_TYPE
    assert event["task_id"] == "task-1"
    assert event["user_id"] == "user-1"
    assert event["details"]["surprise"] == pytest.approx(result.surprise)
    assert event["details"]["corrected_prediction"] == {"price": 120000}
    assert event["evidence"], "a ledger event needs evidence"
    assert result.ledger_record is not None
    assert result.misprediction is not None
    assert len(learner) == 0


def test_correct_prediction_raises_confidence_and_writes_nothing():
    written = []
    learner = _learner(record_outcome=lambda **kwargs: written.append(kwargs))
    record = _record(learner)
    result = learner.observe(record.prediction_id, {"price": 100000})

    assert result.correct is True
    assert result.surprise == 0.0
    assert result.updated_confidence == pytest.approx(0.85)
    assert result.ledger_record is None
    assert result.misprediction is None
    assert written == []
    prior = learner.predict_for(ACTION)
    assert prior is not None
    assert prior["outcome"] == {"price": 100000}


def test_surprise_scores_numbers_categories_and_scalars():
    assert surprise_of({"price": 100}, {"price": 100}) == 0.0
    assert surprise_of({"status": "paid"}, {"status": "unpaid"}) == 1.0
    assert surprise_of({"price": 100000}, {"price": 120000}) == pytest.approx(1 / 6)
    # One field right, one wrong: half surprise.
    assert surprise_of(
        {"price": 100, "status": "paid"}, {"price": 100, "status": "unpaid"}
    ) == pytest.approx(0.5)
    # Scalars score through the single "value" field.
    assert surprise_of("paid", "paid") == 0.0
    assert surprise_of("paid", "unpaid") == 1.0
    assert surprise_of({}, {}) == 0.0
    assert surprise_of({}, {"price": 100}) == 1.0


def test_scalar_prediction_learns_like_a_dict_one():
    learner = _learner()
    record = learner.record_prediction(
        task="check bill status",
        action={"type": "check_bill", "bill_id": "pln-1"},
        predicted="unpaid",
        confidence=0.9,
        user_id="user-1",
        task_id="task-2",
    )
    result = learner.observe(record.prediction_id, "paid")
    assert result.correct is False
    assert result.surprise == 1.0
    assert result.updated_confidence == pytest.approx(0.05)
    prior = learner.predict_for({"type": "check_bill", "bill_id": "pln-1"})
    assert prior is not None
    assert prior["outcome"] == {"value": "paid"}


def test_second_error_updates_the_prior_again():
    learner = _learner()
    first = _record(learner)
    learner.observe(first.prediction_id, {"price": 120000})
    second = _record(learner, predicted={"price": 120000}, confidence=0.6)
    result = learner.observe(second.prediction_id, {"price": 110000})
    assert result.correct is False
    prior = learner.predict_for(ACTION)
    assert prior is not None
    assert prior["outcome"] == {"price": 110000}
    assert len(learner.history()) == 2


def test_threshold_treats_small_surprise_as_correct():
    learner = PredictionSurprise(learning_loop=LearningLoop(), surprise_threshold=0.2)
    record = _record(learner)
    result = learner.observe(record.prediction_id, {"price": 110000})
    assert result.surprise == pytest.approx(1 / 11)
    assert result.correct is True
    assert result.ledger_record is None


def test_no_writer_still_updates_model_and_loop():
    loop = LearningLoop()
    learner = _learner(learning_loop=loop)
    record = _record(learner)
    result = learner.observe(record.prediction_id, {"price": 120000})
    assert result.ledger_record is None
    assert learner.predict_for(ACTION) is not None
    assert loop.correction_for("check rice price") is not None


def test_validation_and_double_observe():
    learner = _learner()
    with pytest.raises(ValueError):
        learner.record_prediction(
            task="t",
            action=dict(ACTION),
            predicted={"price": 1},
            confidence=1.5,
            user_id="user-1",
            task_id="task-1",
        )
    with pytest.raises(ValueError):
        learner.record_prediction(
            task="",
            action=dict(ACTION),
            predicted={"price": 1},
            confidence=0.5,
            user_id="user-1",
            task_id="task-1",
        )
    with pytest.raises(KeyError):
        learner.observe("no-such-prediction", {"price": 1})
    record = _record(learner)
    learner.observe(record.prediction_id, {"price": 120000})
    with pytest.raises(KeyError):
        learner.observe(record.prediction_id, {"price": 120000})
    with pytest.raises(ValueError):
        PredictionSurprise(surprise_threshold=2.0)
    with pytest.raises(ValueError):
        PredictionSurprise(learning_loop=42)
