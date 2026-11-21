"""Three-speed counterfactual simulator (Kosmos K7).

A rehearsal answers one question — "what happens if I do these
actions?" — at three speeds, slowest and strongest last:

- ``fast`` — the world model's immediate forecast: one
  ``WorldModel.predict`` per action on a copy of the model state,
  the same rehearsal the planner already did. Cheap, always
  available for a known action type. This is the model's
  intuition: the belief snapshot as it stands.
- ``medium`` — the dynamics learned from traces: the forecast is
  re-derived from the raw observation history behind the belief
  (the price belief's daily-move history, the schedule belief's
  observed durations, the capability belief's success counts)
  instead of trusting the adopted snapshot. A price trend, for
  example, shows up in medium after two moves in one direction,
  while the fast snapshot waits for three consistent moves
  before it adopts the trend. When the history is too thin to
  re-derive from, medium has no dynamics and the chain falls
  back to fast.
- ``deep`` — a dry-run in a real (simulated) world: each action
  is executed in the supplied world (``SimulatedWorld`` or
  ``NoisySimulatedWorld``) and the outcome is observed, not
  predicted. For an action the world does not know, a
  ``tool_runner`` may dry-run the real tool instead — that is
  where the sandbox-backed tool dry-run plugs in. Deep needs a
  world (or a tool runner); without one it falls back.

The speed is resolved per step: each step tries the requested
speed and falls back down the chain (deep -> medium -> fast)
when the speed cannot answer, with every abandonment recorded.
The result's ``speed_used`` is the weakest speed any step
needed — a rehearsal is only as strong as its weakest step.
Every result is recorded through the learning loop as a
non-teaching experience (the same seam the world model uses
for mispredictions), best effort: a loop that fails never
breaks a simulation, and the result is always kept in the
simulator's own ``simulations`` list.

The live model is never touched: fast and medium work on a
copy of the model state, and deep works on the supplied world —
which the rehearsal DOES consume (a dry-run pay marks the bill
paid in that world), so the caller passes a fresh world per
rehearsal. ``WorldModel`` itself is untouched by this module:
its file-size baseline (1395 lines, may only shrink) forbids a
new method there, so the wiring is this module plus the
planner, which routes its step forecasts through it
(``plan(..., speed=...)``).

No database, no network, no new dependency. All state is in
memory.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from puspa.world.beliefs import _price_key
from puspa.world.model import WorldModel

#: The three speeds, weakest first. A rehearsal's strength is its
#: weakest step, so ``speed_used`` is the minimum over the steps.
SPEEDS = ("fast", "medium", "deep")
_SPEED_STRENGTH = {"fast": 0, "medium": 1, "deep": 2}
_FALLBACKS = {"deep": ("medium", "fast"), "medium": ("fast",), "fast": ()}
#: Medium needs at least this many observed daily price moves
#: before the mean move counts as learned dynamics.
MIN_DELTA_HISTORY = 2
#: Medium needs at least this many observed durations before the
#: mean duration counts as learned dynamics.
MIN_DURATIONS = 2
#: Medium needs at least this many task outcomes before the
#: success rate counts as learned dynamics.
MIN_CAPABILITY_OBSERVATIONS = 1


class SpeedUnavailable(Exception):
    """One speed cannot answer this action; the chain falls back."""


@dataclass
class SimulationResult:
    """One rehearsal at one requested speed.

    ``speed`` is what was asked for, ``speed_used`` is the
    weakest speed any step needed, ``fallbacks`` lists every
    speed that was tried and abandoned, in order. Each step
    holds its action, outcome, confidence, the speed that
    produced it, and (for medium) what trace history the
    forecast was re-derived from. ``recorded`` says whether the
    learning loop accepted the record.
    """

    speed: str
    speed_used: str
    fallbacks: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    predicted_outcome: dict = field(default_factory=dict)
    confidence: float = 0.0
    recorded: bool = False


def _chain(speed: str) -> tuple[str, ...]:
    if speed not in SPEEDS:
        raise ValueError(f"unknown speed: {speed!r} (expected one of {SPEEDS})")
    return (speed,) + _FALLBACKS[speed]


def _medium_confidence(history_length: int) -> float:
    """Confidence from how much trace history the dynamics rest on."""
    return min(0.9, 0.5 + 0.1 * history_length)


class ThreeSpeedSimulator:
    """Rehearse action sequences against a world model at three speeds."""

    def __init__(self, model: WorldModel, world=None, tool_runner=None) -> None:
        self.model = model
        self.world = world
        self.tool_runner = tool_runner
        #: Every result, in order — the record that survives even
        #: when the learning loop is absent or refuses a record.
        self.simulations: list[SimulationResult] = []

    # -- one action -----------------------------------------------------
    def simulate_action(
        self, action: dict, state: dict, speed: str = "fast"
    ) -> tuple[dict, float, str, list[str]]:
        """Forecast one action at ``speed``, falling back down the chain.

        Returns ``(outcome, confidence, speed_used, fallbacks)``.
        An unknown action type raises ValueError, exactly as
        ``WorldModel.predict`` does — the last resort failing is a
        real error, not a fallback.
        """
        fallbacks: list[str] = []
        for candidate in _chain(speed):
            try:
                outcome, confidence = self._forecast_one(action, state, candidate)
                return outcome, confidence, candidate, fallbacks
            except SpeedUnavailable:
                fallbacks.append(candidate)
        raise ValueError(f"unknown action type: {action.get('type')!r}")

    def _forecast_one(self, action: dict, state: dict, speed: str) -> tuple[dict, float]:
        if speed == "fast":
            prediction = self.model.predict(action, state=state)
            return prediction.outcome, prediction.confidence
        if speed == "medium":
            return self._forecast_medium(action, state)
        return self._forecast_deep(action, state)

    def _forecast_medium(self, action: dict, state: dict) -> tuple[dict, float]:
        """Re-derive the forecast from the trace history behind the belief."""
        action_type = action.get("type")
        if action_type == "check_price":
            belief = state["price_beliefs"].get(
                _price_key(str(action["product_id"]), str(action["store"]))
            )
            history = list(getattr(belief, "delta_history", []) or [])
            if belief is None or belief.last_price is None or len(history) < MIN_DELTA_HISTORY:
                raise SpeedUnavailable("no price-move history to learn dynamics from")
            days = max(0, state["day"] - (belief.last_day or state["day"]))
            mean_delta = sum(history) / len(history)
            return {
                "price": belief.last_price + mean_delta * days,
                "known": True,
                "dynamics": f"price_delta_history:{len(history)} observations",
            }, _medium_confidence(len(history))
        if action_type == "estimate_task":
            task_kind = str(action.get("task_kind") or action.get("task") or "unknown")
            belief = state["schedule_beliefs"].get(task_kind)
            durations = list(getattr(belief, "durations", []) or [])
            if belief is None or len(durations) < MIN_DURATIONS:
                raise SpeedUnavailable("no observed durations to learn dynamics from")
            rate = belief.on_time_rate
            return {
                "task_kind": task_kind,
                "expected_days": sum(durations) / len(durations),
                "deadline_reliable": rate is not None and rate >= 0.8,
                "observations": belief.observations,
                "dynamics": f"schedule_durations:{len(durations)} observations",
            }, _medium_confidence(len(durations))
        if action_type == "attempt_task":
            task_kind = str(action.get("task_kind") or action.get("task") or "unknown")
            belief = state["capability_beliefs"].get(task_kind)
            if belief is None or belief.observations < MIN_CAPABILITY_OBSERVATIONS:
                raise SpeedUnavailable("no task outcomes to learn dynamics from")
            rate = belief.success_rate
            if rate is None:
                raise SpeedUnavailable("no task outcomes to learn dynamics from")
            return {
                "task_kind": task_kind,
                "success": rate >= 0.5,
                "success_rate": rate,
                "observations": belief.observations,
                "dynamics": f"capability_outcomes:{belief.observations} observations",
            }, _medium_confidence(belief.observations)
        raise SpeedUnavailable(f"no learned dynamics for action type {action_type!r}")

    def _forecast_deep(self, action: dict, state: dict) -> tuple[dict, float]:
        """Execute the action in the rehearsal world and observe the outcome."""
        if self.world is not None:
            try:
                outcome = self.world.act(action)
            except ValueError:
                outcome = None
            except Exception as exc:
                raise SpeedUnavailable("the rehearsal world failed") from exc
            if outcome is not None:
                return self._normalize_deep_outcome(action, outcome), 1.0
        if self.tool_runner is not None:
            try:
                outcome = self.tool_runner(action)
            except Exception as exc:
                raise SpeedUnavailable("the tool dry-run failed") from exc
            if not isinstance(outcome, dict):
                raise SpeedUnavailable("the tool dry-run returned no outcome")
            outcome = dict(outcome)
            outcome.setdefault("dry_run", True)
            return outcome, 1.0
        raise SpeedUnavailable("deep needs a rehearsal world or a tool runner")

    @staticmethod
    def _normalize_deep_outcome(action: dict, outcome: dict) -> dict:
        """Shape a world observation like a model forecast, so callers
        (the planner) can read it the same way."""
        outcome = dict(outcome)
        if action.get("type") == "check_price" and outcome.get("price") is not None:
            outcome.setdefault("known", True)
        return outcome

    # -- a whole sequence -------------------------------------------------
    def simulate(self, actions: list[dict], speed: str = "fast") -> SimulationResult:
        """Rehearse ``actions`` in order at ``speed``.

        The live model is never touched: fast and medium work on
        a copy of the model state (advanced with
        ``apply_predicted`` after each step), deep works on the
        supplied world. The result is recorded through the
        learning loop and kept in ``self.simulations``.
        """
        _chain(speed)  # raises ValueError on an unknown speed
        state = copy.deepcopy(self.model.state)
        steps: list[dict] = []
        fallbacks: list[str] = []
        for action in actions:
            outcome, confidence, used, step_fallbacks = self.simulate_action(action, state, speed)
            for fallen in step_fallbacks:
                if fallen not in fallbacks:
                    fallbacks.append(fallen)
            steps.append(
                {
                    "action": dict(action),
                    "outcome": dict(outcome),
                    "confidence": confidence,
                    "speed": used,
                    "dynamics": outcome.get("dynamics"),
                }
            )
            state = self.model.apply_predicted(state, action, outcome)
        weakest = min(
            (_SPEED_STRENGTH[step["speed"]] for step in steps),
            default=_SPEED_STRENGTH[speed],
        )
        speed_used = next(name for name, strength in _SPEED_STRENGTH.items() if strength == weakest)
        result = SimulationResult(
            speed=speed,
            speed_used=speed_used,
            fallbacks=fallbacks,
            steps=steps,
            predicted_outcome=dict(steps[-1]["outcome"]) if steps else {},
            confidence=steps[-1]["confidence"] if steps else 0.0,
        )
        result.recorded = self._record(result)
        self.simulations.append(result)
        return result

    def _record(self, result: SimulationResult) -> bool:
        """Record one rehearsal as a non-teaching experience.

        Goes to this model's session loop when one is set, else
        to the module-level default loop through a lazy import.
        Returns True only when the loop accepted the record.
        Never raises.
        """
        entry = {
            "task": "simulation",
            "kind": "simulation",
            "speed": result.speed,
            "speed_used": result.speed_used,
            "fallbacks": list(result.fallbacks),
            "actions": [dict(step["action"]) for step in result.steps],
            "predicted_outcome": dict(result.predicted_outcome),
            "confidence": result.confidence,
        }
        try:
            loop = self.model.learning_loop
            if loop is not None:
                loop.record_experience(entry)
            else:
                from puspa.memory.learning_loop import get_learning_loop

                get_learning_loop().record_experience(entry)
        except Exception:
            return False
        return True


def simulate(
    model: WorldModel,
    actions: list[dict],
    speed: str = "fast",
    world=None,
    tool_runner=None,
) -> SimulationResult:
    """Rehearse ``actions`` against ``model`` at ``speed`` (one-shot).

    Builds a throwaway :class:`ThreeSpeedSimulator`; keep the
    simulator when the record of many rehearsals matters.
    """
    return ThreeSpeedSimulator(model, world, tool_runner).simulate(actions, speed)
