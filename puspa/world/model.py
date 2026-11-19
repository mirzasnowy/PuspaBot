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
  beliefs about prices (per product and store), bills (amount, due
  day, paid flag), schedules (how long a task kind usually takes and
  how reliable its deadline is), capabilities (what the agent
  can and cannot do per task kind, learned from task outcomes),
  contacts (how one contact usually responds: reply rate, usual
  channel, reply delay, learned only from observed interactions),
  and habits (recurring patterns in the user's own task history:
  usual hour, usual weekday, usual interval per task kind,
  learned only from repeated tasks). The state is plain data, so
  a planner can copy it and simulate without touching the live
  model.
- ``predict(action)`` forecasts the outcome of an action BEFORE the
  action is taken, with a confidence. Predicting never mutates the
  world state.
- ``update(observation)`` applies a real observation: the observed
  values win over the model, a miss is recorded as a misprediction,
  the model is corrected, and the miss is reported to the learning
  loop (see the seam note below).
- ``ingest_step_failure(...)`` folds a step that aborted the run:
  a gate/policy refusal teaches capability only (the pending
  prediction is discarded unscored, price and bill beliefs are
  never touched, no misprediction is reported), while a world
  failure scores a pending prediction against the failure, so a
  miss becomes a misprediction in the learning loop.
- ``plan(task)`` lives in ``puspa.world.planner`` and simulates
  multi-step tasks against a copy of this state.

Learning-loop seam
-------------------
Mispredictions are forwarded to the learning loop with a lazy import
inside ``WorldModel._report_misprediction``:

    from puspa.memory.learning_loop import record_misprediction

called with one record dict holding the keys ``task``,
``predicted``, ``observed``, ``error`` and ``context``. The import
is lazy and every failure is swallowed on purpose: the learning loop
module is built in parallel and may not exist yet, and the world
model must keep working without it. When the loop exists, the call
is best effort: a failure to report never breaks an update. The
record is always kept in ``WorldModel.mispredictions`` either way,
so nothing is lost if the loop is absent.

A session may point the model at its own loop instead of the
module-level default: set ``model.learning_loop`` to a
``LearningLoop`` instance (``puspa.chassis.agent_session`` does
this when a turn binds, and ``TaskRunner`` does it when it is
constructed with both a model and a loop). The report then goes to
that instance through the same ``record_misprediction`` call. The
attribute is runtime wiring, not knowledge: it is not part of
``state()`` and is not persisted by ``puspa.world.store``.

