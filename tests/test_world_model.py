"""Tests for the world model (Brain phase).

All tests are in-memory and deterministic: no database, no network,
no randomness. The simulated world has fixed price drifts, so the
accuracy curve is reproducible run after run.

Note: the repo-wide conftest still runs its Postgres reset fixture
for every test file; nothing in this file touches the database.
"""

import subprocess
import sys
import textwrap
import types

from puspa.world import WorldModel, plan, run_episodes
from puspa.world.simulator import (
    BILL_AMOUNT,
    BILL_DUE_DAY,
    BILL_ID,
    PRODUCT_ID,
    STORES,
    SimulatedWorld,
)


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


def test_predict_before_act_returns_prediction_and_confidence():
    model, _ = _grounded_model()
    before = model.state
    prediction = model.predict({"type": "check_price", "product_id": PRODUCT_ID, "store": "Shopee"})
    assert prediction.outcome["known"] is True
    assert prediction.outcome["price"] == 5100000
    assert 0.0 < prediction.confidence <= 1.0
    # Predicting is pure: the world state is unchanged by a prediction.
    assert model.state == before


def test_predict_unknown_entity_has_zero_confidence():
    model = WorldModel()
    prediction = model.predict(
        {"type": "check_price", "product_id": "phone-unknown", "store": "Shopee"}
    )
    assert prediction.outcome == {"price": None, "known": False}
    assert prediction.confidence == 0.0


def test_plan_multi_step_pay_bill_simulation():
    model, _ = _grounded_model()
    before = model.state
    result = plan(model, {"goal": "pay_bill", "bill_id": BILL_ID})
    assert result["feasible"] is True
    actions = [step["action"]["type"] for step in result["steps"]]
    assert actions == ["check_bill", "pay_bill", "check_bill"]
    assert result["steps"][1]["predicted_outcome"]["success"] is True
    assert result["predicted_outcome"]["status"] == "paid"
    assert all(0.0 <= step["confidence"] <= 1.0 for step in result["steps"])
    # Planning is simulation only: the live model is untouched.
    assert model.state == before
    assert model.stats["predictions"] == 0


def test_plan_find_cheapest_price_simulation():
    model, _ = _grounded_model()
    result = plan(model, {"goal": "find_cheapest_price", "product_id": PRODUCT_ID})
    assert result["feasible"] is True
    assert result["predicted_outcome"]["cheapest_store"] == "Shopee"
    assert result["predicted_outcome"]["cheapest_price"] == 5100000
    actions = [step["action"]["type"] for step in result["steps"]]
    assert actions == ["check_price", "check_price", "check_price", "buy_from_store"]
    assert result["steps"][-1]["predicted_outcome"]["success"] is True


def test_plan_sequence_simulates_given_steps():
    model, _ = _grounded_model()
    result = plan(
        model,
        {
            "goal": "sequence",
            "steps": [
                {"type": "check_bill", "bill_id": BILL_ID},
                {"type": "pay_bill", "bill_id": BILL_ID},
            ],
        },
    )
    assert result["feasible"] is True
    assert len(result["steps"]) == 2
    assert result["predicted_outcome"]["status"] == "paid"


def test_plan_unknown_goal_is_not_feasible():
    model, _ = _grounded_model()
    result = plan(model, {"goal": "teleport_to_mars"})
    assert result["feasible"] is False
    assert result["steps"] == []


def test_update_from_observation_changes_state():
    model, world = _grounded_model()
    world.day = 16
    observed = world.observe_price(PRODUCT_ID, "Shopee")
    assert observed["price"] == 5103000
    result = model.update(
        {
            "action": {"type": "check_price", "product_id": PRODUCT_ID, "store": "Shopee"},
            "observed": observed,
            "task": "watch_price",
            "day": 16,
        }
    )
    assert result["observed"]["price"] == 5103000
    prediction = model.predict({"type": "check_price", "product_id": PRODUCT_ID, "store": "Shopee"})
    assert prediction.outcome["price"] == 5103000


def test_misprediction_is_recorded_and_model_corrected():
    model, _ = _grounded_model()
    # The model has no trend yet, so it predicts the price stays flat.
    prediction = model.predict({"type": "check_price", "product_id": PRODUCT_ID, "store": "Shopee"})
    assert prediction.outcome["price"] == 5100000
    result = model.update(
        {
            "action": {"type": "check_price", "product_id": PRODUCT_ID, "store": "Shopee"},
            "observed": {"price": 5103000, "store": "Shopee", "product_id": PRODUCT_ID, "day": 16},
            "task": "watch_price",
            "day": 16,
        }
    )
    assert result["correct"] is False
    assert result["error"] == 3000
    assert len(model.mispredictions) == 1
    record = model.mispredictions[0]
    assert record["task"] == "watch_price"
    assert record["predicted"] == {"price": 5100000, "known": True}
    assert record["observed"]["price"] == 5103000
    assert record["error"] == 3000
    assert record["context"]["action"]["store"] == "Shopee"
    # The correction lands: the model now holds the observed price.
    assert (
        model.predict({"type": "check_price", "product_id": PRODUCT_ID, "store": "Shopee"}).outcome[
            "price"
        ]
        == 5103000
    )


