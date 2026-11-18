"""Immutable, idempotent child results (G25).

A result carries only the child's final text: no transcript, no
hidden reasoning, no intermediate steps. The text is capped at
:data:`puspa.subagents.limits.MAX_RESULT_CHARS` and the result hash
is the SHA-256 of exactly the stored text, so the same output always
produces the same hash and ``result`` can be called any number of
times with the same answer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .limits import MAX_RESULT_CHARS
from .states import SubagentState

__all__ = [
    "SubagentResult",
    "build_result",
    "result_hash",
]


def result_hash(text: str) -> str:
    """Stable hash of result text: SHA-256 of the exact stored text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SubagentResult:
    """The immutable result of one finished child.

    ``text`` is the child's final output (empty when the child
    produced none, for example on failure). ``error`` carries the
    terminal reason for non-success states and is empty on success.
    ``truncated`` is True only when the output was capped at
    ``MAX_RESULT_CHARS``. Frozen, so no caller can rewrite a result
    after it was produced.
    """

    state: SubagentState
    text: str
    result_hash: str
    truncated: bool
    error: str = ""


def build_result(
    *,
    state: SubagentState,
    text: str,
    error: str = "",
) -> SubagentResult:
    """Build a result, capping the text at ``MAX_RESULT_CHARS``.

    The cap is applied here, once, at the boundary: the stored text
    is at most ``MAX_RESULT_CHARS`` long and ``truncated`` records
    whether anything was cut. The hash covers the stored text, so it
    is stable for a given stored result.
    """
    stored = text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS]
    return SubagentResult(
        state=state,
        text=stored,
        result_hash=result_hash(stored),
        truncated=len(text) > MAX_RESULT_CHARS,
        error=error[:500],
    )
