"""Three-speed simulator (Kosmos K7): fast, medium, deep + fallback + recording.

No live provider, no network, no database: the rehearsal worlds
are the deterministic simulated worlds, the learning loop is a
fake object on the model, and the tool runner is a plain
callable. Only modules with a clean import chain on the scrubbed
main are imported here, so these tests run with --noconftest.
"""

from __future__ import annotations

import copy

import pytest

from puspa.world import (
    WorldModel,
    plan,
    simulate,
)
from puspa.world.noisy_simulator import NoisySimulatedWorld
from puspa.world.simulator import (
    BILL_ID,
    PRODUCT_ID,
    STORES,
    SimulatedWorld,
)
from puspa.world.speeds import ThreeSpeedSimulator


def _grounded_model(day=15):
    """A model grounded with one price observation per store and one bill observation."""
    model = WorldModel()
    model.day = day
    world = SimulatedWorld(start_day=day)
    for store in STORES:
        model.update(
            {
                "action": {"type": "check_price", "product_id": PRODUCT_ID, "store": store},
                "observed": world.observe_price(PRODUCT_ID, store),
                "task": "grounding",
                "day": day,
            }
        )
    model.update(
        {
            "action": {"type": "check_bill", "bill_id": BILL_ID},
            "observed": world.observe_bill(),
            "task": "grounding",
            "day": day,
        }
    )
    return model, world


def _model_with_price_history():
    """A model that watched Tokopedia fall two days in a row.

    One world, advancing day by day (a fresh world per day would
    report the base price again — the world's price is relative
    to its own start day). After two moves the raw move history
    holds [-2000, -2000] but the trend is NOT adopted yet
    (adoption needs three consistent moves), so fast still
    forecasts the last price while medium already sees the fall.
    """
    model = WorldModel()
    model.day = 15
    world = SimulatedWorld(start_day=15)
    for store in STORES:
        model.update(
            {
                "action": {"type": "check_price", "product_id": PRODUCT_ID, "store": store},
                "observed": world.observe_price(PRODUCT_ID, store),
                "task": "grounding",
                "day": 15,
            }
        )
    model.update(
        {
            "action": {"type": "check_bill", "bill_id": BILL_ID},
            "observed": world.observe_bill(),
            "task": "grounding",
            "day": 15,
        }
    )
    for day in (16, 17):
        world.day = day
        model.day = day
        for store in STORES:
            model.update(
                {
                    "action": {
                        "type": "check_price",
                        "product_id": PRODUCT_ID,
                        "store": store,
                    },
                    "observed": world.observe_price(PRODUCT_ID, store),
                    "task": "grounding",
                    "day": day,
                }
            )
    return model


def _task_result(task, success, duration_days=None, deadline_days=None, day=15):
    result = {"day": day}
    if duration_days is not None:
        result["duration_days"] = duration_days
    if deadline_days is not None:
        result["deadline_days"] = deadline_days
    return {"task": task, "success": success, "result": result}


def _price_action(store="Tokopedia"):
    return {"type": "check_price", "product_id": PRODUCT_ID, "store": store}


class _RecordingLoop:
    def __init__(self):
        self.entries = []

    def record_experience(self, entry, **kwargs):
        self.entries.append(dict(entry))
        return dict(entry)


class _FailingLoop:
    def record_experience(self, entry, **kwargs):
        raise RuntimeError("loop is down")


class _FailingWorld:
    def act(self, action):
        raise RuntimeError("world crashed")


# ---------------------------------------------------------------------------
# fast: the belief snapshot
# ---------------------------------------------------------------------------


def test_fast_matches_model_predict():
    model, _ = _grounded_model()
    expected = model.predict(_price_action("Shopee"))
    result = simulate(model, [_price_action("Shopee")], speed="fast")
    assert result.speed == "fast"
    assert result.speed_used == "fast"
    assert result.fallbacks == []
    assert result.steps[0]["outcome"]["price"] == expected.outcome["price"] == 5100000
    assert result.steps[0]["confidence"] == expected.confidence
    assert result.steps[0]["speed"] == "fast"
    assert result.predicted_outcome["price"] == 5100000


def test_fast_unknown_action_raises_like_model_predict():
    model, _ = _grounded_model()
    with pytest.raises(ValueError, match="unknown action type"):
        simulate(model, [{"type": "teleport_to_mars"}], speed="fast")


def test_unknown_speed_raises():
    model, _ = _grounded_model()
    with pytest.raises(ValueError, match="unknown speed"):
        simulate(model, [_price_action()], speed="warp")


# ---------------------------------------------------------------------------
# medium: dynamics re-derived from trace history
# ---------------------------------------------------------------------------


