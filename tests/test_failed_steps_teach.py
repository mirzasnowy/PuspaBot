"""Failed steps teach the world model (Gap 2).

End-to-end through the real `TaskRunner` (in-memory executor, no
database, no network): a step that aborts the run is folded into
the model, and the two failure kinds stay strictly separate:

- "gate_rejected" (handoff refused by the schema, budget
  exhausted): the step was refused by policy before the world
  could answer. Capability belief for the step goes down, the
  pending prediction is discarded WITHOUT scoring, price and
  bill beliefs are never touched, and no misprediction is
  reported to the learning loop.
- "world_failed" (the executor raised): the action ran and the
  world said no. A pending prediction is scored against the
  failure, so a miss becomes a misprediction in the learning
  loop, and capability belief for the step goes down.

A successful step still behaves exactly as before. Run with
`pytest tests/test_failed_steps_teach.py -q`.
"""

from puspa.memory.learning_loop import LearningLoop
from puspa.runtime.budget import BudgetTracker
from puspa.runtime.tasks import Step, Task, TaskRunner, create_task_id
from puspa.world.model import WorldModel


def _task(*steps):
    return Task(
        task_id=create_task_id(),
        user_id="user-1",
        agent_id="agent-1",
        purpose="Check the price and pay the bill",
        plan=list(steps),
    )


def _price_step(name="check_kopi_price", product="kopi", store="toko_a"):
    return Step(
        name=name,
        inputs={"type": "check_price", "product_id": product, "store": store},
        expected_output={"type": "result"},
    )


def _pay_step(name="pay_pln", bill_id="pln"):
    return Step(
        name=name,
        inputs={"type": "pay_bill", "bill_id": bill_id},
        expected_output={"type": "result"},
    )


def _generic_step(name="gather_notes"):
    return Step(
        name=name,
        inputs={"type": "request"},
        expected_output={"type": "result"},
    )


def _seed_price(model, product="kopi", store="toko_a", price=10000):
    model.ingest_graph([{
        "id": product, "kind": "product",
        "attributes": {"price": price, "store": store},
    }])


def _seed_bill(model, bill_id="pln", amount=50000, due_day=10):
    model.ingest_graph([{
        "id": bill_id, "kind": "bill",
        "attributes": {"amount": amount, "due_day": due_day,
                       "status": "not_due"},
    }])


def _capability(model, task_kind):
    """Capability forecast for a task kind (success/rate/count)."""
    return model.predict(
        {"type": "attempt_task", "task_kind": task_kind}).outcome


def _events_of(runner, event_type):
    return [e for e in runner.event_memory() if e["type"] == event_type]


def _task_failed(runner):
    failed = _events_of(runner, "task_failed")
    assert len(failed) == 1
    return failed[0]["content"]


# -- (a) handoff rejected: capability down, price untouched ---------


def test_handoff_rejected_teaches_capability_not_price():
    """Handoff refused: capability falls, price belief untouched."""
    model = WorldModel()
    loop = LearningLoop()
    _seed_price(model)
    step = _price_step()
    runner = TaskRunner(
        executor=lambda name, inputs: {"type": "wrong"},
        world_model=model,
        learning_loop=loop,
    )
    task = runner.run(_task(step))
    assert task.status == "failed"
    assert _events_of(runner, "handoff_rejected")
    content = _task_failed(runner)
    assert content["failure_kind"] == "gate_rejected"
    assert content["step"] == "check_kopi_price"

    # Capability for the step went down (one failure observation).
    capability = _capability(model, "check_kopi_price")
    assert capability["success"] is False
    assert capability["success_rate"] == 0.0
    assert capability["observations"] == 1

    # The price belief is exactly as seeded: a refusal says
    # nothing about prices.
    price = model.state["price_beliefs"]["kopi@toko_a"]
    assert price.last_price == 10000
    assert price.observations == 1

    # No misprediction anywhere: the world never answered, so
    # there is nothing to correct.
    assert loop.correction_for("check_kopi_price") is None
    assert loop.experiences == []
    assert model.mispredictions == []
    assert _events_of(runner, "world_misprediction") == []

    # The refusal itself is noted as a gate rejection.
    assert len(model.gate_rejections) == 1
    rejection = model.gate_rejections[0]
    assert rejection["task_kind"] == "check_kopi_price"
    assert rejection["failure_kind"] == "gate_rejected"
    assert rejection["action_type"] == "check_price"

    # The stale prediction was discarded, not left to be scored
    # against a later observation: a different price observed
    # afterwards is grounding only, never a misprediction.
    outcome = model.update({
        "action": {"type": "check_price", "product_id": "kopi",
                   "store": "toko_a", "task_kind": "check_kopi_price"},
        "observed": {"price": 12000}, "day": 0,
    })
    assert outcome["correct"] is None
    assert model.mispredictions == []
    assert loop.experiences == []


