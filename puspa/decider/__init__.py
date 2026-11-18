"""The decider: cheap decisions under the planner and world model.

A decider answers one small question — route this message,
triage this inbox item — with the cheapest model that can, and
never with the big model the planner uses. It sits *below*
``puspa.world.planner`` and ``puspa.world.model``: it does not
plan and does not simulate; it decides.

Contract: three question shapes (choice / score / yes-no) with
same-shaped answers, pluggable backends with a fallback chain,
1.500 ms default timeout, a key-check before every call, and
failure always as a ``{"ok": False, "reason": ...}`` value —
never an exception into the turn. Jobs are listed explicitly in
``puspa.decider.jobs.DECIDER_JOBS``; each job owns its seam.

Wiring status (G9): no clean call site exists in the tree
today — module routing today is workflow triggers in
``puspa.modules.manifest``, and the narrowing path lives in
``puspa.marketplace.narrowing`` (another worker's area). The
exact follow-up call sites are documented in the G9 report:
narrowing should ask the decider a score question over skill
ids, and a future message-to-module dispatcher should use the
``moduleRouting`` job.
"""

from __future__ import annotations

from .backends import (
    BackendError,
    LLMBackend,
    RuleBackend,
    UserEngineBackend,
    parse_reply,
)
from .decider import DEFAULT_TIMEOUT_MS, Decider, Decision, default_backends
from .jobs import DECIDER_JOBS, JOB_SEAMS, SeamError, get_seam
from .keycheck import check_backend
from .log import DecisionLog, state_hash
from .questions import (
    ChoiceAnswer,
    ChoiceOption,
    ChoiceQuestion,
    ScoreAnswer,
    ScoreQuestion,
    YesNoAnswer,
    YesNoQuestion,
    build_prompt,
)

__all__ = [
    "BackendError",
    "ChoiceAnswer",
    "ChoiceOption",
    "ChoiceQuestion",
    "DECIDER_JOBS",
    "DEFAULT_TIMEOUT_MS",
    "Decider",
    "Decision",
    "DecisionLog",
    "JOB_SEAMS",
    "LLMBackend",
    "RuleBackend",
    "ScoreAnswer",
    "ScoreQuestion",
    "SeamError",
    "UserEngineBackend",
    "YesNoAnswer",
    "YesNoQuestion",
    "build_prompt",
    "check_backend",
    "default_backends",
    "get_seam",
    "parse_reply",
    "state_hash",
]
