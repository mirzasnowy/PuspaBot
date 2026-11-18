"""Wiring tests: world model + learning loop in the live task loop.

End-to-end through the real `TaskRunner` (in-memory executor, no
database, no network): the model predicts before acting, the real
result is observed, a miss is recorded in the learning loop, and
the next prediction for the same task is corrected. Plus the
session-brain guarantee (one WorldModel + LearningLoop shared
across turns) and the fail-open behaviour when the brain is absent
or broken. Run with `pytest tests/test_world_brain_wiring.py -q
--noconftest` (no Postgres needed).
"""

import sys
from pathlib import Path

# Same fallback as tests/conftest.py: when a local nanobot clone
# exists (./nanobot), import from there; otherwise nanobot comes
# from the installed dependency. Needed here because --noconftest
# skips conftest, and `puspa.chassis` imports nanobot.
_NANOBOT_LOCAL = Path(__file__).resolve().parent.parent / "nanobot"
if _NANOBOT_LOCAL.exists():
    sys.path.insert(0, str(_NANOBOT_LOCAL))

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from puspa.chassis import agent_session
from puspa.chassis.factory import PuspaChassis
from puspa.memory.learning_loop import LearningLoop
from puspa.runtime.planner import Planner
from puspa.runtime.tasks import Step, Task, TaskRunner, create_task_id
from puspa.scheduler.curriculum import CurriculumRunner
from puspa.world.model import WorldModel


def _estimate_step(name="estimate_check_pln", task_kind="check_pln"):
    return Step(
        name=name,
        inputs={"type": "estimate_task", "task_kind": task_kind},
        expected_output={"type": "result"},
    )


def _estimate_task():
    return Task(
        task_id=create_task_id(),
        user_id="user-1",
        agent_id="agen-1",
        purpose="Estimate how long check pln takes",
        plan=[_estimate_step()],
    )


def _duration_executor(duration_days):
    def run(name, inputs):
        return {
            "type": "result",
            "duration_days": duration_days,
            "deadline_days": 3,
            "success": True,
        }

    return run


def _events_of(runner, event_type):
    return [e for e in runner.event_memory() if e["type"] == event_type]


def test_e2e_miss_recorded_then_next_prediction_corrected():
    """Real TaskRunner: predict, miss, record, corrected next time."""
    model = WorldModel()
    loop = LearningLoop()
    runner = TaskRunner(
        executor=_duration_executor(5),
        world_model=model,
        learning_loop=loop,
    )
    task = runner.run(_estimate_task())
    assert task.status == "completed"

    # The model predicted before acting: unknown duration, advisory.
    predictions = _events_of(runner, "world_prediction")
    assert len(predictions) == 1
    assert predictions[0]["content"]["predicted"]["expected_days"] is None
    assert predictions[0]["content"]["advisory"] is True
    # The same advisory prediction is attached to the handoff event,
    # which is the copy the ledger keeps (closed event-type set).
    proposed = _events_of(runner, "handoff_proposed")
    assert proposed[0]["content"]["world_prediction"]["predicted"][
        "expected_days"
    ] is None

    # The miss was recorded in the learning loop, keyed by step name.
    correction = loop.correction_for("estimate_check_pln")
    assert correction is not None
    assert correction["observed"]["duration_days"] == 5
    assert loop.predict("estimate_check_pln")["duration_days"] == 5
    assert len(_events_of(runner, "world_misprediction")) == 1
    # The model itself learned the duration from the observation.
    assert (
        model.predict({"type": "estimate_task", "task_kind": "check_pln"})
        .outcome["expected_days"]
        == 5.0
    )

    # Second run of the same task, same brain: the next prediction
    # is corrected, and no new misprediction is recorded.
    experiences_before = len(loop.experiences)
    runner2 = TaskRunner(
        executor=_duration_executor(5),
        world_model=model,
        learning_loop=loop,
    )
    task2 = runner2.run(_estimate_task())
    assert task2.status == "completed"
    predictions2 = _events_of(runner2, "world_prediction")
    assert predictions2[0]["content"]["predicted"]["expected_days"] == 5.0
    assert _events_of(runner2, "world_misprediction") == []
    assert len(loop.experiences) == experiences_before


def test_unmapped_step_folds_outcome_without_prediction():
    """A step with no world action is not predicted but still teaches."""
    model = WorldModel()
    loop = LearningLoop()
    step = Step(
        name="gather_listings",
        inputs={"type": "request"},
        expected_output={"type": "result"},
    )
    task = Task(
        task_id=create_task_id(),
        user_id="user-1",
        agent_id="agen-1",
        purpose="Gather listings for the user now",
        plan=[step],
    )
    runner = TaskRunner(
        executor=lambda name, inputs: {"type": "result", "value": "ok"},
        world_model=model,
        learning_loop=loop,
    )
    assert runner.run(task).status == "completed"
    assert _events_of(runner, "world_prediction") == []
    # The completed step taught the capability belief.
    assert (
        model.predict(
            {"type": "attempt_task", "task_kind": "gather_listings"}
        ).outcome["success"]
        is True
    )


