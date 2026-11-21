"""Prediction and surprise (Kosmos K19): close the predict-compare-learn loop.

K7 simulates before acting and K8 watches outcomes after they are
marked done, but nothing between them recorded the prediction,
scored it against reality, and fed the error back into the model.
This module is that missing link, the predictive-processing step
from the v2 design (``docs/kosmos-brain-v2.md``, Phase 3): predict
each action's outcome, compare with reality, learn from the error.

One prediction, one comparison:

- ``record_prediction(task, action, predicted, confidence, ...)``
  records a forecast made BEFORE the action is taken (the K7
  simulator's forecast, or any caller's). The prediction is kept
  pending until reality arrives; recording never mutates the
  model, so a forecast cannot teach itself.
- ``observe(prediction_id, observed)`` compares the recorded
  forecast with what actually happened and computes the
  **surprise** (prediction error): 0 for an exact hit, up to 1
  for a total miss. Numeric fields score by relative difference,
  so a price of 100000 against an observed 120000 is a small
  surprise (0.17), while a wrong category (``"paid"`` against
  ``"unpaid"``) is a full surprise (1.0).
- On a miss (surprise above the threshold, default any miss at
  all) the model updates three ways, and all three are visible
  to the caller: the stored prior for that action becomes the
  observed outcome with its confidence lowered, so the next
  ``predict_for(action)`` returns the corrected value instead of
  the old forecast; the error is fed to the learning loop
  through ``record_misprediction`` (the same seam the world
  model and the outcome watcher use), so the loop's correction
  table learns it too; and the update is recorded as a ledger
  event of type ``"prediction_surprise"`` through the injected
  writer, so the correction is durable and auditable. On a hit
  the confidence rises slightly and nothing is written: a
  correct prediction needs no repair and no ledger noise.

Seams (so the learner never imports a broken chain):

- The ledger writer is injected: ``record_outcome`` is a
  callable with the ``write_outcome`` keyword shape
  (``task_id``, ``agent_id``, ``outcome_type``, ``user_id``,
  ``summary``, ``evidence``, ``details``). With no writer the
  model still updates and the loop is still fed, but no ledger
  record is written — the same split the outcome watcher makes.
  This module never imports ``puspa.evidence.outcomes``
  directly, so its tests need no database.
- The learning loop defaults to the module-level
  ``record_misprediction`` (in-memory, same seam the world
  model uses); a test may inject a fresh ``LearningLoop`` or a
  fake instead. The loop's guardrail runs for real on every
  path, so a prediction carrying an orphan number is
  quarantined by the loop and reported as such instead of
  teaching silently.

Failure handling: this module has no blind-except handler and
adds none. The prior-table update happens first, so it can
never be lost to a later failure; a writer or loop failure
propagates to the caller, which decides whether to retry —
the update itself is already stored and the result is already
in history. Validation failures (bad confidence, unknown or
already-scored prediction id) raise ``ValueError`` /
``KeyError`` before anything is recorded.

All state is in memory. No database, no network.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _default_record_misprediction(record, **kwargs):
    """Default learning-loop feed, imported lazily at call time.

    A top-level import here would pull ``puspa.memory.learning_loop``
    into ``sys.modules`` on any ``puspa.world`` import, breaking the
    lazy seam ``test_world_model_imports_without_learning_loop``
    guards (the world model must import without the loop).
    """
    from puspa.memory.learning_loop import record_misprediction

    return record_misprediction(record, **kwargs)


__all__ = [
    "PredictionRecord",
    "PredictionSurprise",
    "SurpriseResult",
    "surprise_of",
]

#: Ledger outcome type written for a scored prediction error.
PREDICTION_SURPRISE_OUTCOME_TYPE = "prediction_surprise"

#: Confidence floor after repeated misses: a prior never drops
#: to zero, so one more observation can still revive it.
MIN_CONFIDENCE = 0.05
#: Confidence ceiling after repeated hits.
MAX_CONFIDENCE = 0.95
#: Confidence gained by one correct prediction.
CONFIDENCE_GAIN_ON_HIT = 0.05

#: Action fields that identify "the same action" for the prior
#: table (the same keys ``WorldModel._same_action`` compares).
_ACTION_KEY_FIELDS = ("type", "product_id", "store", "bill_id", "task_kind", "contact_id")


def _as_outcome(value) -> dict:
    """Normalise a predicted/observed value to a dict of fields.

    Dicts are copied as-is; a scalar (a price, a status string)
    becomes the single ``"value"`` field, so scalar and dict
    predictions score through the same comparison.
    """
    if isinstance(value, dict):
        return dict(value)
    return {"value": value}


def _field_error(predicted, observed) -> float:
    """Prediction error for one field: 0 for a hit, up to 1 for a miss.

    Booleans score exact (a wrong boolean is a full miss).
    Numbers score by relative difference against the larger
    magnitude, so a small drift is a small surprise and a
    doubling is a full one. Anything else scores exact: equal
    is a hit, different (including a missing value) is a miss.
    """
    if isinstance(predicted, bool) or isinstance(observed, bool):
        return 0.0 if predicted is observed or predicted == observed else 1.0
    if isinstance(predicted, (int, float)) and isinstance(observed, (int, float)):
        scale = max(abs(float(predicted)), abs(float(observed)), 1.0)
        return min(1.0, abs(float(predicted) - float(observed)) / scale)
    if predicted is None or observed is None:
        return 0.0 if predicted is observed else 1.0
    return 0.0 if predicted == observed else 1.0


def surprise_of(predicted, observed) -> float:
    """Surprise (prediction error) between a forecast and reality.

    The mean field error over the union of the outcome's fields:
    0.0 is an exact hit, 1.0 is wrong on every field. Two empty
    outcomes agree (0.0); an outcome against an empty one is a
    total miss (1.0).
    """
    predicted_fields = _as_outcome(predicted)
    observed_fields = _as_outcome(observed)
    keys = set(predicted_fields) | set(observed_fields)
    if not keys:
        return 0.0
    total = sum(_field_error(predicted_fields.get(key), observed_fields.get(key)) for key in keys)
    return total / len(keys)


def _action_key(action: dict) -> str:
    """Stable key for "the same action" in the prior table."""
    parts = [str(action.get(name)) for name in _ACTION_KEY_FIELDS if action.get(name) is not None]
    return "|".join(parts)


@dataclass
class PredictionRecord:
    """One forecast, recorded before the action and scored after."""

    prediction_id: str
    task: str
    action: dict
    predicted: dict
    confidence: float
    user_id: str
    task_id: str
    agent_id: str = "puspa"
    predicted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: "pending" until ``observe`` scores it, then "scored".
    status: str = "pending"
    #: What actually happened, once observed.
    observed: dict | None = None
    #: Surprise of the scored comparison, once observed.
    surprise: float | None = None


@dataclass
class SurpriseResult:
    """The outcome of comparing one prediction with reality."""

    record: PredictionRecord
    #: Surprise (prediction error), 0.0 for an exact hit.
    surprise: float
    #: True when the surprise is within the threshold.
    correct: bool
    #: Confidence of the stored prior after this update.
    updated_confidence: float
    #: The corrected prior: the observed outcome, stored so the
    #: next prediction for this action returns it.
    corrected_prediction: dict
    #: The ledger record, when a surprise was written.
    ledger_record: object = None
    #: The learning-loop result, when the loop was fed.
    misprediction: dict | None = None


class PredictionSurprise:
    """Record predictions, score them against reality, learn.

    ``record_outcome`` is the injected ledger writer (the
    ``write_outcome`` keyword shape); with none, surprises still
    update the model and feed the loop, but no ledger record is
    written. ``learning_loop`` is an object with
    ``record_misprediction(record, ...)``; it defaults to the
    module-level loop function. ``surprise_threshold`` is the
    largest surprise that still counts as correct (default 0.0:
    any difference is a miss).
    """

    def __init__(
        self,
        *,
        record_outcome=None,
        learning_loop=None,
        agent_id: str = "puspa",
        surprise_threshold: float = 0.0,
    ):
        self._record_outcome = record_outcome
        if learning_loop is None:
            self._record_misprediction = _default_record_misprediction
        elif hasattr(learning_loop, "record_misprediction"):
            self._record_misprediction = learning_loop.record_misprediction
        elif callable(learning_loop):
            self._record_misprediction = learning_loop
        else:
            raise ValueError("learning_loop must have record_misprediction or be callable")
        self._agent_id = agent_id
        if not 0.0 <= surprise_threshold <= 1.0:
            raise ValueError("surprise_threshold must be between 0.0 and 1.0")
        self._threshold = surprise_threshold
        self._pending: dict[str, PredictionRecord] = {}
        self._priors: dict[str, dict] = {}
        self._history: list[SurpriseResult] = []

    def __len__(self) -> int:
        """How many predictions are still pending observation."""
        return len(self._pending)

    def record_prediction(
        self,
        *,
        task: str,
        action: dict,
        predicted,
        confidence: float,
        user_id: str,
        task_id: str,
        agent_id: str | None = None,
        now: datetime | None = None,
    ) -> PredictionRecord:
        """Record one forecast made before an action is taken.

        ``predicted`` is the forecast outcome (a dict of fields,
        or a scalar). ``confidence`` is 0.0-1.0. Recording never
        mutates the model: the forecast is kept pending until
        ``observe`` scores it. Returns the stored record.
        """
        if not isinstance(task, str) or not task:
            raise ValueError("task must be a non-empty string")
        if not isinstance(action, dict) or not action:
            raise ValueError("action must be a non-empty dict")
        if not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be a non-empty string")
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        record = PredictionRecord(
            prediction_id=uuid.uuid4().hex,
            task=task,
            action=dict(action),
            predicted=_as_outcome(predicted),
            confidence=float(confidence),
            user_id=user_id,
            task_id=task_id,
            agent_id=agent_id or self._agent_id,
            predicted_at=moment,
        )
        self._pending[record.prediction_id] = record
        return record

    def predict_for(self, action: dict) -> dict | None:
        """The stored prior for an action, after learning, or None.

        After a miss, this returns the observed outcome with its
        lowered confidence, so the next forecast for the same
        action starts from reality instead of the old prediction.
        With no learned prior for the action, there is nothing to
        return: the caller must forecast from its own model, never
        from an invented prior.
        """
        if not isinstance(action, dict) or not action:
            raise ValueError("action must be a non-empty dict")
        prior = self._priors.get(_action_key(action))
        if prior is None:
            return None
        return {"outcome": dict(prior["outcome"]), "confidence": prior["confidence"]}

    def pending(self) -> list[PredictionRecord]:
        """Predictions still awaiting observation, oldest first."""
        return sorted(self._pending.values(), key=lambda item: item.predicted_at)

    def history(self) -> list[SurpriseResult]:
        """Scored predictions, in the order they were observed."""
        return list(self._history)

    def observe(
        self, prediction_id: str, observed, *, now: datetime | None = None
    ) -> SurpriseResult:
        """Compare a recorded prediction with what really happened.

        Computes the surprise, updates the stored prior for the
        action (the observed outcome becomes the prior, with
        confidence lowered on a miss and raised on a hit), feeds
        a miss to the learning loop, and writes a miss to the
        ledger when a writer is set. The prior update happens
        first, so it is never lost to a later failure. Returns
        the scored result.
        """
        record = self._pending.get(str(prediction_id))
        if record is None:
            raise KeyError(f"unknown prediction: {prediction_id}")
        observed_fields = _as_outcome(observed)
        surprise = surprise_of(record.predicted, observed_fields)
        correct = surprise <= self._threshold
        if correct:
            updated = min(MAX_CONFIDENCE, record.confidence + CONFIDENCE_GAIN_ON_HIT)
        else:
            updated = max(MIN_CONFIDENCE, record.confidence * (1.0 - surprise))
        key = _action_key(record.action)
        prior = self._priors.get(key, {"observations": 0})
        self._priors[key] = {
            "outcome": dict(observed_fields),
            "confidence": updated,
            "surprise": surprise,
            "observations": prior["observations"] + 1,
        }
        del self._pending[record.prediction_id]
        record.status = "scored"
        record.observed = dict(observed_fields)
        record.surprise = surprise
        misprediction = None
        ledger_record = None
        if not correct:
            misprediction = self._record_misprediction(
                {
                    "task": record.task,
                    "predicted": dict(record.predicted),
                    "observed": dict(observed_fields),
                    "error": surprise,
                    "context": {
                        "prediction_id": record.prediction_id,
                        "action": dict(record.action),
                        "surprise": surprise,
                        "updated_confidence": updated,
                    },
                },
                evidence_event_id=record.prediction_id,
            )
            if self._record_outcome is not None:
                moment = now or datetime.now(UTC)
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=UTC)
                summary = (
                    f"Prediction for {record.task} missed: "
                    f"predicted {record.predicted!r}, observed {observed_fields!r} "
                    f"(surprise {surprise:.2f})"
                )
                ledger_record = self._record_outcome(
                    task_id=record.task_id,
                    agent_id=record.agent_id,
                    outcome_type=PREDICTION_SURPRISE_OUTCOME_TYPE,
                    user_id=record.user_id,
                    summary=summary,
                    evidence=[
                        {
                            "kind": "prediction",
                            "prediction_id": record.prediction_id,
                            "predicted": dict(record.predicted),
                            "confidence": record.confidence,
                        },
                        {
                            "kind": "observation",
                            "prediction_id": record.prediction_id,
                            "observed": dict(observed_fields),
                            "checked_at": moment.isoformat(),
                        },
                    ],
                    details={
                        "prediction_id": record.prediction_id,
                        "predicted": dict(record.predicted),
                        "observed": dict(observed_fields),
                        "surprise": surprise,
                        "updated_confidence": updated,
                        "corrected_prediction": dict(observed_fields),
                    },
                )
        result = SurpriseResult(
            record=record,
            surprise=surprise,
            correct=correct,
            updated_confidence=updated,
            corrected_prediction=dict(observed_fields),
            ledger_record=ledger_record,
            misprediction=misprediction,
        )
        self._history.append(result)
        return result
