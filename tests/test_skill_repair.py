"""Tests for skill repair + procedure change (one learning loop).

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/, and
is noted in the Brain-phase report.)
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.memory.learning_loop import LearningLoop
from puspa.memory.skill_repair import (
    InMemoryProcedureStore,
    ProcedureUpdater,
    SkillRepairer,
    diagnose_skill_failure,
    lookup_rewriter,
)


def _broken_skill(loop, task="double"):
    """A draft skill whose code is wrong: adds 1, tests expect x2."""
    skill = loop.draft_skill(task, code=lambda x: x + 1)
    loop.verify_skill(
        skill["id"],
        [
            {"args": [2], "expected": 4},
            {"args": [3], "expected": 6},
        ],
    )
    return skill


def _cases():
    return [{"args": [2], "expected": 4}, {"args": [3], "expected": 6}]


# --- diagnosis ------------------------------------------------------------


def test_diagnose_classifies_failing_cases():
    loop = LearningLoop()
    skill = _broken_skill(loop)
    diagnosis = diagnose_skill_failure(loop, skill["id"])
    assert diagnosis["task"] == "double"
    assert diagnosis["total"] == 2
    assert diagnosis["passed"] == 0
    assert diagnosis["failed"] == 2
    assert [f["kind"] for f in diagnosis["failures"]] == ["wrong_output", "wrong_output"]
    assert diagnosis["failures"][0]["expected"] == 4
    assert diagnosis["failures"][0]["observed"] == 3
    assert diagnosis["suggested_fix"] == "adjust_output"


def test_diagnose_marks_exception_and_not_executable():
    loop = LearningLoop()
    boom = loop.draft_skill("boom", code=lambda x: 1 / 0)
    loop.verify_skill(boom["id"], [{"args": [1], "expected": 1}])
    diagnosis = diagnose_skill_failure(loop, boom["id"])
    assert diagnosis["failures"][0]["kind"] == "exception"
    assert diagnosis["suggested_fix"] == "fix_exception"

    text = loop.draft_skill("text", code="def run(x): return x * 2")
    loop.verify_skill(text["id"], [{"args": [2], "expected": 4}])
    diagnosis = diagnose_skill_failure(loop, text["id"])
    assert diagnosis["failures"][0]["kind"] == "not_executable"
    assert diagnosis["suggested_fix"] == "needs_executor"


def test_diagnose_unknown_skill_raises():
    with pytest.raises(KeyError):
        diagnose_skill_failure(LearningLoop(), "missing")


def test_diagnose_carries_loop_correction_as_context():
    loop = LearningLoop()
    loop.record_misprediction(
        {
            "task": "check_pln",
            "predicted": "bill 50000",
            "observed": "bill 75000",
            "error": "bill differs",
            "context": {"source": "pln_reader"},
        }
    )
    skill = loop.draft_skill("check_pln", code=lambda x: x)
    loop.verify_skill(skill["id"], [{"args": [1], "expected": 2}])
    diagnosis = diagnose_skill_failure(loop, skill["id"])
    assert diagnosis["correction"]["observed"] == "bill 75000"
    assert diagnosis["suggested_fix"] == "apply_correction"


# --- repair: diagnose -> rewrite -> re-verify ------------------------------


def test_repair_with_builtin_lookup_rewriter_passes():
    """A failing draft is diagnosed, rewritten, and re-verified:
    the new draft passes every case the old one failed."""
    loop = LearningLoop()
    skill = _broken_skill(loop)
    repairer = SkillRepairer(loop)
    result = repairer.repair(skill["id"], _cases())
    assert result["status"] == "repaired"
    assert result["repaired_from"] == skill["id"]
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["strategy"] == "lookup_table"
    assert result["attempts"][0]["passed"] == 2
    repaired = loop.get_skill(result["skill_id"])
    assert repaired["status"] == "draft"
    assert repaired["active"] is False
    assert repaired["verified"] is True
    assert repaired["verification"]["failed"] == 0


def test_repair_with_custom_rewriter():
    """A caller-supplied rewriter (the production LLM seam) can
    rewrite the code itself; the repair still re-verifies."""
    loop = LearningLoop()
    skill = _broken_skill(loop)

    def doubling_rewriter(code, diagnosis, attempt):
        assert diagnosis["failed"] == 2
        assert diagnosis["suggested_fix"] == "adjust_output"
        return lambda x: x * 2

    repairer = SkillRepairer(loop, rewriter=doubling_rewriter)
    result = repairer.repair(skill["id"], _cases())
    assert result["status"] == "repaired"
    assert result["attempts"][0]["strategy"] == "doubling_rewriter"
    assert loop.get_skill(result["skill_id"])["verified"] is True


def test_repair_reuses_stored_verification_cases():
    """Without explicit cases the repair re-runs the cases stored
    in the skill's last verification."""
    loop = LearningLoop()
    skill = _broken_skill(loop)
    result = SkillRepairer(loop).repair(skill["id"])
    assert result["status"] == "repaired"
    assert result["attempts"][0]["passed"] == 2


