"""Deterministic simulated world: bills plus price watch.

A small fixed world used as proof that the world model learns. No
randomness, no database, no network: every price move and every bill
state is fixed by the constants below, so an accuracy run is fully
reproducible.

World rules (the hidden truth the model must discover):

- One phone product, watched at three stores. Each store moves its
  price by a fixed daily amount: Tokopedia falls Rp 2,000 a day,
  Shopee rises Rp 3,000 a day, Blibli never moves.
- One PLN bill of Rp 150,000, due on day 20 of the month. Paying it
  marks it paid; a new episode starts a fresh billing cycle with the
  bill unpaid again.

An episode starts on day 15. Day 15 is grounding only: prices and
the bill are observed without a prediction, so the model has a
starting point. Days 16 to 19 predict first, then act, then update.
On the last day the agent also predicts and pays the bill. The model
object persists across episodes while the simulated world is rebuilt,
so learning carries over: that is what the accuracy curve measures.
"""

from __future__ import annotations

from puspa.world.model import WorldModel

STORES = ("Tokopedia", "Shopee", "Blibli")
PRODUCT_ID = "phone-x1"
BILL_ID = "bill-pln-001"
BILL_AMOUNT = 150000
BILL_DUE_DAY = 20
EPISODE_START_DAY = 15

BASE_PRICES = {
    "Tokopedia": 5250000,
    "Shopee": 5100000,
    "Blibli": 5300000,
}
PRICE_DRIFT_PER_DAY = {
    "Tokopedia": -2000,
    "Shopee": 3000,
    "Blibli": 0,
}


class SimulatedWorld:
    """The hidden deterministic world the model predicts about."""

    def __init__(self, start_day: int = EPISODE_START_DAY) -> None:
        self.day = start_day
        self._start_day = start_day
        self.bill_paid = False

    def price(self, product_id: str, store: str) -> int:
        days = self.day - self._start_day
        return BASE_PRICES[store] + PRICE_DRIFT_PER_DAY[store] * days

    def bill_status(self) -> str:
        if self.bill_paid:
            return "paid"
        if self.day >= BILL_DUE_DAY:
            return "due"
        if self.day >= BILL_DUE_DAY - 3:
            return "due_soon"
        return "not_due"

    def observe_price(self, product_id: str, store: str) -> dict:
        return {
            "price": self.price(product_id, store),
            "store": store,
            "product_id": product_id,
            "day": self.day,
        }

    def observe_bill(self, bill_id: str = BILL_ID) -> dict:
        return {
            "status": self.bill_status(),
            "amount_due": 0 if self.bill_paid else BILL_AMOUNT,
            "amount": BILL_AMOUNT,
            "due_day": BILL_DUE_DAY,
            "bill_id": bill_id,
            "day": self.day,
        }

    def act(self, action: dict) -> dict:
        """Execute one action in the simulated world, return the observed outcome."""
        action_type = action["type"]
        if action_type == "check_price":
            return self.observe_price(action["product_id"], action["store"])
        if action_type == "check_bill":
            return self.observe_bill(action["bill_id"])
        if action_type == "pay_bill":
            if self.bill_paid:
                return {
                    "success": False,
                    "reason": "already_paid",
                    "bill_id": action["bill_id"],
                    "day": self.day,
                }
            self.bill_paid = True
            return {
                "success": True,
                "status": "paid",
                "amount_due": 0,
                "bill_id": action["bill_id"],
                "day": self.day,
            }
        if action_type == "wait_day":
            self.day += 1
            return {"day": self.day}
        if action_type == "buy_from_store":
            return {
                "success": True,
                "store": action["store"],
                "price": self.price(action["product_id"], action["store"]),
                "product_id": action["product_id"],
                "day": self.day,
            }
        raise ValueError(f"unknown action type: {action_type!r}")


def run_episode(model: WorldModel, days: int = 5) -> dict:
    """Run one episode: predict before every act, learn from each observation.

    Returns ``{"correct": n, "total": n, "accuracy": float,
    "mispredictions": n}`` for this episode only.
    """
    world = SimulatedWorld()
    stats_before = dict(model.stats)
    model.start_episode()
    task = "simulated_episode"
    mispredictions_before = len(model.mispredictions)

    def predict_act_update(action: dict) -> None:
        model.predict(action)
        observed = world.act(action)
        model.update({"action": action, "observed": observed, "task": task, "day": world.day})

    # Day 15: grounding observations only, no prediction is scored.
    for store in STORES:
        model.update(
            {
                "action": {"type": "check_price", "product_id": PRODUCT_ID, "store": store},
                "observed": world.observe_price(PRODUCT_ID, store),
                "task": task,
                "day": world.day,
            }
        )
    model.update(
        {
            "action": {"type": "check_bill", "bill_id": BILL_ID},
            "observed": world.observe_bill(),
            "task": task,
            "day": world.day,
        }
    )

    # Days 16..: predict first, then act, then learn.
    for _ in range(days - 1):
        world.day += 1
        model.day = world.day
        for store in STORES:
            predict_act_update({"type": "check_price", "product_id": PRODUCT_ID, "store": store})
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


def run_episodes(model: WorldModel, count: int = 3, days: int = 5) -> dict:
    """Run several episodes against one persistent model.

    Returns ``{"episodes": [...], "first_accuracy": float,
    "last_accuracy": float, "improved": bool}``. The model is the
    only thing that carries over between episodes; each episode
    rebuilds the simulated world from scratch.
    """
    episodes = [run_episode(model, days=days) for _ in range(count)]
    first = episodes[0]["accuracy"] if episodes else 0.0
    last = episodes[-1]["accuracy"] if episodes else 0.0
    return {
        "episodes": episodes,
        "first_accuracy": first,
        "last_accuracy": last,
        "improved": last > first,
    }
