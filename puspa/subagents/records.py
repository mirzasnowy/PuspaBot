"""Internal per-child record for the subagent lifecycle (G25).

A record is the lifecycle's own bookkeeping for one child: identity,
launch input, current state, steered text queue, cost, and the
terminal result. Records live only in memory, in this process; a
process restart drops them, which is exactly why ``reconnect`` after
a restart reports ``RECONNECT_UNAVAILABLE`` instead of inventing a
replacement child.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .results import SubagentResult
from .states import SubagentState

__all__ = ["ChildRecord"]


@dataclass
class ChildRecord:
    """Bookkeeping for one child, from launch to terminal state."""

    subagent_id: str
    agent_id: str
    task_id: str
    goal: str
    context: str
    metadata: dict
    toolset: str
    parent_toolset: str
    exposed_tools: frozenset[str] = frozenset()
    parent_exposed_tools: frozenset[str] = frozenset()
    state: SubagentState = SubagentState.PENDING
    result: SubagentResult | None = None
    error: str = ""
    steer_queue: list[str] = field(default_factory=list)
    cost_rp: int = 0
    cancel_requested: bool = False
    pool_subagent_id: str = ""
    finished: bool = False