def test_repair_never_mutates_original_skill():
    loop = LearningLoop()
    skill = _broken_skill(loop)
    original_code = loop.skills[skill["id"]]["code"]
    result = SkillRepairer(loop).repair(skill["id"], _cases())
    assert result["status"] == "repaired"
    assert result["skill_id"] != skill["id"]
    assert loop.skills[skill["id"]]["code"] is original_code
    assert loop.get_skill(skill["id"])["status"] == "draft"
    assert loop.get_skill(skill["id"])["verified"] is False
    assert loop.skills[result["skill_id"]]["repaired_from"] == skill["id"]


def test_repaired_skill_stays_inactive_until_approved():
    """The repaired draft is never reusable and never activatable
    without the loop's normal approval path."""
    loop = LearningLoop()
    skill = _broken_skill(loop)
    result = SkillRepairer(loop).repair(skill["id"], _cases())
    repaired_id = result["skill_id"]
    assert loop.find_reusable_skill("double") is None
    with pytest.raises(PermissionError):
        loop.activate_skill(repaired_id, user_id="user-1")
    activated = loop.activate_skill(repaired_id, approved_by="user-1")
    assert activated["status"] == "active"
    assert loop.find_reusable_skill("double")["id"] == repaired_id


def test_repair_without_test_cases_raises():
    loop = LearningLoop()
    skill = loop.draft_skill("double", code=lambda x: x + 1)
    with pytest.raises(ValueError):
        SkillRepairer(loop).repair(skill["id"])


def test_unrepairable_string_code_without_rewriter_is_honest():
    """String code with no executor and no rewriter cannot be
    repaired deterministically: the result says so and the
    original skill is untouched."""
    loop = LearningLoop()
    skill = loop.draft_skill("double", code="def run(x): return x + 1")
    loop.verify_skill(skill["id"], _cases())
    result = SkillRepairer(loop).repair(skill["id"], _cases())
    assert result["status"] == "unrepairable"
    assert result["reason"] == "no_rewriter_for_string_code"
    assert result["skill_id"] == skill["id"]
    assert loop.get_skill(skill["id"])["status"] == "draft"
    assert len(loop.list_skills()) == 1


def test_unrepairable_after_max_attempts_records_each_attempt():
    """A rewriter that keeps returning broken code is tried
    `max_attempts` times, then the repair honestly gives up."""
    loop = LearningLoop()
    skill = _broken_skill(loop)

    def stubborn(code, diagnosis, attempt):
        return lambda x: x + 1

    repairer = SkillRepairer(loop, rewriter=stubborn, max_attempts=2)
    result = repairer.repair(skill["id"], _cases())
    assert result["status"] == "unrepairable"
    assert result["reason"] == "still_failing_after_attempts"
    assert len(result["attempts"]) == 2
    assert all(not a["ok"] for a in result["attempts"])
    assert len(repairer.list_repairs()) == 1
    # Three drafts exist (original + 2 failed repair attempts),
    # all inactive, and the original is untouched.
    assert len(loop.list_skills(status="draft")) == 3
    assert loop.get_skill(skill["id"])["verified"] is False


