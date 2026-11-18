"""Tests for the curriculum runner (idle-time learning tasks).

All tests here are in-memory: a real in-memory ``LearningLoop``,
a ``CurriculumRunner`` with an in-memory executor, and no direct
database access in any test body. (The shared tests/conftest.py
autouse fixture still creates schemas and truncates tables on
collection; that is unavoidable for any file under tests/.)
"""

from puspa.memory.learning_loop import LearningLoop
from puspa.scheduler.curriculum import (
    DEFAULT_COST_CEILING_RP_PER_TASK,
    DEFAULT_MAX_LEARNING_TASKS_PER_DAY,
    CurriculumRunner,
    run_curriculum_tick,
)


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


# --- propose, execute, feed back ------------------------------------------


def test_curriculum_runs_proposed_task_and_records_experience():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(loop)
    outcome = runner.run_once()
    assert outcome["status"] == "completed"
    assert outcome["ran"] is True
    assert outcome["runs_today"] == 1
    assert outcome["cost_rp"] == 0
    # Success is in the experience log and the gap is cleared, so
    # the same retry is not proposed again.
    assert any(
        e.get("outcome") == "completed" and e["task"] == "compare laptop prices across stores"
        for e in loop.experiences
    )
    assert "compare laptop prices across stores" not in loop.gaps
    assert loop.propose_next_task() is None


def test_no_task_when_curriculum_is_empty():
    runner = _idle_runner(LearningLoop())
    outcome = runner.run_once()
    assert outcome == {
        "status": "no_task",
        "ran": False,
        "proposal": None,
        "task_id": None,
        "task_status": None,
        "cost_rp": 0,
        "runs_today": 0,
        "reason": "curriculum has nothing to propose",
    }


def test_verify_draft_proposal_runs_and_is_not_repeated_today():
    loop = LearningLoop()
    skill = loop.draft_skill("compare_price", code=lambda x: x)
    runner = _idle_runner(loop)
    first = runner.run_once()
    assert first["status"] == "completed"
    assert first["proposal"]["reason"] == "unverified_draft"
    assert first["proposal"]["task"] == f"verify_skill:{skill['name']}"
    # The draft is still unverified, but the runner does not run the
    # same learning task twice in one day.
    second = runner.run_once()
    assert second["status"] == "already_completed"
    assert second["ran"] is False


def test_failed_task_is_recorded_as_new_misprediction():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(
        loop,
        executor=lambda name, inputs: {"type": "wrong", "value": None},
    )
    outcome = runner.run_once()
    assert outcome["status"] == "failed"
    assert outcome["ran"] is True
    correction = loop.correction_for("compare laptop prices across stores")
    assert correction["count"] == 2
    assert "failed" in correction["observed"]
    # The gap grows, so the curriculum can propose a retry later.
    assert loop.gaps["compare laptop prices across stores"]["mispredictions"] == 2


# --- daily cap --------------------------------------------------------------


def test_default_daily_cap_is_five():
    assert DEFAULT_MAX_LEARNING_TASKS_PER_DAY == 5


def test_daily_cap_is_enforced():
    loop = _gap_loop(
        "compare laptop prices across stores",
        "remember yesterday preferences",
        "research and summarize 5 articles",
    )
    runner = _idle_runner(loop, max_tasks_per_day=2)
    assert runner.run_once()["status"] == "completed"
    assert runner.run_once()["status"] == "completed"
    capped = runner.run_once()
    assert capped["status"] == "daily_cap_reached"
    assert capped["ran"] is False
    assert capped["runs_today"] == 2
    # The third gap is untouched: the cap stopped the run, not the loop.
    assert loop.propose_next_task() is not None


def test_daily_cap_can_be_changed_per_runner():
    loop = _gap_loop(
        "compare laptop prices across stores",
        "research and summarize 5 articles",
    )
    runner = _idle_runner(loop, max_tasks_per_day=1)
    assert runner.run_once()["status"] == "completed"
    assert runner.run_once()["status"] == "daily_cap_reached"