def test_handoff_rejected_generic_step_teaches_capability():
    """Handoff refused for a generic step: capability falls, no miss."""
    model = WorldModel()
    loop = LearningLoop()
    step = _generic_step()
    runner = TaskRunner(
        executor=lambda name, inputs: {"type": "wrong"},
        world_model=model,
        learning_loop=loop,
    )
    task = runner.run(_task(step))
    assert task.status == "failed"
    assert _task_failed(runner)["failure_kind"] == "gate_rejected"
    capability = _capability(model, "gather_notes")
    assert capability["success"] is False
    assert capability["success_rate"] == 0.0
    assert capability["observations"] == 1
    assert loop.experiences == []
    assert model.mispredictions == []
    assert len(model.gate_rejections) == 1
    assert model.gate_rejections[0]["action_type"] is None


# -- (b) budget exhausted: capability down, price untouched ---------


def test_budget_exhausted_teaches_capability_not_price():
    """Budget stop: the step that never ran still teaches capability."""
    model = WorldModel()
    loop = LearningLoop()
    _seed_price(model)
    budget = BudgetTracker(rupiah_max=2000, step_max=1)
    runner = TaskRunner(
        budget=budget,
        executor=lambda name, inputs: {"type": "result", "value": 1},
        world_model=model,
        learning_loop=loop,
    )
    task = runner.run(_task(_generic_step(), _price_step()))
    assert task.status == "failed"
    assert _events_of(runner, "budget_exhausted")
    content = _task_failed(runner)
    assert content["failure_kind"] == "gate_rejected"
    assert content["step"] == "check_kopi_price"

    # The step that never ran still counts as one failure for its
    # own capability belief; the step that did run counts as one
    # success for its own.
    stopped = _capability(model, "check_kopi_price")
    assert stopped["success"] is False
    assert stopped["success_rate"] == 0.0
    assert stopped["observations"] == 1
    ran = _capability(model, "gather_notes")
    assert ran["success"] is True
    assert ran["success_rate"] == 1.0
    assert ran["observations"] == 1

    # No prediction was ever made for the stopped step, and the
    # price belief is exactly as seeded.
    assert _events_of(runner, "world_prediction") == []
    price = model.state["price_beliefs"]["kopi@toko_a"]
    assert price.last_price == 10000
    assert price.observations == 1
    assert loop.experiences == []
    assert model.mispredictions == []
    assert len(model.gate_rejections) == 1
    assert model.gate_rejections[0]["task_kind"] == "check_kopi_price"


# -- (c) world differs from prediction: misprediction recorded ------


