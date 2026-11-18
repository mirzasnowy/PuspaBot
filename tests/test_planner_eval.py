"""Tests for the planner, the eval harness and the prompt optimiser.

All tests are in-memory / cassette replay only: no live model call,
no network, no Postgres table is touched by the test bodies
themselves (the shared conftest autouse fixture still resets the
test database, as it does for every test file in this repo).
"""

from puspa.cassette.cassette import TASK_REFERENCE
from puspa.evidence.eval_harness import (
    EvalHarness,
    EvalTask,
    load_reference_tasks,
    render_report,
    run_price_injection_eval,
    run_reference_eval,
    sandbox_available,
    score_state,
)
from puspa.memory.optimizer import (
    DEFAULT_PROGRAMS,
    Optimizer,
    OptimizerError,
    PromptProgram,
    evaluate_program,
)
from puspa.runtime.planner import (
    Planner,
    decompose,
    decompose_for_reference_task,
    step_tool,
)
from puspa.runtime.tasks import Task

AGENT = "agent-1"
USER = "user-1"


def _task(purpose, plan=None):
    return Task(task_id="task-1", user_id=USER, agent_id=AGENT,
                purpose=purpose, plan=plan or [])


def _ok_executor(name, inputs):
    return {"type": "result", "value": 1}


# ---------------------------------------------------------------------------
# Planner: decomposition
# ---------------------------------------------------------------------------

def test_decompose_compare_price_covers_three_stores():
    steps = decompose("compare laptop prices across stores")
    names = [step.name for step in steps]
    assert names[0] == "search_listings"
    assert names[1:4] == ["read_price_tokopedia", "read_price_shopee",
                          "read_price_blibli"]
    assert "verify_price" in names
    assert "present_comparison" in names


def test_decompose_bill_plan_ends_in_outbound_payment():
    steps = decompose("check PLN bill and prepare payment")
    assert steps[-1].name == "prepare_payment"
    assert steps[-1].outbound_action is True
    assert steps[0].name == "read_bill"


def test_decompose_research_email_drive_and_generic():
    assert [s.name for s in decompose("research and summarize 5 articles")] == [
        "search_artikel", "read_artikel", "summarize"]
    assert [s.name for s in decompose("read email and create a draft reply")] == [
        "gmail_search", "gmail_read", "gmail_create_draft", "present"]
    assert [s.name for s in decompose("search a file in Drive and summarize it")] == [
        "drive_search", "drive_read", "summarize"]
    generic = decompose("do something completely new for me")
    assert [s.name for s in generic] == ["gather", "verify", "present"]


def test_decompose_all_reference_tasks():
    assert len(TASK_REFERENCE) == 10
    for name in TASK_REFERENCE:
        steps = decompose_for_reference_task(name)
        assert steps, f"empty plan for {name}"
        assert all("type" in s.inputs and "type" in s.expected_output for s in steps)


def test_step_tool_maps_prefixed_steps():
    assert step_tool("read_price_shopee") == "read_price"
    assert step_tool("gmail_create_draft") == "gmail"
    assert step_tool("prepare_payment") == "prepare_payment"


# ---------------------------------------------------------------------------
# Planner: execution + bounded replan
# ---------------------------------------------------------------------------

def test_planner_runs_plan_to_completion():
    planner = Planner(executor=_ok_executor)
    result = planner.run(_task("compare laptop prices across stores"))
    assert result.status == "completed"
    assert planner.attempts == 1
    assert planner.replans == 0
    assert "read_price_tokopedia" in result.result


def test_planner_replans_and_retries_failed_step():
    calls = {}

    def flaky(name, inputs):
        calls[name] = calls.get(name, 0) + 1
        if name == "read_price_shopee" and calls[name] == 1:
            return {"type": "wrong"}  # handoff rejects: wrong schema type
        return {"type": "result", "value": 1}

    planner = Planner(executor=flaky)
    result = planner.run(_task("compare laptop prices across stores"))
    assert result.status == "completed"
    assert planner.replans == 1
    assert planner.attempts == 2
    replan_events = [e for e in planner.events
                     if e["type"] == "plan_created"
                     and e["content"].get("replan")]
    assert len(replan_events) == 1
    assert replan_events[0]["content"]["reason"]


