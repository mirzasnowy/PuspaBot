"""Repair loop (Kosmos v2, Phase 3): detect, classify, fix, prove.

No live provider, no network, no database: the world is the
deterministic K7 simulator, the failure signals come from the
real K19 prediction-surprise scorer and the real K8 outcome
watcher, the ledger is the real in-memory causal ledger, and
the repairers are small callables that re-read that world.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from puspa.memory.learning_loop import LearningLoop
from puspa.self.repair_loop import (
    FAILURE_STALE_WORLD_STATE,
    FAILURE_TOOL_MISUSE,
    FAILURE_UNKNOWN,
    FAILURE_WRONG_PREDICTION,
    FailureSignal,
    RepairLoop,
    RepairOutcome,
    classify,
)
from puspa.sentinel.outcome_watcher import OutcomeWatcher
from puspa.world.causal import CausalLedger, causal_effect_key
from puspa.world.prediction_surprise import PredictionSurprise
from puspa.world.simulator import SimulatedWorld

ACTION = {"type": "check_price", "product_id": "phone-x1", "store": "Tokopedia"}
NOW = datetime(2026, 11, 22, 4, 0, tzinfo=UTC)


def _surprise_result(*, predicted_price=5250000, observed_price=5246000):
    """A scored K19 miss: forecast 5250000, reality 5246000."""
    learner = PredictionSurprise(learning_loop=LearningLoop())
    record = learner.record_prediction(
        task="check phone price",
        action=dict(ACTION),
        predicted={
            "price": predicted_price,
            "store": "Tokopedia",
            "product_id": "phone-x1",
            "day": 15,
        },
        confidence=0.8,
        user_id="user-1",
        task_id="task-1",
    )
    result = learner.observe(
        record.prediction_id,
        {"price": observed_price, "store": "Tokopedia", "product_id": "phone-x1", "day": 17},
    )
    return learner, result


def test_seeded_prediction_error_detected_classified_repaired_and_regression_added():
    """Done criterion: a seeded failure is fixed with no help.

    The K7 world drifts the Tokopedia price down Rp 2,000 a day.
    The forecast says 5,250,000 (day 15); two days later the
    world says 5,246,000. K19 scores the miss, the loop detects
    it from the scored result, classifies it as stale world
    state (a fresh read confirms the new price), repairs it by
    re-reading the world, records the repair as a hypothesis
    with an expiry, and adds a regression case that replays.
    """
    world = SimulatedWorld()
    assert world.observe_price("phone-x1", "Tokopedia")["price"] == 5250000
    world.act({"type": "wait_day"})
    world.act({"type": "wait_day"})
    observed = world.observe_price("phone-x1", "Tokopedia")
    assert observed["price"] == 5246000

    learner, result = _surprise_result()
    assert not result.correct
    # K19 already corrected the prior: the next forecast for the
    # same action is the observed value, not the old one.
    assert learner.predict_for(ACTION)["outcome"]["price"] == 5246000

    ledger = CausalLedger()
    loop = RepairLoop(
        ledger=ledger,
        reobserve=lambda signal: world.observe_price("phone-x1", "Tokopedia"),
    )
    outcome = loop.handle_surprise(result, fresh_observation=dict(observed), now=NOW)

    assert outcome.status == "repaired"
    assert outcome.diagnosis.failure_class == FAILURE_STALE_WORLD_STATE
    assert len(outcome.attempts) == 1
    assert outcome.attempts[0].verified is True
    assert outcome.escalation is None
    hypothesis = outcome.hypothesis
    assert hypothesis is not None
    assert hypothesis["status"] == "active"
    assert hypothesis["strength"] > 0.5  # confirmed once, not just proposed
    # The confirming observation is stamped one second after the
    # loop started (attempts are sequential), so the refreshed
    # expiry is NOW + 1s + 30 days.
    assert hypothesis["expires_at"] == (NOW + timedelta(days=30, seconds=1)).isoformat()
    assert hypothesis["cause"].startswith("repair:stale_world_state:check phone price")
    assert hypothesis["effect"] == causal_effect_key("refresh_world_state")
    assert len(loop.suite) == 1
    case = loop.suite.cases()[0]
    assert case.task == "check phone price"
    assert case.failure_class == FAILURE_STALE_WORLD_STATE
    assert case.expected["price"] == 5246000
    replayed = loop.suite.replay(
        lambda item: world.observe_price("phone-x1", "Tokopedia")["price"] == item.expected["price"]
    )
    assert replayed == [{"case_id": case.case_id, "task": "check phone price", "passed": True}]


def test_late_failure_repaired_through_outcome_watcher():
    """A K8 late failure is detected, classified, and repaired.

    A bill recorded as paid bounces: the watcher rechecks and
    finds "unpaid". The loop classifies it as stale world state
    and repairs it by re-reading the bill status, which now
    agrees with the fresh value.
    """
    watcher = OutcomeWatcher(learning_loop=LearningLoop())
    watched = watcher.watch(
        {
            "outcome_id": "evt-bill-1",
            "task": "pay PLN bill",
            "predicted": "paid",
            "observed": "paid",
            "user_id": "user-1",
            "task_id": "task-2",
        },
        recheck=lambda: "unpaid",
        delay=timedelta(hours=1),
        now=NOW,
    )
    assert watched.outcome_id == "evt-bill-1"
    checks = watcher.check_due(now=NOW + timedelta(hours=2))
    assert len(checks) == 1
    assert checks[0].status == "late_failure"

    loop = RepairLoop(ledger=CausalLedger(), reobserve=lambda signal: {"value": "unpaid"})
    outcome = loop.handle_late_failure(checks[0], now=NOW + timedelta(hours=2))

    assert outcome.status == "repaired"
    assert outcome.diagnosis.failure_class == FAILURE_STALE_WORLD_STATE
    assert len(outcome.attempts) == 1
    assert len(loop.suite) == 1
    assert loop.suite.cases()[0].task == "pay PLN bill"


def test_wrong_prediction_repair_uses_corrected_prior():
    """A miss with no fresh read is a wrong prior, fixed by it.

    The forecast says the price is 100,000 and reality says
    120,000, with no second read to blame the world. The loop
    classifies it as a wrong prediction and repairs it by
    asking the model for its corrected prediction, which K19
    already stored as the observed value.
    """
    learner, result = _surprise_result(predicted_price=100000, observed_price=120000)
    assert not result.correct
    loop = RepairLoop(
        ledger=CausalLedger(),
        repredict=lambda signal: learner.predict_for(ACTION)["outcome"],
    )
    outcome = loop.handle_surprise(result, now=NOW)

    assert outcome.status == "repaired"
    assert outcome.diagnosis.failure_class == FAILURE_WRONG_PREDICTION
    assert outcome.attempts[0].strategy == "correct_prior"
    assert outcome.hypothesis["effect"] == causal_effect_key("correct_prior")


def test_classify_each_signal_source():
    """Classification reads the source and the evidence, not text."""
    assert (
        classify(
            FailureSignal(source="late_failure", task="t", observed={"value": "x"})
        ).failure_class
        == FAILURE_STALE_WORLD_STATE
    )
    assert (
        classify(
            FailureSignal(source="tool_error", task="t", evidence={"error": "boom"})
        ).failure_class
        == FAILURE_TOOL_MISUSE
    )
    assert (
        classify(
            FailureSignal(
                source="postcondition",
                task="t",
                predicted={"done": True},
                observed={"done": False},
            )
        ).failure_class
        == "postcondition_failed"
    )
    assert (
        classify(
            FailureSignal(
                source="prediction_surprise",
                task="t",
                predicted={"price": 100},
                observed={"price": 120},
            )
        ).failure_class
        == FAILURE_WRONG_PREDICTION
    )
    assert (
        classify(
            FailureSignal(
                source="prediction_surprise",
                task="t",
                predicted={"price": 100},
                observed={"price": 120},
                fresh_observation={"price": 120},
            )
        ).failure_class
        == FAILURE_STALE_WORLD_STATE
    )
    # A fresh read that disagrees with the observed value proves
    # nothing about the world, so the prior stays the suspect.
    assert (
        classify(
            FailureSignal(
                source="prediction_surprise",
                task="t",
                predicted={"price": 100},
                observed={"price": 120},
                fresh_observation={"price": 100},
            )
        ).failure_class
        == FAILURE_WRONG_PREDICTION
    )


def test_two_failed_attempts_escalate_plainly():
    """Two failed repairs end in a plain escalation, never a third.

    The repairer always returns a wrong corrected value, so
    verification fails twice. The loop tries exactly twice, the
    hypothesis ends contradicted (two contradicting observations
    drop it from 0.5 below the 0.2 floor), and the escalation
    names the diagnosis and both attempts without apologising.
    """
    calls = []

    def always_wrong(signal, attempt):
        calls.append(attempt)
        return RepairOutcome(ok=True, detail="repaired, or so it claims", corrected={"price": 1})

    loop = RepairLoop(
        ledger=CausalLedger(),
        repairers={FAILURE_WRONG_PREDICTION: always_wrong},
    )
    signal = FailureSignal(
        source="prediction_surprise",
        task="check phone price",
        predicted={"price": 100},
        observed={"price": 120},
        action=dict(ACTION),
    )
    outcome = loop.handle(signal, now=NOW)

    assert outcome.status == "escalated"
    assert calls == [1, 2]
    assert len(outcome.attempts) == 2
    assert all(not item.verified for item in outcome.attempts)
    assert outcome.regression_case is None
    assert outcome.skill_candidate is None
    assert outcome.hypothesis is not None
    assert outcome.hypothesis["status"] == "contradicted"
    assert outcome.hypothesis["contradicting"] == 2
    escalation = outcome.escalation
    assert escalation is not None
    assert "wrong_prediction" in escalation.message or "prior was wrong" in escalation.message
    assert "attempt 1" in escalation.message
    assert "attempt 2" in escalation.message
    assert "sorry" not in escalation.message.lower()
    assert "apolog" not in escalation.message.lower()


def test_unknown_failure_escalates_without_attempts():
    """A failure no repairer knows escalates immediately."""
    loop = RepairLoop(ledger=CausalLedger())
    signal = FailureSignal(source="mystery_signal", task="do something odd")
    outcome = loop.handle(signal, now=NOW)

    assert outcome.status == "escalated"
    assert outcome.diagnosis.failure_class == FAILURE_UNKNOWN
    assert outcome.attempts == []
    assert outcome.hypothesis is None
    assert outcome.escalation is not None
    assert "no automatic repair was attempted" in outcome.escalation.message


def test_repair_without_world_access_fails_then_escalates():
    """A stale-state repair with no reobserve callable cannot run.

    The built-in repairer reports the missing callable as a
    failed attempt instead of raising, so the loop still spends
    its two attempts and escalates with the reason recorded.
    """
    loop = RepairLoop(ledger=CausalLedger())
    signal = FailureSignal(
        source="late_failure",
        task="pay PLN bill",
        predicted={"value": "paid"},
        observed={"value": "unpaid"},
    )
    outcome = loop.handle(signal, now=NOW)

    assert outcome.status == "escalated"
    assert len(outcome.attempts) == 2
    assert "no reobserve callable" in outcome.attempts[0].detail
    assert "no reobserve callable" in outcome.escalation.message


def test_repair_hypothesis_expires_when_unconfirmed():
    """A repair hypothesis is a claim with an expiry, not a fact.

    Repaired at NOW with a 1-day TTL, the hypothesis expires at
    NOW + 1 day; two days later the ledger prunes it as expired,
    so an unconfirmed repair never becomes permanent knowledge.
    """
    ledger = CausalLedger()
    loop = RepairLoop(
        ledger=ledger,
        hypothesis_ttl_days=1,
        reobserve=lambda signal: {"price": 120},
    )
    signal = FailureSignal(
        source="prediction_surprise",
        task="check price",
        predicted={"price": 100},
        observed={"price": 120},
        fresh_observation={"price": 120},
    )
    outcome = loop.handle(signal, now=NOW)
    assert outcome.status == "repaired"
    hypothesis_id = outcome.hypothesis["id"]
    # The confirming observation is stamped one second after the
    # loop started, so the refreshed expiry is NOW + 1s + 1 day.
    assert outcome.hypothesis["expires_at"] == (NOW + timedelta(days=1, seconds=1)).isoformat()
    stored = ledger.get(hypothesis_id)
    assert stored.is_expired_at(NOW + timedelta(days=2))
    assert ledger.prune_expired(now=NOW + timedelta(days=2)) == 1
    assert ledger.get(hypothesis_id).status == "expired"


def test_successful_repair_offers_skill_candidate_to_compiler():
    """The compiler hook runs when a skill and runs are present.

    A tool-misuse repair that returns replay runs plus a signal
    carrying a compiled skill offers the repair to the injected
    compiler; the result keeps the compiler's answer and the
    skill candidate the caller can feed to the compiler later.
    """

    class FakeCompiler:
        def __init__(self):
            self.calls = []

        def repair(self, skill, *, runs=None):
            self.calls.append({"skill": skill, "runs": runs})
            return {"action": "repaired", "skill": skill}

    compiler = FakeCompiler()
    skill = object()
    runs = [{"task": "buy rice", "steps": [{"tool": "buy", "args": {}}]}]
    loop = RepairLoop(
        ledger=CausalLedger(),
        skill_compiler=compiler,
        repairers={
            FAILURE_TOOL_MISUSE: lambda signal, attempt: RepairOutcome(
                ok=True,
                detail="retried with fixed arguments",
                corrected={"bought": True},
                runs=runs,
            )
        },
    )
    signal = FailureSignal(
        source="tool_error",
        task="buy rice",
        predicted={"bought": True},
        observed={"bought": True},
        evidence={"error": "unknown action: buy_rice"},
        skill=skill,
    )
    outcome = loop.handle(signal, now=NOW)

    assert outcome.status == "repaired"
    assert compiler.calls == [{"skill": skill, "runs": runs}]
    assert outcome.compiler_result == {"action": "repaired", "skill": skill}
    assert outcome.skill_candidate == {
        "task": "buy rice",
        "strategy": "retry_corrected_action",
        "failure_class": FAILURE_TOOL_MISUSE,
        "runs": runs,
    }


def test_regression_suite_replay_reports_failures():
    """The suite replays its cases and reports pass/fail per case."""
    loop = RepairLoop(
        ledger=CausalLedger(),
        reobserve=lambda signal: {"price": 120},
    )
    signal = FailureSignal(
        source="prediction_surprise",
        task="check price",
        predicted={"price": 100},
        observed={"price": 120},
        fresh_observation={"price": 120},
    )
    outcome = loop.handle(signal, now=NOW)
    assert outcome.status == "repaired"
    assert len(loop.suite) == 1
    assert loop.suite.replay(lambda case: True)[0]["passed"] is True
    assert loop.suite.replay(lambda case: False)[0]["passed"] is False


def test_signal_validation_rejects_empty_task():
    """A signal without a task is a caller bug, raised at once."""
    with pytest.raises(ValueError, match="task"):
        FailureSignal(source="tool_error", task="")
