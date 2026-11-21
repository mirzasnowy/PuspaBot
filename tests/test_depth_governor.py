"""Tests for the depth governor (Kosmos Brain v2, Phase 2 "Fast").

Three layers, matching the wiring:

- Pure depth picking (in-memory, deterministic, no database):
  reflex needs a high-confidence skill match plus a reversible,
  low-stakes, low-uncertainty step; deep fires on high
  uncertainty times stakes, on no evidence, on an irreversible
  high-stakes step, or on a previous failure; deliberate is the
  default.
- Escalation (in-memory): a failed verification retries exactly
  once, one level up, carrying the failure context; a failed
  deep step and a second failure after the one retry have no
  further retry.
- Compilation (in-memory store, plus one database test): three
  consecutive deep successes compile the task into a reflex
  skill that passes its regression case; a failed regression
  stores nothing; after compiling, the same task picks reflex.
"""

from __future__ import annotations

import pytest

from puspa.governor import (
    COMPILE_AFTER_SUCCESSES,
    CompiledSkill,
    Depth,
    DepthDecision,
    DepthGovernor,
    ProceduralSkillStore,
    RegressionCase,
    StepSignal,
)
from puspa.memory import procedural as procedural_store

TASK_KEY = "check-price-beras"

STEPS = [
    {"tool": "check_price", "arguments": {"product": "beras", "store": "tokopedia"}},
]


def _price_runner(step: dict) -> dict:
    """Fake tool registry: the price check always finds Rp100."""
    assert step["tool"] == "check_price"
    return {"ok": True, "price": 100}


def _regression() -> RegressionCase:
    """The regression case for the price-check steps."""
    return RegressionCase.from_steps(STEPS, {"price": 100})


def _compile(governor: DepthGovernor, task_key: str = TASK_KEY) -> CompiledSkill | None:
    """Feed three deep successes; return whatever the third compiles."""
    compiled = None
    for _ in range(COMPILE_AFTER_SUCCESSES):
        compiled = governor.record_outcome(
            task_key,
            depth=Depth.DEEP,
            succeeded=True,
            steps=STEPS,
            regression=_regression(),
            step_runner=_price_runner,
        )
    return compiled


# ---------------------------------------------------------------------------
# Pure depth picking
# ---------------------------------------------------------------------------


def test_reflex_when_skill_matches_and_step_is_safe():
    governor = DepthGovernor()
    decision = governor.decide(
        StepSignal(
            uncertainty=0.1,
            stakes=0.1,
            reversible=True,
            skill_match=0.95,
            skill_id="skill:check-price-beras",
        )
    )
    assert decision.depth is Depth.REFLEX
    assert decision.skill_id == "skill:check-price-beras"
    assert decision.risk == pytest.approx(0.01)


def test_no_reflex_when_step_is_irreversible():
    governor = DepthGovernor()
    decision = governor.decide(
        StepSignal(
            uncertainty=0.1,
            stakes=0.1,
            reversible=False,
            skill_match=0.95,
            skill_id="skill:check-price-beras",
        )
    )
    # Irreversible at low stakes is not deep either: deliberate,
    # where pre-flight handles the irreversibility.
    assert decision.depth is Depth.DELIBERATE
    assert decision.skill_id is None


def test_no_reflex_when_stakes_high_despite_skill_match():
    governor = DepthGovernor()
    decision = governor.decide(
        StepSignal(
            uncertainty=0.1,
            stakes=0.9,
            reversible=True,
            skill_match=0.95,
            skill_id="skill:check-price-beras",
        )
    )
    assert decision.depth is Depth.DELIBERATE


def test_no_reflex_when_uncertainty_high_despite_skill_match():
    governor = DepthGovernor()
    decision = governor.decide(
        StepSignal(
            uncertainty=0.9,
            stakes=0.1,
            reversible=True,
            skill_match=0.95,
            skill_id="skill:check-price-beras",
        )
    )
    assert decision.depth is Depth.DELIBERATE


def test_deep_on_high_uncertainty_times_stakes():
    governor = DepthGovernor()
    decision = governor.decide(StepSignal(uncertainty=0.9, stakes=0.9))
    assert decision.depth is Depth.DEEP
    assert decision.risk == pytest.approx(0.81)
    assert "0.81" in decision.reason


