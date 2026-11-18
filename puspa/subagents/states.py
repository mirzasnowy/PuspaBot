"""Stable states of a public subagent lifecycle (G25).

The state set is fixed by the lifecycle contract (see
``docs/subagent-lifecycle.md``): a child moves PENDING -> STARTING ->
RUNNING -> one terminal state. ``CANCEL_REQUESTED`` is the transient
state between a cancel request and the child reaching a safe
iteration boundary; it is never terminal, and no operation may claim
a child is done while it is in that state.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "TERMINAL_STATES",
    "SubagentState",
    "is_terminal",
]


class SubagentState(StrEnum):
    """One state in the public subagent lifecycle."""

    PENDING = "PENDING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


#: States after which a child never runs again. ``wait`` and
#: ``result`` only report completion from these states.
TERMINAL_STATES = frozenset(
    {
        SubagentState.SUCCEEDED,
        SubagentState.FAILED,
        SubagentState.INTERRUPTED,
        SubagentState.CANCELLED,
    }
)


def is_terminal(state: SubagentState) -> bool:
    """True when the state is terminal (the child will not run again)."""
    return state in TERMINAL_STATES
