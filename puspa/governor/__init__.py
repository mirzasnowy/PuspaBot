"""The depth governor: how much thinking each step gets.

Picks reflex, deliberate, or deep per step
(:class:`DepthGovernor`), escalates a failed verification one
level up exactly once, and compiles a repeatedly succeeding deep
task into a reflex skill that must pass its regression case
first. See :mod:`puspa.governor.governor` for the rules and
:mod:`puspa.governor.skills` for the compiled-skill shape and
stores.
"""

from __future__ import annotations

from .governor import (
    COMPILE_AFTER_SUCCESSES,
    DEEP_MIN_STAKES_IRREVERSIBLE,
    DEEP_RISK,
    REFLEX_MAX_STAKES,
    REFLEX_MAX_UNCERTAINTY,
    REFLEX_SKILL_MATCH,
    Depth,
    DepthDecision,
    DepthGovernor,
    StepSignal,
)
from .skills import (
    COMPILED_SERVICE_PREFIX,
    CompiledSkill,
    InMemorySkillStore,
    ProceduralSkillStore,
    RegressionCase,
    RegressionResult,
    SkillStore,
)

__all__ = [
    "COMPILED_SERVICE_PREFIX",
    "COMPILE_AFTER_SUCCESSES",
    "DEEP_MIN_STAKES_IRREVERSIBLE",
    "DEEP_RISK",
    "REFLEX_MAX_STAKES",
    "REFLEX_MAX_UNCERTAINTY",
    "REFLEX_SKILL_MATCH",
    "CompiledSkill",
    "Depth",
    "DepthDecision",
    "DepthGovernor",
    "InMemorySkillStore",
    "ProceduralSkillStore",
    "RegressionCase",
    "RegressionResult",
    "SkillStore",
    "StepSignal",
]
