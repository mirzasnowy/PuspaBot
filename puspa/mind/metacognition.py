"""Metacognition (Kosmos K20): calibrated confidence per decision area.

The brain watches its own thinking: every decision stores the
confidence it was made with, and once the outcome is known the
decision is scored against reality. Calibration is the Brier
score, computed PER AREA (an area is one kind of decision, such
as a price check, a bill, or a schedule — never one global
number, because being well calibrated on prices says nothing
about bills).

- ``record_decision(area, confidence, outcome)`` stores one
  scored decision. The confidence stored with it is the
  calibrated one: the raw confidence corrected by the area's
  measured bias so far (its mean forecast minus its observed
  accuracy), with the correction weighted by how much history
  the area has. An area with no history yet trusts the raw
  number — there is nothing to correct it with.
- ``brier_score(area)`` is the mean squared error between the
  forecast probability and the outcome (1 when the decision was
  right, 0 when it was wrong), over that area's decisions only.
  0 is perfect, 1 is perfectly wrong.
- ``observe_prediction(area, confidence, surprise)`` is the K19
  seam: a prediction whose surprise is above the threshold
  counts as a wrong decision in its area, a hit counts as a
  right one. Surprise is the calibration signal — predictions
  that keep missing in one area drag that area's confidence
  down. This module never imports the K19 module: surprise is
  a plain number here, so the seam cannot break on an import.
- ``assess(area, confidence, stakes)`` answers the only question
  the rest of the brain asks of metacognition: should this
  decision be escalated to the user? The rule from the design
  doc is an AND: escalate only when uncertainty is high AND
  stakes are high. High uncertainty alone (act carefully and
  log the doubt) does not escalate, and high stakes alone (the
  agent is sure, so it acts) does not escalate either. Asking
  costs the user attention, so it is spent only when the
  expected loss from a wrong act is larger.
- ``to_step_signal(area, confidence, stakes, ...)`` is the seam
  into the depth governor (:mod:`puspa.governor.governor`): it
  builds a :class:`StepSignal <puspa.governor.governor.StepSignal>`
  whose uncertainty is 1 minus the calibrated confidence. The
  governor owns the deep/reflex thresholds and the retry
  escalation; K20 only supplies the measured number, it never
  re-decides depth and never writes a second escalate path.
  The governor is imported lazily inside that method, so this
  module imports standalone.

What K20 does NOT do: it never changes the autonomy dial
itself — the dial (:mod:`puspa.autonomy.dial`) reads these
per-area numbers through ``calibration(area)`` / ``summary()``
and moves on its own rules; it never touches the live turn
loop (no wiring here: the loop cannot branch on calibrated
confidence until the router maps it, the same split the
governor documents); and it never persists anything. All state
is in memory, like the K6 ignorance map and the K11 dial: a
restart resets every area to "no history", which can only make
the agent more cautious, never bolder. Persisting per-area
calibration (a Postgres table + migration, the K3/K4/K5
pattern) is the follow-up when it needs to survive restarts.

Pure Python: no database, no network, no model call, and no
blind-except handler anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

__all__ = [
    "CALIBRATION_PRIOR_WEIGHT",
    "STAKES_HIGH",
    "UNCERTAINTY_HIGH",
    "AreaCalibration",
    "DecisionRecord",
    "EscalationDecision",
    "Metacognition",
]

#: Uncertainty at or above this counts as high (0 = certain,
#: 1 = guessing). Initial value to tune from traces.
UNCERTAINTY_HIGH = 0.5
#: Stakes at or above this count as high (0 = nothing lost,
#: 1 = irreversible harm). Initial value to tune from traces.
STAKES_HIGH = 0.5
#: How much history it takes before the area's measured bias
#: is mostly trusted: the correction weight is n / (n + this),
#: so 10 decisions trust the bias at 50% and 90 at 90%.
CALIBRATION_PRIOR_WEIGHT = 10


def _check_unit(name: str, value: float) -> float:
    """Validate a 0..1 value; ValueError with the name and value otherwise."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number within 0..1, got {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be within 0..1, got {value}")
    return float(value)


