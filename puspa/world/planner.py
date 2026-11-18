"""Multi-step planning by simulating against the world model.

``plan(model, task)`` never touches the real world and never mutates
the live model: it copies the model state, forecasts each step with
``WorldModel.predict`` on that copy, and applies each forecast with
``WorldModel.apply_predicted`` to build the state the next step sees.
A plan is therefore a rehearsal, not an execution.

Supported tasks (all values English):

- ``{"goal": "pay_bill", "bill_id": ...}``: check the bill, pay it
  when it is unpaid, then check again to verify it is paid.
- ``{"goal": "find_cheapest_price", "product_id": ...}``: check the
  price at every known store, then buy from the cheapest one.
- ``{"goal": "sequence", "steps": [action, ...]}``: simulate the
  given actions in order.

The returned plan is ``{"task": ..., "steps": [...], "feasible":
bool, "predicted_outcome": {...}}`` where each step holds its action
plus the predicted outcome and confidence for that step.
"""

from __future__ import annotations

import copy

from puspa.world.model import WorldModel
from puspa.world.simulator import STORES

MAX_PLAN_STEPS = 10


def plan(model: WorldModel, task: dict) -> dict:
    """Plan a multi-step task by simulating it against the world model."""
    goal = task.get("goal")
    if goal == "pay_bill":
        return _plan_pay_bill(model, task)
    if goal == "find_cheapest_price":
        return _plan_find_cheapest(model, task)
    if goal == "sequence":
        return _plan_sequence(model, task)
    return {
        "task": task,
        "steps": [],
        "feasible": False,
        "predicted_outcome": {},
        "reason": "unknown_goal",
    }


def _simulate_steps(
    model: WorldModel, actions: list[dict], state: dict | None = None
) -> tuple[list[dict], dict]:
    """Simulate actions in order on a copy of the model state."""
    state = copy.deepcopy(state if state is not None else model.state)
    steps = []
    for action in actions:
        prediction = model.predict(action, state=state)
        steps.append(
            {
                "action": dict(action),
                "predicted_outcome": prediction.outcome,
                "confidence": prediction.confidence,
            }
        )
        state = model.apply_predicted(state, action, prediction.outcome)
    return steps, state


def _plan_pay_bill(model: WorldModel, task: dict) -> dict:
    bill_id = str(task["bill_id"])
    check = {"type": "check_bill", "bill_id": bill_id}
    steps, state = _simulate_steps(model, [check])
    first = steps[0]["predicted_outcome"]
    if first.get("status") == "paid":
        return {
            "task": task,
            "steps": steps,
            "feasible": True,
            "predicted_outcome": {"status": "paid", "amount_due": 0, "already_paid": True},
        }
    if first.get("status") == "unknown":
        return {
            "task": task,
            "steps": steps,
            "feasible": False,
            "predicted_outcome": {"status": "unknown"},
            "reason": "unknown_bill",
        }
    pay = {"type": "pay_bill", "bill_id": bill_id}
    more_steps, _final_state = _simulate_steps(model, [pay, check], state=state)
    steps.extend(more_steps)
    final_status = more_steps[-1]["predicted_outcome"].get("status")
    return {
        "task": task,
        "steps": steps,
        "feasible": final_status == "paid",
        "predicted_outcome": {"status": final_status, "amount_due": 0},
    }


def _plan_find_cheapest(model: WorldModel, task: dict) -> dict:
    product_id = str(task["product_id"])
    stores = list(task.get("stores") or STORES)
    actions = [
        {"type": "check_price", "product_id": product_id, "store": store} for store in stores
    ]
    steps, state = _simulate_steps(model, actions)
    known = [
        (step["action"]["store"], step["predicted_outcome"]["price"])
        for step in steps
        if step["predicted_outcome"].get("known")
    ]
    if not known:
        return {
            "task": task,
            "steps": steps,
            "feasible": False,
            "predicted_outcome": {},
            "reason": "no_known_price",
        }
    cheapest_store, cheapest_price = min(known, key=lambda item: item[1])
    buy = {"type": "buy_from_store", "product_id": product_id, "store": cheapest_store}
    buy_steps, _ = _simulate_steps(model, [buy], state=state)
    steps.extend(buy_steps)
    return {
        "task": task,
        "steps": steps,
        "feasible": True,
        "predicted_outcome": {"cheapest_store": cheapest_store, "cheapest_price": cheapest_price},
    }


def _plan_sequence(model: WorldModel, task: dict) -> dict:
    actions = list(task.get("steps") or [])
    if not actions:
        return {
            "task": task,
            "steps": [],
            "feasible": False,
            "predicted_outcome": {},
            "reason": "empty_sequence",
        }
    if len(actions) > MAX_PLAN_STEPS:
        return {
            "task": task,
            "steps": [],
            "feasible": False,
            "predicted_outcome": {},
            "reason": "too_many_steps",
        }
    steps, _ = _simulate_steps(model, actions)
    last = steps[-1]["predicted_outcome"] if steps else {}
    return {"task": task, "steps": steps, "feasible": True, "predicted_outcome": last}
