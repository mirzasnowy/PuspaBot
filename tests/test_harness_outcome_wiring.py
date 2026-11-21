"""Eval-harness outcome wiring (Kosmos K8 follow-up).

The eval harness registers every task that passes with the
outcome watcher, and cassette replay registers a task recorded
as passing that newly fails — both feed the same watcher, so a
task that was done and later fails is caught as a late failure
instead of staying "done" forever.

No live provider, no network, no database: the watcher is
in-memory, the writer and the learning loop are fakes, the
harness runs inline cassettes, and the replay tasks are fakes
monkeypatched in. The real reference tasks are deterministic
and cheap, but fakes keep the flip under the test's control:
a real task cannot be made to fail on demand.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from puspa.eval.reference_tasks import REFERENCE_TASK_IDS, TaskOutcome
from puspa.eval.replay import record_run, replay_run
from puspa.evidence.eval_harness import EVAL_WATCH_USER_ID, EvalHarness, EvalTask
from puspa.sentinel.outcome_watcher import OutcomeWatcher

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)


class _FakeWriter:
    """A ledger writer that only records what it was asked to write."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        record = {"outcome_id": f"evt-late-{len(self.calls)}"}
        record.update(kwargs)
        return record


class _FakeLoop:
    """A learning loop that only records what it was fed."""

    def __init__(self):
        self.calls: list[dict] = []

    def record_misprediction(self, record, *, condition="", evidence_event_id=None):
        self.calls.append(
            {"record": dict(record), "condition": condition, "evidence_event_id": evidence_event_id}
        )
        return {"task": record["task"], "quarantined": False}


def _watcher():
    writer = _FakeWriter()
    loop = _FakeLoop()
    return OutcomeWatcher(record_outcome=writer, learning_loop=loop), writer, loop


def _harness_task(expected, *, task_id="eval-t1", name="wired_task"):
    """An EvalTask with an inline (empty) cassette and a fixed expectation."""
    return EvalTask(
        task_id=task_id,
        name=name,
        prompt="A wired task.",
        cassette_name="wired",
        expected_state=dict(expected),
        cassette={"recording": []},
    )


def _state_solver(states):
    """A solver that returns the next state per call (then repeats the last)."""
    calls = {"n": 0}

    def solver(task, player):
        state = states[min(calls["n"], len(states) - 1)]
        calls["n"] += 1
        return dict(state)

    return solver


# ---------------------------------------------------------------------------
# EvalHarness registration
# ---------------------------------------------------------------------------


def test_passing_task_is_watched_and_failing_task_is_not():
    watcher, _, _ = _watcher()
    harness = EvalHarness(solver=_state_solver([{"flag": 1}]), watcher=watcher)
    passing = _harness_task({"flag": 1})
    failing = _harness_task({"flag": 9}, task_id="eval-t2", name="failing_task")
    assert harness.run_task(passing).passed is True
    assert harness.run_task(failing).passed is False
    assert len(watcher) == 1
    watched = watcher.pending()[0]
    assert watched.outcome_id == "eval:eval-t1"
    assert watched.task == "wired_task"
    assert watched.observed == "passed"
    assert watched.user_id == EVAL_WATCH_USER_ID
    assert watched.user_id == "eval-harness"


def test_harness_without_a_watcher_runs_exactly_as_before():
    harness = EvalHarness(solver=_state_solver([{"flag": 1}]))
    assert harness.watcher is None
    assert harness.run_task(_harness_task({"flag": 1})).passed is True
    assert harness.run_task(_harness_task({"flag": 9}, task_id="eval-t2")).passed is False


def test_passing_then_failing_task_is_a_late_failure():
    watcher, writer, loop = _watcher()
    harness = EvalHarness(solver=_state_solver([{"flag": 1}, {"flag": 2}]), watcher=watcher)
    assert harness.run_task(_harness_task({"flag": 1})).passed is True
    assert len(watcher) == 1
    results = watcher.check_due(now=NOW + timedelta(days=2))
    assert [result.status for result in results] == ["late_failure"]
    (result,) = results
    assert result.fresh_observed == "failed"
    assert result.outcome_record is not None
    assert writer.calls and writer.calls[0]["outcome_type"] == "late_failure"
    assert writer.calls[0]["user_id"] == EVAL_WATCH_USER_ID
    assert len(loop.calls) == 1
    fed = loop.calls[0]["record"]
    assert fed["predicted"] == "passed"
    assert fed["observed"] == "failed"
    assert result.issue is not None
    assert result.issue["detector"] == "outcome_failure"


def test_still_passing_task_is_ok_not_flagged():
    watcher, writer, loop = _watcher()
    harness = EvalHarness(solver=_state_solver([{"flag": 1}]), watcher=watcher)
    assert harness.run_task(_harness_task({"flag": 1})).passed is True
    results = watcher.check_due(now=NOW + timedelta(days=2))
    assert [result.status for result in results] == ["ok"]
    assert writer.calls == []
    assert loop.calls == []


