"""Postgres/SQLite consistency: write-ahead, recovery, reconcile (E-06).

Covers the one-page rules in ``docs/data-consistency.md``: the
ledger is the authority for task status, the session database is
rebuildable, uncertain outbound steps are never auto-retried, and
the kill -9 fault-injection drill proves gate 12 (no duplicate
actions, no lost task, hash chain valid).
"""

from __future__ import annotations

from puspa.consistency import (
    WriteAheadLog,
    idempotency_key,
    rebuild_sessions_from_ledger,
    reconcile_startup,
    recover_task,
    run_kill_drill,
)
from puspa.consistency.runner import StepSpec
from puspa.consistency.worker import drill_executor, drill_steps
from puspa.ledger import Ledger
from puspa.runtime.tasks import Step, Task, TaskRunner
from puspa.sessions import open_persistence


def test_idempotency_key_is_deterministic_and_arg_sensitive():
    key_a = idempotency_key("task-1", 0, {"type": "send", "to": "seller"})
    key_b = idempotency_key("task-1", 0, {"type": "send", "to": "seller"})
    key_other_args = idempotency_key("task-1", 0, {"type": "send", "to": "buyer"})
    key_other_step = idempotency_key("task-1", 1, {"type": "send", "to": "seller"})
    assert key_a == key_b
    assert key_a != key_other_args
    assert key_a != key_other_step
    assert key_a.startswith("task-1:0:")


def test_write_ahead_intent_without_result_is_uncertain(ledger):
    wal = WriteAheadLog(ledger)
    wal.record_task_created("task-wa", "puspa")
    wal.record_intent("task-wa", "puspa", 0, "read", {"type": "read"}, outbound=False)
    wal.record_intent("task-wa", "puspa", 1, "send", {"type": "send"}, outbound=True)
    wal.record_result("task-wa", "puspa", 0, "read", {"type": "read"}, {"type": "ok"})
    uncertain = wal.find_uncertain_steps("task-wa")
    assert len(uncertain) == 1
    assert uncertain[0]["step"] == 1
    assert uncertain[0]["step_name"] == "send"
    assert uncertain[0]["outbound"] is True
    assert uncertain[0]["idempotency_key"] == idempotency_key("task-wa", 1, {"type": "send"})


def test_task_status_derivation(ledger):
    wal = WriteAheadLog(ledger)
    assert wal.task_status("task-missing") == "unknown"
    wal.record_task_created("task-s1", "puspa")
    assert wal.task_status("task-s1") == "running"
    wal.mark_completed("task-s1", "puspa")
    assert wal.task_status("task-s1") == "completed"

    wal.record_task_created("task-s2", "puspa")
    wal.mark_failed("task-s2", "puspa", reason="boom")
    assert wal.task_status("task-s2") == "failed"

    wal.record_task_created("task-s3", "puspa")
    ledger.append_event(
        task_id="task-s3",
        agent_id="puspa",
        event_type="budget_exhausted",
        content={"reason": "over budget"},
    )
    assert wal.task_status("task-s3") == "budget_exceeded"

    wal.record_task_created("task-s4", "puspa")
    wal.mark_interrupted("task-s4", "puspa", reason="no heartbeat")
    assert wal.task_status("task-s4") == "interrupted"

    # The last terminal event wins.
    wal.record_task_created("task-s5", "puspa")
    wal.mark_interrupted("task-s5", "puspa", reason="stale")
    wal.mark_completed("task-s5", "puspa")
    assert wal.task_status("task-s5") == "completed"


def test_recovery_retries_read_only_never_outbound(ledger):
    wal = WriteAheadLog(ledger)
    steps = [
        StepSpec(name="read", args={"type": "read"}, outbound=False),
        StepSpec(name="send", args={"type": "send"}, outbound=True),
    ]
    # Simulate a crash: record both intents, only the read result.
    wal.record_task_created("task-rec", "puspa")
    wal.record_intent("task-rec", "puspa", 0, "read", {"type": "read"}, outbound=False)
    wal.record_intent("task-rec", "puspa", 1, "send", {"type": "send"}, outbound=True)
    wal.record_result("task-rec", "puspa", 0, "read", {"type": "read"}, {"type": "ok"})

    calls: list[str] = []

    def executor(step: StepSpec, index: int) -> dict:
        calls.append(step.name)
        return {"type": "retried", "step": step.name}

    report = recover_task(ledger, "task-rec", "puspa", steps, executor)
    # The read step already had a result, so nothing was retried;
    # the outbound step is uncertain and must be decided by a human.
    assert report["status"] == "interrupted"
    assert report["retried"] == []
    assert len(report["needs_user_decision"]) == 1
    assert report["needs_user_decision"][0]["step_name"] == "send"
    assert calls == []
    assert wal.task_status("task-rec") == "interrupted"


