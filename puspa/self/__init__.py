"""Self-repair for Puspa (Kosmos v2, Phase 3): the Self part.

The Mind plans and the Body acts; the Self watches what happened,
repairs what failed, and proves the repair. This package is the
Self: the repair loop that turns a detected failure into a
diagnosis, at most two automatic repair attempts, and either a
verified fix with a regression case or a plain escalation.
"""

from __future__ import annotations

from puspa.self.repair_loop import (
    MAX_ATTEMPTS,
    Escalation,
    FailureSignal,
    RegressionCase,
    RegressionSuite,
    RepairAttempt,
    RepairLoop,
    RepairOutcome,
    RepairResult,
    classify,
)

__all__ = [
    "MAX_ATTEMPTS",
    "Escalation",
    "FailureSignal",
    "RegressionCase",
    "RegressionSuite",
    "RepairAttempt",
    "RepairLoop",
    "RepairOutcome",
    "RepairResult",
    "classify",
]