No database, no network, no new dependency. All state is in memory.
"""

from __future__ import annotations

import copy

from .agents import AgentModelMixin
from .beliefs import (
    DEFAULT_CONFIDENCE,
    INTERVAL_TOLERANCE_DAYS,
    Prediction,
    _BillBelief,
    _CapabilityBelief,
    _ContactBelief,
    _HabitBelief,
    _price_key,
    _PriceBelief,
    _ScheduleBelief,
)
from .causal import CausalExpectationMixin
from .ignorance import IgnoranceMixin

TREND_CONFIRMATIONS = 3
PRICE_TOLERANCE_RP = 0
KNOWN_CONFIDENCE = 0.6
TREND_CONFIDENCE = 0.9
MAX_CONFIDENCE = 0.95
# Schedule and capability beliefs follow the procedural memory
# pattern (puspa/memory/procedural.py): a belief becomes verified
# after a few consistent observations, a verified belief untouched
# for BELIEF_STALE_AFTER_DAYS decays back to trial, and an
# observation that contradicts a verified belief is held aside as
# a candidate instead of silently overwriting it. There is no
# demotion step anywhere here: revision 7 section 3.3 forbids it.
SCHEDULE_MIN_OBSERVATIONS = 3
CAPABILITY_MIN_OBSERVATIONS = 3
CONTACT_MIN_OBSERVATIONS = 3
HABIT_MIN_OBSERVATIONS = 3
BELIEF_STALE_AFTER_DAYS = 90
CONTRADICTION_CONFIRMATIONS = 3
CAPABILITY_CONTRADICTION_RUNS = 2
CONTACT_CONTRADICTION_RUNS = 2
HABIT_CONTRADICTION_CONFIRMATIONS = 3
DURATION_TOLERANCE_DAYS = 1.0

ACTION_TYPES = (
    "check_price",
    "check_bill",
    "pay_bill",
    "wait_day",
    "buy_from_store",
    "estimate_task",
    "attempt_task",
    "contact_reply",
    "habit_pattern",
)

#: Kinds of step failure folded by ``ingest_step_failure``.
#: "gate_rejected" means policy refused the step before the world
#: could answer (handoff schema rejection, budget exhaustion):
#: capability learns, the pending prediction is discarded
#: unscored, and price/bill beliefs are never touched.
#: "world_failed" means the step ran and the world said no (the
#: executor raised): a pending prediction is scored against the
#: failure, so a miss becomes a misprediction in the learning
#: loop. The two paths are deliberately separate: a refusal is
#: policy evidence, not world evidence.
STEP_FAILURE_KINDS = ("gate_rejected", "world_failed")


class WorldModel(CausalExpectationMixin, AgentModelMixin, IgnoranceMixin):
    """Structured world model with predict, update, and correction."""

    def __init__(self) -> None:
        super().__init__()
        self.entities: dict[str, dict] = {}
        self.relations: list[dict] = []
        self.day: int = 0
        self._price_beliefs: dict[str, _PriceBelief] = {}
        self._bill_beliefs: dict[str, _BillBelief] = {}
        self._schedule_beliefs: dict[str, _ScheduleBelief] = {}
        self._capability_beliefs: dict[str, _CapabilityBelief] = {}
        self._contact_beliefs: dict[str, _ContactBelief] = {}
        self._habit_beliefs: dict[str, _HabitBelief] = {}
        self._pending: Prediction | None = None
        self.mispredictions: list[dict] = []
        self.gate_rejections: list[dict] = []
        self.belief_contradictions: list[dict] = []
        self.stats: dict[str, int] = {"predictions": 0, "correct": 0}
        self.episode_accuracies: list[float] = []
        self._episode_start: dict[str, int] | None = None
        # Session learning loop (None: module-level default loop, see
        # the seam note above). Set by the owning session; not state().
        self.learning_loop = None

    def _report_misprediction(self, record: dict) -> bool:
        """Forward one misprediction record to the learning loop.

        Goes to this model's session loop when one is set, else to
        the module-level default loop through a lazy import (the
        loop module may not exist yet). Returns True only when the
        loop accepted the record. Never raises.
        """
        try:
            if self.learning_loop is not None:
                self.learning_loop.record_misprediction(record)
            else:
                from puspa.memory.learning_loop import record_misprediction

                record_misprediction(record)
        except Exception:
            return False
        return True

    # -- state snapshot (planner copies this, never mutates the live model)
    @property
    def state(self) -> dict:
        return {
            "entities": copy.deepcopy(self.entities),
            "relations": copy.deepcopy(self.relations),
            "day": self.day,
            "price_beliefs": copy.deepcopy(self._price_beliefs),
            "bill_beliefs": copy.deepcopy(self._bill_beliefs),
            "schedule_beliefs": copy.deepcopy(self._schedule_beliefs),
            "capability_beliefs": copy.deepcopy(self._capability_beliefs),
            "contact_beliefs": copy.deepcopy(self._contact_beliefs),
            "habit_beliefs": copy.deepcopy(self._habit_beliefs),
            "agent_models": copy.deepcopy(self._agent_models),
            "belief_contradictions": copy.deepcopy(self.belief_contradictions),
        }

    def _restore_state(self, state: dict) -> None:
        self.entities = copy.deepcopy(state["entities"])
        self.relations = copy.deepcopy(state["relations"])
        self.day = state["day"]
        self._price_beliefs = copy.deepcopy(state["price_beliefs"])
        self._bill_beliefs = copy.deepcopy(state["bill_beliefs"])
        self._schedule_beliefs = copy.deepcopy(state.get("schedule_beliefs") or {})
        self._capability_beliefs = copy.deepcopy(state.get("capability_beliefs") or {})
        self._contact_beliefs = copy.deepcopy(state.get("contact_beliefs") or {})
        self._habit_beliefs = copy.deepcopy(state.get("habit_beliefs") or {})
        self._agent_models = copy.deepcopy(state.get("agent_models") or {})
        self.belief_contradictions = copy.deepcopy(state.get("belief_contradictions") or [])

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
            if (
                self.entities[entity_id]["kind"] == "product"
                and "price" in attrs
                and "store" in attrs
            ):
                self._apply_price_observation(
                    product_id=entity_id,
                    store=str(attrs["store"]),
                    price=float(attrs["price"]),
                    day=self.day,
                )
            if self.entities[entity_id]["kind"] == "bill":
                self._apply_bill_observation(
                    bill_id=entity_id,
                    observed={
                        "amount": attrs.get("amount"),
                        "due_day": attrs.get("due_day"),
                        "status": attrs.get("status"),
                        "amount_due": attrs.get("amount_due"),
                    },
                    day=self.day,
                )
        for relation in relations or []:
            if isinstance(relation, dict):
                self.relations.append(dict(relation))
            else:
                subject, predicate, obj = relation
                self.relations.append(
                    {"subject": str(subject), "predicate": str(predicate), "object": str(obj)}
                )

    def ingest_task_result(self, task: dict) -> None:
        """Fold one finished task result into the model.

        ``task`` is ``{"task": name, "success": bool, "result": {...}}``.
        Every finished task teaches the capability and schedule
        beliefs, success or failure: a failure is evidence about
        what the agent cannot do, not a reason to skip learning.
        A result carrying a price observation updates the price
        belief; a result carrying a bill status updates the bill
        belief; any other result is stored as an entity attribute
        when it names an ``entity_id``. Failed tasks do not change
        price or bill beliefs.
        """
        result = task.get("result") or {}
        task_kind = str(task.get("task") or result.get("task_kind") or "unknown")
        day = int(result.get("day", self.day))
        self._apply_capability_observation(
            task_kind=task_kind, success=bool(task.get("success", True)), day=day
        )
        # Every finished task is one more data point in the user's
        # own history: it feeds the habit belief for its kind.
        # Hour and weekday come only from the result when the
        # caller observed them; they are never guessed.
        self._apply_habit_observation(
            task_kind=task_kind,
            day=day,
            hour=(int(result["hour"]) if result.get("hour") is not None else None),
            weekday=(int(result["weekday"]) if result.get("weekday") is not None else None),
        )
        # A result that names a contact is one observed
        # interaction with that contact: reply or silence, the
        # channel used, and the reply delay when it replied.
        if result.get("contact_id") is not None:
            self._apply_contact_observation(
                contact_id=str(result["contact_id"]),
                channel=(str(result["channel"]) if result.get("channel") is not None else None),
                replied=(bool(result["replied"]) if result.get("replied") is not None else None),
                reply_delay_hours=(
                    float(result["reply_delay_hours"])
                    if result.get("reply_delay_hours") is not None
                    else None
                ),
                day=day,
            )
            # The same observed interaction also feeds the
            # contact's goal model (K5, AgentModelMixin).
            self.feed_task_result(result, day)
        if "duration_days" in result:
            self._apply_schedule_observation(
                task_kind=task_kind,
                duration_days=float(result["duration_days"]),
                deadline_days=(
                    float(result["deadline_days"])
                    if result.get("deadline_days") is not None
                    else None
                ),
                day=day,
            )
        if not task.get("success", True):
            return
        if "price" in result and "store" in result and "product_id" in result:
            self._apply_price_observation(
                product_id=str(result["product_id"]),
                store=str(result["store"]),
                price=float(result["price"]),
                day=int(result.get("day", self.day)),
            )
        if "bill_id" in result and "status" in result:
            self._apply_bill_observation(
                bill_id=str(result["bill_id"]),
                observed=result,
                day=int(result.get("day", self.day)),
            )
        if "entity_id" in result:
            entity_id = str(result["entity_id"])
            entity = self.entities.setdefault(
                entity_id, {"id": entity_id, "kind": "unknown", "attributes": {}}
            )
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
        if state is None:
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
        if action_type == "estimate_task":
            return self._forecast_schedule(action, state)
        if action_type == "attempt_task":
            return self._forecast_capability(action, state)
        if action_type == "contact_reply":
            return self._forecast_contact(action, state)
        if action_type == "habit_pattern":
            return self._forecast_habit(action, state)
        return self._forecast_buy(action, state)

    def _forecast_price(self, action: dict, state: dict) -> tuple[dict, float]:
        belief = state["price_beliefs"].get(
            _price_key(str(action["product_id"]), str(action["store"]))
        )
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
        capability = state["capability_beliefs"].get("pay_bill")
        if capability is not None and capability.observations >= CAPABILITY_MIN_OBSERVATIONS:
            rate = capability.success_rate or 0.0
            if rate < 0.5:
                return {"success": False, "reason": "low_capability"}, capability.confidence
            return {"success": True, "status": "paid", "amount_due": 0}, min(
                MAX_CONFIDENCE, 0.5 + 0.4 * rate
            )
        return {"success": True, "status": "paid", "amount_due": 0}, 0.85

    def _forecast_buy(self, action: dict, state: dict) -> tuple[dict, float]:
        outcome, confidence = self._forecast_price(action, state)
        if not outcome["known"]:
            return {"success": False, "reason": "unknown_price"}, 0.0
        capability = state["capability_beliefs"].get("buy_from_store")
        if capability is not None and capability.observations >= CAPABILITY_MIN_OBSERVATIONS:
            rate = capability.success_rate or 0.0
            if rate < 0.5:
                return {"success": False, "reason": "low_capability"}, capability.confidence
            confidence = min(MAX_CONFIDENCE, 0.5 + 0.4 * rate)
        return {
            "success": True,
            "store": str(action["store"]),
            "price": outcome["price"],
        }, confidence

    def _forecast_schedule(self, action: dict, state: dict) -> tuple[dict, float]:
        """Forecast how long a task kind usually takes.

        Learned only from observed durations: with no schedule
        belief the answer is unknown, never invented.
        """
        task_kind = str(action.get("task_kind") or action.get("task") or "unknown")
        belief = state["schedule_beliefs"].get(task_kind)
        if belief is None or belief.mean_duration is None:
            return {"task_kind": task_kind, "expected_days": None, "known": False}, 0.0
        rate = belief.on_time_rate
        return {
            "task_kind": task_kind,
            "expected_days": belief.mean_duration,
            "deadline_reliable": rate is not None and rate >= 0.8,
            "observations": belief.observations,
        }, belief.confidence

    def _forecast_capability(self, action: dict, state: dict) -> tuple[dict, float]:
        """Forecast whether the agent can do a task kind.

        Learned only from task outcomes: with no capability belief
        the answer is unknown, never invented.
        """
        task_kind = str(action.get("task_kind") or action.get("task") or "unknown")
        belief = state["capability_beliefs"].get(task_kind)
        if belief is None or belief.success_rate is None:
            return {"task_kind": task_kind, "success": None, "known": False}, 0.0
        return {
            "task_kind": task_kind,
            "success": belief.success_rate >= 0.5,
            "success_rate": belief.success_rate,
            "observations": belief.observations,
        }, belief.confidence

    def _forecast_contact(self, action: dict, state: dict) -> tuple[dict, float]:
        """Forecast how a contact will respond, from observed interactions only.

        With no observed interaction the answer is unknown, never
        invented: a contact the model never saw has no reply
        rate, no preferred channel, and no expected delay.
        """
        contact_id = str(action.get("contact_id") or "")
        belief = state["contact_beliefs"].get(contact_id)
        if belief is None or belief.reply_rate is None:
            return {"contact_id": contact_id, "reply_likely": None, "known": False}, 0.0
        return {
            "contact_id": contact_id,
            "reply_likely": belief.reply_rate >= 0.5,
            "reply_rate": belief.reply_rate,
            "preferred_channel": belief.preferred_channel,
            "mean_reply_delay_hours": belief.mean_reply_delay_hours,
            "observations": belief.observations,
        }, belief.confidence

    def _forecast_habit(self, action: dict, state: dict) -> tuple[dict, float]:
        """Forecast the recurring pattern for one kind of task.

        Learned only from the user's own repeated tasks: with no
        repeated pattern the answer is unknown, never invented.
        A single task, or scattered tasks with nothing repeating,
        reports no pattern.
        """
        task_kind = str(action.get("task_kind") or action.get("task") or "unknown")
        belief = state["habit_beliefs"].get(task_kind)
        if (
            belief is None
            or belief.observations < HABIT_MIN_OBSERVATIONS
            or not belief.has_repeated_pattern
        ):
            return {
                "task_kind": task_kind,
                "pattern_known": False,
                "peak_hour": None,
                "peak_weekday": None,
                "mean_interval_days": None,
            }, 0.0
        return {
            "task_kind": task_kind,
            "pattern_known": True,
            "peak_hour": belief.peak_hour,
            "peak_weekday": belief.peak_weekday,
            "mean_interval_days": belief.mean_interval_days,
            "observations": belief.observations,
        }, belief.confidence

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
                self._report_misprediction(record)
            self._apply_observation(action, observed, correct=correct)
            return {
                "correct": correct,
                "error": error,
                "predicted": predicted,
                "observed": observed,
            }
        self._apply_observation(action, observed, correct=None)
        return {"correct": None, "error": None, "predicted": None, "observed": observed}

    def discard_pending_prediction(self, action: dict | None = None) -> bool:
        """Drop the pending prediction without scoring it.

        Used when a step never produced a world observation (a
        gate/policy refusal, a budget stop): the world never
        answered, so there is nothing to score, and a stale
        prediction must not be scored against a later, unrelated
        observation. With ``action`` given, only a pending
        prediction for that same action is dropped. Returns True
        when a prediction was dropped.
        """
        if self._pending is None:
            return False
        if action is not None and not self._same_action(self._pending.action, action):
            return False
        self._pending = None
        return True

    def ingest_step_failure(
        self,
        *,
        task_kind: str,
        action: dict | None = None,
        failure_kind: str,
        reason: str = "",
        day: int | None = None,
    ) -> dict:
        """Fold one failed step into the model.

        ``task_kind`` is the step name; ``action`` is the world
        action the step was translated to, or None for a generic
        step. Two failure kinds, deliberately separate paths:

        - ``"gate_rejected"``: policy refused the step before the
          world could answer (handoff schema rejection, budget
          exhaustion). The pending prediction for the step is
          discarded WITHOUT scoring (there is no observation to
          score it against), the capability belief for the task
          kind records one failure, and the rejection is noted
          in ``gate_rejections``. Price and bill beliefs are
          never touched: a refusal says nothing about prices or
          bills, and no misprediction is reported to the
          learning loop.
        - ``"world_failed"``: the step ran and the world said no
          (the executor raised). A pending prediction for the
          step is scored against the failure (observed
          ``{"success": False}``), so a miss is recorded and
          reported to the learning loop as a misprediction
          through the usual seam; the capability belief for the
          task kind records one failure.

        Returns a summary dict with ``task_kind``,
        ``failure_kind``, the ``update`` outcome under
        ``"scored"`` (only for a scored ``"world_failed"``) and
        whether a pending prediction was discarded. Raises
        ValueError for an unknown failure kind.
        """
        if failure_kind not in STEP_FAILURE_KINDS:
            raise ValueError(f"unknown step failure kind: {failure_kind!r}")
        day = self.day if day is None else int(day)
        scored = None
        discarded = False
        if failure_kind == "gate_rejected":
            discarded = self.discard_pending_prediction(action)
            self._apply_capability_observation(task_kind=str(task_kind), success=False, day=day)
            self.gate_rejections.append(
                {
                    "task_kind": str(task_kind),
                    "failure_kind": failure_kind,
                    "reason": reason,
                    "action_type": (action or {}).get("type"),
                    "day": day,
                }
            )
        else:  # "world_failed"
            pending_matches = (
                action is not None
                and self._pending is not None
                and self._same_action(self._pending.action, action)
            )
            if pending_matches and action is not None:
                scored = self.update(
                    {
                        "action": dict(action),
                        "observed": {"success": False},
                        "task": str(task_kind),
                        "day": day,
                    }
                )
                # update() already taught capability for the step
                # when the action is attempt_task (its task kind
                # is the step name); every other action needs the
                # step-name observation applied here, once.
                if not (
                    action.get("type") == "attempt_task"
                    and str(action.get("task_kind") or "") == str(task_kind)
                ):
                    self._apply_capability_observation(
                        task_kind=str(task_kind), success=False, day=day
                    )
            else:
                self._apply_capability_observation(task_kind=str(task_kind), success=False, day=day)
        return {
            "task_kind": str(task_kind),
            "failure_kind": failure_kind,
            "scored": scored,
            "discarded_pending": discarded,
        }

    @staticmethod
    def _same_action(first: dict, second: dict) -> bool:
        if first.get("type") != second.get("type"):
            return False
        for key in ("product_id", "store", "bill_id", "task_kind", "contact_id"):
            if (key in first or key in second) and str(first.get(key)) != str(second.get(key)):
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
        if action_type == "estimate_task":
            if predicted.get("expected_days") is None or observed.get("duration_days") is None:
                return 1.0
            diff = abs(float(predicted["expected_days"]) - float(observed["duration_days"]))
            return 0.0 if diff <= DURATION_TOLERANCE_DAYS else diff
        if action_type == "attempt_task":
            if predicted.get("success") is None or observed.get("success") is None:
                return 1.0
            return 0.0 if bool(predicted["success"]) == bool(observed["success"]) else 1.0
        if action_type == "contact_reply":
            if predicted.get("reply_likely") is None or observed.get("replied") is None:
                return 1.0
            return 0.0 if bool(predicted["reply_likely"]) == bool(observed["replied"]) else 1.0
        if action_type == "habit_pattern":
            if (
                predicted.get("mean_interval_days") is not None
                and observed.get("interval_days") is not None
            ):
                diff = abs(
                    float(predicted["mean_interval_days"]) - float(observed["interval_days"])
                )
                return 0.0 if diff <= INTERVAL_TOLERANCE_DAYS else diff
            if predicted.get("peak_hour") is not None and observed.get("hour") is not None:
                return 0.0 if int(predicted["peak_hour"]) == int(observed["hour"]) else 1.0
            return 1.0
        return 1.0

    def _apply_observation(self, action: dict, observed: dict, correct: bool | None) -> None:
        action_type = action.get("type")
        if action_type in ("check_price", "buy_from_store"):
            price_belief = self._price_beliefs.setdefault(
                _price_key(str(action["product_id"]), str(action["store"])), _PriceBelief()
            )
            if observed.get("price") is not None:
                self._apply_price_observation(
                    product_id=str(action["product_id"]),
                    store=str(action["store"]),
                    price=float(observed["price"]),
                    day=self.day,
                )
            if correct is True:
                price_belief.confidence = min(MAX_CONFIDENCE, price_belief.confidence + 0.05)
            elif correct is False:
                price_belief.confidence = max(0.1, price_belief.confidence - 0.2)
            if action_type == "buy_from_store" and observed.get("success") is not None:
                self._apply_capability_observation(
                    task_kind="buy_from_store", success=bool(observed["success"]), day=self.day
                )
        elif action_type in ("check_bill", "pay_bill"):
            self._apply_bill_observation(
                bill_id=str(action["bill_id"]), observed=observed, day=self.day
            )
            bill_belief = self._bill_beliefs.get(str(action["bill_id"]))
            if bill_belief is not None:
                if correct is True:
                    bill_belief.confidence = min(MAX_CONFIDENCE, bill_belief.confidence + 0.05)
                elif correct is False:
                    bill_belief.confidence = max(0.1, bill_belief.confidence - 0.2)
            if action_type == "pay_bill" and observed.get("success") is not None:
                self._apply_capability_observation(
                    task_kind="pay_bill", success=bool(observed["success"]), day=self.day
                )
        elif action_type == "wait_day" and observed.get("day") is not None:
            self.day = int(observed["day"])
        elif action_type == "estimate_task" and observed.get("duration_days") is not None:
            task_kind = str(action.get("task_kind") or action.get("task") or "unknown")
            self._apply_schedule_observation(
                task_kind=task_kind,
                duration_days=float(observed["duration_days"]),
                deadline_days=(
                    float(observed["deadline_days"])
                    if observed.get("deadline_days") is not None
                    else None
                ),
                day=self.day,
            )
            schedule_belief = self._schedule_beliefs.get(task_kind)
            if schedule_belief is not None:
                if correct is True:
                    schedule_belief.confidence = min(
                        MAX_CONFIDENCE, schedule_belief.confidence + 0.05
                    )
                elif correct is False:
                    schedule_belief.confidence = max(0.1, schedule_belief.confidence - 0.2)
        elif action_type == "attempt_task" and observed.get("success") is not None:
            task_kind = str(action.get("task_kind") or action.get("task") or "unknown")
            self._apply_capability_observation(
                task_kind=task_kind, success=bool(observed["success"]), day=self.day
            )
        elif action_type == "contact_reply":
            contact_id = str(action.get("contact_id") or "")
            self._apply_contact_observation(
                contact_id=contact_id,
                channel=(str(observed["channel"]) if observed.get("channel") is not None else None),
                replied=(
                    bool(observed["replied"]) if observed.get("replied") is not None else None
                ),
                reply_delay_hours=(
                    float(observed["reply_delay_hours"])
                    if observed.get("reply_delay_hours") is not None
                    else None
                ),
                day=self.day,
            )
            contact_belief = self._contact_beliefs.get(contact_id)
            if contact_belief is not None:
                if correct is True:
                    contact_belief.confidence = min(
                        MAX_CONFIDENCE, contact_belief.confidence + 0.05
                    )
                elif correct is False:
                    contact_belief.confidence = max(0.1, contact_belief.confidence - 0.2)
        elif action_type == "habit_pattern":
            task_kind = str(action.get("task_kind") or action.get("task") or "unknown")
            self._apply_habit_observation(
                task_kind=task_kind,
                day=self.day,
                hour=(int(observed["hour"]) if observed.get("hour") is not None else None),
                weekday=(int(observed["weekday"]) if observed.get("weekday") is not None else None),
                interval_days=(
                    float(observed["interval_days"])
                    if observed.get("interval_days") is not None
                    else None
                ),
            )
            habit_belief = self._habit_beliefs.get(task_kind)
            if habit_belief is not None:
                if correct is True:
                    habit_belief.confidence = min(MAX_CONFIDENCE, habit_belief.confidence + 0.05)
                elif correct is False:
                    habit_belief.confidence = max(0.1, habit_belief.confidence - 0.2)

    def _apply_price_observation(
        self, *, product_id: str, store: str, price: float, day: int
    ) -> None:
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
            entity_id, {"id": entity_id, "kind": "product", "attributes": {}}
        )
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
            bill_id, {"id": bill_id, "kind": "bill", "attributes": {}}
        )
        entity["attributes"].update({k: v for k, v in observed.items() if v is not None})

    # -- schedule and capability beliefs (learned only from observations)
    def _snapshot_schedule(self, belief: _ScheduleBelief, reason: str, day: int) -> None:
        """Keep the current schedule version in history before replacing it."""
        belief.history.append(
            {
                "version": belief.version,
                "status": belief.status,
                "observations": belief.observations,
                "mean_duration": belief.mean_duration,
                "on_time_rate": belief.on_time_rate,
                "durations": list(belief.durations),
                "reason": reason,
                "day": day,
            }
        )

    def _snapshot_capability(self, belief: _CapabilityBelief, reason: str, day: int) -> None:
        """Keep the current capability version in history before replacing it."""
        belief.history.append(
            {
                "version": belief.version,
                "status": belief.status,
                "observations": belief.observations,
                "success_rate": belief.success_rate,
                "successes": belief.successes,
                "failures": belief.failures,
                "reason": reason,
                "day": day,
            }
        )

    def _apply_schedule_observation(
        self, *, task_kind: str, duration_days: float, deadline_days: float | None, day: int
    ) -> None:
        """Fold one observed duration into the schedule belief.

        A duration that disagrees wildly with a verified belief
        (more than twice the mean, or less than half of it) never
        overwrites it: it is held as a candidate, and only three
        confirming observations promote it to a new version, with
        the old version kept in history.
        """
        belief = self._schedule_beliefs.setdefault(task_kind, _ScheduleBelief(task_kind=task_kind))
        mean = belief.mean_duration
        if (
            belief.status == "verified"
            and mean is not None
            and mean > 0
            and (duration_days > 2 * mean or duration_days < mean / 2)
        ):
            candidate = None
            for existing in belief.candidates:
                if abs(existing["durations"][-1] - duration_days) <= DURATION_TOLERANCE_DAYS:
                    candidate = existing
                    break
            if candidate is None:
                candidate = {"durations": [], "on_time": 0, "with_deadline": 0, "confirmations": 0}
                belief.candidates.append(candidate)
            candidate["durations"].append(duration_days)
            candidate["confirmations"] += 1
            if deadline_days is not None:
                candidate["with_deadline"] += 1
                if duration_days <= deadline_days:
                    candidate["on_time"] += 1
            self.belief_contradictions.append(
                {
                    "kind": "schedule",
                    "task_kind": task_kind,
                    "observed_duration_days": duration_days,
                    "believed_mean_days": mean,
                    "day": day,
                    "confirmations": candidate["confirmations"],
                }
            )
            if candidate["confirmations"] >= CONTRADICTION_CONFIRMATIONS:
                self._snapshot_schedule(belief, reason="contradiction", day=day)
                belief.version += 1
                belief.durations = list(candidate["durations"])
                belief.observations = candidate["confirmations"]
                belief.on_time = candidate["on_time"]
                belief.with_deadline = candidate["with_deadline"]
                belief.candidates = []
                belief.last_day = day
                belief.status = "verified"
                belief.confidence = KNOWN_CONFIDENCE
            return
        belief.durations.append(duration_days)
        belief.durations = belief.durations[-5:]
        belief.observations += 1
        if deadline_days is not None:
            belief.with_deadline += 1
            if duration_days <= deadline_days:
                belief.on_time += 1
        belief.last_day = day
        if belief.observations >= SCHEDULE_MIN_OBSERVATIONS:
            belief.status = "verified"
        belief.confidence = min(0.9, DEFAULT_CONFIDENCE + 0.05 * belief.observations)

    def _apply_capability_observation(self, *, task_kind: str, success: bool, day: int) -> None:
        """Fold one task outcome into the capability belief.

        An outcome that contradicts a verified belief (a failure
        when the agent almost always succeeds, or a success when
        it almost always fails) is held as a candidate: only two
        consecutive contradictions replace the belief, with the
        old version kept in history. Ordinary outcomes fold in
        directly.
        """
        belief = self._capability_beliefs.setdefault(
            task_kind, _CapabilityBelief(task_kind=task_kind)
        )
        rate = belief.success_rate
        contradicts = (
            belief.status == "verified"
            and rate is not None
            and ((rate >= 0.8 and not success) or (rate <= 0.2 and success))
        )
        if contradicts:
            if belief.contradiction_value is success:
                belief.consecutive_contradictions += 1
            else:
                belief.contradiction_value = success
                belief.consecutive_contradictions = 1
            belief.candidates.append({"success": success, "day": day})
            self.belief_contradictions.append(
                {
                    "kind": "capability",
                    "task_kind": task_kind,
                    "observed_success": success,
                    "believed_success_rate": rate,
                    "day": day,
                    "consecutive": belief.consecutive_contradictions,
                }
            )
            if belief.consecutive_contradictions >= CAPABILITY_CONTRADICTION_RUNS:
                self._snapshot_capability(belief, reason="contradiction", day=day)
                belief.version += 1
                belief.successes = CAPABILITY_CONTRADICTION_RUNS if success else 0
                belief.failures = 0 if success else CAPABILITY_CONTRADICTION_RUNS
                belief.observations = CAPABILITY_CONTRADICTION_RUNS
                belief.candidates = []
                belief.consecutive_contradictions = 0
                belief.contradiction_value = None
                belief.last_day = day
                belief.status = "trial"
                belief.confidence = DEFAULT_CONFIDENCE
            return
        belief.consecutive_contradictions = 0
        belief.contradiction_value = None
        if success:
            belief.successes += 1
        else:
            belief.failures += 1
        belief.observations += 1
        belief.last_day = day
        if belief.observations >= CAPABILITY_MIN_OBSERVATIONS:
            belief.status = "verified"
        belief.confidence = min(0.9, DEFAULT_CONFIDENCE + 0.05 * belief.observations)

    # -- contact and habit beliefs (learned only from observations)
    def _snapshot_contact(self, belief: _ContactBelief, reason: str, day: int) -> None:
        """Keep the current contact version in history before replacing it."""
        belief.history.append(
            {
                "version": belief.version,
                "status": belief.status,
                "observations": belief.observations,
                "reply_rate": belief.reply_rate,
                "preferred_channel": belief.preferred_channel,
                "mean_reply_delay_hours": belief.mean_reply_delay_hours,
                "reason": reason,
                "day": day,
            }
        )

    def _snapshot_habit(self, belief: _HabitBelief, reason: str, day: int) -> None:
        """Keep the current habit version in history before replacing it."""
        belief.history.append(
            {
                "version": belief.version,
                "status": belief.status,
                "observations": belief.observations,
                "peak_hour": belief.peak_hour,
                "peak_weekday": belief.peak_weekday,
                "mean_interval_days": belief.mean_interval_days,
                "reason": reason,
                "day": day,
            }
        )

    def _apply_contact_observation(
        self,
        *,
        contact_id: str,
        channel: str | None,
        replied: bool | None,
        reply_delay_hours: float | None,
        day: int,
    ) -> None:
        """Fold one observed interaction into the contact belief.

        An interaction with no reply when the contact almost
        always replies (or a reply when it almost never replies)
        contradicts a verified belief: it is held as a
        candidate, and only two consecutive contradictions
        replace the belief, with the old version kept in
        history. Ordinary interactions fold in directly. An
        observation that carries neither a reply flag nor a
        channel still counts as one observed interaction, but
        teaches nothing about reply rate or channel.
        """
        belief = self._contact_beliefs.setdefault(contact_id, _ContactBelief(contact_id=contact_id))
        rate = belief.reply_rate
        contradicts = (
            belief.status == "verified"
            and rate is not None
            and replied is not None
            and ((rate >= 0.8 and not replied) or (rate <= 0.2 and replied))
        )
        if contradicts:
            if belief.contradiction_value is replied:
                belief.consecutive_contradictions += 1
            else:
                belief.contradiction_value = replied
                belief.consecutive_contradictions = 1
            belief.candidates.append(
                {
                    "replied": replied,
                    "channel": channel,
                    "reply_delay_hours": reply_delay_hours,
                    "day": day,
                }
            )
            self.belief_contradictions.append(
                {
                    "kind": "contact",
                    "contact_id": contact_id,
                    "observed_replied": replied,
                    "believed_reply_rate": rate,
                    "day": day,
                    "consecutive": belief.consecutive_contradictions,
                }
            )
            if belief.consecutive_contradictions >= CONTACT_CONTRADICTION_RUNS:
                self._snapshot_contact(belief, reason="contradiction", day=day)
                belief.version += 1
                kept = list(belief.candidates)
                belief.observations = len(kept)
                belief.replies = sum(1 for item in kept if item.get("replied"))
                belief.reply_delays = [
                    float(item["reply_delay_hours"])
                    for item in kept
                    if item.get("replied") and item.get("reply_delay_hours") is not None
                ]
                belief.channels = {}
                for item in kept:
                    if item.get("channel") is not None:
                        belief.channels[item["channel"]] = (
                            belief.channels.get(item["channel"], 0) + 1
                        )
                belief.candidates = []
                belief.consecutive_contradictions = 0
                belief.contradiction_value = None
                belief.last_day = day
                belief.status = "trial"
                belief.confidence = DEFAULT_CONFIDENCE
            return
        belief.consecutive_contradictions = 0
        belief.contradiction_value = None
        belief.observations += 1
        if replied:
            belief.replies += 1
            if reply_delay_hours is not None:
                belief.reply_delays.append(float(reply_delay_hours))
                belief.reply_delays = belief.reply_delays[-5:]
        if channel is not None:
            belief.channels[channel] = belief.channels.get(channel, 0) + 1
        belief.last_day = day
        if belief.observations >= CONTACT_MIN_OBSERVATIONS:
            belief.status = "verified"
        belief.confidence = min(0.9, DEFAULT_CONFIDENCE + 0.05 * belief.observations)

    def _apply_habit_observation(
        self,
        *,
        task_kind: str,
        day: int,
        hour: int | None = None,
        weekday: int | None = None,
        interval_days: float | None = None,
    ) -> None:
        """Fold one observed task into the habit belief for its kind.

        The gap since the previous observed task of the same kind
        is the interval; hour and weekday are counted only when
        the caller observed them. A habit is verified only after
        enough observations AND a pattern that actually repeats:
        the same hour, the same weekday, or a stable interval.
        Tasks at a different hour than a verified peak hour are
        held as candidates; only three confirming observations
        promote a new version, with the old version kept in
        history.
        """
        belief = self._habit_beliefs.setdefault(task_kind, _HabitBelief(task_kind=task_kind))
        peak = belief.peak_hour
        contradicts = (
            belief.status == "verified" and peak is not None and hour is not None and hour != peak
        )
        if contradicts:
            if belief.contradiction_hour == hour:
                belief.consecutive_contradictions += 1
            else:
                belief.contradiction_hour = hour
                belief.consecutive_contradictions = 1
            belief.candidates.append(
                {
                    "hour": hour,
                    "weekday": weekday,
                    "day": day,
                }
            )
            self.belief_contradictions.append(
                {
                    "kind": "habit",
                    "task_kind": task_kind,
                    "observed_hour": hour,
                    "believed_peak_hour": peak,
                    "day": day,
                    "consecutive": belief.consecutive_contradictions,
                }
            )
            if belief.consecutive_contradictions >= HABIT_CONTRADICTION_CONFIRMATIONS:
                self._snapshot_habit(belief, reason="contradiction", day=day)
                belief.version += 1
                kept = list(belief.candidates)
                belief.observations = len(kept)
                belief.hours = {}
                belief.weekdays = {}
                belief.intervals = []
                previous_day = None
                for item in kept:
                    if item.get("hour") is not None:
                        key = str(item["hour"])
                        belief.hours[key] = belief.hours.get(key, 0) + 1
                    if item.get("weekday") is not None:
                        key = str(item["weekday"])
                        belief.weekdays[key] = belief.weekdays.get(key, 0) + 1
                    if previous_day is not None and item["day"] > previous_day:
                        belief.intervals.append(item["day"] - previous_day)
                    previous_day = item["day"]
                belief.candidates = []
                belief.consecutive_contradictions = 0
                belief.contradiction_hour = None
                belief.last_day = day
                belief.status = "verified" if belief.has_repeated_pattern else "trial"
                belief.confidence = (
                    KNOWN_CONFIDENCE if belief.status == "verified" else DEFAULT_CONFIDENCE
                )
            return
        belief.consecutive_contradictions = 0
        belief.contradiction_hour = None
        if interval_days is not None:
            # An explicit interval (from a habit_pattern observation)
            # is the gap; do not also derive one from the day count.
            belief.intervals.append(float(interval_days))
            belief.intervals = belief.intervals[-5:]
        elif belief.last_day is not None and day > belief.last_day:
            belief.intervals.append(day - belief.last_day)
            belief.intervals = belief.intervals[-5:]
        if hour is not None:
            key = str(hour)
            belief.hours[key] = belief.hours.get(key, 0) + 1
        if weekday is not None:
            key = str(weekday)
            belief.weekdays[key] = belief.weekdays.get(key, 0) + 1
        belief.observations += 1
        belief.last_day = day
        if belief.observations >= HABIT_MIN_OBSERVATIONS and belief.has_repeated_pattern:
            belief.status = "verified"
        belief.confidence = min(0.9, DEFAULT_CONFIDENCE + 0.05 * belief.observations)

    def decay_stale_beliefs(self, now_day: int | None = None) -> list[dict]:
        """Sweep verified beliefs untouched for too long back to trial.

        Mirrors ``procedural.apply_decay``: a verified belief
        untouched for ``BELIEF_STALE_AFTER_DAYS`` returns to trial
        and must earn verification again. The old version is kept
        in history and the evidence (durations, run counts,
        reply counts, observed hours) is kept; only trust
        (status, confidence, observation count) is reset. Trial
        beliefs are untouched. Returns what decayed, as
        ``{"kind", "task_kind" | "contact_id", "version", "day"}``.
        There is no demotion step here: revision 7 section 3.3
        forbids it, and decay is the procedural pattern, not a
        demotion.
        """
        day = self.day if now_day is None else int(now_day)
        decayed = []
        for kind, beliefs in (
            ("schedule", self._schedule_beliefs),
            ("capability", self._capability_beliefs),
            ("contact", self._contact_beliefs),
            ("habit", self._habit_beliefs),
        ):
            for key, belief in beliefs.items():
                if belief.status != "verified" or belief.last_day is None:
                    continue
                if day - belief.last_day < BELIEF_STALE_AFTER_DAYS:
                    continue
                # kind always matches its source dict, so the
                # isinstance here only narrows the type for mypy;
                # it does not change behavior.
                if kind == "schedule" and isinstance(belief, _ScheduleBelief):
                    self._snapshot_schedule(belief, reason="decay", day=day)
                elif kind == "capability" and isinstance(belief, _CapabilityBelief):
                    self._snapshot_capability(belief, reason="decay", day=day)
                elif kind == "contact" and isinstance(belief, _ContactBelief):
                    self._snapshot_contact(belief, reason="decay", day=day)
                elif isinstance(belief, _HabitBelief):
                    self._snapshot_habit(belief, reason="decay", day=day)
                belief.version += 1
                belief.status = "trial"
                belief.confidence = DEFAULT_CONFIDENCE
                belief.observations = 0
                entry = {"kind": kind, "version": belief.version, "day": day}
                if kind == "contact":
                    entry["contact_id"] = key
                else:
                    entry["task_kind"] = key
                decayed.append(entry)
        return decayed

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