def test_lookup_rewriter_needs_callable_code():
    with pytest.raises(ValueError):
        lookup_rewriter("def run(x): return x", {"failures": []}, 1)


# --- repair never overwrites a manual edit --------------------------------


def test_repair_never_touches_manual_edit():
    loop = LearningLoop()
    loop.note_manual_edit("double", ["open calc", "multiply by hand"])
    before = dict(loop.manual_edits)
    skill = loop.draft_skill("double", code=lambda x: x + 1)
    loop.verify_skill(skill["id"], _cases())
    assert skill["conflicts_with_manual_edit"] is True
    result = SkillRepairer(loop).repair(skill["id"], _cases())
    assert result["status"] == "repaired"
    repaired = loop.get_skill(result["skill_id"])
    assert repaired["conflicts_with_manual_edit"] is True
    assert loop.manual_edits == before
    with pytest.raises(ValueError):
        loop.activate_skill(result["skill_id"], approved_by="user-1")


# --- repair never touches price/bill beliefs ------------------------------


def test_repair_does_not_touch_price_beliefs():
    loop = LearningLoop()
    loop.record_misprediction(
        {
            "task": "check_pln",
            "predicted": "bill 50000",
            "observed": "bill 75000",
            "error": "bill differs",
            "context": {"source": "pln_reader"},
        }
    )
    before_correction = loop.correction_for("check_pln")
    before_predict = loop.predict("check_pln")
    skill = _broken_skill(loop, task="double")
    result = SkillRepairer(loop).repair(skill["id"], _cases())
    assert result["status"] == "repaired"
    assert loop.correction_for("check_pln") == before_correction
    assert loop.predict("check_pln") == before_predict == "bill 75000"
    assert len(loop.experiences) == 1


# --- repair through the live loop: suspect skills --------------------------


def test_repair_suspect_skills_after_misprediction():
    """The live-loop path: an active skill passes its stored
    tests but fails in real use on a new input, the loop marks
    it suspect, and the repairer repairs it from the failing
    live case."""
    loop = LearningLoop()
    # Correct for small inputs, wrong for large ones: the stored
    # tests pass, so the skill activates, then live use fails.
    skill = loop.draft_skill("double", code=lambda x: x * 2 if x < 10 else x)
    loop.verify_skill(skill["id"], _cases())
    loop.activate_skill(skill["id"], approved_by="user-1")
    assert loop.get_skill(skill["id"])["status"] == "active"
    loop.record_misprediction(
        {
            "task": "double",
            "predicted": 12,
            "observed": 24,
            "error": "wrong double for large input",
            "context": {"source": "live_use"},
        }
    )
    assert loop.get_skill(skill["id"])["status"] == "suspect"
    results = SkillRepairer(loop).repair_suspect_skills(
        {
            "double": [{"args": [12], "expected": 24}],
        }
    )
    assert len(results) == 1
    assert results[0]["status"] == "repaired"
    assert results[0]["repaired_from"] == skill["id"]
    repaired = loop.get_skill(results[0]["skill_id"])
    assert repaired["status"] == "draft"
    assert repaired["active"] is False
    assert repaired["verified"] is True


def test_repair_suspect_skills_empty_when_none_suspect():
    assert SkillRepairer(LearningLoop()).repair_suspect_skills() == []


# --- procedure change: repeated outcomes -----------------------------------


def _step(method):
    return [{"step": "check bill", "method": method}]