def test_deep_when_no_evidence():
    governor = DepthGovernor()
    decision = governor.decide(StepSignal(uncertainty=0.1, stakes=0.1, has_evidence=False))
    assert decision.depth is Depth.DEEP
    assert "no evidence" in decision.reason


def test_deep_when_irreversible_and_high_stakes():
    governor = DepthGovernor()
    decision = governor.decide(
        StepSignal(uncertainty=0.1, stakes=0.8, reversible=False),
    )
    assert decision.depth is Depth.DEEP
    assert "irreversible" in decision.reason


def test_deliberate_is_the_default():
    governor = DepthGovernor()
    decision = governor.decide(StepSignal())
    assert decision.depth is Depth.DELIBERATE
    assert decision.risk == pytest.approx(0.25)


def test_previous_failure_forces_deep_even_with_skill_match():
    governor = DepthGovernor()
    decision = governor.decide(
        StepSignal(
            uncertainty=0.1,
            stakes=0.1,
            reversible=True,
            skill_match=0.95,
            skill_id="skill:check-price-beras",
            previous_failure=True,
        )
    )
    assert decision.depth is Depth.DEEP
    assert "previous attempt failed" in decision.reason


@pytest.mark.parametrize(
    "signal_kwargs",
    [
        {"uncertainty": 1.5},
        {"uncertainty": -0.1},
        {"stakes": 2.0},
        {"skill_match": 2.0},
        {"skill_id": "skill:x"},  # a skill id without a match confidence
        {"skill_match": 0.9, "skill_id": "  "},  # a blank skill id
    ],
)
def test_invalid_signal_values_are_rejected(signal_kwargs):
    with pytest.raises(ValueError):
        StepSignal(**signal_kwargs)


def test_decision_serializes_for_the_ledger():
    decision = DepthDecision(depth=Depth.REFLEX, reason="skill matched", risk=0.01)
    data = decision.to_dict()
    assert data == {
        "depth": "reflex",
        "reason": "skill matched",
        "risk": 0.01,
        "skill_id": None,
        "failure_context": None,
        "retried": False,
    }


# ---------------------------------------------------------------------------
# Escalation: exactly one retry, one level up
# ---------------------------------------------------------------------------


def test_failed_reflex_retries_as_deliberate_without_the_skill():
    governor = DepthGovernor()
    first = governor.decide(
        StepSignal(
            uncertainty=0.1,
            stakes=0.1,
            skill_match=0.95,
            skill_id="skill:check-price-beras",
        )
    )
    assert first.depth is Depth.REFLEX
    retry = governor.escalate(first, failure="postcondition failed: price missing")
    assert retry is not None
    assert retry.depth is Depth.DELIBERATE
    assert retry.retried is True
    assert retry.failure_context == "postcondition failed: price missing"
    # The replay failed, so the retry replans instead of replaying
    # the same skill.
    assert retry.skill_id is None
    assert "reflex" in retry.reason and "deliberate" in retry.reason


def test_failed_deliberate_retries_as_deep_carrying_the_failure():
    governor = DepthGovernor()
    first = governor.decide(StepSignal())
    assert first.depth is Depth.DELIBERATE
    retry = governor.escalate(first, failure="verifier found the total wrong")
    assert retry is not None
    assert retry.depth is Depth.DEEP
    assert retry.retried is True
    assert retry.failure_context == "verifier found the total wrong"


def test_failed_deep_has_no_further_retry():
    governor = DepthGovernor()
    deep = governor.decide(StepSignal(uncertainty=0.9, stakes=0.9))
    assert deep.depth is Depth.DEEP
    assert governor.escalate(deep, failure="still wrong at deep") is None


def test_second_failure_after_the_one_retry_has_no_further_retry():
    governor = DepthGovernor()
    first = governor.decide(StepSignal())
    retry = governor.escalate(first, failure="first verification failed")
    assert retry is not None and retry.depth is Depth.DEEP
    assert governor.escalate(retry, failure="second verification failed") is None


def test_escalate_needs_a_failure_description():
    governor = DepthGovernor()
    first = governor.decide(StepSignal())
    with pytest.raises(ValueError):
        governor.escalate(first, failure="  ")


