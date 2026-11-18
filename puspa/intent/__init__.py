"""Intent Engine package (Kosmos K1).

Intent as a distribution over three levels (``docs/kosmos-design.md``
section B): the current desire, the ongoing goal, and long-term
values. One message is read at all three levels at once.

- :mod:`puspa.intent.distribution`: the distribution itself
  (``IntentDistribution``, ``IntentLevel``, ``IntentHypothesis``).
- :mod:`puspa.intent.inference`: the default deterministic
  inferrer (keyword/topic overlap against profile goals/values
  and world beliefs — no LLM call, no cost), the pluggable
  :class:`~puspa.intent.inference.IntentInferrer` interface, and
  the LLM seam (:func:`~puspa.intent.inference.build_llm_prompt`).
- :mod:`puspa.intent.act_ask`: the act-vs-ask decision from the
  distribution — the intent SIGNAL feeding the K2 rule, not a
  second approval system.
- :mod:`puspa.intent.turn_wiring`: the turn-loop advisor
  (:class:`~puspa.intent.turn_wiring.IntentTurnAdvisor`).

See ``docs/intent-engine.md`` for the full description, the
seams, and the honest limits.
"""

from __future__ import annotations

from .act_ask import ActAskDecision, clarifying_question, decide_act_or_ask
from .distribution import (
    CLEAR_CONFIDENCE,
    CONFLICT_MARGIN,
    LEVELS,
    IntentDistribution,
    IntentHypothesis,
    IntentLevel,
)
from .inference import (
    DeterministicInferrer,
    IntentContext,
    IntentInferrer,
    build_llm_prompt,
    infer_intent,
)
from .turn_wiring import ActionSpec, IntentTurnAdvisor, ask_payload

__all__ = [
    "CLEAR_CONFIDENCE",
    "CONFLICT_MARGIN",
    "LEVELS",
    "ActAskDecision",
    "ActionSpec",
    "DeterministicInferrer",
    "IntentContext",
    "IntentDistribution",
    "IntentHypothesis",
    "IntentInferrer",
    "IntentLevel",
    "IntentTurnAdvisor",
    "ask_payload",
    "build_llm_prompt",
    "clarifying_question",
    "decide_act_or_ask",
    "infer_intent",
]