def test_medium_price_uses_learned_delta_before_trend_adoption():
    model = _model_with_price_history()
    assert model.day == 17
    # Two moves in, the trend is not adopted yet: fast still
    # forecasts the last price, medium already sees the fall.
    state = copy.deepcopy(model.state)
    state["day"] = 19
    sim = ThreeSpeedSimulator(model)
    outcome, confidence, used, fallbacks = sim.simulate_action(
        _price_action("Tokopedia"), state, "medium"
    )
    assert used == "medium"
    assert fallbacks == []
    assert outcome["price"] == 5246000 + (-2000) * 2
    assert outcome["known"] is True
    assert outcome["dynamics"] == "price_delta_history:2 observations"
    assert 0.5 < confidence <= 0.9
    fast = model.predict(_price_action("Tokopedia"), state=state)
    assert fast.outcome["price"] == 5246000
    assert outcome["price"] != fast.outcome["price"]


def test_medium_price_without_history_falls_back_to_fast():
    model, _ = _grounded_model()
    result = simulate(model, [_price_action()], speed="medium")
    assert result.speed_used == "fast"
    assert result.fallbacks == ["medium"]
    assert result.steps[0]["speed"] == "fast"
    assert result.steps[0]["outcome"]["price"] == 5250000


def test_medium_schedule_recomputes_mean_duration():
    model = WorldModel()
    model.ingest_task_result(_task_result("pay_bill", True, 2, 3, day=15))
    model.ingest_task_result(_task_result("pay_bill", True, 4, 3, day=16))
    sim = ThreeSpeedSimulator(model)
    outcome, _confidence, used, _fallbacks = sim.simulate_action(
        {"type": "estimate_task", "task_kind": "pay_bill"}, model.state, "medium"
    )
    assert used == "medium"
    assert outcome["expected_days"] == 3.0
    assert outcome["dynamics"] == "schedule_durations:2 observations"


def test_medium_schedule_without_history_falls_back():
    model = WorldModel()
    result = simulate(model, [{"type": "estimate_task", "task_kind": "pay_bill"}], speed="medium")
    assert result.speed_used == "fast"
    assert result.fallbacks == ["medium"]
    assert result.steps[0]["outcome"]["known"] is False


def test_medium_capability_recomputes_success_rate():
    model = WorldModel()
    model.ingest_task_result(_task_result("pay_bill", True, day=15))
    model.ingest_task_result(_task_result("pay_bill", True, day=16))
    model.ingest_task_result(_task_result("pay_bill", False, day=17))
    sim = ThreeSpeedSimulator(model)
    outcome, _confidence, used, _fallbacks = sim.simulate_action(
        {"type": "attempt_task", "task_kind": "pay_bill"}, model.state, "medium"
    )
    assert used == "medium"
    assert outcome["success"] is True
    assert outcome["success_rate"] == pytest.approx(2 / 3)
    assert outcome["dynamics"] == "capability_outcomes:3 observations"


def test_medium_action_without_dynamics_falls_back():
    model, _ = _grounded_model()
    result = simulate(model, [{"type": "check_bill", "bill_id": BILL_ID}], speed="medium")
    assert result.speed_used == "fast"
    assert result.fallbacks == ["medium"]
    assert result.steps[0]["outcome"]["status"] == "not_due"


# ---------------------------------------------------------------------------
# deep: dry-run in a rehearsal world (and the tool-runner seam)
# ---------------------------------------------------------------------------


def test_deep_observes_world_outcomes():
    model, _ = _grounded_model(day=17)
    world = SimulatedWorld(start_day=17)
    result = simulate(model, [_price_action()], speed="deep", world=world)
    assert result.speed_used == "deep"
    assert result.fallbacks == []
    assert result.steps[0]["outcome"]["price"] == world.price(PRODUCT_ID, "Tokopedia")
    assert result.steps[0]["confidence"] == 1.0
    assert result.steps[0]["outcome"]["known"] is True


def test_deep_pay_bill_sequence_observes_paid_and_leaves_model_untouched():
    model, _ = _grounded_model(day=15)
    world = SimulatedWorld(start_day=15)
    actions = [
        {"type": "check_bill", "bill_id": BILL_ID},
        {"type": "pay_bill", "bill_id": BILL_ID},
        {"type": "check_bill", "bill_id": BILL_ID},
    ]
    result = simulate(model, actions, speed="deep", world=world)
    assert result.speed_used == "deep"
    assert result.predicted_outcome["status"] == "paid"
    # The rehearsal world is consumed by the dry-run...
    assert world.bill_paid is True
    # ...but the live model is untouched: its bill belief still says unpaid.
    assert model.state["bill_beliefs"][BILL_ID].paid is False


def test_deep_without_world_falls_back_down_the_chain():
    model, _ = _grounded_model()
    result = simulate(model, [_price_action()], speed="deep")
    assert result.speed_used == "fast"
    assert result.fallbacks == ["deep", "medium"]
    assert result.steps[0]["outcome"]["price"] == 5250000


def test_deep_with_failing_world_falls_back_to_medium():
    model = _model_with_price_history()
    sim = ThreeSpeedSimulator(model, world=_FailingWorld())
    result = sim.simulate([_price_action()], speed="deep")
    assert result.speed_used == "medium"
    assert result.fallbacks == ["deep"]
    assert result.steps[0]["outcome"]["price"] == 5246000


