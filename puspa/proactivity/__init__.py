"""Proactivity (Kosmos H): the interruption budget (K10).

Public surface:

- ``budget`` — the daily interruption budget: every proactive
  initiative pays from it, an empty budget means silence.
"""

from .budget import (
    ALLOW,
    DEFAULT_DAILY_BUDGET,
    DENIAL_REASONS,
    DENY_BUDGET_EMPTY,
    DENY_INSUFFICIENT_BUDGET,
    DENY_LOW_VALUE_WHEN_SCARCE,
    DENY_VALUE_BELOW_THRESHOLD,
    HIGH_VALUE_THRESHOLD,
    INITIATIVE_KINDS,
    LOW_REMAINING_THRESHOLD,
    Decision,
    Initiative,
    InitiativeKind,
    InterruptionBudget,
    SpendRecord,
    initiative_from,
)

__all__ = [
    "ALLOW",
    "DEFAULT_DAILY_BUDGET",
    "DENIAL_REASONS",
    "DENY_BUDGET_EMPTY",
    "DENY_INSUFFICIENT_BUDGET",
    "DENY_LOW_VALUE_WHEN_SCARCE",
    "DENY_VALUE_BELOW_THRESHOLD",
    "HIGH_VALUE_THRESHOLD",
    "INITIATIVE_KINDS",
    "LOW_REMAINING_THRESHOLD",
    "Decision",
    "Initiative",
    "InitiativeKind",
    "InterruptionBudget",
    "SpendRecord",
    "initiative_from",
]