def test_executor_raise_scores_pending_prediction_as_misprediction():
    """World failure: predicted success, world said no, miss recorded."""
    model = WorldModel()
    loop = LearningLoop()
    _seed_bill(model)
    step = _pay_step()

    def failing_executor(name, inputs):
        raise RuntimeError("payment gateway unreachable")

    runner = TaskRunner(
        executor=failing_executor,
        world_model=model,
        learning_loop=loop,
    )
    task = runner.run(_task(step))
    assert task.status == "failed"
    content = _task_failed(runner)
    assert content["failure_kind"] == "world_failed"
    assert content["step"] == "pay_pln"

    # The prediction (pay succeeds) was scored against the failure:
    # a miss, recorded as a misprediction in the learning loop.
    correction = loop.correction_for("pay_pln")
    assert correction is not None
    assert correction["predicted"]["success"] is True
    assert correction["observed"] == {"success": False}
    assert len(model.mispredictions) == 1
    misses = _events_of(runner, "world_misprediction")
    assert len(misses) == 1
    assert misses[0]["content"]["failure_kind"] == "world_failed"
    assert misses[0]["content"]["predicted"]["success"] is True
    assert misses[0]["content"]["observed"] == {"success": False}

    # Capability went down for the step and for paying bills.
    step_capability = _capability(model, "pay_pln")
    assert step_capability["success"] is False
    assert step_capability["observations"] == 1
    pay_capability = _capability(model, "pay_bill")
    assert pay_capability["success"] is False
    assert pay_capability["observations"] == 1

    # The failure did not change what the bill costs or its paid flag.
    bill = model.state["bill_beliefs"]["pln"]
    assert bill.amount == 50000
    assert bill.paid is False
    assert model.gate_rejections == []


def test_executor_raise_generic_step_teaches_capability_only():
    """World failure with no prediction: capability falls, no invented miss."""
    model = WorldModel()
    loop = LearningLoop()

    def failing_executor(name, inputs):
        raise RuntimeError("tool crashed")

    runner = TaskRunner(
        executor=failing_executor,
        world_model=model,
        learning_loop=loop,
    )
    task = runner.run(_task(_generic_step()))
    assert task.status == "failed"
    assert _task_failed(runner)["failure_kind"] == "world_failed"
    capability = _capability(model, "gather_notes")
    assert capability["success"] is False
    assert capability["observations"] == 1
    # No prediction existed, so no misprediction may be invented.
    assert loop.experiences == []
    assert model.mispredictions == []
    assert _events_of(runner, "world_misprediction") == []
    assert model.gate_rejections == []


def test_world_miss_on_successful_step_corrects_price():
    """World differs on a completed step: price belief may change."""
    model = WorldModel()
    loop = LearningLoop()
    _seed_price(model)
    step = _price_step()
    runner = TaskRunner(
        executor=lambda name, inputs: {
            "type": "result", "price": 12000,
            "product_id": "kopi", "store": "toko_a", "success": True,
        },
        world_model=model,
        learning_loop=loop,
    )
    task = runner.run(_task(step))
    assert task.status == "completed"
    correction = loop.correction_for("check_kopi_price")
    assert correction is not None
    assert correction["predicted"]["price"] == 10000
    assert correction["observed"]["price"] == 12000
    assert len(_events_of(runner, "world_misprediction")) == 1
    # A world miss is allowed to move the price belief.
    price = model.state["price_beliefs"]["kopi@toko_a"]
    assert price.last_price == 12000
    assert model.gate_rejections == []


# -- (d) successful steps behave as before --------------------------


def test_successful_steps_unchanged():
    """Success path: completed, prediction observed, no failure folding."""
    model = WorldModel()
    loop = LearningLoop()
    _seed_price(model)

    def executor(name, inputs):
        if name == "gather_notes":
            return {"type": "result", "value": 1}
        return {"type": "result", "price": 10000,
                "product_id": "kopi", "store": "toko_a", "success": True}

    runner = TaskRunner(
        executor=executor,
        world_model=model,
        learning_loop=loop,
    )
    task = runner.run(_task(_generic_step(), _price_step()))
    assert task.status == "completed"
    assert task.stage == "present"
    assert _events_of(runner, "task_failed") == []
    assert _events_of(runner, "world_misprediction") == []
    # One prediction (the price step); the generic step was not predicted.
    assert len(_events_of(runner, "world_prediction")) == 1
    # The completed generic step taught capability success.
    assert _capability(model, "gather_notes")["success_rate"] == 1.0
    # The price matched the prediction, so the belief only gained
    # one more observation and no correction exists.
    price = model.state["price_beliefs"]["kopi@toko_a"]
    assert price.last_price == 10000
    assert price.observations == 2
    assert loop.experiences == []
    assert model.mispredictions == []
    assert model.gate_rejections == []


