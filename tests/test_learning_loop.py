"""Tests for the learning loop (hermes-agent + Voyager shape).

All tests here are in-memory: no test body in this file touches
the shared Postgres database. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/, and
is noted in the Brain-phase report.)

Note on the approval test below: `puspa/policy/gate.py` has no
kind for skill activation and must not be modified, so the test
obtains a real approved Gate request with kind "send" and the
real action described in the request content. The loop only
verifies that an approved Gate request for the user exists.
"""

import time

import pytest

from puspa.memory import learning_loop
from puspa.memory.learning_loop import LearningLoop, record_misprediction
from puspa.policy.gate import Gate


def _record(task="check_pln", predicted="bill 50000", observed="bill 75000"):
    return {
        "task": task,
        "predicted": predicted,
        "observed": observed,
        "error": "predicted bill differs from observed bill",
        "context": {"source": "pln_reader"},
    }


def _gate_approval(user_id="user-1", agent_id="agent-puspa", skill_id="skill-1"):
    """A real Gate with a real approved request for the user."""
    now = int(time.time())
    token = {
        "user_id": user_id,
        "agent_id": agent_id,
        "tools": ["send"],
        "domains": [],
        "connectors": {},
        "budget": {},
        "exp": now + 3600,
        "iat": now,
    }
    gate = Gate(log=None, spending=None)
    request = gate.request_approval(
        user_id=user_id,
        agent_id=agent_id,
        token=token,
        kind="send",
        content={"action": "activate_learned_skill", "skill_id": skill_id},
    )
    gate.approve(request.request_id, user_id)
    return gate, request


def _verified_draft(loop, task="check_pln"):
    skill = loop.draft_skill(task, code=lambda bill: bill * 1)
    loop.verify_skill(skill["id"], [{"args": [100], "expected": 100}])
    return skill


# --- seam: record_misprediction -----------------------------------------

def test_record_misprediction_requires_all_keys():
    loop = LearningLoop()
    with pytest.raises(ValueError):
        loop.record_misprediction({"task": "check_pln"})
    with pytest.raises(ValueError):
        loop.record_misprediction("not a dict")


def test_predict_changes_after_misprediction():
    """Behaviour change: the same predict call returns the corrected
    value after one misprediction experience."""
    loop = LearningLoop()
    assert loop.predict("check_pln", default="bill 50000") == "bill 50000"
    correction = loop.record_misprediction(_record())
    assert correction["observed"] == "bill 75000"
    assert correction["count"] == 1
    assert loop.predict("check_pln", default="bill 50000") == "bill 75000"
    assert loop.correction_for("check_pln")["error"].startswith("predicted bill")


def test_second_misprediction_counts_and_keeps_latest():
    loop = LearningLoop()
    loop.record_misprediction(_record())
    loop.record_misprediction(_record(observed="bill 80000"))
    correction = loop.correction_for("check_pln")
    assert correction["count"] == 2
    assert correction["observed"] == "bill 80000"
    assert len(correction["experience_ids"]) == 2


def test_module_level_seam_delegates_to_default_loop():
    learning_loop.reset_learning_loop()
    try:
        assert record_misprediction(_record())["count"] == 1
        assert learning_loop.get_learning_loop().predict("check_pln") == "bill 75000"
    finally:
        learning_loop.reset_learning_loop()


# --- curriculum -----------------------------------------------------------

def test_curriculum_proposes_retry_for_worst_gap_first():
    loop = LearningLoop()
    loop.record_misprediction(_record(task="compare_price"))
    loop.record_misprediction(_record(task="compare_price"))
    loop.record_misprediction(_record(task="check_pln"))
    proposal = loop.propose_next_task()
    assert proposal["task"] == "compare_price"
    assert proposal["reason"] == "misprediction_gap"
    assert proposal["difficulty"] == "retry"
    assert proposal["gap"]["mispredictions"] == 2


def test_curriculum_proposes_verification_when_no_gaps():
    loop = LearningLoop()
    skill = loop.draft_skill("check_pln", code=lambda x: x)
    proposal = loop.propose_next_task()
    assert proposal["task"] == f"verify_skill:{skill['name']}"
    assert proposal["reason"] == "unverified_draft"


def test_curriculum_empty_when_nothing_to_learn():
    assert LearningLoop().propose_next_task() is None


# --- skill library: draft, verify, approve, activate ----------------------

def test_drafted_skill_is_inactive_until_approved():
    loop = LearningLoop()
    skill = loop.draft_skill("check_pln", code=lambda x: x)
    assert skill["status"] == "draft"
    assert skill["active"] is False
    assert loop.find_reusable_skill("check_pln") is None


def test_verify_callable_skill_all_cases_pass():
    loop = LearningLoop()
    skill = loop.draft_skill("double", code=lambda x: x * 2)
    result = loop.verify_skill(skill["id"], [
        {"args": [2], "expected": 4},
        {"args": [3], "expected": 6},
    ])
    assert result == {
        "skill_id": skill["id"], "passed": 2, "failed": 0,
        "verified": True,
        "details": [
            {"case": {"args": [2], "expected": 4}, "result": 4, "passed": True},
            {"case": {"args": [3], "expected": 6}, "result": 6, "passed": True},
        ],
    }
    assert loop.get_skill(skill["id"])["verified"] is True