def _check_area(area: str) -> str:
    """Validate an area name; ValueError when blank or not a string."""
    if not isinstance(area, str) or not area.strip():
        raise ValueError(f"area must be a non-empty string, got {area!r}")
    return area.strip()


@dataclass(frozen=True)
class DecisionRecord:
    """One decision, scored once its outcome is known.

    ``confidence`` is the raw forecast probability the decision
    was made with (the chance the agent gave its chosen outcome
    being right). ``calibrated_confidence`` is that number
    corrected by the area's measured bias at record time — the
    confidence the decision is stored with. ``outcome`` is True
    when the decision turned out right, False when it was
    wrong. ``stakes`` is what the decision could cost if wrong.
    """

    area: str
    confidence: float
    calibrated_confidence: float
    outcome: bool
    stakes: float = 0.5
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        """JSON-safe dict of this record (ledger/audit shape)."""
        return {
            "area": self.area,
            "confidence": self.confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "outcome": self.outcome,
            "stakes": self.stakes,
            "recorded_at": self.recorded_at.isoformat(),
        }


@dataclass(frozen=True)
class AreaCalibration:
    """How calibrated one area is, from its scored decisions.

    ``brier`` is the Brier score: the mean of (forecast -
    outcome)^2 over the area, 0 perfect, 1 perfectly wrong.
    ``mean_forecast`` is the area's average confidence and
    ``accuracy`` the fraction of its decisions that were right;
    their difference (``bias``) is the systematic over- or
    under-confidence: positive means the area claims more than
    it delivers. ``decisions`` is how many scored decisions the
    numbers rest on.
    """

    area: str
    decisions: int
    brier: float
    mean_forecast: float
    accuracy: float

    @property
    def bias(self) -> float:
        """Mean forecast minus accuracy: positive is overconfident."""
        return self.mean_forecast - self.accuracy

    def to_dict(self) -> dict:
        """JSON-safe dict of this calibration (weekly-report shape)."""
        return {
            "area": self.area,
            "decisions": self.decisions,
            "brier": self.brier,
            "mean_forecast": self.mean_forecast,
            "accuracy": self.accuracy,
            "bias": self.bias,
        }


@dataclass(frozen=True)
class EscalationDecision:
    """Whether one decision goes to the user, and why.

    ``escalate`` is True only when uncertainty is high AND
    stakes are high. ``reason`` names the rule outcome in plain
    words so the ledger can quote it.
    """

    area: str
    calibrated_confidence: float
    uncertainty: float
    stakes: float
    escalate: bool
    reason: str

    def to_dict(self) -> dict:
        """JSON-safe dict of this decision (ledger/audit shape)."""
        return {
            "area": self.area,
            "calibrated_confidence": self.calibrated_confidence,
            "uncertainty": self.uncertainty,
            "stakes": self.stakes,
            "escalate": self.escalate,
            "reason": self.reason,
        }


