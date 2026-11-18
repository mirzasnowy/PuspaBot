"""Tests for the noisy simulated world.

All tests are in-memory and deterministic: the noise is seeded, so
a run is reproducible. No database, no network.

Note: the repo-wide conftest runs a Postgres reset fixture for
every test file, but nothing in this file touches the database.
Run with `--noconftest` on a host without Postgres.

Honesty rule: accuracy on the noisy world is reported as it is.
No test here demands 1.0, or any fixed high number. What is tested
is the mechanism (the model learns from each observation, misses
are recorded and corrected) and that the numbers are real.
"""

from puspa.world import WorldModel, run_episodes, run_noisy_episodes
from puspa.world.noisy_simulator import NoisySimulatedWorld
from puspa.world.simulator import BASE_PRICES, PRODUCT_ID, STORES, SimulatedWorld


def test_noisy_world_is_deterministic_for_a_seed():
    first = NoisySimulatedWorld(seed=7)
    second = NoisySimulatedWorld(seed=7)
    for day in range(15, 25):
        first.day = day
        second.day = day
        for store in STORES:
            assert first.price(PRODUCT_ID, store) == second.price(PRODUCT_ID, store)
    other = NoisySimulatedWorld(seed=8)
    # A different seed must produce a different price path somewhere.
    first = NoisySimulatedWorld(seed=7)
    other = NoisySimulatedWorld(seed=8)
    paths_differ = False
    for day in range(15, 30):
        first.day = day
        other.day = day
        if any(
            first.price(PRODUCT_ID, store) != other.price(PRODUCT_ID, store) for store in STORES
        ):
            paths_differ = True
    assert paths_differ


def test_noisy_world_differs_from_deterministic_world():
    noisy = NoisySimulatedWorld(seed=7)
    plain = SimulatedWorld()
    differences = 0
    for day in range(15, 30):
        noisy.day = day
        plain.day = day
        for store in STORES:
            if noisy.price(PRODUCT_ID, store) != plain.price(PRODUCT_ID, store):
                differences += 1
    # Noise and spikes must show up somewhere in 15 days x 3 stores.
    assert differences > 0
    # But the drift is still there underneath: day 15 prices match.
    noisy.day = 15
    plain.day = 15
    for store in STORES:
        assert noisy.price(PRODUCT_ID, store) == BASE_PRICES[store]
        assert plain.price(PRODUCT_ID, store) == BASE_PRICES[store]


def test_noisy_world_has_the_same_interface():
    world = NoisySimulatedWorld()
    assert world.observe_price(PRODUCT_ID, "Shopee")["price"] == 5100000
    assert world.observe_bill()["status"] == "not_due"
    assert (
        world.act({"type": "check_price", "product_id": PRODUCT_ID, "store": "Shopee"})["price"]
        == 5100000
    )
    assert world.act({"type": "wait_day"}) == {"day": 16}
    paid = world.act({"type": "pay_bill", "bill_id": "bill-pln-001"})
    assert paid == {
        "success": True,
        "status": "paid",
        "amount_due": 0,
        "bill_id": "bill-pln-001",
        "day": 16,
    }
    assert (
        world.act({"type": "buy_from_store", "product_id": PRODUCT_ID, "store": "Shopee"})[
            "success"
        ]
        is True
    )


def test_noisy_accuracy_reported_honestly_below_one():
    model = WorldModel()
    summary = run_noisy_episodes(model, count=3, days=5)
    accuracies = [episode["accuracy"] for episode in summary["episodes"]]
    # Key evidence, printed so the run log carries the numbers.
    print(
        f"noisy episode accuracies: {accuracies} "
        f"(first={summary['first_accuracy']:.3f}, "
        f"last={summary['last_accuracy']:.3f})"
    )
    assert all(episode["total"] == 17 for episode in summary["episodes"])
    assert all(0.0 <= accuracy <= 1.0 for accuracy in accuracies)
    # Noise makes exact price prediction impossible: no episode is 1.0.
    assert all(accuracy < 1.0 for accuracy in accuracies)
    # The mechanism still learns: misses are recorded, not hidden.
    assert sum(episode["mispredictions"] for episode in summary["episodes"]) > 0
    # And it is strictly below the deterministic world's final accuracy.
    deterministic = WorldModel()
    deterministic_summary = run_episodes(deterministic, count=3, days=5)
    assert summary["last_accuracy"] < deterministic_summary["last_accuracy"]
    assert model.episode_accuracies == accuracies


def test_noisy_model_corrects_to_observed_price():
    model = WorldModel()
    summary = run_noisy_episodes(model, count=1, days=5)
    assert summary["episodes"][0]["total"] == 17
    # After the run the model holds the last observed noisy price,
    # not the deterministic drift price: the correction landed.
    world = NoisySimulatedWorld(seed=7)
    world.day = 19
    for store in STORES:
        prediction = model.predict(
            {"type": "check_price", "product_id": PRODUCT_ID, "store": store}
        )
        assert prediction.outcome["price"] == world.price(PRODUCT_ID, store)