# ---------------------------------------------------------------------------
# Compilation: deep once, reflex afterwards
# ---------------------------------------------------------------------------


def test_deep_successes_below_threshold_compile_nothing():
    governor = DepthGovernor()
    for _ in range(COMPILE_AFTER_SUCCESSES - 1):
        compiled = governor.record_outcome(
            TASK_KEY,
            depth=Depth.DEEP,
            succeeded=True,
            steps=STEPS,
            regression=_regression(),
            step_runner=_price_runner,
        )
        assert compiled is None
    assert governor.streak(TASK_KEY) == COMPILE_AFTER_SUCCESSES - 1
    assert governor.find_skill(TASK_KEY) is None


def test_third_deep_success_compiles_a_reflex_skill():
    governor = DepthGovernor()
    compiled = _compile(governor)
    assert compiled is not None
    assert compiled.task_key == TASK_KEY
    assert compiled.skill_id == f"skill:{TASK_KEY}"
    assert compiled.status == "trial"
    assert compiled.steps == STEPS
    assert governor.find_skill(TASK_KEY) is not None
    assert governor.streak(TASK_KEY) == 0


def test_compiled_skill_passes_its_regression_case():
    skill = CompiledSkill(task_key=TASK_KEY, steps=STEPS, regression=_regression())
    result = skill.run_regression(_price_runner)
    assert result.passed is True
    assert result.steps_run == len(STEPS)
    assert result.last_result == {"ok": True, "price": 100}


def test_regression_fails_on_a_wrong_postcondition():
    case = RegressionCase.from_steps(STEPS, {"price": 200})
    skill = CompiledSkill(task_key=TASK_KEY, steps=STEPS, regression=case)
    result = skill.run_regression(_price_runner)
    assert result.passed is False
    assert "price" in result.detail
    assert result.last_result == {"ok": True, "price": 100}


def test_regression_fails_on_a_non_dict_step_result():
    skill = CompiledSkill(task_key=TASK_KEY, steps=STEPS, regression=_regression())
    result = skill.run_regression(lambda step: "not a dict")
    assert result.passed is False
    assert "non-dict" in result.detail


def test_failed_regression_blocks_compilation_and_stores_nothing():
    governor = DepthGovernor()
    compiled = None
    for _ in range(COMPILE_AFTER_SUCCESSES):
        compiled = governor.record_outcome(
            TASK_KEY,
            depth=Depth.DEEP,
            succeeded=True,
            steps=STEPS,
            regression=RegressionCase.from_steps(STEPS, {"price": 200}),
            step_runner=_price_runner,
        )
    assert compiled is None
    assert governor.find_skill(TASK_KEY) is None
    # The streak stays full: a later call with a passing
    # regression can still compile the same task.
    assert governor.streak(TASK_KEY) == COMPILE_AFTER_SUCCESSES


def test_failure_breaks_the_deep_streak():
    governor = DepthGovernor()
    for _ in range(2):
        assert (
            governor.record_outcome(
                TASK_KEY,
                depth=Depth.DEEP,
                succeeded=True,
                steps=STEPS,
                regression=_regression(),
                step_runner=_price_runner,
            )
            is None
        )
    assert governor.streak(TASK_KEY) == 2
    assert governor.record_outcome(TASK_KEY, depth=Depth.DEEP, succeeded=False) is None
    assert governor.streak(TASK_KEY) == 0
    compiled = _compile(governor)
    assert compiled is not None


def test_non_deep_successes_do_not_compile():
    governor = DepthGovernor()
    for _ in range(COMPILE_AFTER_SUCCESSES + 2):
        assert (
            governor.record_outcome(
                TASK_KEY,
                depth=Depth.DELIBERATE,
                succeeded=True,
                steps=STEPS,
                regression=_regression(),
                step_runner=_price_runner,
            )
            is None
        )
    assert governor.streak(TASK_KEY) == 0
    assert governor.find_skill(TASK_KEY) is None


