"""Metacognition (Kosmos K20): calibrated confidence per area.

See :mod:`puspa.mind.metacognition` for the rules: Brier score
per area, calibrated confidence from each area's measured bias,
and escalation only when uncertainty is high AND stakes are
high. The depth governor (:mod:`puspa.governor.governor`) reads
the calibrated numbers through
:meth:`Metacognition.to_step_signal
<puspa.mind.metacognition.Metacognition.to_step_signal>`.

See :mod:`puspa.mind.felt_state` for the K21 felt state: the
viability variables, appraisal into the felt-state vector
F(t), and the honest report read from F(t) and its logged
causes.

See :mod:`puspa.mind.workspace` for the K18 global workspace
bus: modules post scored candidates, the top score broadcasts
subject to hard constraints, and the broadcast is recorded.
"""

from __future__ import annotations

from .felt_state import (
    FORBIDDEN_VARIABLES,
    VARIABLES,
    AppraisalRecord,
    FeltReport,
    FeltState,
    FeltVector,
)
from .metacognition import (
    CALIBRATION_PRIOR_WEIGHT,
    STAKES_HIGH,
    UNCERTAINTY_HIGH,
    AreaCalibration,
    DecisionRecord,
    EscalationDecision,
    Metacognition,
)
from .workspace import (
    ASK_USER_ACTION,
    BLOCKED_ACTION,
    CONFIDENCE_WEIGHT,
    RELEVANCE_WEIGHT,
    URGENCY_WEIGHT,
    Broadcast,
    Candidate,
    WorkspaceBus,
    ledger_recorder,
)

__all__ = [
    "ASK_USER_ACTION",
    "BLOCKED_ACTION",
    "CALIBRATION_PRIOR_WEIGHT",
    "CONFIDENCE_WEIGHT",
    "FORBIDDEN_VARIABLES",
    "RELEVANCE_WEIGHT",
    "STAKES_HIGH",
    "UNCERTAINTY_HIGH",
    "URGENCY_WEIGHT",
    "VARIABLES",
    "AppraisalRecord",
    "AreaCalibration",
    "Broadcast",
    "Candidate",
    "DecisionRecord",
    "EscalationDecision",
    "FeltReport",
    "FeltState",
    "FeltVector",
    "Metacognition",
    "WorkspaceBus",
    "ledger_recorder",
]