def test_deep_tool_runner_dry_runs_unknown_action():
    model, _ = _grounded_model()

    def tool_runner(action):
        return {"tool": "browser.open", "opened": True, "url": action["url"]}

    result = simulate(
        model,
        [{"type": "browse_page", "url": "https://example.com"}],
        speed="deep",
        tool_runner=tool_runner,
    )
    assert result.speed_used == "deep"
    assert result.steps[0]["outcome"]["opened"] is True
    assert result.steps[0]["outcome"]["dry_run"] is True


def test_deep_with_no_world_and_no_runner_and_unknown_action_raises():
    model, _ = _grounded_model()
    with pytest.raises(ValueError, match="unknown action type"):
        simulate(model, [{"type": "browse_page", "url": "https://example.com"}], speed="deep")


def test_noisy_world_works_as_a_deep_world():
    model, _ = _grounded_model(day=17)
    world = NoisySimulatedWorld(start_day=17, seed=7)
    result = simulate(model, [_price_action("Shopee")], speed="deep", world=world)
    assert result.speed_used == "deep"
    assert result.steps[0]["outcome"]["price"] == world.price(PRODUCT_ID, "Shopee")


# ---------------------------------------------------------------------------
# recording through the learning loop
# ---------------------------------------------------------------------------


def test_simulate_records_through_session_loop():
    model, _ = _grounded_model()
    loop = _RecordingLoop()
    model.learning_loop = loop
    sim = ThreeSpeedSimulator(model)
    result = sim.simulate([_price_action()], speed="fast")
    assert result.recorded is True
    assert len(loop.entries) == 1
    entry = loop.entries[0]
    assert entry["kind"] == "simulation"
    assert entry["speed"] == "fast"
    assert entry["speed_used"] == "fast"
    assert entry["fallbacks"] == []
    assert entry["predicted_outcome"]["price"] == 5250000
    assert sim.simulations == [result]


def test_simulate_records_through_default_loop_when_no_session_loop():
    model, _ = _grounded_model()
    assert model.learning_loop is None
    result = simulate(model, [_price_action()], speed="fast")
    assert result.recorded is True


def test_recording_failure_never_breaks_a_simulation():
    model, _ = _grounded_model()
    model.learning_loop = _FailingLoop()
    result = simulate(model, [_price_action()], speed="fast")
    assert result.recorded is False
    assert result.steps[0]["outcome"]["price"] == 5250000


def test_simulate_empty_actions_returns_an_empty_result():
    model, _ = _grounded_model()
    result = simulate(model, [], speed="fast")
    assert result.steps == []
    assert result.predicted_outcome == {}
    assert result.speed_used == "fast"
    assert result.recorded is True


# ---------------------------------------------------------------------------
# planner wiring: plan(..., speed=...)
# ---------------------------------------------------------------------------


def test_planner_fast_plan_is_unchanged():
    model, _ = _grounded_model()
    before = model.state
    result = plan(model, {"goal": "pay_bill", "bill_id": BILL_ID})
    assert result["feasible"] is True
    assert result["speed"] == "fast"
    assert result["fallbacks"] == []
    assert [step["speed"] for step in result["steps"]] == ["fast", "fast", "fast"]
    assert result["predicted_outcome"]["status"] == "paid"
    assert model.state == before


def test_planner_medium_plan_uses_medium_for_price_steps():
    model = _model_with_price_history()
    result = plan(model, {"goal": "find_cheapest_price", "product_id": PRODUCT_ID}, speed="medium")
    assert result["feasible"] is True
    assert result["speed"] == "medium"
    assert result["fallbacks"] == ["medium"]
    speeds = [step["speed"] for step in result["steps"]]
    assert speeds[:3] == ["medium", "medium", "medium"]
    assert speeds[3] == "fast"  # the buy step has no medium dynamics
    assert result["predicted_outcome"]["cheapest_store"] == "Shopee"
    assert result["predicted_outcome"]["cheapest_price"] == 5106000


def test_planner_deep_plan_executes_in_the_world():
    model, _ = _grounded_model(day=15)
    world = SimulatedWorld(start_day=15)
    before = model.state
    result = plan(model, {"goal": "pay_bill", "bill_id": BILL_ID}, speed="deep", world=world)
    assert result["feasible"] is True
    assert result["speed"] == "deep"
    assert result["fallbacks"] == []
    assert all(step["speed"] == "deep" for step in result["steps"])
    assert world.bill_paid is True
    assert model.state == before


def test_planner_deep_without_world_falls_back():
    model, _ = _grounded_model()
    result = plan(model, {"goal": "pay_bill", "bill_id": BILL_ID}, speed="deep")
    assert result["feasible"] is True
    assert result["speed"] == "deep"
    assert "deep" in result["fallbacks"]
    assert result["predicted_outcome"]["status"] == "paid"


def test_planner_unknown_goal_still_carries_speed_and_fallbacks():
    model, _ = _grounded_model()
    result = plan(model, {"goal": "teleport_to_mars"}, speed="medium")
    assert result["feasible"] is False
    assert result["reason"] == "unknown_goal"
    assert result["speed"] == "medium"
    assert result["fallbacks"] == []