def test_recheck_does_not_rearm_the_watch():
    watcher, _, _ = _watcher()
    harness = EvalHarness(solver=_state_solver([{"flag": 1}]), watcher=watcher)
    assert harness.run_task(_harness_task({"flag": 1})).passed is True
    assert len(watcher) == 1
    assert watcher.check_due(now=NOW + timedelta(days=2))[0].status == "ok"
    assert len(watcher) == 0
    assert watcher.check_due(now=NOW + timedelta(days=3)) == []


def test_a_recheck_that_raises_is_unverifiable_not_a_crash():
    watcher, writer, loop = _watcher()

    def raising_solver(task, player):
        if raising_solver.calls == 0:
            raising_solver.calls = 1
            return {"flag": 1}
        raise RuntimeError("the world is gone")

    raising_solver.calls = 0
    harness = EvalHarness(solver=raising_solver, watcher=watcher)
    assert harness.run_task(_harness_task({"flag": 1})).passed is True
    results = watcher.check_due(now=NOW + timedelta(days=2))
    assert [result.status for result in results] == ["unverifiable"]
    assert writer.calls == []
    assert loop.calls == []


# ---------------------------------------------------------------------------
# replay_run detection
# ---------------------------------------------------------------------------


class _FakeRefTask:
    """A reference task whose outcome the test controls."""

    def __init__(self, task_id, *, ok=True, steps=1):
        self.id = task_id
        self.name = f"fake {task_id}"
        self._ok = ok
        self._steps = steps

    def execute(self):
        return TaskOutcome(ok=self._ok, steps=self._steps)


def _fake_tasks(monkeypatch, *, failing=()):
    """Ten fake reference tasks; ``failing`` ids execute as not-ok."""
    tasks = [_FakeRefTask(task_id, ok=task_id not in failing) for task_id in REFERENCE_TASK_IDS]
    monkeypatch.setattr("puspa.eval.replay.reference_tasks", lambda: list(tasks))
    monkeypatch.setattr("puspa.eval.reference_tasks.reference_tasks", lambda: list(tasks))
    return tasks


def test_replay_registers_a_newly_failing_task(tmp_path, monkeypatch):
    watcher, writer, loop = _watcher()
    _fake_tasks(monkeypatch)
    cassette = tmp_path / "cassette.jsonl"
    record_run(cassette, repetitions=1)
    _fake_tasks(monkeypatch, failing={"RT-03"})
    comparison = replay_run(cassette, repetitions=1, watcher=watcher)
    assert comparison.total == 10
    assert comparison.identical == 9
    assert len(comparison.differing) == 1
    assert comparison.differing[0].startswith("RT-03:")
    assert len(watcher) == 1
    watched = watcher.pending()[0]
    assert watched.outcome_id == "replay:RT-03"
    assert watched.observed == "passed"
    assert watched.user_id == EVAL_WATCH_USER_ID
    results = watcher.check_due(now=NOW + timedelta(days=2))
    assert [result.status for result in results] == ["late_failure"]
    assert writer.calls and writer.calls[0]["outcome_type"] == "late_failure"
    assert len(loop.calls) == 1
    assert loop.calls[0]["record"]["observed"] == "failed"


def test_replay_still_passing_tasks_are_not_registered(tmp_path, monkeypatch):
    watcher, writer, loop = _watcher()
    _fake_tasks(monkeypatch)
    cassette = tmp_path / "cassette.jsonl"
    record_run(cassette, repetitions=1)
    comparison = replay_run(cassette, repetitions=1, watcher=watcher)
    assert comparison.identical == 10
    assert comparison.differing == []
    assert len(watcher) == 0
    assert writer.calls == []
    assert loop.calls == []


def test_replay_without_a_watcher_still_reports_the_flip(tmp_path, monkeypatch):
    _fake_tasks(monkeypatch)
    cassette = tmp_path / "cassette.jsonl"
    record_run(cassette, repetitions=1)
    _fake_tasks(monkeypatch, failing={"RT-03"})
    comparison = replay_run(cassette, repetitions=1)
    assert comparison.identical == 9
    assert len(comparison.differing) == 1
    assert comparison.differing[0].startswith("RT-03:")


def test_replay_task_that_was_already_failing_is_not_a_late_failure(tmp_path, monkeypatch):
    watcher, _, _ = _watcher()
    _fake_tasks(monkeypatch, failing={"RT-03"})
    cassette = tmp_path / "cassette.jsonl"
    record_run(cassette, repetitions=1)
    comparison = replay_run(cassette, repetitions=1, watcher=watcher)
    assert comparison.identical == 10
    assert len(watcher) == 0


def test_replay_task_that_newly_passes_is_not_a_late_failure(tmp_path, monkeypatch):
    watcher, _, _ = _watcher()
    _fake_tasks(monkeypatch, failing={"RT-03"})
    cassette = tmp_path / "cassette.jsonl"
    record_run(cassette, repetitions=1)
    _fake_tasks(monkeypatch)
    comparison = replay_run(cassette, repetitions=1, watcher=watcher)
    assert comparison.identical == 9
    assert len(comparison.differing) == 1
    assert comparison.differing[0].startswith("RT-03:")
    assert len(watcher) == 0
