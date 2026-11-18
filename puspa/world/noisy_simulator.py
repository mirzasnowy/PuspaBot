"""Noisy simulated world: the same bill plus price watch, with drift.

Same interface as ``puspa.world.simulator`` (``SimulatedWorld``,
``run_episode``, ``run_episodes``) so the same accuracy run can be
executed on both worlds. The difference is the price truth: the
deterministic world moves each store by a fixed daily amount, while
this world adds noise and occasional spikes on top of the same
drift, so the model can never predict prices exactly.

World rules (the hidden truth the model must discover):

- One phone product, watched at three stores, one PLN bill of
  Rp 150,000 due on day 20, same as the deterministic world.
- Each store drifts by the same daily amount as the deterministic
  world (Tokopedia falls Rp 2,000 a day, Shopee rises Rp 3,000 a
  day, Blibli never moves), but most days the move is exact and
  some days it is not: about 20 percent of days the move is off by
  Rp 1,000 to Rp 2,000, and about 10 percent of days a spike of
  Rp 10,000 to Rp 20,000 hits in either direction.

The noise is seeded, so a run is fully reproducible: the same seed
always produces the same price path. Each episode in
``run_episodes`` uses ``seed + episode_index``, so episodes differ
from each other while staying reproducible.

Honest reporting: accuracy on this world is expected to sit below
1.0. That is the point. The deterministic world checks that the
learning mechanism works; this world checks that the reported
number is the real one, noise included, not a claimed 1.0.
"""

from __future__ import annotations

import random

from puspa.world.model import WorldModel
from puspa.world.simulator import (
    BILL_AMOUNT,
    BILL_DUE_DAY,
    BILL_ID,
    EPISODE_START_DAY,
    PRICE_DRIFT_PER_DAY,
    PRODUCT_ID,
    STORES,
    BASE_PRICES,
)

NOISE_DAYS = 120
CLEAN_DAY_CHANCE = 0.70
SMALL_NOISE_CHANCE = 0.20
SMALL_NOISE = (-2000, -1000, 1000, 2000)
SPIKE_NOISE = (-20000, -15000, 10000, 15000)


class NoisySimulatedWorld:
    """The hidden noisy world the model predicts about."""

    def __init__(self, start_day: int = EPISODE_START_DAY, seed: int = 7) -> None:
        self.day = start_day
        self._start_day = start_day
        self.bill_paid = False
        self.seed = seed
        rng = random.Random(seed)
        self._moves: dict[str, list[int]] = {}
        for store in STORES:
            drift = PRICE_DRIFT_PER_DAY[store]
            moves = []
            for _ in range(NOISE_DAYS):
                roll = rng.random()
                if roll < CLEAN_DAY_CHANCE:
                    moves.append(drift)
                elif roll < CLEAN_DAY_CHANCE + SMALL_NOISE_CHANCE:
                    moves.append(drift + rng.choice(SMALL_NOISE))
                else:
                    moves.append(drift + rng.choice(SPIKE_NOISE))
            self._moves[store] = moves

    def price(self, product_id: str, store: str) -> int:
        days = self.day - self._start_day
        return BASE_PRICES[store] + sum(self._moves[store][:days])

    def bill_status(self) -> str:
        if self.bill_paid:
            return "paid"
        if self.day >= BILL_DUE_DAY:
            return "due"
        if self.day >= BILL_DUE_DAY - 3:
            return "due_soon"
        return "not_due"

    def observe_price(self, product_id: str, store: str) -> dict:
        return {"price": self.price(product_id, store), "store": store,
                "product_id": product_id, "day": self.day}

    def observe_bill(self, bill_id: str = BILL_ID) -> dict:
        return {"status": self.bill_status(),
                "amount_due": 0 if self.bill_paid else BILL_AMOUNT,
                "amount": BILL_AMOUNT, "due_day": BILL_DUE_DAY,
                "bill_id": bill_id, "day": self.day}

    def act(self, action: dict) -> dict:
        """Execute one action in the simulated world, return the observed outcome."""
        action_type = action["type"]
        if action_type == "check_price":
            return self.observe_price(action["product_id"], action["store"])
        if action_type == "check_bill":
            return self.observe_bill(action["bill_id"])
        if action_type == "pay_bill":
            if self.bill_paid:
                return {"success": False, "reason": "already_paid",
                        "bill_id": action["bill_id"], "day": self.day}
            self.bill_paid = True
            return {"success": True, "status": "paid", "amount_due": 0,
                    "bill_id": action["bill_id"], "day": self.day}
        if action_type == "wait_day":
            self.day += 1
            return {"day": self.day}
        if action_type == "buy_from_store":
            return {"success": True, "store": action["store"],
                    "price": self.price(action["product_id"], action["store"]),
                    "product_id": action["product_id"], "day": self.day}
        raise ValueError(f"unknown action type: {action_type!r}")


def run_episode(model: WorldModel, days: int = 5, seed: int = 7) -> dict:
    """Run one episode against the noisy world.

    Same shape as ``puspa.world.simulator.run_episode``: day 15 is
    grounding only, days 16 and later predict first, then act, then
    learn, and the last day predicts and pays the bill. Returns
    ``{"correct": n, "total": n, "accuracy": float,
    "mispredictions": n}`` for this episode only.
    """
    world = NoisySimulatedWorld(seed=seed)
    stats_before = dict(model.stats)
    model.start_episode()
    task = "noisy_episode"
    mispredictions_before = len(model.mispredictions)

    def predict_act_update(action: dict) -> None:
        model.predict(action)
        observed = world.act(action)
        model.update({"action": action, "observed": observed,
                      "task": task, "day": world.day})

    # Day 15: grounding observations only, no prediction is scored.
    for store in STORES:
        model.update({"action": {"type": "check_price", "product_id": PRODUCT_ID, "store": store},
                      "observed": world.observe_price(PRODUCT_ID, store),
                      "task": task, "day": world.day})
    model.update({"action": {"type": "check_bill", "bill_id": BILL_ID},
                  "observed": world.observe_bill(),
                  "task": task, "day": world.day})

    # Days 16..: predict first, then act, then learn.
    for _ in range(days - 1):
        world.day += 1
        model.day = world.day
        for store in STORES:
            predict_act_update(
                {"type": "check_price", "product_id": PRODUCT_ID, "store": store})
        predict_act_update({"type": "check_bill", "bill_id": BILL_ID})

    # Last day: predict and pay the bill before it falls due.
    predict_act_update({"type": "pay_bill", "bill_id": BILL_ID})
    accuracy = model.end_episode()
    return {
        "correct": model.stats["correct"] - stats_before["correct"],
        "total": model.stats["predictions"] - stats_before["predictions"],
        "accuracy": accuracy,
        "mispredictions": len(model.mispredictions) - mispredictions_before,
    }


def run_episodes(model: WorldModel, count: int = 3, days: int = 5,
                 seed: int = 7) -> dict:
    """Run several episodes against one persistent model.

    Same shape as ``puspa.world.simulator.run_episodes``. Each
    episode uses ``seed + episode_index`` so episodes differ while
    staying reproducible. Returns ``{"episodes": [...],
    "first_accuracy": float, "last_accuracy": float,
    "improved": bool}``.
    """
    episodes = [run_episode(model, days=days, seed=seed + i)
                for i in range(count)]
    first = episodes[0]["accuracy"] if episodes else 0.0
    last = episodes[-1]["accuracy"] if episodes else 0.0
    return {
        "episodes": episodes,
        "first_accuracy": first,
        "last_accuracy": last,
        "improved": last > first,
    }