# --- cost ceiling -----------------------------------------------------------


def test_default_cost_ceiling_is_rp2000_per_task():
    assert DEFAULT_COST_CEILING_RP_PER_TASK == 2000


def test_cost_ceiling_is_enforced_per_task():
    loop = _gap_loop("compare laptop prices across stores")
    calls = {"n": 0}

    def pricey_executor(name, inputs):
        calls["n"] += 1
        return {"type": "result", "value": None, "cost_rp": 1500}

    runner = _idle_runner(loop, executor=pricey_executor)
    outcome = runner.run_once()
    assert outcome["status"] == "failed"
    # First step charged Rp1.500, second step would pass Rp2.000.
    assert calls["n"] == 2
    assert runner.last_budget is not None
    assert runner.last_budget.rupiah_used == 1500
    assert runner.last_budget.rupiah_max == 2000
    assert "budget_exhausted" in [e["type"] for e in runner.events]
    # The over-budget failure is recorded as a new misprediction.
    correction = loop.correction_for("compare laptop prices across stores")
    assert correction["count"] == 2
    assert "2000" in correction["error"]


def test_cost_ceiling_can_be_changed_per_runner():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(
        loop,
        executor=lambda name, inputs: {"type": "result", "cost_rp": 500},
        cost_ceiling_rp=5000,
    )
    outcome = runner.run_once()
    assert outcome["status"] == "completed"
    assert outcome["cost_rp"] == 500 * 6


# --- idle only ---------------------------------------------------------------


def test_runner_refuses_when_user_task_is_running():
    loop = _gap_loop("compare laptop prices across stores")
    runner = CurriculumRunner(loop=loop, is_idle=lambda: False)
    outcome = runner.run_once()
    assert outcome["status"] == "busy"
    assert outcome["ran"] is False
    assert outcome["runs_today"] == 0
    # Only the original misprediction is in the log; the runner
    # added no curriculum experience.
    assert len(loop.experiences) == 1
    assert all("kind" not in e for e in loop.experiences)
    assert loop.propose_next_task() is not None


def test_user_task_active_flag_blocks_the_runner():
    loop = _gap_loop("compare laptop prices across stores")
    runner = CurriculumRunner(loop=loop)
    runner.set_user_task_active(True)
    assert runner.run_once()["status"] == "busy"
    runner.set_user_task_active(False)
    assert runner.run_once()["status"] == "completed"


def test_busy_run_does_not_consume_daily_budget():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(loop, max_tasks_per_day=1, is_idle=lambda: False)
    assert runner.run_once()["status"] == "busy"
    runner.is_idle = lambda: True
    assert runner.run_once()["status"] == "completed"


# --- never outbound without approval ----------------------------------------


def test_outbound_learning_task_stops_at_pending_approval():
    loop = _gap_loop("check PLN bill and prepare payment")
    calls = {"n": 0}

    def counting_executor(name, inputs):
        calls["n"] += 1
        return {"type": "result", "value": None}

    runner = _idle_runner(loop, executor=counting_executor)
    outcome = runner.run_once()
    assert outcome["status"] == "pending_approval"
    assert outcome["ran"] is True
    # Nothing was executed: the executor was never called.
    assert calls["n"] == 0
    assert "approval_requested" in [e["type"] for e in runner.events]
    # Recorded, but not as a misprediction and the gap stays open.
    assert any(e.get("outcome") == "pending_approval" for e in loop.experiences)
    assert loop.gaps["check PLN bill and prepare payment"]["mispredictions"] == 1


# --- scheduler hookup ---------------------------------------------------------


def test_run_curriculum_tick_with_runner_keeps_daily_cap():
    loop = _gap_loop(
        "compare laptop prices across stores",
        "research and summarize 5 articles",
    )
    runner = _idle_runner(loop, max_tasks_per_day=1)
    assert run_curriculum_tick(runner=runner)["status"] == "completed"
    assert run_curriculum_tick(runner=runner)["status"] == "daily_cap_reached"


