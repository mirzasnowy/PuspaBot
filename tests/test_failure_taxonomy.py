"""Failure taxonomy (Kosmos Brain v2, Phase 3, V2-16).

Pure Python, no database, no network: each test seeds one
failure of each of the five detection signals and asserts the
classification is correct (class + repair) for all five. The
late-failure seed goes through the real K8 outcome watcher, so
the test proves the watcher-to-taxonomy seam, not just the
classifier in isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from puspa.sentinel.failure_taxonomy import (
    MAX_AUTO_REPAIR_ATTEMPTS,
    REPAIR_BY_CLASS,
    Classification,
    FailureClass,
    FailureEvent,
    FailureSignal,
    RepairAction,
    classify,
    from_late_failure,
)
from puspa.sentinel.outcome_watcher import OutcomeWatcher

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)


def _event(signal, task="pay the electricity bill", **context):
    return FailureEvent(signal=signal, task=task, detail="seeded failure", context=context)


# -- the five signals, one seeded failure each -----------------------


def test_tool_error_classifies_as_tool_misuse_with_retry():
    result = classify(_event("tool_error", tool="pay_bill", transient=True))
    assert result.signal is FailureSignal.TOOL_ERROR
    assert result.failure_class is FailureClass.TOOL_MISUSE
    assert result.repair is RepairAction.RETRY
    assert not result.escalated


def test_postcondition_failure_on_a_skill_classifies_as_tool_misuse_with_repair():
    result = classify(
        _event(
            "postcondition_failure",
            skill_id="pay-bill",
            expected="paid",
            actual="unpaid",
        )
    )
    assert result.failure_class is FailureClass.TOOL_MISUSE
    assert result.repair is RepairAction.REPAIR_SKILL


def test_prediction_error_classifies_as_wrong_intent_with_model_update():
    result = classify(
        _event("prediction_error", predicted="paid", observed="unpaid"),
    )
    assert result.failure_class is FailureClass.WRONG_INTENT
    assert result.repair is RepairAction.UPDATE_INTENT_MODEL


def test_silent_human_repeat_classifies_as_wrong_intent_with_ask_user():
    result = classify(_event("silent_human", human_action="repeated the question"))
    assert result.failure_class is FailureClass.WRONG_INTENT
    assert result.repair is RepairAction.ASK_USER


def test_late_k8_failure_classifies_as_environment_changed():
    watcher = OutcomeWatcher()
    watcher.watch(
        {
            "outcome_id": "evt-outcome-1",
            "task": "pay the electricity bill",
            "predicted": "bill unpaid",
            "observed": "paid",
            "user_id": "user-1",
            "task_id": "task-1",
        },
        recheck=lambda: "unpaid",
        delay=timedelta(minutes=1),
        now=NOW,
    )
    results = watcher.check_due(NOW + timedelta(minutes=2))
    assert len(results) == 1
    assert results[0].status == "late_failure"
    result = classify(from_late_failure(results[0]))
    assert result.signal is FailureSignal.LATE_FAILURE
    assert result.failure_class is FailureClass.ENVIRONMENT_CHANGED
    assert result.repair is RepairAction.REFRESH_WORLD_STATE
    assert not result.escalated


# -- context disambiguates the class ---------------------------------


def test_repeated_tool_error_is_a_model_error_not_a_retry():
    result = classify(_event("tool_error", tool="pay_bill", repeat_count=2))
    assert result.failure_class is FailureClass.REPEATED_MODEL_ERROR
    assert result.repair is RepairAction.MOVE_MODEL_TIER


def test_postcondition_failure_without_a_skill_is_a_bad_plan():
    result = classify(_event("postcondition_failure", expected="paid", actual="unpaid"))
    assert result.failure_class is FailureClass.BAD_PLAN
    assert result.repair is RepairAction.REPLAN


def test_prediction_error_with_a_knowledge_gap_is_missing_knowledge():
    result = classify(_event("prediction_error", knowledge_gap=True))
    assert result.failure_class is FailureClass.MISSING_KNOWLEDGE
    assert result.repair is RepairAction.RESEARCH


def test_prediction_error_when_the_world_moved_is_environment_changed():
    result = classify(_event("prediction_error", world_changed=True))
    assert result.failure_class is FailureClass.ENVIRONMENT_CHANGED
    assert result.repair is RepairAction.REFRESH_WORLD_STATE


def test_silent_human_undo_and_ignore_also_ask_the_user():
    for human_action in ("undid the payment", "ignored the suggestion"):
        result = classify(_event("silent_human", human_action=human_action))
        assert result.failure_class is FailureClass.WRONG_INTENT
        assert result.repair is RepairAction.ASK_USER


# -- loop rules --------------------------------------------------------


def test_two_spent_repairs_escalate_but_keep_the_diagnosis():
    result = classify(
        _event("tool_error", tool="pay_bill", repair_attempts=MAX_AUTO_REPAIR_ATTEMPTS)
    )
    assert result.repair is RepairAction.ESCALATE
    assert result.escalated
    assert result.failure_class is FailureClass.TOOL_MISUSE
    assert "two automatic repair attempts" in result.reason


def test_one_spent_repair_still_retries():
    result = classify(_event("tool_error", tool="pay_bill", repair_attempts=1))
    assert result.repair is RepairAction.RETRY
    assert not result.escalated


# -- shape and validation ----------------------------------------------


def test_every_class_has_a_default_repair():
    assert set(REPAIR_BY_CLASS) == set(FailureClass)
    assert all(isinstance(repair, RepairAction) for repair in REPAIR_BY_CLASS.values())


def test_classification_serialises_to_a_dict():
    result = classify(_event("tool_error", tool="pay_bill"))
    data = result.to_dict()
    assert data == {
        "signal": "tool_error",
        "task": "pay the electricity bill",
        "detail": "seeded failure",
        "failure_class": "tool_misuse",
        "repair": "retry",
        "reason": result.reason,
        "escalated": False,
    }
    assert isinstance(result, Classification)


def test_unknown_signal_is_rejected():
    with pytest.raises(ValueError, match="unknown failure signal"):
        FailureEvent(signal="mystery", task="pay the bill")


def test_empty_task_is_rejected():
    with pytest.raises(ValueError, match="non-empty task"):
        FailureEvent(signal="tool_error", task="  ")


def test_from_late_failure_rejects_a_non_late_result():
    watcher = OutcomeWatcher()
    watcher.watch(
        {
            "outcome_id": "evt-outcome-2",
            "task": "pay the electricity bill",
            "predicted": "bill unpaid",
            "observed": "paid",
            "user_id": "user-1",
            "task_id": "task-1",
        },
        recheck=lambda: "paid",
        delay=timedelta(minutes=1),
        now=NOW,
    )
    results = watcher.check_due(NOW + timedelta(minutes=2))
    assert results[0].status == "ok"
    with pytest.raises(ValueError, match="late_failure"):
        from_late_failure(results[0])