def test_misprediction_reported_to_learning_loop_when_present(monkeypatch):
    received = []
    fake_loop = types.ModuleType("puspa.memory.learning_loop")
    fake_loop.record_misprediction = received.append
    monkeypatch.setitem(sys.modules, "puspa.memory.learning_loop", fake_loop)
    model, _ = _grounded_model()
    model.predict({"type": "check_price", "product_id": PRODUCT_ID, "store": "Tokopedia"})
    model.update(
        {
            "action": {"type": "check_price", "product_id": PRODUCT_ID, "store": "Tokopedia"},
            "observed": {
                "price": 5248000,
                "store": "Tokopedia",
                "product_id": PRODUCT_ID,
                "day": 16,
            },
            "task": "watch_price",
            "day": 16,
        }
    )
    assert len(received) == 1
    assert set(received[0]) == {"task", "predicted", "observed", "error", "context"}
    assert received[0]["task"] == "watch_price"
    assert received[0]["error"] == 2000


def test_world_model_imports_without_learning_loop():
    """Importing the world model must not import the learning loop.

    The seam is a lazy import inside `_report_misprediction`, so a
    fresh interpreter that imports only `puspa.world.model` must
    not have `puspa.memory.learning_loop` in `sys.modules`, and a
    misprediction in that interpreter must still land in
    `WorldModel.mispredictions` without raising. This runs in a
    subprocess on purpose: in the full suite other test modules
    import the learning loop first, so the check is meaningless
    in-process. The live seam (real loop receives the record) is
    verified by the integration check in docs/status-brain-phase.md.
    """
    script = textwrap.dedent("""
        import sys
        from puspa.world.model import WorldModel
        assert "puspa.memory.learning_loop" not in sys.modules
        model = WorldModel()
        model.ingest_graph([{"id": "P1", "kind": "product",
                             "attributes": {"price": 10000, "store": "S1"}}])
        model.predict({"type": "check_price", "product_id": "P1", "store": "S1"})
        result = model.update({
            "action": {"type": "check_price", "product_id": "P1", "store": "S1"},
            "observed": {"price": 12000}, "task": "check_price:P1@S1", "day": 0})
        assert result["correct"] is False
        assert len(model.mispredictions) == 1
        assert "puspa.memory.learning_loop" in sys.modules
        print("world model imports without the loop; miss recorded")
    """)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "world model imports without the loop; miss recorded" in proc.stdout


def test_ingest_graph_and_task_result_build_beliefs():
    model = WorldModel()
    model.ingest_graph(
        [
            {
                "id": PRODUCT_ID,
                "kind": "product",
                "attributes": {"price": 5100000, "store": "Shopee"},
            },
            {
                "id": BILL_ID,
                "kind": "bill",
                "attributes": {
                    "amount": BILL_AMOUNT,
                    "due_day": BILL_DUE_DAY,
                    "status": "not_due",
                    "amount_due": BILL_AMOUNT,
                },
            },
        ],
        relations=[(PRODUCT_ID, "sold_at", "Shopee")],
    )
    assert (
        model.predict({"type": "check_price", "product_id": PRODUCT_ID, "store": "Shopee"}).outcome[
            "price"
        ]
        == 5100000
    )
    assert model.predict({"type": "check_bill", "bill_id": BILL_ID}).outcome["status"] == "not_due"
    assert len(model.relations) == 1
    model.ingest_task_result(
        {
            "task": "pay_bill",
            "success": True,
            "result": {"bill_id": BILL_ID, "status": "paid", "amount_due": 0, "day": 16},
        }
    )
    assert model.predict({"type": "check_bill", "bill_id": BILL_ID}).outcome["status"] == "paid"
    # A failed task result must not change the model.
    model.ingest_task_result(
        {
            "task": "pay_bill",
            "success": False,
            "result": {
                "bill_id": BILL_ID,
                "status": "not_due",
                "amount_due": BILL_AMOUNT,
                "day": 16,
            },
        }
    )
    assert model.predict({"type": "check_bill", "bill_id": BILL_ID}).outcome["status"] == "paid"


def test_prediction_accuracy_improves_across_episodes():
    model = WorldModel()
    summary = run_episodes(model, count=3, days=5)
    accuracies = [episode["accuracy"] for episode in summary["episodes"]]
    # Key evidence, printed so the run log carries the numbers:
    # episode 1 predicts flat prices and misses every drifting store
    # until the trend is confirmed; later episodes predict exactly.
    print(
        f"episode accuracies: {accuracies} "
        f"(first={summary['first_accuracy']:.3f}, "
        f"last={summary['last_accuracy']:.3f})"
    )
    assert summary["episodes"][0]["total"] == 17
    assert accuracies[0] < 0.75
    assert accuracies[1] > accuracies[0]
    assert accuracies[2] >= 0.95
    assert summary["improved"] is True
    assert summary["last_accuracy"] > summary["first_accuracy"]
    assert model.episode_accuracies == accuracies


def test_bill_observation_corrects_stale_paid_belief():
    model, _ = _grounded_model()
    model.ingest_task_result(
        {
            "task": "pay_bill",
            "success": True,
            "result": {"bill_id": BILL_ID, "status": "paid", "amount_due": 0, "day": 16},
        }
    )
    assert model.predict({"type": "check_bill", "bill_id": BILL_ID}).outcome["status"] == "paid"
    # A fresh billing cycle: the bill is unpaid again.
    model.update(
        {
            "action": {"type": "check_bill", "bill_id": BILL_ID},
            "observed": {
                "status": "not_due",
                "amount_due": BILL_AMOUNT,
                "amount": BILL_AMOUNT,
                "due_day": BILL_DUE_DAY,
                "bill_id": BILL_ID,
                "day": 15,
            },
            "task": "grounding",
            "day": 15,
        }
    )
    assert model.predict({"type": "check_bill", "bill_id": BILL_ID}).outcome["status"] == "not_due"
