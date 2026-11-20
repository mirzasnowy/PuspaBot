"""Tests for the reference tasks and the cassette replay."""

from puspa.eval.reference_tasks import (
    REFERENCE_TASK_IDS,
    reference_tasks,
    run_reference_tasks,
    summarize_runs,
)
from puspa.eval.replay import record_run, replay_run


def test_ten_reference_tasks_exist():
    tasks = reference_tasks()
    assert [t.id for t in tasks] == REFERENCE_TASK_IDS
    assert len(tasks) == 10


def test_reference_tasks_pass_three_repetitions():
    runs = run_reference_tasks(repetitions=3)
    assert len(runs) == 30
    failures = [r for r in runs if not r.ok]
    assert failures == []
    summary = summarize_runs(runs)
    assert summary["successes"] == 30
    assert summary["trials"] == 30
    assert summary["total_cost_rupiah"] == 0.0


def test_reference_task_steps_are_recorded():
    runs = run_reference_tasks(repetitions=1)
    assert all(r.steps >= 1 for r in runs)
    by_id = {r.task_id: r for r in runs}
    assert by_id["RT-09"].steps == 1


def test_replay_is_identical_ten_of_ten(tmp_path):
    cassette = tmp_path / "cassette.jsonl"
    record_run(cassette, repetitions=1)
    comparison = replay_run(cassette, repetitions=1)
    assert comparison.total == 10
    assert comparison.identical == 10
    assert comparison.differing == []
    assert comparison.passed_gate
