"""Tests for the curriculum runner guardrails (plan section 7.5).

Daily caps per user and global, verified-only teaching, the
quarantine flag, the weekly skill cap, and holdout evaluation
before approval. All tests here are in-memory: a real
in-memory ``LearningLoop``, a ``CurriculumRunner`` with an
in-memory executor, and no direct database access in any test
body. (The shared tests/conftest.py autouse fixture still
creates schemas and truncates tables on collection; that is
unavoidable for any file under tests/.)
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.memory.curriculum_guardrails import (
    DEFAULT_MAX_NEW_SKILLS_PER_WEEK,
    DEFAULT_MAX_SPEND_RP_GLOBAL_PER_DAY,
    DEFAULT_MAX_SPEND_RP_PER_USER_PER_DAY,
    DEFAULT_MAX_TASKS_GLOBAL_PER_DAY,
    DEFAULT_MAX_TASKS_PER_USER_PER_DAY,
    DailyCaps,
    WeeklySkillCap,
    classify_experience,
)
from puspa.memory.learning_loop import LearningLoop
from puspa.scheduler.curriculum import CurriculumRunner


def _record(task, predicted="old answer", observed="new answer"):
    return {
        "task": task,
        "predicted": predicted,
        "observed": observed,
        "error": f"predicted differs for {task}",
        "context": {"source": "test"},
    }


def _gap_loop(*tasks):
    loop = LearningLoop()
    for task in tasks:
        loop.record_misprediction(_record(task))
    return loop


def _idle_runner(loop, **kwargs):
    kwargs.setdefault("is_idle", lambda: True)
    return CurriculumRunner(loop=loop, **kwargs)


def _verified_draft(loop, task="double"):
    skill = loop.draft_skill(task, code=lambda x: x * 2)
    loop.verify_skill(skill["id"], [{"args": [2], "expected": 4}])
    return skill


def _holdout_cases():
    # Deliberately different inputs from the verification cases.
    return [{"args": [5], "expected": 10}, {"args": [7], "expected": 14}]


# --- DailyCaps: unit behaviour -----------------------------------------


def test_daily_caps_defaults():
    caps = DailyCaps()
    assert caps.max_tasks_per_user_per_day == DEFAULT_MAX_TASKS_PER_USER_PER_DAY == 5
    assert caps.max_spend_rp_per_user_per_day == DEFAULT_MAX_SPEND_RP_PER_USER_PER_DAY == 10_000
    assert caps.max_tasks_global_per_day == DEFAULT_MAX_TASKS_GLOBAL_PER_DAY == 50
    assert caps.max_spend_rp_global_per_day == DEFAULT_MAX_SPEND_RP_GLOBAL_PER_DAY == 100_000


def test_daily_caps_check_and_record():
    caps = DailyCaps(max_tasks_per_user_per_day=1, max_spend_rp_per_user_per_day=3000)
    assert caps.check("user-1", day="2026-11-20", cost_ceiling_rp=2000)["allowed"] is True
    totals = caps.record("user-1", day="2026-11-20", cost_rp=1500)
    assert totals == {
        "user_runs": 1,
        "user_spent_rp": 1500,
        "global_runs": 1,
        "global_spent_rp": 1500,
    }
    assert caps.runs_today("user-1", "2026-11-20") == 1
    assert caps.spent_today("user-1", "2026-11-20") == 1500
    # The task cap is reached, and the spend cap would also block
    # a second task: 1500 spent + 2000 ceiling passes 3000.
    assert caps.check("user-1", day="2026-11-20", cost_ceiling_rp=2000)["status"] in (
        "user_daily_cap_reached",
        "user_daily_spend_reached",
    )


def test_daily_caps_spend_reservation_blocks_before_the_cap_is_hit():
    # 1500 spent of a 3000 cap: the day is not over budget yet,
    # but no new task may start because its 2000 ceiling would
    # take the day to 3500.
    caps = DailyCaps(max_tasks_per_user_per_day=5, max_spend_rp_per_user_per_day=3000)
    caps.record("user-1", day="2026-11-20", cost_rp=1500)
    denied = caps.check("user-1", day="2026-11-20", cost_ceiling_rp=2000)
    assert denied["allowed"] is False
    assert denied["status"] == "user_daily_spend_reached"
    # A cheaper ceiling still fits.
    assert caps.check("user-1", day="2026-11-20", cost_ceiling_rp=1000)["allowed"] is True


def test_daily_caps_reset_on_a_new_day():
    caps = DailyCaps(max_tasks_per_user_per_day=1)
    caps.record("user-1", day="2026-11-20", cost_rp=500)
    assert caps.check("user-1", day="2026-11-20", cost_ceiling_rp=2000)["allowed"] is False
    assert caps.check("user-1", day="2026-11-21", cost_ceiling_rp=2000)["allowed"] is True


def test_daily_caps_reject_negative_limits_and_costs():
    with pytest.raises(ValueError):
        DailyCaps(max_tasks_per_user_per_day=-1)
    caps = DailyCaps()
    with pytest.raises(ValueError):
        caps.record("user-1", cost_rp=-5)
    with pytest.raises(ValueError):
        caps.check("user-1", cost_ceiling_rp=-5)


# --- Daily caps through the runner --------------------------------------


def test_per_user_daily_task_cap_holds_across_runner_instances():
    caps = DailyCaps(max_tasks_per_user_per_day=1)
    first = _idle_runner(_gap_loop("compare laptop prices across stores"), caps=caps)
    assert first.run_once()["status"] == "completed"
    # A second runner for the same user shares the ledger: the
    # cap holds even though the runner instance is fresh.
    second = _idle_runner(_gap_loop("research and summarize 5 articles"), caps=caps)
    outcome = second.run_once()
    assert outcome["status"] == "user_daily_cap_reached"
    assert outcome["ran"] is False
    assert outcome["runs_today"] == 0
    assert second.loop.propose_next_task() is not None  # gap untouched


def test_global_daily_task_cap_holds_across_users():
    caps = DailyCaps(max_tasks_global_per_day=1)
    first = _idle_runner(
        _gap_loop("compare laptop prices across stores"), user_id="user-a", caps=caps
    )
    assert first.run_once()["status"] == "completed"
    second = _idle_runner(
        _gap_loop("research and summarize 5 articles"), user_id="user-b", caps=caps
    )
    outcome = second.run_once()
    assert outcome["status"] == "global_daily_cap_reached"
    assert outcome["ran"] is False


def test_per_user_daily_spend_cap_blocks_the_next_task():
    caps = DailyCaps(max_spend_rp_per_user_per_day=2500)
    loop = _gap_loop("compare laptop prices across stores")

    def pricey_executor(name, inputs):
        return {"type": "result", "value": None, "cost_rp": 1500}

    runner = _idle_runner(loop, executor=pricey_executor, caps=caps)
    first = runner.run_once()
    assert first["status"] == "failed"  # second step would pass Rp2.000
    assert first["cost_rp"] == 1500
    assert caps.spent_today("system") == 1500
    # 1500 spent + 2000 ceiling passes the 2500 cap.
    second = runner.run_once()
    assert second["status"] == "user_daily_spend_reached"
    assert second["ran"] is False
    assert caps.spent_today("system") == 1500  # denial spent nothing


def test_global_daily_spend_cap_blocks_another_user():
    caps = DailyCaps(max_spend_rp_global_per_day=2500)

    def pricey_executor(name, inputs):
        return {"type": "result", "value": None, "cost_rp": 1500}

    first = _idle_runner(
        _gap_loop("compare laptop prices across stores"),
        user_id="user-a",
        executor=pricey_executor,
        caps=caps,
    )
    assert first.run_once()["status"] == "failed"
    assert caps.global_spent_today() == 1500
    second = _idle_runner(
        _gap_loop("research and summarize 5 articles"),
        user_id="user-b",
        executor=pricey_executor,
        caps=caps,
    )
    outcome = second.run_once()
    assert outcome["status"] == "global_daily_spend_reached"
    assert outcome["ran"] is False


def test_cap_denial_consumes_no_daily_run():
    caps = DailyCaps(max_tasks_per_user_per_day=1)
    runner = _idle_runner(
        _gap_loop("compare laptop prices across stores", "research and summarize 5 articles"),
        caps=caps,
    )
    assert runner.run_once()["status"] == "completed"
    # The second run proposes a different task, so it reaches
    # the cap check instead of the already-completed dedup.
    assert runner.run_once()["status"] == "user_daily_cap_reached"
    assert runner.runs_today() == 1
    assert caps.runs_today("system") == 1


# --- verified-only teaching: the runner never teaches itself ----------


def test_unverified_completion_moves_on_to_the_next_gap():
    loop = _gap_loop("compare laptop prices across stores", "research and summarize 5 articles")
    runner = _idle_runner(loop)
    first = runner.run_once()
    assert first["status"] == "completed"
    assert first["proposal"]["task"] == "compare laptop prices across stores"
    # The gap stays open (unverified), but the runner does not
    # re-run the same task today: it moves on to the next gap.
    second = runner.run_once()
    assert second["status"] == "completed"
    assert second["proposal"]["task"] == "research and summarize 5 articles"
    # Both tasks ran today, so the third tick reports the
    # already-completed task instead of running it again.
    third = runner.run_once()
    assert third["status"] == "already_completed"
    assert third["ran"] is False


def test_failed_run_is_quarantined_and_never_teaches():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(
        loop,
        executor=lambda name, inputs: {"type": "wrong", "value": None},
    )
    outcome = runner.run_once()
    assert outcome["status"] == "failed"
    assert outcome["ran"] is True
    # Nothing was learned from the runner's own failure: the
    # correction, the gap, and the prediction are unchanged.
    correction = loop.correction_for("compare laptop prices across stores")
    assert correction["count"] == 1
    assert correction["observed"] == "new answer"
    assert loop.gaps["compare laptop prices across stores"]["mispredictions"] == 1
    assert loop.predict("compare laptop prices across stores") == "new answer"
    # The failure is stored, quarantined, for user review.
    quarantined = loop.list_quarantined("compare laptop prices across stores")
    assert len(quarantined) == 1
    assert quarantined[0]["outcome"] == "failed"
    assert quarantined[0]["quarantine_reasons"] == ["unverified"]


def test_completed_run_without_verifier_is_quarantined_and_keeps_the_gap():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(loop)
    outcome = runner.run_once()
    assert outcome["status"] == "completed"
    assert outcome["verified"] is False
    # The runner's own say-so clears nothing: the gap stays
    # open and the curriculum proposes the retry again.
    assert "compare laptop prices across stores" in loop.gaps
    assert loop.propose_next_task()["task"] == "compare laptop prices across stores"
    quarantined = loop.list_quarantined("compare laptop prices across stores")
    assert len(quarantined) == 1
    assert quarantined[0]["outcome"] == "completed"
    assert quarantined[0]["verification"] == "quarantined"


def test_completed_run_with_a_real_verifier_clears_the_gap():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(loop, verifier=lambda result: (True, ""))
    outcome = runner.run_once()
    assert outcome["status"] == "completed"
    assert outcome["verified"] is True
    assert "compare laptop prices across stores" not in loop.gaps
    assert loop.propose_next_task() is None
    verified = [e for e in loop.experiences if e.get("verification") == "verified"]
    assert len(verified) == 1
    assert verified[0]["outcome"] == "completed"
    assert loop.list_quarantined() == []


def test_confirming_a_quarantined_failure_teaches():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(
        loop,
        executor=lambda name, inputs: {"type": "wrong", "value": None},
    )
    assert runner.run_once()["status"] == "failed"
    entry = loop.list_quarantined("compare laptop prices across stores")[0]
    released = loop.confirm_experience(entry["id"], by="user-1")
    assert released["quarantined"] is False
    assert released["verification"] == "user_confirmed"
    assert released["confirmed_by"] == "user-1"
    correction = loop.correction_for("compare laptop prices across stores")
    assert correction["count"] == 2
    assert "failed" in correction["observed"]
    assert loop.gaps["compare laptop prices across stores"]["mispredictions"] == 2
    assert loop.list_quarantined() == []


def test_confirming_a_quarantined_completion_clears_the_gap():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(loop)
    assert runner.run_once()["status"] == "completed"
    assert "compare laptop prices across stores" in loop.gaps
    entry = loop.list_quarantined("compare laptop prices across stores")[0]
    loop.confirm_experience(entry["id"], by="user-1")
    assert "compare laptop prices across stores" not in loop.gaps
    assert loop.propose_next_task() is None


def test_confirm_rejects_unknown_or_unquarantined_entries():
    loop = _gap_loop("compare laptop prices across stores")
    with pytest.raises(KeyError):
        loop.confirm_experience("missing")
    plain = loop.experiences[0]
    assert plain.get("quarantined") is not True
    with pytest.raises(ValueError):
        loop.confirm_experience(plain["id"])


# --- quarantine at the loop seam -----------------------------------------


def test_unverified_misprediction_is_quarantined_and_never_teaches():
    loop = LearningLoop()
    result = loop.record_misprediction(_record("check_pln"), verified=False)
    assert result["quarantined"] is True
    assert result["quarantine_reasons"] == ["unverified"]
    assert loop.correction_for("check_pln") is None
    assert loop.predict("check_pln", default="bill 50000") == "bill 50000"
    assert loop.propose_next_task() is None
    assert len(loop.list_quarantined("check_pln")) == 1
    # The user releases it: only then does it teach.
    entry = loop.list_quarantined("check_pln")[0]
    loop.confirm_experience(entry["id"], by="user-1")
    assert loop.predict("check_pln", default="bill 50000") == "new answer"
    assert loop.propose_next_task()["task"] == "check_pln"


def test_orphan_number_misprediction_is_quarantined_even_when_verified():
    loop = LearningLoop()
    result = loop.record_misprediction(
        _record("check_pln", predicted="bill 50000", observed="bill Rp75000"),
        verified=True,
    )
    assert result["quarantined"] is True
    assert result["quarantine_reasons"] == ["orphan_numbers"]
    assert loop.correction_for("check_pln") is None
    assert loop.predict("check_pln", default="bill 50000") == "bill 50000"


def test_classify_experience_marks_orphan_numbers_and_unverified():
    assert classify_experience(predicted="old", observed="new") == {
        "verification": "world_observation",
        "quarantined": False,
        "quarantine_reasons": [],
    }
    assert (
        classify_experience(predicted="old", observed="new", verified=True)["verification"]
        == "verified"
    )
    flagged = classify_experience(predicted="bill 50000", observed="bill Rp75000")
    assert flagged["quarantined"] is True
    assert flagged["quarantine_reasons"] == ["orphan_numbers"]
    flagged = classify_experience(predicted="old", observed="new", verified=False)
    assert flagged["quarantined"] is True
    assert flagged["quarantine_reasons"] == ["unverified"]


def test_user_correction_with_orphan_number_is_quarantined():
    loop = LearningLoop()
    result = loop.record_feedback("check_pln", "wrong", correction="bill Rp75000")
    assert result["status"] == "quarantined"
    assert result["quarantine_reasons"] == ["orphan_numbers"]
    # Held, not applied: the prediction is unchanged.
    assert loop.correction_for("check_pln") is None
    assert loop.predict("check_pln", default="bill 50000") == "bill 50000"
    # The "wrong" verdict itself is user-confirmed, so the gap
    # stands even though the figure is held.
    assert loop.gaps["check_pln"]["mispredictions"] == 1
    assert len(loop.list_quarantined("check_pln")) == 1
    # The user releases the held correction: only then it applies.
    entry = loop.list_quarantined("check_pln")[0]
    loop.confirm_experience(entry["id"], by="user-1")
    assert loop.predict("check_pln", default="bill 50000") == "bill Rp75000"
    assert loop.gaps["check_pln"]["mispredictions"] == 1  # counted once


def test_plain_user_correction_still_applies():
    loop = LearningLoop()
    result = loop.record_feedback("check_pln", "wrong", correction="bill 75000")
    assert result["status"] == "applied"
    assert loop.predict("check_pln", default="bill 50000") == "bill 75000"


# --- weekly skill cap ------------------------------------------------------


def test_weekly_skill_cap_defaults_to_three():
    assert DEFAULT_MAX_NEW_SKILLS_PER_WEEK == 3
    loop = LearningLoop()
    assert loop.max_new_skills_per_week == 3
    assert loop.new_skills_remaining_this_week() == 3


def test_weekly_skill_cap_blocks_the_fourth_new_skill():
    loop = LearningLoop()
    for name in ("skill_a", "skill_b", "skill_c"):
        loop.draft_skill(name, code=lambda x: x)
    assert loop.skills_drafted_this_week() == 3
    assert loop.new_skills_remaining_this_week() == 0
    with pytest.raises(ValueError, match="weekly learned-skill cap reached"):
        loop.draft_skill("skill_d", code=lambda x: x)


def test_weekly_skill_cap_resets_in_a_new_week():
    loop = LearningLoop()
    now = datetime.now(UTC)
    for name in ("skill_a", "skill_b", "skill_c"):
        loop.draft_skill(name, code=lambda x: x, now=now)
    with pytest.raises(ValueError):
        loop.draft_skill("skill_d", code=lambda x: x, now=now)
    next_week = now + timedelta(days=7)
    loop.draft_skill("skill_d", code=lambda x: x, now=next_week)
    assert loop.skills_drafted_this_week(next_week) == 1


def test_weekly_skill_cap_is_configurable_and_repairs_do_not_count():
    loop = LearningLoop(max_new_skills_per_week=1)
    loop.draft_skill("skill_a", code=lambda x: x)
    with pytest.raises(ValueError):
        loop.draft_skill("skill_b", code=lambda x: x)
    # A repair maintains an existing skill, it does not learn a
    # new one, so it is exempt from the cap.
    repair = loop.draft_skill("skill_a", code=lambda x: x * 2, is_repair=True)
    assert repair["status"] == "draft"
    assert loop.skills_drafted_this_week() == 1


def test_weekly_skill_cap_unit():
    cap = WeeklySkillCap(max_per_week=2)
    assert cap.record() == 1
    assert cap.remaining_this_week() == 1
    assert cap.record() == 2
    with pytest.raises(ValueError):
        cap.record()
    with pytest.raises(ValueError):
        WeeklySkillCap(max_per_week=-1)


# --- holdout evaluation before approval ------------------------------------


def test_holdout_evaluation_records_a_pass():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    result = loop.evaluate_holdout(skill["id"], _holdout_cases())
    assert result == {
        "skill_id": skill["id"],
        "passed": 2,
        "failed": 0,
        "verified": True,
        "details": [
            {"case": {"args": [5], "expected": 10}, "result": 10, "passed": True},
            {"case": {"args": [7], "expected": 14}, "result": 14, "passed": True},
        ],
    }
    assert loop.get_skill(skill["id"])["holdout_passed"] is True


def test_submit_for_approval_requires_a_passed_holdout():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    with pytest.raises(ValueError, match="holdout"):
        loop.submit_for_approval(skill["id"])
    with pytest.raises(ValueError, match="holdout"):
        loop.activate_skill(skill["id"], approved_by="user-1")


def test_submit_for_approval_with_failing_holdout_is_refused():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    with pytest.raises(ValueError, match="holdout"):
        loop.submit_for_approval(skill["id"], [{"args": [5], "expected": 11}])
    assert loop.get_skill(skill["id"])["holdout_passed"] is False
    assert loop.get_skill(skill["id"])["submitted_for_approval"] is False
    # The skill stays a draft and can never be activated.
    with pytest.raises(ValueError, match="holdout"):
        loop.activate_skill(skill["id"], approved_by="user-1")


def test_submit_for_approval_then_activate():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    submission = loop.submit_for_approval(skill["id"], _holdout_cases())
    assert submission["status"] == "submitted_for_approval"
    assert submission["skill_id"] == skill["id"]
    assert submission["holdout"]["passed"] == 2
    assert loop.get_skill(skill["id"])["submitted_for_approval"] is True
    activated = loop.activate_skill(skill["id"], approved_by="user-1")
    assert activated["status"] == "active"
    assert loop.find_reusable_skill("double") is not None


def test_submit_for_approval_reuses_a_recorded_passing_holdout():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    loop.evaluate_holdout(skill["id"], _holdout_cases())
    submission = loop.submit_for_approval(skill["id"])
    assert submission["status"] == "submitted_for_approval"


def test_holdout_must_differ_from_verification_cases():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    with pytest.raises(ValueError, match="must differ"):
        loop.evaluate_holdout(skill["id"], [{"args": [2], "expected": 4}])
    with pytest.raises(ValueError, match="must differ"):
        loop.submit_for_approval(skill["id"], [{"args": [2], "expected": 4}])


def test_holdout_rejects_unverified_and_rejected_skills():
    loop = LearningLoop()
    draft = loop.draft_skill("double", code=lambda x: x * 2)
    with pytest.raises(ValueError, match="verified"):
        loop.submit_for_approval(draft["id"], _holdout_cases())
    skill = _verified_draft(loop, task="other")
    loop.reject_skill(skill["id"], reason="wrong approach")
    with pytest.raises(ValueError, match="rejected"):
        loop.submit_for_approval(skill["id"], _holdout_cases())


def test_holdout_fails_closed_for_string_code_without_executor():
    loop = LearningLoop()
    skill = loop.draft_skill("double", code="def run(x): return x * 2")
    loop.verify_skill(
        skill["id"],
        [{"args": [2], "expected": 4}],
        executor=lambda code, case: case["args"][0] * 2,
    )
    result = loop.evaluate_holdout(skill["id"], _holdout_cases())
    assert result["verified"] is False
    assert result["failed"] == 2
    with pytest.raises(ValueError, match="holdout"):
        loop.submit_for_approval(skill["id"])


def test_holdout_validation_errors():
    loop = LearningLoop()
    with pytest.raises(KeyError):
        loop.evaluate_holdout("missing", _holdout_cases())
    with pytest.raises(KeyError):
        loop.submit_for_approval("missing")
    skill = _verified_draft(loop)
    with pytest.raises(ValueError):
        loop.evaluate_holdout(skill["id"], [])