def test_recovery_retries_uncertain_read_only_step(ledger):
    wal = WriteAheadLog(ledger)
    steps = [StepSpec(name="read", args={"type": "read"}, outbound=False)]
    wal.record_task_created("task-rec2", "puspa")
    wal.record_intent("task-rec2", "puspa", 0, "read", {"type": "read"}, outbound=False)
    # Crash between intent and result: no result recorded.

    def executor(step: StepSpec, index: int) -> dict:
        return {"type": "computed", "step": step.name}

    report = recover_task(ledger, "task-rec2", "puspa", steps, executor)
    assert report["status"] == "completed"
    assert report["retried"] == [idempotency_key("task-rec2", 0, {"type": "read"})]
    assert report["needs_user_decision"] == []
    assert wal.find_uncertain_steps("task-rec2") == []
    assert wal.task_status("task-rec2") == "completed"
    ok, broken = ledger.verify_chain("task-rec2")
    assert ok and broken == []


def test_reconcile_startup_marks_stale_running_interrupted(ledger, tmp_path):
    wal = WriteAheadLog(ledger)
    wal.record_task_created("task-stale", "puspa")
    wal.record_task_created("task-fresh", "puspa")
    wal.record_task_created("task-done", "puspa")
    wal.mark_completed("task-done", "puspa")

    with open_persistence(home=tmp_path) as gateway:
        gateway.append_message(gateway.create_session("hello")["id"], "user", "PLN bill reminder")
        report = reconcile_startup(ledger, gateway.store, stale_after_seconds=0)
    assert report["interrupted"] == ["task-stale", "task-fresh"]
    assert report["running_found"] == 2
    assert report["errors"] == []
    assert report["fts"]["ok"]
    assert wal.task_status("task-stale") == "interrupted"
    assert wal.task_status("task-fresh") == "interrupted"
    assert wal.task_status("task-done") == "completed"

    # A generous threshold leaves a live task alone.
    wal.record_task_created("task-live", "puspa")
    report = reconcile_startup(ledger, None, stale_after_seconds=3600)
    assert report["interrupted"] == []
    assert wal.task_status("task-live") == "running"


def test_rebuild_sessions_from_ledger(ledger, tmp_path):
    ledger.append_event(
        task_id="task-rb",
        agent_id="puspa",
        event_type="user_message",
        content={"sender": "u1", "text": "check PLN bill for this month"},
    )
    ledger.append_event(
        task_id="task-rb",
        agent_id="puspa",
        event_type="agent_message",
        content={"text": "Your PLN bill is Rp 123.000."},
    )
    with open_persistence(home=tmp_path) as gateway:
        report = rebuild_sessions_from_ledger(ledger, gateway.store)
        assert report["rebuilt"] == ["task-rb"]
        assert report["messages"] == 2
        sessions = gateway.store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["metadata"]["recovered_from_ledger"] is True
        assert sessions[0]["metadata"]["ledger_task_id"] == "task-rb"
        full = gateway.store.get_session(sessions[0]["id"])
        assert [m["role"] for m in full["messages"]] == ["user", "assistant"]
        assert full["messages"][0]["content"] == "check PLN bill for this month"
        assert full["messages"][1]["content"] == "Your PLN bill is Rp 123.000."
        # The rebuilt transcript is searchable (both messages mention PLN).
        hits = gateway.store.search_messages("PLN")
        assert len(hits) == 2
        # A second rebuild is a no-op, not a duplicate.
        again = rebuild_sessions_from_ledger(ledger, gateway.store)
        assert again["rebuilt"] == []
        assert again["skipped"] == ["task-rb"]
        assert len(gateway.store.list_sessions()) == 1


