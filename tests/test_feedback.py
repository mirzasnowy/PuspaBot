"""Tests for user feedback into the learning loop.

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/, and
is noted in the Brain-phase report.)
"""

import pytest

from puspa.memory import learning_loop
from puspa.memory.learning_loop import (
    FEEDBACK_SOURCE,
    LearningLoop,
    record_feedback,
)


def _wrong_feedback(task="check_pln", correction="bill 75000"):
    return {"task": task, "verdict": "wrong", "correction": correction}


# --- wrong feedback corrects the next prediction ------------------------


def test_wrong_feedback_with_correction_changes_predict():
    """Behaviour change: after wrong feedback with a correction,
    the same predict call returns the corrected value."""
    loop = LearningLoop()
    assert loop.predict("check_pln", default="bill 50000") == "bill 50000"
    result = loop.record_feedback("check_pln", "wrong", correction="bill 75000")
    assert result["status"] == "applied"
    assert result["verdict"] == "wrong"
    assert result["correction"]["observed"] == "bill 75000"
    assert result["correction"]["count"] == 1
    assert loop.predict("check_pln", default="bill 50000") == "bill 75000"
    assert loop.correction_for("check_pln")["observed"] == "bill 75000"


def test_wrong_feedback_proposes_retry_for_the_gap():
    loop = LearningLoop()
    loop.record_feedback("check_pln", "wrong", correction="bill 75000")
    proposal = loop.propose_next_task()
    assert proposal["task"] == "check_pln"
    assert proposal["reason"] == "misprediction_gap"
    assert proposal["difficulty"] == "retry"
    assert proposal["gap"]["mispredictions"] == 1


def test_wrong_feedback_without_correction_logs_gap_but_keeps_predict():
    loop = LearningLoop()
    result = loop.record_feedback("check_pln", "wrong")
    assert result["status"] == "recorded"
    assert result["correction"] is None
    assert loop.predict("check_pln", default="bill 50000") == "bill 50000"
    assert loop.correction_for("check_pln") is None
    assert loop.propose_next_task()["task"] == "check_pln"


def test_wrong_feedback_marks_active_skill_suspect():
    loop = LearningLoop()
    skill = loop.draft_skill("check_pln", code=lambda x: x)
    loop.verify_skill(skill["id"], [{"args": [1], "expected": 1}])
    loop.activate_skill(skill["id"], approved_by="user-1")
    assert loop.get_skill(skill["id"])["status"] == "active"
    loop.record_feedback("check_pln", "wrong", correction="bill 75000")
    assert loop.get_skill(skill["id"])["status"] == "suspect"
    assert loop.find_reusable_skill("check_pln") is None


# --- correct feedback reinforces without changing anything ---------------


def test_correct_feedback_keeps_learned_prediction():
    loop = LearningLoop()
    loop.record_feedback("check_pln", "wrong", correction="bill 75000")
    assert loop.predict("check_pln") == "bill 75000"
    result = loop.record_feedback("check_pln", "correct")
    assert result["status"] == "recorded"
    assert result["verdict"] == "correct"
    assert loop.predict("check_pln") == "bill 75000"
    assert loop.correction_for("check_pln")["observed"] == "bill 75000"
    assert loop.correction_for("check_pln")["count"] == 1


def test_correct_feedback_on_fresh_task_creates_no_correction():
    loop = LearningLoop()
    result = loop.record_feedback("check_pln", "correct")
    assert result["status"] == "recorded"
    assert loop.correction_for("check_pln") is None
    assert loop.predict("check_pln", default="bill 50000") == "bill 50000"
    assert loop.propose_next_task() is None


# --- every feedback entry carries its source -----------------------------


def test_feedback_entries_carry_user_feedback_source():
    loop = LearningLoop()
    loop.record_feedback("check_pln", "wrong", correction="bill 75000")
    loop.record_feedback("compare_price", "correct")
    assert len(loop.experiences) == 2
    for experience in loop.experiences:
        assert experience["kind"] == "user_feedback"
        assert experience["source"] == FEEDBACK_SOURCE
        assert experience["source"] == "user_feedback"
        assert experience["feedback_source"] == "user"
    assert loop.correction_for("check_pln")["source"] == FEEDBACK_SOURCE


def test_feedback_source_actor_is_kept():
    loop = LearningLoop()
    result = loop.record_feedback("check_pln", "wrong", correction="bill 75000", source="pwa")
    assert result["source"] == FEEDBACK_SOURCE
    assert result["feedback_source"] == "pwa"
    assert loop.experiences[0]["feedback_source"] == "pwa"


# --- feedback never overwrites a manual user edit -------------------------


def test_feedback_correction_becomes_proposal_when_manual_edit_exists():
    loop = LearningLoop()
    loop.note_manual_edit("check_pln", ["open pln site", "read bill"])
    result = loop.record_feedback("check_pln", "wrong", correction="bill 75000")
    assert result["status"] == "proposed"
    assert result["conflicts_with_manual_edit"] is True
    assert result["correction"] == "bill 75000"
    # The manual version stands: no correction applied, predict unchanged.
    assert loop.correction_for("check_pln") is None
    assert loop.predict("check_pln", default="bill 50000") == "bill 50000"
    proposals = loop.list_feedback_proposals("check_pln")
    assert len(proposals) == 1
    assert proposals[0]["status"] == "proposed"
    assert proposals[0]["correction"] == "bill 75000"
    assert proposals[0]["source"] == FEEDBACK_SOURCE
    assert proposals[0]["conflicts_with_manual_edit"] is True


def test_feedback_without_manual_edit_has_no_proposal():
    loop = LearningLoop()
    loop.record_feedback("check_pln", "wrong", correction="bill 75000")
    assert loop.list_feedback_proposals() == []


def test_manual_edit_after_feedback_does_not_revoke_applied_correction():
    """A manual edit noted later is a separate record; the already
    applied correction is not silently rewritten by it."""
    loop = LearningLoop()
    loop.record_feedback("check_pln", "wrong", correction="bill 75000")
    loop.note_manual_edit("check_pln", ["open pln site", "read bill"])
    assert loop.predict("check_pln") == "bill 75000"


# --- validation and module-level seam --------------------------------------


def test_record_feedback_rejects_bad_verdict_and_empty_task():
    loop = LearningLoop()
    with pytest.raises(ValueError):
        loop.record_feedback("check_pln", "maybe")
    with pytest.raises(ValueError):
        loop.record_feedback("", "wrong", correction="bill 75000")


def test_module_level_seam_delegates_to_default_loop():
    learning_loop.reset_learning_loop()
    try:
        result = record_feedback("check_pln", "wrong", correction="bill 75000")
        assert result["status"] == "applied"
        assert learning_loop.get_learning_loop().predict("check_pln") == "bill 75000"
        assert record_feedback("check_pln", "correct")["status"] == "recorded"
        assert learning_loop.get_learning_loop().predict("check_pln") == "bill 75000"
    finally:
        learning_loop.reset_learning_loop()
