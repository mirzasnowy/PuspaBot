"""Turn phase: overflow (G21).

Overflow is the decision, truncation is the act. This module
only decides: given the history and the config caps, is the
history fine (``"ok"``), should the oldest turns be cut
(``"truncate"``), or should the middle be compressed first
(``"compress"``)? The loop applies the decision — truncation
via ``puspa.turns.turn_truncate``, compression via the injected
compressor callable (an auxiliary model call on its own
fallback chain; when no compressor is wired, the decision
falls back to ``"truncate"`` and says so).

The order is fixed: flush nothing here (memory flushing lives
in the context-compression module), check the caps, and never
split the protected tail — the last ``keep_last_messages``
messages are never the thing that gets cut.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import TurnConfig
from .messages import InternalMessage

__all__ = ["OverflowDecision", "check_overflow"]


@dataclass
class OverflowDecision:
    """What the loop should do about the history size."""

    action: str  # "ok" | "truncate" | "compress"
    reason: str
    messages_over: int = 0
    chars_over: int = 0
    compressor_available: bool = False


def _history_chars(messages: list[InternalMessage]) -> int:
    return sum(message.char_count() for message in messages)


def check_overflow(
    messages: list[InternalMessage],
    *,
    config: TurnConfig | None = None,
    compressor=None,
) -> OverflowDecision:
    """Decide whether the history overflows the config caps.

    ``"ok"`` when both caps hold. Otherwise ``"compress"`` when
    a compressor is available (the middle is worth summarising
    rather than dropping) and ``"truncate"`` when it is not.
    The decision names the overshoot in both dimensions, so the
    loop's record says why the history shrank.
    """
    config = config or TurnConfig()
    messages_over = max(0, len(messages) - config.max_history_messages)
    chars = _history_chars(messages)
    chars_over = max(0, chars - config.max_history_chars)
    if messages_over == 0 and chars_over == 0:
        return OverflowDecision(action="ok", reason="history is within the caps")
    if compressor is not None:
        return OverflowDecision(
            action="compress",
            reason=(
                f"history overflows the caps "
                f"({messages_over} messages, {chars_over} chars over); "
                "compressing the middle instead of dropping it"
            ),
            messages_over=messages_over,
            chars_over=chars_over,
            compressor_available=True,
        )
    return OverflowDecision(
        action="truncate",
        reason=(
            f"history overflows the caps "
            f"({messages_over} messages, {chars_over} chars over); "
            "no compressor is wired, truncating the oldest turns"
        ),
        messages_over=messages_over,
        chars_over=chars_over,
        compressor_available=False,
    )