class Metacognition:
    """Calibrated confidence per area, and the escalate-or-act call.

    ``uncertainty_high`` / ``stakes_high`` are the bars the AND
    rule reads (initial values to tune from traces, per the
    design doc). ``prior_weight`` is how many decisions an area
    needs before its measured bias is mostly trusted.
    """

    def __init__(
        self,
        *,
        uncertainty_high: float = UNCERTAINTY_HIGH,
        stakes_high: float = STAKES_HIGH,
        prior_weight: int = CALIBRATION_PRIOR_WEIGHT,
    ) -> None:
        self._uncertainty_high = _check_unit("uncertainty_high", uncertainty_high)
        self._stakes_high = _check_unit("stakes_high", stakes_high)
        if not isinstance(prior_weight, int) or isinstance(prior_weight, bool):
            raise ValueError(f"prior_weight must be a positive integer, got {prior_weight!r}")
        if prior_weight < 1:
            raise ValueError(f"prior_weight must be at least 1, got {prior_weight}")
        self._prior_weight = prior_weight
        self._records: dict[str, list[DecisionRecord]] = {}

    def __len__(self) -> int:
        """How many scored decisions are stored, across all areas."""
        return sum(len(records) for records in self._records.values())

    def areas(self) -> list[str]:
        """Areas with at least one scored decision, in first-seen order."""
        return list(self._records)

    def decisions(self, area: str | None = None) -> list[DecisionRecord]:
        """Scored decisions, oldest first; all areas when none is given."""
        if area is None:
            ordered: list[DecisionRecord] = []
            for records in self._records.values():
                ordered.extend(records)
            return sorted(ordered, key=lambda record: record.recorded_at)
        return list(self._records.get(_check_area(area), []))

    # -- recording ---------------------------------------------------

    def record_decision(
        self,
        area: str,
        confidence: float,
        outcome: bool,
        *,
        stakes: float = 0.5,
        now: datetime | None = None,
    ) -> DecisionRecord:
        """Store one scored decision, with its calibrated confidence.

        The calibrated confidence is computed from the area's
        history BEFORE this decision is added: a decision cannot
        calibrate itself with its own outcome. ``outcome`` must
        be a bool (True = the decision was right); ``stakes``
        is what the decision could cost if wrong. Returns the
        stored record.
        """
        area = _check_area(area)
        confidence = _check_unit("confidence", confidence)
        stakes = _check_unit("stakes", stakes)
        if not isinstance(outcome, bool):
            raise ValueError(f"outcome must be a bool, got {outcome!r}")
        calibrated = self.calibrated_confidence(area, confidence)
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        record = DecisionRecord(
            area=area,
            confidence=confidence,
            calibrated_confidence=calibrated,
            outcome=outcome,
            stakes=stakes,
            recorded_at=moment,
        )
        self._records.setdefault(area, []).append(record)
        return record

    def observe_prediction(
        self,
        area: str,
        confidence: float,
        surprise: float,
        *,
        stakes: float = 0.5,
        surprise_threshold: float = 0.0,
        now: datetime | None = None,
    ) -> DecisionRecord:
        """Score one K19 prediction into its area's calibration.

        ``surprise`` is the prediction error (0 = exact hit, 1 =
        total miss); a prediction counts as right when its
        surprise is within ``surprise_threshold`` (default 0.0:
        any difference is a miss, the same default the K19
        module uses). This is how prediction misses drag an
        area's confidence down: the outcome is recorded exactly
        like a scored decision, through :meth:`record_decision`.
        """
        surprise = _check_unit("surprise", surprise)
        surprise_threshold = _check_unit("surprise_threshold", surprise_threshold)
        return self.record_decision(
            area,
            confidence,
            surprise <= surprise_threshold,
            stakes=stakes,
            now=now,
        )

    # -- calibration -------------------------------------------------

    def calibration(self, area: str) -> AreaCalibration | None:
        """This area's calibration, or None when it has no decisions."""
        records = self._records.get(_check_area(area))
        if not records:
            return None
        count = len(records)
        brier = (
            sum((record.confidence - (1.0 if record.outcome else 0.0)) ** 2 for record in records)
            / count
        )
        mean_forecast = sum(record.confidence for record in records) / count
        accuracy = sum(1.0 if record.outcome else 0.0 for record in records) / count
        return AreaCalibration(
            area=area,
            decisions=count,
            brier=brier,
            mean_forecast=mean_forecast,
            accuracy=accuracy,
        )

    def brier_score(self, area: str) -> float | None:
        """This area's Brier score, or None when it has no decisions."""
        found = self.calibration(area)
        return None if found is None else found.brier

    def calibrated_confidence(self, area: str, confidence: float) -> float:
        """The raw confidence corrected by this area's measured bias.

        With no history the raw number stands — there is nothing
        to correct it with. Otherwise the area's bias (mean
        forecast minus accuracy) is subtracted, weighted by
        n / (n + prior_weight), so a few decisions barely move
        the number and a long history mostly trusts the bias.
        The result is clamped to 0..1. Two areas with different
        accuracy therefore give different calibrated numbers for
        the same raw confidence.
        """
        confidence = _check_unit("confidence", confidence)
        found = self.calibration(_check_area(area))
        if found is None:
            return confidence
        weight = found.decisions / (found.decisions + self._prior_weight)
        adjusted = confidence - weight * found.bias
        return min(1.0, max(0.0, adjusted))

    def uncertainty(self, area: str, confidence: float) -> float:
        """How much this decision is a guess: 1 minus calibrated confidence."""
        return 1.0 - self.calibrated_confidence(area, confidence)

    def summary(self) -> dict[str, dict]:
        """Per-area calibration for every area with decisions.

        One entry per area (area name -> calibration dict, the
        weekly-report shape from the design doc). Deliberately
        no global score: a single number across areas would hide
        exactly the differences this module exists to measure.
        """
        return {
            area: self.calibration(area).to_dict()  # type: ignore[union-attr]
            for area in sorted(self._records)
        }

    # -- escalate or act ----------------------------------------------

    def should_escalate(self, *, uncertainty: float, stakes: float) -> bool:
        """True only when uncertainty is high AND stakes are high.

        Either condition alone is not enough: high uncertainty
        with low stakes means act carefully and log the doubt;
        low uncertainty with high stakes means the agent is sure
        enough to act. The bars are the constructor thresholds.
        """
        uncertainty = _check_unit("uncertainty", uncertainty)
        stakes = _check_unit("stakes", stakes)
        return uncertainty >= self._uncertainty_high and stakes >= self._stakes_high

    def assess(self, area: str, confidence: float, stakes: float) -> EscalationDecision:
        """Calibrate one decision and say whether it escalates.

        Calibrates the raw confidence against the area's history,
        derives the uncertainty from it, and applies the AND rule
        through :meth:`should_escalate`. The reason names which
        side of each bar the decision fell on, so the ledger can
        quote why the user was (or was not) asked.
        """
        area = _check_area(area)
        calibrated = self.calibrated_confidence(area, confidence)
        uncertainty = 1.0 - calibrated
        stakes = _check_unit("stakes", stakes)
        escalate = self.should_escalate(uncertainty=uncertainty, stakes=stakes)
        uncertainty_word = "high" if uncertainty >= self._uncertainty_high else "low"
        stakes_word = "high" if stakes >= self._stakes_high else "low"
        if escalate:
            reason = (
                f"uncertainty {uncertainty:.2f} is {uncertainty_word} and stakes "
                f"{stakes:.2f} are {stakes_word}: escalate to the user"
            )
        else:
            reason = (
                f"uncertainty {uncertainty:.2f} is {uncertainty_word} but stakes "
                f"{stakes:.2f} are {stakes_word}: act and log, do not escalate"
            )
        return EscalationDecision(
            area=area,
            calibrated_confidence=calibrated,
            uncertainty=uncertainty,
            stakes=stakes,
            escalate=escalate,
            reason=reason,
        )

    def to_step_signal(self, area: str, confidence: float, stakes: float, **kwargs):
        """Build the governor's step signal from calibrated confidence.

        The signal's uncertainty is 1 minus the area's calibrated
        confidence, so the governor's deep/reflex bars read a
        measured number instead of a caller-supplied guess. Any
        extra keyword (``reversible``, ``has_evidence``,
        ``skill_match``, ``skill_id``, ``previous_failure``) is
        passed straight to the signal. The governor is imported
        lazily so this module stands alone; the governor owns
        depth and retry escalation, this method only hands it
        the number.
        """
        from puspa.governor.governor import StepSignal

        return StepSignal(
            uncertainty=self.uncertainty(area, confidence),
            stakes=_check_unit("stakes", stakes),
            **kwargs,
        )
