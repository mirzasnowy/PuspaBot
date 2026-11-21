"""Self-repair for Puspa (Kosmos v2, Phase 3): the Self part.

The Mind plans and the Body acts; the Self watches what happened,
repairs what failed, and proves the repair. This package is the
Self: the repair loop that turns a detected failure into a
diagnosis, at most two automatic repair attempts, and either a
verified fix with a regression case or a plain escalation.
"""

from __future__ import annotations

from puspa.self.initiative import (
    DIGEST_INTERRUPTION_COST,
    LOOP_EXPIRING,
    LOOP_FAILED_REPAIR,
    LOOP_PENDING_COMMITMENT,
    LOOP_STALE_FACT,
    LOOP_UNANSWERED_MESSAGE,
    OPEN_LOOP_KINDS,
    SILENT_MAX_STAKES,
    SILENT_MIN_CONFIDENCE,
    ActionResult,
    DailyDigest,
    InitiativeEngine,
    InitiativeReport,
    OpenLoop,
    StandingGoal,
    SubGoal,
    TickResult,
    derive_subgoals,
    open_loops_from_snapshot,
    run_daily_digest_job,
    standing_goals_from_snapshot,
)
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
    "DIGEST_INTERRUPTION_COST",
    "LOOP_EXPIRING",
    "LOOP_FAILED_REPAIR",
    "LOOP_PENDING_COMMITMENT",
    "LOOP_STALE_FACT",
    "LOOP_UNANSWERED_MESSAGE",
    "MAX_ATTEMPTS",
    "OPEN_LOOP_KINDS",
    "SILENT_MAX_STAKES",
    "SILENT_MIN_CONFIDENCE",
    "ActionResult",
    "DailyDigest",
    "Escalation",
    "FailureSignal",
    "InitiativeEngine",
    "InitiativeReport",
    "OpenLoop",
    "RegressionCase",
    "RegressionSuite",
    "RepairAttempt",
    "RepairLoop",
    "RepairOutcome",
    "RepairResult",
    "StandingGoal",
    "SubGoal",
    "TickResult",
    "classify",
    "derive_subgoals",
    "open_loops_from_snapshot",
    "run_daily_digest_job",
    "standing_goals_from_snapshot",
]
