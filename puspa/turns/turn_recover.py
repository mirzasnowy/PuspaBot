"""Turn phase: recovery (G21).

Recovery is the rule that makes interruption safe: the history
is only ever extended with a COMPLETE reply. The loop takes a
checkpoint (a deep copy) before every model call; when the call
is interrupted, times out, or fails on every tier, the loop
restores the checkpoint — the history is exactly what it was
before the call, with no partial message in it.

This module holds the checkpoint mechanics and the
human-readable recovery notes. It never invents a reply: a
recovered turn ends with a clear status (``"interrupted"``,
``"failed"``) and a message saying what happened, not a
fabricated answer.
"""

from __future__ import annotations

import copy

from .messages import InternalMessage

__all__ = [
    "interruption_note",
    "recovery_note",
    "restore_checkpoint",
    "take_checkpoint",
    "timeout_note",
]


def take_checkpoint(messages: list[InternalMessage]) -> list[InternalMessage]:
    """Deep-copy a history: the point recovery returns to."""
    return copy.deepcopy(messages)


def restore_checkpoint(checkpoint: list[InternalMessage]) -> list[InternalMessage]:
    """Return a fresh copy of a checkpoint (the caller owns it)."""
    return copy.deepcopy(checkpoint)


def recovery_note(reason: str, *, discarded_tool_calls: int = 0) -> str:
    """Human-readable note for a recovered turn."""
    note = f"turn recovered: {reason}; the history was restored to before the model call"
    if discarded_tool_calls:
        note += f"; {discarded_tool_calls} in-flight tool call(s) were discarded, none ran"
    return note + "."


def interruption_note() -> str:
    """The note for a user interrupt: nothing partial survived."""
    return (
        "turn interrupted by the user; the in-flight model reply was discarded "
        "and no partial response entered the history."
    )


def timeout_note(timeout_seconds: float) -> str:
    """The note for a model call that ran past its timeout."""
    return (
        f"model call timed out after {timeout_seconds:g}s; the history was "
        "restored to before the call and no partial response entered it."
    )
