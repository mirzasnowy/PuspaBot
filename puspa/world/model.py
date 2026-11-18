"""World model: a structured model of the world that predicts, plans, and learns.

Patterns adapted (rewritten from scratch, never copied):

- Stanford Generative Agents (joonspk-research/generative_agents, MIT):
  agents keep a memory stream, reflect episodes into higher level
  inferences, and retrieve and revise plans. Puspa takes the shape
  only: observations feed a structured model, the model is revised
  when a prediction misses, and plans are simulated against the model
  before any real action is taken.
- Voyager (MineDojo/Voyager, MIT): an automatic curriculum plus
  self-verification plus a growing skill library. Puspa takes the
  self-verification shape only: every prediction is checked against
  the observation that follows it, and a miss corrects the model and
  is reported to the learning loop.

What this module does, in its own design:

- Keeps a structured world state: entities, relations, and learned
  beliefs about prices (per product and store) and bills (amount, due
  day, paid flag). The state is plain data, so a planner can copy it
  and simulate without touching the live model.
- ``predict(action)`` forecasts the outcome of an action BEFORE the
  action is taken, with a confidence. Predicting never mutates the
  world state.
- ``update(observation)`` applies a real observation: the observed
  values win over the model, a miss is recorded as a misprediction,
  the model is corrected, and the miss is reported to the learning
  loop (see the seam note below).
- ``plan(task)`` lives in ``puspa.world.planner`` and simulates
  multi-step tasks against a copy of this state.

Learning-loop seam
-------------------
Mispredictions are forwarded to the learning loop with a lazy import
inside ``_report_misprediction``:

    from puspa.memory.learning_loop import record_misprediction

called with one record dict holding the keys ``task``,
``predicted``, ``observed``, ``error`` and ``context``. The import
is lazy and every failure is swallowed on purpose: the learning loop
module is built in parallel and may not exist yet, and the world
model must keep working without it. When the loop exists, the call
is best effort: a failure to report never breaks an update. The
record is always kept in ``WorldModel.mispredictions`` either way,
so nothing is lost if the loop is absent.

No database, no network, no new dependency. All state is in memory.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

TREND_CONFIRMATIONS = 3
PRICE_TOLERANCE_RP = 0
DEFAULT_CONFIDENCE = 0.5
KNOWN_CONFIDENCE = 0.6
TREND_CONFIDENCE = 0.9
MAX_CONFIDENCE = 0.95

ACTION_TYPES = (
    "check_price",
    "check_bill",
    "pay_bill",
    "wait_day",
    "buy_from_store",
)


@dataclass
class Prediction:
    """One forecast made before an action is taken."""

    action: dict
    outcome: dict
    confidence: float


@dataclass
class _PriceBelief:
    last_price: float | None = None
    last_day: int | None = None
    daily_delta: float = 0.0
    trend_adopted: bool = False
    delta_history: list = field(default_factory=list)
    observations: int = 0
    confidence: float = DEFAULT_CONFIDENCE


@dataclass
class _BillBelief:
    amount: float | None = None
    due_day: int | None = None
    paid: bool = False
    observations: int = 0
    confidence: float = DEFAULT_CONFIDENCE


def _report_misprediction(record: dict) -> bool:
    """Forward one misprediction record to the learning loop, if present.

    Lazy import on purpose: ``puspa.memory.learning_loop`` is built in
    parallel and may not exist yet. Returns True only when the loop
    accepted the record. Never raises.
    """
    try:
        from puspa.memory.learning_loop import record_misprediction
    except Exception:
        return False
    try:
        record_misprediction(record)
    except Exception:
        return False
    return True


def _price_key(product_id: str, store: str) -> str:
    return f"{product_id}@{store}"


class WorldModel:
    """Structured world model with predict, update, and correction."""

    def __init__(self) -> None:
        self.entities: dict[str, dict] = {}
        self.relations: list[dict] = []
        self.day: int = 0
        self._price_beliefs: dict[str, _PriceBelief] = {}
        self._bill_beliefs: dict[str, _BillBelief] = {}
        self._pending: Prediction | None = None
        self.mispredictions: list[dict] = []
        self.stats: dict[str, int] = {"predictions": 0, "correct": 0}
        self.episode_accuracies: list[float] = []
        self._episode_start: dict[str, int] | None = None

    # -- state snapshot (planner copies this, never mutates the live model)
    @property
    def state(self) -> dict:
        return {
            "entities": copy.deepcopy(self.entities),
            "relations": copy.deepcopy(self.relations),
            "day": self.day,
            "price_beliefs": copy.deepcopy(self._price_beliefs),
            "bill_beliefs": copy.deepcopy(self._bill_beliefs),
        }

    def _restore_state(self, state: dict) -> None:
        self.entities = copy.deepcopy(state["entities"])
        self.relations = copy.deepcopy(state["relations"])
        self.day = state["day"]
        self._price_beliefs = copy.deepcopy(state["price_beliefs"])
        self._bill_beliefs = copy.deepcopy(state["bill_beliefs"])

    # -- building the model from memory graphs and task results --------
    def ingest_graph(self, entities: list[dict], relations: list[dict] | None = None) -> None:
        """Seed the model from graph memory (entities plus relations).

        Entity dicts look like ``{"id": ..., "kind": "product"|"bill",
        "attributes": {...}}``. A product entity with ``price`` and
        ``store`` attributes seeds a price belief; a bill entity with
        ``amount`` and ``due_day`` seeds a bill belief. Relations are
        stored as given, either dicts or ``(subject, predicate,
        object)`` tuples.
        """
        for entity in entities or []:
            entity_id = str(entity["id"])
            self.entities[entity_id] = {
                "id": entity_id,
                "kind": entity.get("kind", "unknown"),
                "attributes": dict(entity.get("attributes") or {}),
            }
            attrs = self.entities[entity_id]["attributes"]
            if self.entities[entity_id]["kind"] == "product" and "price" in attrs and "store" in attrs:
                self._apply_price_observation(
                    product_id=entity_id, store=str(attrs["store"]),
                    price=float(attrs["price"]), day=self.day)
            if self.entities[entity_id]["kind"] == "bill":
                self._apply_bill_observation(
                    bill_id=entity_id,
                    observed={
                        "amount": attrs.get("amount"),
                        "due_day": attrs.get("due_day"),
                        "status": attrs.get("status"),
                        "amount_due": attrs.get("amount_due"),
                    },
                    day=self.day)
        for relation in relations or []:
            if isinstance(relation, dict):
                self.relations.append(dict(relation))
            else:
                subject, predicate, obj = relation
                self.relations.append(
                    {"subject": str(subject), "predicate": str(predicate), "object": str(obj)})

    def ingest_task_result(self, task: dict) -> None:
        """Fold one finished task result into the model.

        ``task`` is ``{"task": name, "success": bool, "result": {...}}``.
        A result carrying a price observation updates the price belief;
        a result carrying a bill status updates the bill belief; any
        other result is stored as an entity attribute when it names an
        ``entity_id``. Failed tasks do not change the model.
        """
        if not task.get("success", True):
            return
        result = task.get("result") or {}
        if "price" in result and "store" in result and "product_id" in result:
            self._apply_price_observation(
                product_id=str(result["product_id"]), store=str(result["store"]),
                price=float(result["price"]), day=int(result.get("day", self.day)))
        if "bill_id" in result and "status" in result:
            self._apply_bill_observation(
                bill_id=str(result["bill_id"]), observed=result,
                day=int(result.get("day", self.day)))
        if "entity_id" in result:
            entity_id = str(result["entity_id"])
            entity = self.entities.setdefault(
                entity_id, {"id": entity_id, "kind": "unknown", "attributes": {}})
            for key, value in result.items():
                if key != "entity_id":
                    entity["attributes"][key] = value

    # -- prediction ----------------------------------------------------
    def predict(self, action: dict, state: dict | None = None) -> Prediction:
        """Forecast the outcome of ``action`` BEFORE it is taken.

        Pure with respect to the world state: predicting never changes
        entities, beliefs, or the day. When called on the live model
        (``state`` is None) the prediction is kept as the pending
        prediction so the next ``update`` can score it. When called
        with an explicit ``state`` (planning simulation) nothing is
        kept.
        """
        action_type = action.get("type")
        if action_type not in ACTION_TYPES:
            raise ValueError(f"unknown action type: {action_type!r}")
        live = state is None
        if live:
            state = self.state
        outcome, confidence = self._forecast(action, state)
        prediction = Prediction(action=dict(action), outcome=outcome, confidence=confidence)
        if live:
            self._pending = prediction
        return prediction

    def _forecast(self, action: dict, state: dict) -> tuple[dict, float]:
        action_type = action["type"]
        if action_type == "check_price":
            return self._forecast_price(action, state)
        if action_type == "check_bill":
            return self._forecast_bill(action, state)
        if action_type == "pay_bill":
            return self._forecast_pay(action, state)
        if action_type == "wait_day":
            return {"day": state["day"] + 1}, 1.0
        return self._forecast_buy(action, state)

    def _forecast_price(self, action: dict, state: dict) -> tuple[dict, float]:
        belief = state["price_beliefs"].get(
            _price_key(str(action["product_id"]), str(action["store"])))
        if belief is None or belief.last_price is None:
            return {"price": None, "known": False}, 0.0
        days = max(0, state["day"] - (belief.last_day or state["day"]))
        delta = belief.daily_delta if belief.trend_adopted else 0.0
        price = belief.last_price + delta * days
        confidence = TREND_CONFIDENCE if belief.trend_adopted else KNOWN_CONFIDENCE
        return {"price": price, "known": True}, confidence

    def _forecast_bill(self, action: dict, state: dict) -> tuple[dict, float]:
        belief = state["bill_beliefs"].get(str(action["bill_id"]))
        if belief is None or belief.amount is None:
            return {"status": "unknown", "amount_due": None}, 0.0
        if belief.paid:
            return {"status": "paid", "amount_due": 0}, TREND_CONFIDENCE
        day = state["day"]
        if belief.due_day is not None and day >= belief.due_day:
            status = "due"
        elif belief.due_day is not None and day >= belief.due_day - 3:
            status = "due_soon"
        else:
            status = "not_due"
        return {"status": status, "amount_due": belief.amount}, KNOWN_CONFIDENCE

    def _forecast_pay(self, action: dict, state: dict) -> tuple[dict, float]:
        belief = state["bill_beliefs"].get(str(action["bill_id"]))
        if belief is None or belief.amount is None:
            return {"success": False, "reason": "unknown_bill"}, DEFAULT_CONFIDENCE
        if belief.paid:
            return {"success": False, "reason": "already_paid"}, TREND_CONFIDENCE
        return {"success": True, "status": "paid", "amount_due": 0}, 0.85

    def _forecast_buy(self, action: dict, state: dict) -> tuple[dict, float]:
        outcome, confidence = self._forecast_price(action, state)
        if not outcome["known"]:
            return {"success": False, "reason": "unknown_price"}, 0.0
        return {"success": True, "store": str(action["store"]),
                "price": outcome["price"]}, confidence

    # -- simulation helper for the planner ------------------------------
    def apply_predicted(self, state: dict, action: dict, outcome: dict) -> dict:
        """Return a new state as if ``outcome`` really happened.

        Used by the planner to simulate multi-step tasks. The live
        model is never touched; the input state is copied.
        """
        new_state = copy.deepcopy(state)
        if action["type"] == "pay_bill" and outcome.get("success") is True:
            belief = new_state["bill_beliefs"].get(str(action["bill_id"]))
            if belief is not None:
                belief.paid = True
        if action["type"] == "wait_day":
            new_state["day"] = outcome.get("day", new_state["day"] + 1)
        return new_state

    # -- update from observation, with correction -----------------------
    def update(self, observation: dict) -> dict:
        """Apply a real observation and correct the model if it missed.

        ``observation`` is ``{"action": {...}, "observed": {...},
        "task": name or None, "day": int or None}``. The observed
        values always win over the model. When a pending prediction
        for the same action exists, the observation is scored against
        it: a hit raises confidence, a miss is recorded, the model is
        corrected, and the miss is reported to the learning loop. An
        observation with no pending prediction is grounding only: it
        teaches the model without being scored.
        """
        action = dict(observation.get("action") or {})
        observed = dict(observation.get("observed") or {})
        day = observation.get("day")
        if day is not None:
            self.day = int(day)
        pending = None
        if self._pending is not None and self._same_action(self._pending.action, action):
            pending = self._pending
            self._pending = None
        if pending is not None:
            predicted = pending.outcome
            error = self._error(action.get("type"), predicted, observed)
            correct = error <= 0
            self.stats["predictions"] += 1
            if correct:
                self.stats["correct"] += 1
            else:
                record = {
                    "task": observation.get("task") or action.get("type"),
                    "predicted": predicted,
                    "observed": observed,
                    "error": error,
                    "context": {"action": action, "day": self.day},
                }
                self.mispredictions.append(record)
                _report_misprediction(record)
            self._apply_observation(action, observed, correct=correct)
            return {"correct": correct, "error": error,
                    "predicted": predicted, "observed": observed}
        self._apply_observation(action, observed, correct=None)
        return {"correct": None, "error": None,
                "predicted": None, "observed": observed}

    @staticmethod
    def _same_action(first: dict, second: dict) -> bool:
        if first.get("type") != second.get("type"):
            return False
        for key in ("product_id", "store", "bill_id"):
            if key in first or key in second:
                if str(first.get(key)) != str(second.get(key)):
                    return False
        return True

    @staticmethod
    def _error(action_type: str | None, predicted: dict, observed: dict) -> float:
        """Prediction error: 0 means an exact hit, above 0 a miss.

        Prices must match exactly (whole rupiah, deterministic world);
        bill and pay outcomes must match field for field.
        """
        if action_type == "check_price":
            if predicted.get("price") is None or observed.get("price") is None:
                return 1.0
            diff = abs(float(predicted["price"]) - float(observed["price"]))
            return 0.0 if diff <= PRICE_TOLERANCE_RP else diff
        if action_type == "check_bill":
            if predicted.get("status") != observed.get("status"):
                return 1.0
            if predicted.get("amount_due") is None or observed.get("amount_due") is None:
                return 1.0 if predicted.get("amount_due") != observed.get("amount_due") else 0.0
            return 0.0 if float(predicted["amount_due"]) == float(observed["amount_due"]) else 1.0
        if action_type == "pay_bill":
            if predicted.get("success") != observed.get("success"):
                return 1.0
            if predicted.get("success") is True:
                return 0.0 if predicted.get("status") == observed.get("status") else 1.0
            return 0.0 if predicted.get("reason") == observed.get("reason") else 1.0
        if action_type == "wait_day":
            return 0.0 if predicted.get("day") == observed.get("day") else 1.0
        if action_type == "buy_from_store":
            if predicted.get("success") != observed.get("success"):
                return 1.0
            if predicted.get("success") is True:
                return 0.0 if float(predicted["price"]) == float(observed["price"]) else 1.0
            return 0.0 if predicted.get("reason") == observed.get("reason") else 1.0
        return 1.0

    def _apply_observation(self, action: dict, observed: dict, correct: bool | None) -> None:
        action_type = action.get("type")
        if action_type in ("check_price", "buy_from_store"):
            belief = self._price_beliefs.setdefault(
                _price_key(str(action["product_id"]), str(action["store"])), _PriceBelief())
            if observed.get("price") is not None:
                self._apply_price_observation(
                    product_id=str(action["product_id"]), store=str(action["store"]),
                    price=float(observed["price"]), day=self.day)
            if correct is True:
                belief.confidence = min(MAX_CONFIDENCE, belief.confidence + 0.05)
            elif correct is False:
                belief.confidence = max(0.1, belief.confidence - 0.2)
        elif action_type in ("check_bill", "pay_bill"):
            self._apply_bill_observation(
                bill_id=str(action["bill_id"]), observed=observed, day=self.day)
            belief = self._bill_beliefs.get(str(action["bill_id"]))
            if belief is not None:
                if correct is True:
                    belief.confidence = min(MAX_CONFIDENCE, belief.confidence + 0.05)
                elif correct is False:
                    belief.confidence = max(0.1, belief.confidence - 0.2)
        elif action_type == "wait_day" and observed.get("day") is not None:
            self.day = int(observed["day"])

    def _apply_price_observation(self, *, product_id: str, store: str,
                                 price: float, day: int) -> None:
        belief = self._price_beliefs.setdefault(_price_key(product_id, store), _PriceBelief())
        if belief.last_price is not None and belief.last_day is not None:
            elapsed = day - belief.last_day
            if elapsed > 0:
                daily_delta = (price - belief.last_price) / elapsed
                self._record_delta(belief, daily_delta)
        belief.last_price = price
        belief.last_day = day
        belief.observations += 1
        entity_id = product_id
        entity = self.entities.setdefault(
            entity_id, {"id": entity_id, "kind": "product", "attributes": {}})
        entity["attributes"]["price"] = price
        entity["attributes"]["store"] = store

    def _record_delta(self, belief: _PriceBelief, daily_delta: float) -> None:
        """Learn the daily price trend, but only trust a repeated move.

        A single price move can be a one-off promo, so the model
        adopts a daily delta only after the same delta repeats
        ``TREND_CONFIRMATIONS`` days in a row. A move that breaks an
        adopted trend drops the trend and starts a fresh history.
        """
        if belief.trend_adopted and daily_delta != belief.daily_delta:
            belief.trend_adopted = False
            belief.daily_delta = 0.0
            belief.delta_history = [daily_delta]
            return
        belief.delta_history.append(daily_delta)
        belief.delta_history = belief.delta_history[-5:]
        recent = belief.delta_history[-TREND_CONFIRMATIONS:]
        if len(recent) == TREND_CONFIRMATIONS and len(set(recent)) == 1:
            belief.daily_delta = recent[0]
            belief.trend_adopted = True

    def _apply_bill_observation(self, *, bill_id: str, observed: dict, day: int) -> None:
        belief = self._bill_beliefs.setdefault(bill_id, _BillBelief())
        if observed.get("amount") is not None:
            belief.amount = float(observed["amount"])
        if observed.get("due_day") is not None:
            belief.due_day = int(observed["due_day"])
        status = observed.get("status")
        if status == "paid":
            belief.paid = True
        elif status in ("due", "due_soon", "not_due"):
            belief.paid = False
            if observed.get("amount_due") is not None:
                belief.amount = float(observed["amount_due"])
        if observed.get("amount_due") == 0 and status == "paid":
            belief.paid = True
        belief.observations += 1
        entity = self.entities.setdefault(
            bill_id, {"id": bill_id, "kind": "bill", "attributes": {}})
        entity["attributes"].update(
            {k: v for k, v in observed.items() if v is not None})

    # -- accuracy tracking ------------------------------------------------
    def accuracy(self) -> float:
        """Overall prediction accuracy so far (0.0 when nothing scored)."""
        total = self.stats["predictions"]
        return self.stats["correct"] / total if total else 0.0

    def start_episode(self) -> None:
        """Mark the start of one episode for per-episode accuracy."""
        self._episode_start = dict(self.stats)

    def end_episode(self) -> float:
        """Close the episode and record its accuracy. Returns it."""
        if self._episode_start is None:
            return self.accuracy()
        total = self.stats["predictions"] - self._episode_start["predictions"]
        correct = self.stats["correct"] - self._episode_start["correct"]
        episode_accuracy = correct / total if total else 0.0
        self.episode_accuracies.append(episode_accuracy)
        self._episode_start = None
        return episode_accuracy
