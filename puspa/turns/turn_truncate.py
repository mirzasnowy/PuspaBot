"""Turn phase: truncation (G21).

Cut the oldest turns out of a history that overflowed, without
ever touching what must survive:

- system messages at the start are kept whole;
- the last ``keep_last_messages`` messages are kept whole
  (the protected tail — the current exchange);
- only the middle is dropped, oldest first, until both caps
  (message count and char count) hold.

A truncated history is still alternation-valid: dropping a
middle run can leave two same-role messages adjacent (for
example the user message before the cut and the assistant
message after it are fine, but two user messages can meet when
the assistant turn between them was cut). The loop therefore
runs the alternation enforcer again after truncation; this
module only reports how much it cut.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import TurnConfig
from .messages import InternalMessage

__all__ = ["TruncationResult", "truncate_history"]


@dataclass
class TruncationResult:
    """The truncated history plus what was cut."""

    messages: list[InternalMessage]
    dropped_count: int
    dropped_chars: int


def truncate_history(
    messages: list[InternalMessage], *, config: TurnConfig | None = None
) -> TruncationResult:
    """Drop the oldest middle turns until the caps hold.

    Returns the new history (a new list; the input is untouched)
    and the cut size. A history already within the caps is
    returned unchanged with zero drops. Truncation never drops
    below the system block plus the protected tail, even when
    that tail alone still exceeds the char cap — cutting the
    tail would destroy the current exchange, so the overflow
    then stays visible to the caller instead.
    """
    config = config or TurnConfig()
    total_chars = sum(message.char_count() for message in messages)
    if len(messages) <= config.max_history_messages and total_chars <= config.max_history_chars:
        return TruncationResult(messages=list(messages), dropped_count=0, dropped_chars=0)

    system_block: list[InternalMessage] = []
    rest: list[InternalMessage] = []
    for message in messages:
        if not rest and message.role == "system":
            system_block.append(message)
        else:
            rest.append(message)
    tail = rest[-config.keep_last_messages :] if rest else []
    middle = rest[: len(rest) - len(tail)] if rest else []

    dropped_chars = 0
    while middle and (
        len(system_block) + len(middle) + len(tail) > config.max_history_messages
        or sum(m.char_count() for m in system_block + middle + tail) > config.max_history_chars
    ):
        dropped = middle.pop(0)
        dropped_chars += dropped.char_count()
    kept = system_block + middle + tail
    return TruncationResult(
        messages=kept,
        dropped_count=len(messages) - len(kept),
        dropped_chars=dropped_chars,
    )
