"""The interruptible turn loop (G21).

Thin facade over the ``puspa.turns`` package: one import point
for the loop, the message format, the budgets, the config, and
the intercepted tools. Each turn phase lives in its own module
(``turn_prepare``, ``turn_api``, ``turn_api_error``,
``turn_overflow``, ``turn_truncate``, ``turn_recover``); this
package's ``__init__`` only re-exports them.

The loop that was split is Puspa's ``TaskRunner`` (see
``puspa.turns.loop``): the task lifecycle stays in
``puspa.runtime.tasks`` and this loop is the conversational
engine inside its ``run`` stage, reached from every surface
through the existing ``TaskRunnerAgent`` seam.
"""

from __future__ import annotations

from .budget import IterationBudget, SubagentIterationBudget, TurnBudgetExceeded
from .config import TurnConfig
from .intercepts import INTERCEPTED_TOOLS, InterceptedTools
from .interrupt import TurnInterrupted, TurnTimeout, run_interruptible
from .loop import (
    TURN_BUDGET_EXCEEDED,
    TURN_COMPLETED,
    TURN_FAILED,
    TURN_INTERRUPTED,
    TurnLoop,
    TurnResult,
    make_task_executor,
)
from .messages import (
    ROLES,
    InternalMessage,
    MessageConversion,
    enforce_alternation,
    from_anthropic,
    from_chat_completions,
    from_responses,
    to_anthropic,
    to_chat_completions,
    to_responses,
    validate_alternation,
)
from .todos import TodoItem, TodoStore
from .turn_api import ModelCallOutcome, ModelReply, call_model, make_openrouter_model_call
from .turn_api_error import describe_failure, is_auth_failure, wrap_with_auth_refresh
from .turn_overflow import OverflowDecision, check_overflow
from .turn_prepare import PreparedIteration, prepare_iteration
from .turn_recover import (
    interruption_note,
    recovery_note,
    restore_checkpoint,
    take_checkpoint,
    timeout_note,
)
from .turn_truncate import TruncationResult, truncate_history

__all__ = [
    "INTERCEPTED_TOOLS",
    "ROLES",
    "TURN_BUDGET_EXCEEDED",
    "TURN_COMPLETED",
    "TURN_FAILED",
    "TURN_INTERRUPTED",
    "InterceptedTools",
    "InternalMessage",
    "IterationBudget",
    "MessageConversion",
    "ModelCallOutcome",
    "ModelReply",
    "OverflowDecision",
    "PreparedIteration",
    "SubagentIterationBudget",
    "TodoItem",
    "TodoStore",
    "TruncationResult",
    "TurnBudgetExceeded",
    "TurnConfig",
    "TurnInterrupted",
    "TurnLoop",
    "TurnResult",
    "TurnTimeout",
    "call_model",
    "check_overflow",
    "describe_failure",
    "enforce_alternation",
    "from_anthropic",
    "from_chat_completions",
    "from_responses",
    "interruption_note",
    "is_auth_failure",
    "make_openrouter_model_call",
    "make_task_executor",
    "prepare_iteration",
    "recovery_note",
    "restore_checkpoint",
    "run_interruptible",
    "take_checkpoint",
    "timeout_note",
    "to_anthropic",
    "to_chat_completions",
    "to_responses",
    "truncate_history",
    "validate_alternation",
    "wrap_with_auth_refresh",
]