def test_repeated_outcome_creates_new_version():
    """The same new step succeeding 3 times changes the procedure:
    a new version is born, the old version is untouched."""
    updater = ProcedureUpdater(repeat_threshold=3)
    first = updater.record_outcome("check_pln", "pln_bill", _step("open site"), success=True)
    assert first["status"] == "recorded"
    assert first["procedure"]["version"] == 1
    assert first["procedure"]["status"] == "trial"
    counted = updater.record_outcome("check_pln", "pln_bill", _step("use api"), success=True)
    assert counted["status"] == "counted"
    assert counted["repeat_count"] == 1
    assert updater.current_procedure("pln_bill")["version"] == 1
    updater.record_outcome("check_pln", "pln_bill", _step("use api"), success=True)
    changed = updater.record_outcome("check_pln", "pln_bill", _step("use api"), success=True)
    assert changed["status"] == "changed"
    assert changed["procedure"]["version"] == 2
    assert changed["procedure"]["status"] == "trial"
    assert changed["procedure"]["supersedes"] == first["procedure"]["id"]
    versions = updater.list_versions("pln_bill")
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["step"] == _step("open site")
    assert versions[1]["step"] == _step("use api")
    assert updater.current_procedure("pln_bill")["version"] == 2


def test_matching_step_records_a_run_and_promotes():
    """Success with the stored step is a run, not a new version:
    3 runs by 2 agents verify the procedure (revision 7 3.3)."""
    updater = ProcedureUpdater()
    updater.record_outcome("check_pln", "pln_bill", _step("open site"), success=True)
    updater.record_outcome(
        "check_pln", "pln_bill", _step("open site"), success=True, agent_id="agent-a"
    )
    result = updater.record_outcome(
        "check_pln", "pln_bill", _step("open site"), success=True, agent_id="agent-b"
    )
    assert result["status"] == "recorded"
    assert result["procedure"]["status"] == "verified"
    assert result["procedure"]["successful_runs"] == 3
    assert len(updater.list_versions("pln_bill")) == 1


def test_conflicting_step_is_proposed_not_overwritten():
    """A new step that shares the task but changes the method
    conflicts: it is stored as its own candidate and reported,
    the old procedure is untouched."""
    updater = ProcedureUpdater(repeat_threshold=1)
    old_step = [
        {"step": "read bill", "method": "site"},
        {"step": "pay", "method": "manual"},
    ]
    new_step = [
        {"step": "read bill", "method": "site"},
        {"step": "pay", "method": "auto"},
    ]
    first = updater.record_outcome("check_pln", "pln_bill", old_step, success=True)
    changed = updater.record_outcome("check_pln", "pln_bill", new_step, success=True)
    assert changed["status"] == "proposed"
    assert changed["conflicts_with"] == first["procedure"]["id"]
    assert changed["procedure"]["version"] == 2
    old = updater.list_versions("pln_bill")[0]
    assert old["step"] == old_step
    assert old["status"] == "trial"


def test_two_consecutive_failures_break_a_procedure():
    """Revision 7 section 3.3, exactly: a verified procedure
    survives one failure, two consecutive failures break it."""
    updater = ProcedureUpdater()
    for agent in ("agent-a", "agent-b", "agent-a"):
        updater.record_outcome(
            "check_pln", "pln_bill", _step("open site"), success=True, agent_id=agent
        )
    assert updater.current_procedure("pln_bill")["status"] == "verified"
    one = updater.record_outcome("check_pln", "pln_bill", _step("open site"), success=False)
    assert one["procedure"]["status"] == "verified"
    assert one["procedure"]["consecutive_failed_runs"] == 1
    two = updater.record_outcome("check_pln", "pln_bill", _step("open site"), success=False)
    assert two["procedure"]["status"] == "broken"
    assert updater.current_procedure("pln_bill") is None


def test_failure_with_unstored_step_creates_nothing():
    updater = ProcedureUpdater()
    result = updater.record_outcome("check_pln", "pln_bill", _step("use api"), success=False)
    assert result["status"] == "ignored"
    assert updater.list_versions("pln_bill") == []


