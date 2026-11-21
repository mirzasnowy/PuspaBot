"""Multi-step planning by simulating against the world model.

``plan(model, task)`` never touches the real world and never mutates
the live model's beliefs or state: it copies the model state,
forecasts each step with ``WorldModel.predict`` on that copy, and
applies each forecast with ``WorldModel.apply_predicted`` to build
the state the next step sees. A plan is therefore a rehearsal, not
an execution. One exception is metadata, not world state: when a
plan cannot proceed for lack of evidence, it records the gap in
the model's ignorance map (K6, ``puspa.world.ignorance``) — the
beliefs and ``model.state`` are still unchanged by planning.

Supported tasks (all values English):

- ``{"goal": "pay_bill", "bill_id": ...}``: check the bill, pay it
  when it is unpaid, then check again to verify it is paid.
- ``{"goal": "find_cheapest_price", "product_id": ...}``: check the
  price at every known store, then buy from the cheapest one. When
  no store has a known price the plan is infeasible with reason
  ``"needs_probe"`` instead of guessing a store: the missing price
  areas are recorded in the model's ignorance map (K6) and the plan
  carries one probe step (check the price at the first store) plus
  the ``"probe"`` and ``"unknown_areas"`` fields, so the caller can
  go look once and re-plan with evidence.
- ``{"goal": "message_contact", "contact_id": ...}``: ask what the
  contact will do next (K5, ``WorldModel.predict_agent``) and plan
  the message only when a reply is likely; when an ``"ask"`` goal
  is given, the plan also checks whether the contact is predicted
  to agree to it. The plan never sends anything: it is a rehearsal
  that accounts for the other person's reaction.
- ``{"goal": "sequence", "steps": [action, ...]}``: simulate the
  given actions in order.

Three speeds (K7, ``puspa.world.speeds``): ``plan`` takes an
optional ``speed`` (``"fast"`` by default) and an optional
rehearsal ``world``. Each step is forecast at the requested
speed through a shared ``ThreeSpeedSimulator`` — fast answers
from the model's belief snapshot, medium re-derives the
forecast from the trace history, deep executes the action in
the supplied world and observes the outcome. A step that its
speed cannot answer falls back down the chain, and the plan
records the requested ``"speed"`` and every ``"fallbacks"``
that happened. A deep plan consumes the world it is given (a
dry-run pay marks the bill paid in that world), so the caller
passes a fresh world per plan. The returned plan is
``{"task": ..., "steps": [...], "feasible": bool,
"predicted_outcome": {...}, "speed": ..., "fallbacks": [...]}``
where each step holds its action plus the predicted outcome,
confidence, and the speed that produced it for that step.
"""

from __future__ import annotations

import copy

from puspa.world.model import WorldModel
from puspa.world.simulator import STORES
from puspa.world.speeds import ThreeSpeedSimulator

MAX_PLAN_STEPS = 10


def plan(
    model: WorldModel,
    task: dict,
    *,
    speed: str = "fast",
    world=None,
    tool_runner=None,
) -> dict:
    """Plan a multi-step task by simulating it against the world model.

    ``speed`` is the rehearsal speed for the step forecasts
    (``"fast"`` by default; see :mod:`puspa.world.speeds`).
    ``world`` is the rehearsal world a deep plan executes in
    (consumed by the plan — pass a fresh world per plan);
    ``tool_runner`` is the sandbox dry-run seam for actions the
    world does not know. The returned plan always carries the
    requested ``"speed"`` and the ``"fallbacks"`` that happened.
    """
    simulator = ThreeSpeedSimulator(model, world, tool_runner)
    fallbacks: list[str] = []
    result = _plan(model, task, simulator, speed, fallbacks)
    result["speed"] = speed
    result["fallbacks"] = fallbacks
    return result


def _plan(model: WorldModel, task: dict, simulator, speed: str, fallbacks: list) -> dict:
    goal = task.get("goal")
    if goal == "pay_bill":
        return _plan_pay_bill(model, task, simulator, speed, fallbacks)
    if goal == "find_cheapest_price":
        return _plan_find_cheapest(model, task, simulator, speed, fallbacks)
    if goal == "message_contact":
        return _plan_message_contact(model, task)
    if goal == "sequence":
        return _plan_sequence(model, task, simulator, speed, fallbacks)
    return {
        "task": task,
        "steps": [],
        "feasible": False,
        "predicted_outcome": {},
        "reason": "unknown_goal",
    }


def _simulate_steps(
    model: WorldModel,
    actions: list[dict],
    state: dict | None = None,
    *,
    simulator: ThreeSpeedSimulator | None = None,
    speed: str = "fast",
    fallbacks: list | None = None,
) -> tuple[list[dict], dict]:
    """Simulate actions in order on a copy of the model state.

    Each step is forecast at ``speed`` through the shared
    simulator (one per plan, so a deep plan keeps executing in
    the same world); a step its speed cannot answer falls back
    down the chain, and the fallback is appended to ``fallbacks``
    when given. With no simulator given, a throwaway one is
    built (fast, no world) — the old behaviour.
    """
    simulator = simulator or ThreeSpeedSimulator(model)
    state = copy.deepcopy(state if state is not None else model.state)
    steps = []
    for action in actions:
        outcome, confidence, used, step_fallbacks = simulator.simulate_action(action, state, speed)
        if fallbacks is not None:
            for fallen in step_fallbacks:
                if fallen not in fallbacks:
                    fallbacks.append(fallen)
        steps.append(
            {
                "action": dict(action),
                "predicted_outcome": outcome,
                "confidence": confidence,
                "speed": used,
            }
        )
        state = model.apply_predicted(state, action, outcome)
    return steps, state


