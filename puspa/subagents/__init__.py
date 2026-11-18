"""Public subagent lifecycle (G25).

The public lifecycle API wraps the harness spawn path with a
serializable capability handle, stable states, cooperative cancel
and steer, and an immutable, idempotent result. See
``docs/subagent-lifecycle.md`` for the contract.
"""

from __future__ import annotations

from .errors import (
    LifecycleError,
    LimitExceededError,
    NotTerminalError,
    ReconnectUnavailableError,
    ToolsetRejectedError,
    UnknownHandleError,
    UnsupportedOptionError,
)
from .handles import HANDLE_VERSION, SubagentHandle, mint_handle
from .host import HostSession, build_child_token_payload, build_parent_token_payload
from .lifecycle import SubagentLifecycle
from .limits import (
    MAX_CONTEXT_CHARS,
    MAX_GOAL_CHARS,
    MAX_METADATA_CHARS,
    MAX_METADATA_KEYS,
    MAX_RESULT_CHARS,
    MAX_STEER_CHARS,
    MAX_STEER_ITEMS,
)
from .results import SubagentResult, build_result, result_hash
from .runner import ChildCancelled, ChildContext, Runner, ThreadRunner
from .states import TERMINAL_STATES, SubagentState, is_terminal

__all__ = [
    "HANDLE_VERSION",
    "MAX_CONTEXT_CHARS",
    "MAX_GOAL_CHARS",
    "MAX_METADATA_CHARS",
    "MAX_METADATA_KEYS",
    "MAX_RESULT_CHARS",
    "MAX_STEER_CHARS",
    "MAX_STEER_ITEMS",
    "TERMINAL_STATES",
    "ChildCancelled",
    "ChildContext",
    "HostSession",
    "LifecycleError",
    "LimitExceededError",
    "NotTerminalError",
    "ReconnectUnavailableError",
    "Runner",
    "SubagentHandle",
    "SubagentLifecycle",
    "SubagentResult",
    "SubagentState",
    "ThreadRunner",
    "ToolsetRejectedError",
    "UnknownHandleError",
    "UnsupportedOptionError",
    "build_child_token_payload",
    "build_parent_token_payload",
    "build_result",
    "is_terminal",
    "mint_handle",
    "result_hash",
]
