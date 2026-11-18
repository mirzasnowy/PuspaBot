"""Iteration budgets: the cost guard for one turn (G21).

Separate from the Rupiah budget (``puspa.runtime.budget``):
this counts MODEL CALLS per turn. A turn that keeps calling
the model — a tool loop that never converges, a model that
keeps asking for one more tool — stops at the budget with a
clear message instead of looping silently or crashing.

- :class:`IterationBudget`: the parent agent's counter
  (default 500, from config).
- :class:`SubagentIterationBudget`: a subagent's own counter
  (default 50, from config) with a separate cap, narrowed by
  the parent's remaining budget — the same narrowing rule the
  Rupiah budget already uses (``BudgetTracker.subagent_budget``).
  Every subagent iteration also consumes one parent iteration,
  so the parent's total always accounts for its children.

Exceeding the budget raises :class:`TurnBudgetExceeded`, whose
message states the limit and how many iterations were used.
The loop catches it and ends the turn with status
``"budget_exceeded"`` — a clean stop, never a crash.
"""

from __future__ import annotations

__all__ = [
    "IterationBudget",
    "SubagentIterationBudget",
    "TurnBudgetExceeded",
]


class TurnBudgetExceeded(Exception):
    """The iteration budget ran out: the turn stops cleanly."""

    def __init__(self, *, used: int, limit: int, scope: str = "agent") -> None:
        self.used = used
        self.limit = limit
        self.scope = scope
        super().__init__(
            f"turn stopped: {scope} iteration budget of {limit} exhausted "
            f"after {used} iterations (per-turn limit from config); "
            "no further model calls were made"
        )


class IterationBudget:
    """Per-agent iteration counter for one turn."""

    def __init__(self, max_iterations: int = 500) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        self.max_iterations = max_iterations
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.max_iterations - self.used

    @property
    def exhausted(self) -> bool:
        return self.used >= self.max_iterations

    def consume(self, amount: int = 1) -> int:
        """Spend iterations; raises :class:`TurnBudgetExceeded`.

        Returns the new ``used`` count. The check happens before
        any spend, so ``used`` never passes the limit.
        """
        if amount < 1:
            raise ValueError("amount must be at least 1")
        if self.used + amount > self.max_iterations:
            raise TurnBudgetExceeded(used=self.used, limit=self.max_iterations)
        self.used += amount
        return self.used

    def record(self) -> dict:
        """Usage so far, for the turn result and the ledger."""
        return {
            "used": self.used,
            "limit": self.max_iterations,
            "remaining": self.remaining,
        }


class SubagentIterationBudget(IterationBudget):
    """A subagent's own iteration budget, narrowed by its parent.

    ``cap`` is the smaller of the subagent limit (default 50)
    and the parent's remaining iterations at creation time.
    Consuming here also consumes one parent iteration, so the
    parent's total accounts for every child call. The subagent
    stops at whichever limit hits first, and the exception says
    which scope ran out.
    """

    def __init__(self, parent: IterationBudget, max_iterations: int = 50) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        cap = min(max_iterations, parent.remaining)
        super().__init__(max_iterations=cap)
        self.parent = parent
        self.subagent_limit = max_iterations

    def consume(self, amount: int = 1) -> int:
        if amount < 1:
            raise ValueError("amount must be at least 1")
        if self.used + amount > self.max_iterations:
            raise TurnBudgetExceeded(used=self.used, limit=self.max_iterations, scope="subagent")
        # The parent pays too: a child call is still a call.
        self.parent.consume(amount)
        self.used += amount
        return self.used

    def record(self) -> dict:
        record = super().record()
        record["subagent_limit"] = self.subagent_limit
        record["parent_used"] = self.parent.used
        record["parent_limit"] = self.parent.max_iterations
        return record