def test_planner_drops_persistently_failing_step_on_second_replan():
    def always_bad_verify(name, inputs):
        if name == "verify_price":
            return {"type": "wrong"}
        return {"type": "result", "value": 1}

    planner = Planner(executor=always_bad_verify)
    result = planner.run(_task("compare laptop prices across stores"))
    assert result.status == "completed"
    assert planner.replans == 2
    assert planner.attempts == 3
    assert "verify_price" not in result.result


def test_planner_respects_replan_limit():
    def always_wrong(name, inputs):
        return {"type": "wrong"}

    planner = Planner(executor=always_wrong, max_replans=0)
    result = planner.run(_task("compare laptop prices across stores"))
    assert result.status == "failed"
    assert planner.replans == 0
    assert planner.attempts == 1


def test_planner_does_not_replan_around_approval_gate():
    calls = []

    def recording(name, inputs):
        calls.append(name)
        return {"type": "result", "value": 1}

    planner = Planner(executor=recording)
    result = planner.run(_task("check PLN bill and prepare payment"))
    assert result.status == "pending_approval"
    assert planner.replans == 0
    assert calls == []  # nothing executed before approval


# ---------------------------------------------------------------------------
# Eval harness
# ---------------------------------------------------------------------------

def test_reference_eval_runs_ten_tasks_and_scores_them():
    tasks = load_reference_tasks()
    assert len(tasks) == 10
    assert {task.name for task in tasks} == set(TASK_REFERENCE)
    report = run_reference_eval()
    assert report.total == 10
    assert report.passed == 10
    assert report.score == 1.0
    assert "10/10" in report.summary()
    assert "10/10" in render_report(report)


def test_eval_scorer_reports_which_state_key_failed():
    task = EvalTask(task_id="eval-x", name="x", prompt="x",
                    cassette_name="compare_price_laptop",
                    expected_state={"cheapest_price": 100.0, "prices_seen": 3})
    passed, details = score_state(task, {"cheapest_price": 200.0, "prices_seen": 3})
    assert passed is False
    assert details["cheapest_price"]["matched"] is False
    assert details["prices_seen"]["matched"] is True


def test_eval_harness_records_verification_events():
    harness = EvalHarness()
    report = harness.run(load_reference_tasks()[:2])
    assert report.total == 2
    assert len(harness.events) == 2
    assert all(e["type"] == "verification_completed" for e in harness.events)


def test_price_injection_is_caught_by_verifier():
    result = run_price_injection_eval()
    assert result.passed is True
    assert result.details["caught"] is True
    assert result.details["verdict"] != "Verified"
    assert result.details["tampered_value"] == 1000.0
    assert result.details["true_value"] == 11999000.0


def test_sandbox_availability_reports_without_executing():
    assert isinstance(sandbox_available(), bool)


# ---------------------------------------------------------------------------
# Prompt optimiser (DSPy pattern, cassettes only)
# ---------------------------------------------------------------------------

def test_optimizer_scores_planner_on_all_cassettes():
    outcome = evaluate_program(DEFAULT_PROGRAMS["planner"])
    assert len(outcome["per_task"]) == 10
    assert outcome["score"] >= 0.9


def test_optimizer_keeps_best_variant_and_never_scores_below_baseline():
    result = Optimizer().optimize(DEFAULT_PROGRAMS["planner"])
    assert result.best_score >= result.baseline_score
    assert len(result.scores) == 6  # baseline + 5 deterministic variants
    assert result.best_program.role == "planner"


def test_optimizer_refuses_the_security_gate():
    gate_program = PromptProgram(name="gate", role="gate", template="x")
    try:
        Optimizer().optimize(gate_program)
    except OptimizerError:
        return
    raise AssertionError("optimiser must refuse the security gate")


def test_optimizer_refuses_gate_by_name_even_with_planner_role():
    sneaky = PromptProgram(name="gate", role="planner", template="x")
    try:
        Optimizer().optimize(sneaky)
    except OptimizerError:
        return
    raise AssertionError("optimiser must refuse a program named gate")
