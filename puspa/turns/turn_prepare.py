"""Turn phase: iteration preparation (G21).

One iteration starts here, before any model call:

1. Interrupt check — a pending interrupt ends the iteration
   before a single token is spent.
2. Budget — one iteration is consumed (raises
   ``TurnBudgetExceeded`` when the budget is gone; the loop ends
   the turn with that message, never silently).
3. Alternation — the history is validated and repaired so roles
   strictly alternate (see ``puspa.turns.messages``). The model
   is never trusted to fix its own history.

The prepared history is a NEW list; the caller's history is
never mutated by preparation.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .budget import IterationBudget
from .interrupt import TurnInterrupted
from .messages import InternalMessage, enforce_alternation

__all__ = ["PreparedIteration", "prepare_iteration"]


@dataclass
class PreparedIteration:
    """The history as it will be sent, plus what preparation did."""

    messages: list[InternalMessage]
    iteration: int
    repairs: list[str]


def prepare_iteration(
    history: list[InternalMessage],
    *,
    budget: IterationBudget,
    interrupt_event: threading.Event | None = None,
) -> PreparedIteration:
    """Prepare one iteration: interrupt check, budget, alternation.

    Raises :class:`TurnInterrupted` when the event is already set
    and :class:`TurnBudgetExceeded` when the budget is gone.
    Returns the repaired history (a new list) and the repair
    report; the input history is untouched.
    """
    if interrupt_event is not None and interrupt_event.is_set():
        raise TurnInterrupted("turn interrupted before the iteration started")
    iteration = budget.consume()
    repaired = enforce_alternation(history)
    return PreparedIteration(
        messages=list(repaired.value),
        iteration=iteration,
        repairs=list(repaired.notes),
    )