def _plan_pay_bill(model: WorldModel, task: dict, simulator, speed: str, fallbacks: list) -> dict:
    bill_id = str(task["bill_id"])
    check = {"type": "check_bill", "bill_id": bill_id}
    steps, state = _simulate_steps(
        model, [check], simulator=simulator, speed=speed, fallbacks=fallbacks
    )
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
    more_steps, _final_state = _simulate_steps(
        model, [pay, check], state=state, simulator=simulator, speed=speed, fallbacks=fallbacks
    )
    steps.extend(more_steps)
    final_status = more_steps[-1]["predicted_outcome"].get("status")
    return {
        "task": task,
        "steps": steps,
        "feasible": final_status == "paid",
        "predicted_outcome": {"status": final_status, "amount_due": 0},
    }


def _plan_find_cheapest(
    model: WorldModel, task: dict, simulator, speed: str, fallbacks: list
) -> dict:
    product_id = str(task["product_id"])
    stores = list(task.get("stores") or STORES)
    actions = [
        {"type": "check_price", "product_id": product_id, "store": store} for store in stores
    ]
    steps, state = _simulate_steps(
        model, actions, simulator=simulator, speed=speed, fallbacks=fallbacks
    )
    known = [
        (step["action"]["store"], step["predicted_outcome"]["price"])
        for step in steps
        if step["predicted_outcome"].get("known")
    ]
    if not known:
        # No store has a price: do not guess a store. Record the
        # gap in the model's ignorance map (K6) and return one
        # small probe — check the price at the first store — so
        # the caller can go look once and re-plan with evidence.
        # Registering the gap is metadata about the model's own
        # blindness, not world state: the beliefs and model.state
        # are unchanged by this plan.
        unknown_areas = []
        for store in stores:
            area = model.register_unknown("price", f"{product_id}@{store}")
            if area is not None:
                unknown_areas.append(area.key)
        probe = model.probe_for("price", f"{product_id}@{stores[0]}")
        probe_steps: list[dict] = []
        if probe is not None:
            probe_steps, _ = _simulate_steps(
                model, [probe["action"]], simulator=simulator, speed=speed, fallbacks=fallbacks
            )
            probe_steps[0]["probe"] = True
        return {
            "task": task,
            "steps": probe_steps,
            "feasible": False,
            "predicted_outcome": {},
            "reason": "needs_probe",
            "probe": probe,
            "unknown_areas": unknown_areas,
        }
    cheapest_store, cheapest_price = min(known, key=lambda item: item[1])
    buy = {"type": "buy_from_store", "product_id": product_id, "store": cheapest_store}
    buy_steps, _ = _simulate_steps(
        model, [buy], state=state, simulator=simulator, speed=speed, fallbacks=fallbacks
    )
    steps.extend(buy_steps)
    return {
        "task": task,
        "steps": steps,
        "feasible": True,
        "predicted_outcome": {"cheapest_store": cheapest_store, "cheapest_price": cheapest_price},
    }


def _plan_message_contact(model: WorldModel, task: dict) -> dict:
    """Plan a message to a contact, accounting for their reaction.

    The real caller for the K5 goal models: the plan asks the
    model what the contact will do next (``predict_agent``) and
    is feasible only when a reply is likely. With an ``"ask"``
    goal, feasibility also needs a predicted agreement with
    confidence at least 0.5; otherwise the plan is infeasible
    with reason ``"agreement_unlikely"`` and the user sees the
    prediction instead of a sent message. Nothing here sends
    anything: the step is the message as planned, with the
    prediction attached.
    """
    contact_id = str(task["contact_id"])
    proposed = {"goal": task["ask"]} if task.get("ask") else None
    forecast = model.predict_agent(contact_id, proposed)
    action = {
        "type": "message_contact",
        "contact_id": contact_id,
        "channel": task.get("channel") or forecast.get("preferred_channel"),
        "ask": task.get("ask"),
    }
    if not forecast["known"]:
        return {
            "task": task,
            "steps": [{"action": action, "predicted_outcome": forecast, "confidence": 0.0}],
            "feasible": False,
            "predicted_outcome": forecast,
            "reason": "unknown_contact",
        }
    reply_prediction = next(
        (item for item in forecast["predictions"] if item["kind"] == "reply"), None
    )
    reply_likely = forecast["reply_likely"] is True
    confidence = reply_prediction["confidence"] if reply_prediction is not None else 0.0
    agreement = next(
        (item for item in forecast["predictions"] if item["kind"] == "agreement"), None
    )
    if agreement is not None and agreement["confidence"] < 0.5:
        return {
            "task": task,
            "steps": [{"action": action, "predicted_outcome": forecast, "confidence": confidence}],
            "feasible": False,
            "predicted_outcome": forecast,
            "reason": "agreement_unlikely",
        }
    return {
        "task": task,
        "steps": [{"action": action, "predicted_outcome": forecast, "confidence": confidence}],
        "feasible": reply_likely,
        "predicted_outcome": forecast,
        "reason": None if reply_likely else "reply_unlikely",
    }


def _plan_sequence(model: WorldModel, task: dict, simulator, speed: str, fallbacks: list) -> dict:
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
    steps, _ = _simulate_steps(
        model, actions, simulator=simulator, speed=speed, fallbacks=fallbacks
    )
    last = steps[-1]["predicted_outcome"] if steps else {}
    return {"task": task, "steps": steps, "feasible": True, "predicted_outcome": last}