def test_compile_without_steps_regression_or_runner_compiles_nothing():
    governor = DepthGovernor()
    for _ in range(COMPILE_AFTER_SUCCESSES):
        assert governor.record_outcome(TASK_KEY, depth=Depth.DEEP, succeeded=True) is None
    assert governor.streak(TASK_KEY) == COMPILE_AFTER_SUCCESSES
    assert governor.find_skill(TASK_KEY) is None
    # The missing pieces arrive on the next success: it compiles then.
    compiled = governor.record_outcome(
        TASK_KEY,
        depth=Depth.DEEP,
        succeeded=True,
        steps=STEPS,
        regression=_regression(),
        step_runner=_price_runner,
    )
    assert compiled is not None


def test_after_compile_the_same_task_picks_reflex():
    governor = DepthGovernor()
    compiled = _compile(governor)
    assert compiled is not None
    decision = governor.decide(StepSignal(uncertainty=0.1, stakes=0.1), task_key=TASK_KEY)
    assert decision.depth is Depth.REFLEX
    assert decision.skill_id == compiled.skill_id


def test_different_task_keys_count_separately():
    governor = DepthGovernor()
    for _ in range(COMPILE_AFTER_SUCCESSES - 1):
        governor.record_outcome(
            "check-price-beras",
            depth=Depth.DEEP,
            succeeded=True,
            steps=STEPS,
            regression=_regression(),
            step_runner=_price_runner,
        )
        governor.record_outcome(
            "check-price-gula",
            depth=Depth.DEEP,
            succeeded=True,
            steps=STEPS,
            regression=_regression(),
            step_runner=_price_runner,
        )
    assert governor.streak("check-price-beras") == COMPILE_AFTER_SUCCESSES - 1
    assert governor.streak("check-price-gula") == COMPILE_AFTER_SUCCESSES - 1
    assert governor.find_skill("check-price-beras") is None
    assert governor.find_skill("check-price-gula") is None


def test_regression_case_rejects_empty_steps():
    with pytest.raises(ValueError):
        RegressionCase.from_steps([], {"price": 100})


def test_depth_names_are_stable_strings():
    assert Depth.REFLEX.value == "reflex"
    assert Depth.DELIBERATE.value == "deliberate"
    assert Depth.DEEP.value == "deep"
    assert Depth.REFLEX.one_level_up() is Depth.DELIBERATE
    assert Depth.DELIBERATE.one_level_up() is Depth.DEEP
    assert Depth.DEEP.one_level_up() is None


# ---------------------------------------------------------------------------
# Durable store: the compiled skill lands in the procedural layer
# ---------------------------------------------------------------------------


def test_compiled_skill_lands_in_the_procedural_store(test_dsn, make_user):
    user = make_user()
    store = ProceduralSkillStore(user_id=user["user_id"], dsn=test_dsn)
    governor = DepthGovernor(skill_store=store)
    compiled = _compile(governor)
    assert compiled is not None
    assert compiled.skill_id is not None
    assert compiled.status == "trial"
    rows = [
        row
        for row in procedural_store.list_procedures(user["user_id"], dsn=test_dsn)
        if row["service"] == f"compiled:{TASK_KEY}"
    ]
    assert len(rows) == 1
    assert rows[0]["status"] == "trial"
    assert rows[0]["successful_runs"] == 1
    assert rows[0]["step"] == STEPS


def test_stored_skill_round_trips_and_passes_regression_again(test_dsn, make_user):
    user = make_user()
    store = ProceduralSkillStore(user_id=user["user_id"], dsn=test_dsn)
    governor = DepthGovernor(skill_store=store)
    compiled = _compile(governor)
    assert compiled is not None
    found = store.find(TASK_KEY)
    assert found is not None
    assert found.skill_id == compiled.skill_id
    assert found.steps == STEPS
    assert found.regression.expect == {"price": 100}
    assert found.run_regression(_price_runner).passed is True
    # The governor sees the stored skill too: the task picks reflex.
    decision = governor.decide(StepSignal(uncertainty=0.1, stakes=0.1), task_key=TASK_KEY)
    assert decision.depth is Depth.REFLEX


def test_procedure_without_a_regression_entry_is_not_a_compiled_skill(test_dsn, make_user):
    user = make_user()
    procedural_store.record_successful_task(
        user["user_id"],
        f"compiled:{TASK_KEY}",
        STEPS,
        agent_id=user["agent_id"],
        dsn=test_dsn,
    )
    store = ProceduralSkillStore(user_id=user["user_id"], dsn=test_dsn)
    assert store.find(TASK_KEY) is None