def test_broken_model_fails_open_to_plain_runner():
    """A model that raises on every call must not fail the task."""

    class _BrokenModel:
        def predict(self, action, state=None):
            raise RuntimeError("boom")

        def update(self, observation):
            raise RuntimeError("boom")

        def ingest_task_result(self, task):
            raise RuntimeError("boom")

    runner = TaskRunner(
        executor=_duration_executor(5),
        world_model=_BrokenModel(),
        learning_loop=LearningLoop(),
    )
    assert runner.run(_estimate_task()).status == "completed"
    assert _events_of(runner, "world_prediction") == []


def test_no_brain_is_plain_runner():
    """Without a model and loop the runner behaves exactly as before."""
    runner = TaskRunner(executor=_duration_executor(5))
    assert runner.run(_estimate_task()).status == "completed"
    assert _events_of(runner, "world_prediction") == []
    assert _events_of(runner, "world_misprediction") == []
    assert [e["type"] for e in runner.event_memory()] == [
        "task_created",
        "plan_created",
        "handoff_proposed",
        "handoff_handed_over",
        "verification_completed",
        "task_completed",
    ]


def _bind(monkeypatch, agent_id="agen-9", **kwargs):
    monkeypatch.setattr(
        agent_session, "validate_session", lambda token, dsn=None: "user-9"
    )
    keys = Ed25519PrivateKey.generate()
    return agent_session.bind_turn(
        session_token="tok",
        signing_key=keys,
        public_key=keys.public_key(),
        agent_id=agent_id,
        tools=["read"],
        **kwargs,
    )


def test_session_brain_is_shared_across_turns(monkeypatch):
    """Two turns for one (user, agent) share one brain, not two."""
    agent_session.reset_session_brains()
    _bind(monkeypatch)
    brain1 = agent_session.get_session_brain("user-9", "agen-9")
    brain1.world_model.ingest_task_result(
        {
            "task": "pay_bill",
            "success": True,
            "result": {"duration_days": 2, "deadline_days": 3, "day": 1},
        }
    )
    _bind(monkeypatch)  # second turn, no brain passed
    brain2 = agent_session.get_session_brain("user-9", "agen-9")
    assert brain2 is brain1
    assert brain2.world_model is brain1.world_model
    assert brain2.learning_loop is brain1.learning_loop
    # What turn 1 learned is still there in turn 2.
    assert (
        brain2.world_model.predict(
            {"type": "estimate_task", "task_kind": "pay_bill"}
        ).outcome["expected_days"]
        == 2.0
    )
    agent_session.reset_session_brains()


def test_bind_turn_registers_explicit_brain(monkeypatch):
    """A brain passed to bind_turn becomes the session brain."""
    agent_session.reset_session_brains()
    model = WorldModel()
    loop = LearningLoop()
    _bind(monkeypatch, world_model=model, learning_loop=loop)
    brain = agent_session.get_session_brain("user-9", "agen-9")
    assert brain.world_model is model
    assert brain.learning_loop is loop
    assert model.learning_loop is loop
    # A later turn without a brain reuses the registered one.
    _bind(monkeypatch)
    assert agent_session.get_session_brain("user-9", "agen-9") is brain
    agent_session.reset_session_brains()


def test_chassis_carries_the_session_brain():
    """PuspaChassis.brain returns the same pair the session holds."""
    model = WorldModel()
    loop = LearningLoop()
    chassis = PuspaChassis(world_model=model, learning_loop=loop)
    assert chassis.brain() == (model, loop)
    assert PuspaChassis().brain() is None


def test_planner_forwards_brain_to_runner():
    """Planner passes the brain through to the TaskRunner it builds."""
    model = WorldModel()
    loop = LearningLoop()
    planner = Planner(
        executor=_duration_executor(5),
        world_model=model,
        learning_loop=loop,
    )
    result = planner.run(_estimate_task())
    assert result.status == "completed"
    assert loop.correction_for("estimate_check_pln") is not None
    assert (
        model.predict({"type": "estimate_task", "task_kind": "check_pln"})
        .outcome["expected_days"]
        == 5.0
    )


def test_curriculum_runner_forwards_brain():
    """A curriculum retry runs through the brain and clears the gap."""
    loop = LearningLoop()
    loop.record_misprediction(
        {
            "task": "check_pln",
            "predicted": {"expected_days": None},
            "observed": {"duration_days": 5},
            "error": 1.0,
            "context": {},
        }
    )
    model = WorldModel()

    def plan_builder(proposal, purpose):
        return [
            Step(
                name=proposal["task"],
                inputs={
                    "type": "estimate_task",
                    "task_kind": proposal["task"],
                },
                expected_output={"type": "result"},
            )
        ]

    runner = CurriculumRunner(
        loop=loop,
        executor=_duration_executor(5),
        is_idle=lambda: True,
        plan_builder=plan_builder,
        world_model=model,
        learning_loop=loop,
    )
    outcome = runner.run_once()
    assert outcome["status"] == "completed"
    assert "check_pln" not in loop.gaps
    assert (
        model.predict({"type": "estimate_task", "task_kind": "check_pln"})
        .outcome["expected_days"]
        == 5.0
    )
