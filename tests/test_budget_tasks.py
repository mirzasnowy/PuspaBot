"""Tes anggaran + siklus tugas 5 tahap (§2.4)."""

import pytest

from puspa.runtime.budget import BudgetExhausted, BudgetTracker, calibrate_from_ledger
from puspa.runtime.tasks import Step, Task, TaskError, TaskRunner, create_plan


def _step(name="search", jenis_logout="result"):
    return Step(
        name=name,
        inputs={"type": "search", "q": "x"},
        expected_output={"type": jenis_logout},
        completion_condition="hasil ditemukan",
    )


def test_budget_step_and_rupiah():
    b = BudgetTracker(rupiah_max=2000, step_max=25)
    b.record_step(25)
    with pytest.raises(BudgetExhausted):
        b.record_step(1)
    b2 = BudgetTracker()
    b2.record_cost(1999)
    with pytest.raises(BudgetExhausted):
        b2.record_cost(2)


def test_budget_subagent_narrows_downward():
    b = BudgetTracker(rupiah_max=2000, step_max=25)
    b.record_step(20)
    b.record_cost(1500)
    sub = b.subagent_budget()
    assert sub["step_max"] == 5
    assert sub["rupiah_max"] == 500


def test_calibration_from_ledger_without_honest_data():
    result = calibrate_from_ledger([])
    assert result["amount_task"] == 0 and result["average_rp"] is None


def test_calibration_from_ledger_with_data():
    event = [
        {"task_id": "t1", "content": {"cost_rp": 500}},
        {"task_id": "t1", "content": {"cost_rp": 300}},
        {"task_id": "t2", "content": {"cost_rp": 1200}},
    ]
    result = calibrate_from_ledger(event)
    assert result["amount_task"] == 2
    assert result["average_rp"] == 1000
    assert result["max_rp"] == 1200


def test_cycle_five_stage_completed():
    runner = TaskRunner(executor=lambda name, inputs: {"type": "result", "value": 42})
    task = runner.run(
        Task(
            task_id="tugas-1",
            user_id="u1",
            agent_id="puspa",
            purpose="riset dan rangkum lima artikel",
            plan=[_step("search"), _step("rangkum")],
        )
    )
    assert task.status == "completed" and task.stage == "present"
    event_type = [e["type"] for e in runner.event_memory()]
    for required in (
        "task_created",
        "plan_created",
        "handoff_proposed",
        "handoff_handed_over",
        "verification_completed",
        "task_completed",
    ):
        assert required in event_type


def test_ambiguous_purpose_asks_one_question():
    runner = TaskRunner()
    task = runner.run(Task(task_id="tugas-2", user_id="u1", agent_id="puspa", purpose="cek harga"))
    assert task.status == "pending_user"
    assert task.question  # maks 1 pertanyaan


def test_plan_action_logout_stops_at_approval():
    step = _step("send")
    step.outbound_action = True
    runner = TaskRunner()
    task = runner.run(
        Task(
            task_id="tugas-3",
            user_id="u1",
            agent_id="puspa",
            purpose="kirim email ke tim keuangan",
            plan=[step],
        )
    )
    assert task.status == "pending_approval"
    assert task.result == {}


def test_step_failing_schema_fails_task():
    runner = TaskRunner(executor=lambda name, inputs: {"type": "wrong"})
    task = runner.run(
        Task(
            task_id="tugas-4",
            user_id="u1",
            agent_id="puspa",
            purpose="riset dan rangkum lima artikel",
            plan=[_step()],
        )
    )
    assert task.status == "failed"


def test_budget_exhausted_task_stops_and_reports():
    budget = BudgetTracker(rupiah_max=2000, step_max=1)
    runner = TaskRunner(budget=budget, executor=lambda name, inputs: {"type": "result", "value": 1})
    task = runner.run(
        Task(
            task_id="tugas-5",
            user_id="u1",
            agent_id="puspa",
            purpose="riset dan rangkum lima artikel",
            plan=[_step("a"), _step("b")],
        )
    )
    assert task.status == "failed"
    assert "budget_exhausted" in [e["type"] for e in runner.event_memory()]


def test_plan_with_more_than_25_steps_rejected():
    with pytest.raises(TaskError):
        create_plan("tujuan panjang untuk uji batas langkah", [_step(f"l{i}") for i in range(26)])


def test_tombstone_delete_task_without_update_ledger(tmp_path):
    """C129: hapus tugas = append tombstone, rantai tetap utuh; kaset ikut terhapus."""
    from puspa.cassette.cassette import save_cassette
    from puspa.runtime.tasks import tombstone_task

    save_cassette({"task_id": "t-hapus-2", "recording": []}, "kaset-hapus-2", cassette_dir=tmp_path)
    event = []
    result = tombstone_task(
        "t-hapus-2",
        "user-1",
        "puspa",
        sink=lambda event_type, content: event.append({"type": event_type, "content": content}),
        cassette_dir=tmp_path,
    )
    assert result["tombstone"] is True
    assert result["cassette_deleted"] == ["kaset-hapus-2"]
    assert event[0]["type"] == "task_stopped"
    assert event[0]["content"]["deleted"] is True