def test_verify_fails_closed_for_string_code_without_executor():
    loop = LearningLoop()
    skill = loop.draft_skill("double", code="def run(x): return x * 2")
    result = loop.verify_skill(skill["id"], [{"args": [2], "expected": 4}])
    assert result["verified"] is False
    assert result["failed"] == 1


def test_verify_string_code_with_sandbox_executor():
    loop = LearningLoop()
    skill = loop.draft_skill("double", code="def run(x): return x * 2")

    def fake_sandbox(code, case):
        assert "def run" in code
        return case["args"][0] * 2

    result = loop.verify_skill(
        skill["id"], [{"args": [2], "expected": 4}], executor=fake_sandbox)
    assert result["verified"] is True


def test_activate_without_approval_raises():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    with pytest.raises(PermissionError):
        loop.activate_skill(skill["id"], user_id="user-1")


def test_activate_with_real_gate_approval_then_reusable():
    """Behaviour change: a verified trial skill becomes active and
    reusable only after a real approved Gate request."""
    loop = LearningLoop()
    skill = _verified_draft(loop)
    assert loop.find_reusable_skill("check_pln") is None
    gate, request = _gate_approval(skill_id=skill["id"])
    activated = loop.activate_skill(
        skill["id"], user_id="user-1", gate=gate,
        approval_request_id=request.request_id,
    )
    assert activated["status"] == "active"
    assert activated["active"] is True
    reused = loop.find_reusable_skill("check_pln")
    assert reused is not None
    assert reused["id"] == skill["id"]
    assert reused["reuse_score"] >= 0.7


def test_activate_with_wrong_user_gate_request_denied():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    gate, request = _gate_approval(user_id="user-1", skill_id=skill["id"])
    with pytest.raises(PermissionError):
        loop.activate_skill(
            skill["id"], user_id="user-2", gate=gate,
            approval_request_id=request.request_id,
        )


def test_activate_with_unapproved_gate_request_denied():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    now = int(time.time())
    token = {
        "user_id": "user-1", "agent_id": "agent-puspa", "tools": ["send"],
        "domains": [], "connectors": {}, "budget": {},
        "exp": now + 3600, "iat": now,
    }
    gate = Gate(log=None, spending=None)
    request = gate.request_approval(
        user_id="user-1", agent_id="agent-puspa", token=token, kind="send",
        content={"action": "activate_learned_skill", "skill_id": skill["id"]},
    )
    with pytest.raises(PermissionError):
        loop.activate_skill(
            skill["id"], user_id="user-1", gate=gate,
            approval_request_id=request.request_id,
        )


def test_activate_with_explicit_user_approval():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    activated = loop.activate_skill(
        skill["id"], user_id="user-1", approved_by="user-1")
    assert activated["status"] == "active"


def test_activate_unverified_or_rejected_skill_refused():
    loop = LearningLoop()
    draft = loop.draft_skill("check_pln", code=lambda x: x)
    with pytest.raises(ValueError):
        loop.activate_skill(draft["id"], approved_by="user-1")
    skill = _verified_draft(loop, task="compare_price")
    loop.reject_skill(skill["id"], reason="wrong approach")
    assert loop.get_skill(skill["id"])["status"] == "rejected"
    with pytest.raises(ValueError):
        loop.activate_skill(skill["id"], approved_by="user-1")


def test_misprediction_marks_active_skill_suspect():
    loop = LearningLoop()
    skill = _verified_draft(loop)
    loop.activate_skill(skill["id"], approved_by="user-1")
    assert loop.get_skill(skill["id"])["status"] == "active"
    loop.record_misprediction(_record(task="check_pln"))
    assert loop.get_skill(skill["id"])["status"] == "suspect"
    assert loop.find_reusable_skill("check_pln") is None


# --- manual edits are never overwritten ------------------------------------

def test_manual_edit_blocks_activation_and_flags_draft():
    loop = LearningLoop()
    loop.note_manual_edit("check_pln", ["open pln site", "read bill"])
    skill = loop.draft_skill("check_pln", code=lambda x: x)
    assert skill["conflicts_with_manual_edit"] is True
    loop.verify_skill(skill["id"], [{"args": [1], "expected": 1}])
    with pytest.raises(ValueError):
        loop.activate_skill(skill["id"], approved_by="user-1")
    assert loop.get_skill(skill["id"])["status"] == "draft"


def test_draft_without_manual_edit_has_no_conflict_flag():
    loop = LearningLoop()
    skill = loop.draft_skill("check_pln", code=lambda x: x)
    assert skill["conflicts_with_manual_edit"] is False


# --- durable drafts ---------------------------------------------------------

class _FakeProcedureStore:
    def __init__(self):
        self.recorded = []

    def record_successful_task(self, user_id, service, step, **kwargs):
        proc = {"id": "proc-1", "user_id": user_id, "service": service,
                "step": step, "status": "trial"}
        self.recorded.append(proc)
        return proc


def test_save_draft_to_procedures_uses_draft_prefix():
    store = _FakeProcedureStore()
    loop = LearningLoop(procedure_store=store)
    skill = loop.draft_skill("check_pln", code=lambda x: x)
    proc = loop.save_draft_to_procedures(skill["id"], "user-1")
    assert proc["service"] == "learned_draft:learned_check_pln"
    assert proc["status"] == "trial"
    assert len(store.recorded) == 1


def test_save_unknown_skill_raises():
    with pytest.raises(KeyError):
        LearningLoop().save_draft_to_procedures("missing", "user-1")