def test_task_runner_emits_write_ahead_events():
    def step(name):
        return Step(
            name=name,
            inputs={"type": "search", "q": "x"},
            expected_output={"type": "result"},
            completion_condition="done",
        )

    runner = TaskRunner(executor=lambda name, inputs: {"type": "result", "value": 1})
    task = runner.run(
        Task(
            task_id="task-runner",
            user_id="u1",
            agent_id="puspa",
            purpose="research and summarize five articles",
            plan=[step("search"), step("summarize")],
        )
    )
    assert task.status == "completed"
    events = runner.event_memory()
    kinds = [(e["type"], e["content"].get("kind")) for e in events]
    assert ("heartbeat", None) in kinds
    intents = [e for e in events if e["content"].get("kind") == "step_intent"]
    results = [e for e in events if e["content"].get("kind") == "step_result"]
    assert len(intents) == 2 and len(results) == 2
    for index, (intent, result) in enumerate(zip(intents, results)):
        assert intent["type"] == "tool_called"
        assert result["type"] == "tool_completed"
        assert intent["content"]["idempotency_key"] == result["content"]["idempotency_key"]
        assert intent["content"]["idempotency_key"] == idempotency_key(
            "task-runner", index, {"type": "search", "q": "x"}
        )


def test_kill_drill_all_boundaries(test_dsn, tmp_path):
    report = run_kill_drill(dsn=test_dsn, work_dir=tmp_path / "drill")
    assert report["passed"], report
    cases = report["cases"]
    assert len(cases) == 7

    ledger = Ledger(test_dsn)
    wal = WriteAheadLog(ledger)
    for label, case in cases.items():
        assert case["returncode"] == -9, label
        assert case["chain_ok"], label
        assert case["status"] in ("interrupted", "completed"), label

    # Killed before the outbound intent: no effect, nothing uncertain.
    before = cases["before_intent@step1"]
    assert before["status"] == "interrupted"
    assert before["outbound_effect_count"] == 0
    assert before["recovery"]["needs_user_decision"] == []

    # Killed after the outbound intent, before the effect: uncertain
    # outbound step, effect never happened, human must decide.
    after_intent = cases["after_intent@step1"]
    assert after_intent["status"] == "interrupted"
    assert after_intent["outbound_effect_count"] == 0
    assert len(after_intent["recovery"]["needs_user_decision"]) == 1
    assert after_intent["recovery"]["needs_user_decision"][0]["step_name"] == "send_quote"

    # Killed after the effect, before the result: the effect happened
    # exactly once and was never retried.
    after_effect = cases["after_effect@step1"]
    assert after_effect["status"] == "interrupted"
    assert after_effect["outbound_effect_count"] == 1
    assert len(after_effect["recovery"]["needs_user_decision"]) == 1

    # Killed after the result / during checkpoint: the outbound step
    # is done; the task is interrupted only because later steps
    # never ran.
    for label in ("after_result@step1", "checkpoint@step1"):
        assert cases[label]["status"] == "interrupted", label
        assert cases[label]["outbound_effect_count"] == 1, label
        assert cases[label]["recovery"]["needs_user_decision"] == [], label

    # Killed during a read-only step: recovery retries it once.
    read_retry = cases["after_intent@step0"]
    assert read_retry["status"] == "interrupted"
    assert read_retry["recovery"]["retried"] == [
        idempotency_key(read_retry["recovery"]["task_id"], 0, drill_steps()[0].args)
    ]
    assert read_retry["recovery"]["needs_user_decision"] == []

    # Killed after the last step's effect: recovery retries the
    # read-only step and the task completes.
    last = cases["after_effect@step2"]
    assert last["status"] == "completed"
    assert last["recovery"]["retried"] == [
        idempotency_key(last["recovery"]["task_id"], 2, drill_steps()[2].args)
    ]
    assert last["recovery"]["needs_user_decision"] == []
    assert wal.find_uncertain_steps(last["recovery"]["task_id"]) == []
    ok, broken = ledger.verify_chain(last["recovery"]["task_id"])
    assert ok and broken == []


def test_kill_drill_never_retries_outbound_effect(test_dsn, tmp_path):
    """Direct check: the drill executor is only called by recovery
    for read-only steps, so the outbound effect count cannot grow
    during recovery even when the step is uncertain."""
    effect_log = tmp_path / "effects.jsonl"
    task_id = "task-no-retry"
    ledger = Ledger(test_dsn)
    wal = WriteAheadLog(ledger)
    wal.record_task_created(task_id, "puspa")
    wal.record_intent(task_id, "puspa", 1, "send_quote", drill_steps()[1].args, outbound=True)
    report = recover_task(
        ledger, task_id, "puspa", drill_steps(), drill_executor(effect_log, task_id)
    )
    assert len(report["needs_user_decision"]) == 1
    assert not effect_log.exists()