def test_no_brain_run_failure_becomes_failed_task():
    """Without a brain, a crashing executor fails the task, not the run."""

    def crashing_executor(name, inputs):
        raise RuntimeError("tool crashed")

    runner = TaskRunner(executor=crashing_executor)
    task = runner.run(_task(_generic_step()))
    assert task.status == "failed"
    content = _task_failed(runner)
    assert content["failure_kind"] == "world_failed"
    assert content["step"] == "gather_notes"


# -- model unit tests: the two failure paths, directly --------------


def test_model_ingest_step_failure_gate_rejected():
    """Gate refusal at the model level: capability only, nothing else."""
    model = WorldModel()
    model.learning_loop = LearningLoop()
    _seed_price(model)
    action = {"type": "check_price", "product_id": "kopi",
              "store": "toko_a", "task_kind": "check_kopi_price"}
    prediction = model.predict(action)
    assert prediction.outcome["price"] == 10000
    summary = model.ingest_step_failure(
        task_kind="check_kopi_price", action=action,
        failure_kind="gate_rejected", reason="keluaran tidak lolos skema")
    assert summary["failure_kind"] == "gate_rejected"
    assert summary["discarded_pending"] is True
    assert summary["scored"] is None
    assert model._pending is None
    price = model.state["price_beliefs"]["kopi@toko_a"]
    assert price.last_price == 10000
    assert price.observations == 1
    assert model.learning_loop.experiences == []
    assert model.mispredictions == []
    assert len(model.gate_rejections) == 1


def test_model_ingest_step_failure_world_failed_attempt_task_once():
    """World failure on attempt_task: one failure observation, not two."""
    model = WorldModel()
    loop = LearningLoop()
    model.learning_loop = loop
    # Two successes first, so the prediction says success; still
    # trial (verified needs three), so the failure folds directly.
    for _ in range(2):
        model.ingest_task_result(
            {"task": "try_pay", "success": True, "result": {}})
    action = {"type": "attempt_task", "task_kind": "try_pay"}
    assert model.predict(action).outcome["success"] is True
    summary = model.ingest_step_failure(
        task_kind="try_pay", action=action,
        failure_kind="world_failed", reason="RuntimeError: tool crashed")
    assert summary["scored"] is not None
    assert summary["scored"]["correct"] is False
    assert summary["discarded_pending"] is False
    assert loop.correction_for("try_pay") is not None
    assert loop.correction_for("try_pay")["observed"] == {"success": False}
    # Exactly one failure observation: update() already taught
    # capability for this task kind, so no second one is applied.
    belief = model.state["capability_beliefs"]["try_pay"]
    assert belief.successes == 2
    assert belief.failures == 1
    assert belief.observations == 3


def test_model_discard_pending_prediction():
    """A pending prediction can be dropped unscored, or left alone."""
    model = WorldModel()
    action = {"type": "check_price", "product_id": "kopi",
              "store": "toko_a", "task_kind": "check_kopi_price"}
    assert model.discard_pending_prediction(action) is False
    model.predict(action)
    other = {"type": "check_bill", "bill_id": "pln"}
    assert model.discard_pending_prediction(other) is False
    assert model._pending is not None
    assert model.discard_pending_prediction(action) is True
    assert model._pending is None
    assert model.discard_pending_prediction() is False


def test_model_ingest_step_failure_rejects_unknown_kind():
    """An unknown failure kind is a programmer error, not a silent fold."""
    model = WorldModel()
    try:
        model.ingest_step_failure(
            task_kind="some_step", failure_kind="mystery")
    except ValueError as exc:
        assert "mystery" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown failure kind")