def test_repeated_old_step_is_readopted_not_duplicated():
    """After v2 replaces v1, succeeding with the v1 step again
    re-adopts v1 (a run on it) instead of minting a v3."""
    updater = ProcedureUpdater(repeat_threshold=1)
    updater.record_outcome("check_pln", "pln_bill", _step("open site"), success=True)
    updater.record_outcome("check_pln", "pln_bill", _step("use api"), success=True)
    assert len(updater.list_versions("pln_bill")) == 2
    result = updater.record_outcome("check_pln", "pln_bill", _step("open site"), success=True)
    assert result["status"] == "recorded"
    assert result["procedure"]["version"] == 1
    assert result["procedure"]["successful_runs"] == 2
    assert len(updater.list_versions("pln_bill")) == 2


# --- decay ------------------------------------------------------------------


def test_stale_verified_procedure_decays_and_must_reearn():
    """A verified procedure untouched for 90 days decays to trial
    with zero runs; a fresh one is untouched."""
    store = InMemoryProcedureStore()
    old = store.record_successful_task("user-1", "pln_bill", _step("open site"), agent_id="agent-a")
    store.mark_run(old["id"], "user-1", True, agent_id="agent-b")
    store.mark_run(old["id"], "user-1", True, agent_id="agent-a")
    assert store.get_procedure(old["id"], "user-1")["status"] == "verified"
    now = datetime.now(UTC)
    store._procedures[old["id"]]["updated_at"] = now - timedelta(days=100)
    fresh = store.record_successful_task("user-1", "other", _step("open site"), agent_id="agent-a")
    store._procedures[fresh["id"]]["updated_at"] = now - timedelta(days=1)
    decayed = store.apply_decay_all(now=now, older_than_days=90)
    assert [d["id"] for d in decayed] == [old["id"]]
    after = store.get_procedure(old["id"], "user-1")
    assert after["status"] == "trial"
    assert after["successful_runs"] == 0
    assert after["version"] == old["version"]
    assert store.get_procedure(fresh["id"], "user-1")["status"] == "trial"


def test_updater_decay_stale_delegates_to_store():
    updater = ProcedureUpdater()
    store = updater.store
    proc = store.record_successful_task(
        "user-1", "pln_bill", _step("open site"), agent_id="agent-a"
    )
    store.mark_run(proc["id"], "user-1", True, agent_id="agent-b")
    store.mark_run(proc["id"], "user-1", True, agent_id="agent-a")
    now = datetime.now(UTC)
    store._procedures[proc["id"]]["updated_at"] = now - timedelta(days=91)
    decayed = updater.decay_stale(now=now, older_than_days=90)
    assert len(decayed) == 1
    assert decayed[0]["status"] == "trial"


# --- one loop: repair + procedure change share the same loop -----------------


def test_repair_and_procedure_change_share_the_loop():
    """End to end on one loop: a misprediction marks the skill
    suspect and corrects the prediction, the repairer rebuilds
    the skill as a draft, and the repeated task outcome changes
    the procedure - all from the same experience."""
    loop = LearningLoop()
    skill = loop.draft_skill("double", code=lambda x: x * 2 if x < 10 else x)
    loop.verify_skill(skill["id"], _cases())
    loop.activate_skill(skill["id"], approved_by="user-1")
    loop.record_misprediction(
        {
            "task": "double",
            "predicted": 12,
            "observed": 24,
            "error": "wrong double for large input",
            "context": {"source": "live_use"},
        }
    )
    assert loop.get_skill(skill["id"])["status"] == "suspect"
    repairer = SkillRepairer(loop)
    results = repairer.repair_suspect_skills(
        {
            "double": [{"args": [12], "expected": 24}],
        }
    )
    assert len(results) == 1
    assert results[0]["status"] == "repaired"
    assert loop.predict("double") == 24

    updater = ProcedureUpdater(repeat_threshold=2)
    updater.record_outcome("double", "calc", _step("mental math"), success=True)
    updater.record_outcome("double", "calc", _step("mental math"), success=True)
    counted = updater.record_outcome("double", "calc", _step("use skill"), success=True)
    assert counted["status"] == "counted"
    changed = updater.record_outcome("double", "calc", _step("use skill"), success=True)
    assert changed["status"] == "changed"
    assert changed["procedure"]["version"] == 2
    assert loop.correction_for("double")["observed"] == 24
