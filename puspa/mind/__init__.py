"""Metacognition (Kosmos K20): calibrated confidence per area.

See :mod:`puspa.mind.metacognition` for the rules: Brier score
per area, calibrated confidence from each area's measured bias,
and escalation only when uncertainty is high AND stakes are
high. The depth governor (:mod:`puspa.governor.governor`) reads
the calibrated numbers through
:meth:`Metacognition.to_step_signal
<puspa.mind.metacognition.Metacognition.to_step_signal>`.
"""

from __future__ import annotations

from .metacognition import (
    CALIBRATION_PRIOR_WEIGHT,
    STAKES_HIGH,
    UNCERTAINTY_HIGH,
    AreaCalibration,
    DecisionRecord,
    EscalationDecision,
    Metacognition,
)

__all__ = [
    "CALIBRATION_PRIOR_WEIGHT",
    "STAKES_HIGH",
    "UNCERTAINTY_HIGH",
    "AreaCalibration",
    "DecisionRecord",
    "EscalationDecision",
    "Metacognition",
]