def test_run_curriculum_tick_builds_a_runner_when_none_given():
    loop = _gap_loop("compare laptop prices across stores")
    outcome = run_curriculum_tick(loop=loop, is_idle=lambda: True)
    assert outcome["status"] == "completed"


def test_tick_calls_curriculum_only_when_a_runner_is_passed(monkeypatch):
    from puspa.scheduler import scheduler

    monkeypatch.setattr(scheduler, "heartbeat", lambda dsn=None, now=None: None)
    monkeypatch.setattr(scheduler, "list_due_triggers", lambda dsn=None, now=None: [])
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(loop)
    assert scheduler.tick() == []
    assert runner.history == []
    assert scheduler.tick(curriculum=runner) == []
    assert runner.history[-1]["status"] == "completed"


def test_tick_never_breaks_when_curriculum_fails(monkeypatch):
    from puspa.scheduler import scheduler

    monkeypatch.setattr(scheduler, "heartbeat", lambda dsn=None, now=None: None)
    monkeypatch.setattr(scheduler, "list_due_triggers", lambda dsn=None, now=None: [])

    class _BrokenRunner:
        def run_once(self, now=None):
            raise RuntimeError("curriculum broke")

    # A broken curriculum must not change the trigger result.
    assert scheduler.tick(curriculum=_BrokenRunner()) == []


# --- ledger budget gate (G8: curriculum sits on the general scheduler) ------


def test_budget_hold_when_no_budget_remaining():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(loop, budget_check=lambda user_id, task_id, cap_rp=2000: 0)
    outcome = runner.run_once()
    assert outcome["status"] == "budget_hold"
    assert outcome["ran"] is False
    assert outcome["runs_today"] == 0  # a hold consumes no daily run
    assert loop.propose_next_task() is not None  # gap untouched


def test_budget_check_with_remaining_budget_runs():
    loop = _gap_loop("compare laptop prices across stores")
    seen = {}

    def stub_check(user_id, task_id, cap_rp=2000):
        seen["call"] = (user_id, task_id, cap_rp)
        return 1500

    runner = _idle_runner(loop, budget_check=stub_check)
    outcome = runner.run_once()
    assert outcome["status"] == "completed"
    assert seen["call"] == ("system", "compare laptop prices across stores", 2000)


def test_budget_check_none_means_no_gate():
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(loop, budget_check=lambda *a, **k: None)
    assert runner.run_once()["status"] == "completed"


def test_default_budget_check_resolves_to_the_ledger():
    # The G2 worker landed puspa/billing/ledger.py with exactly the
    # specified signature, so the default resolver now returns a
    # real callable: no spend recorded means the full cap remains.
    from puspa.scheduler.curriculum import default_task_budget_check

    check = default_task_budget_check()
    assert callable(check)
    assert check("system", "a-fresh-task", cap_rp=2000) == 2000
    loop = _gap_loop("compare laptop prices across stores")
    runner = _idle_runner(loop)
    assert runner.budget_check is not None
    assert runner.run_once()["status"] == "completed"


def test_default_budget_check_is_fail_soft_without_ledger(monkeypatch):
    # Hide the ledger module: the resolver must return None
    # instead of raising, and the runner must run ungated.
    import sys

    from puspa.scheduler.curriculum import default_task_budget_check

    monkeypatch.setitem(sys.modules, "puspa.billing.ledger", None)
    assert default_task_budget_check() is None


def test_budget_check_failure_is_fail_soft():
    loop = _gap_loop("compare laptop prices across stores")

    def broken_check(user_id, task_id, cap_rp=2000):
        raise RuntimeError("ledger down")

    runner = _idle_runner(loop, budget_check=broken_check)
    assert runner.run_once()["status"] == "completed"
