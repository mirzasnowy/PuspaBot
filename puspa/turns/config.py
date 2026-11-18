"""Turn-loop configuration (G21).

Every limit the loop enforces comes from here, never from a
hardcoded constant in a phase module. Defaults follow the
hermes-agent pattern the comparison report documents (iteration
budget 500 per agent, 50 per subagent); the Rupiah budget stays
where it already lives (``puspa.runtime.budget.BudgetTracker``,
Rp2.000 per task) and is NOT duplicated here — the iteration
budget is a second, independent guard.
"""

from __future__ import annotations

from dataclasses import dataclass

from puspa.router.chains import FallbackChainConfig

__all__ = ["DEFAULT_CALL_TIMEOUT_SECONDS", "TurnConfig"]

#: Default per-agent iteration budget (hermes-agent default).
DEFAULT_MAX_ITERATIONS = 500

#: Default per-subagent iteration budget (hermes-agent default).
DEFAULT_SUBAGENT_MAX_ITERATIONS = 50

#: Default timeout for one model call (all fallback tiers together).
DEFAULT_CALL_TIMEOUT_SECONDS = 120.0

#: Default history caps: 100 messages / ~120k chars, keep the last 10.
DEFAULT_MAX_HISTORY_MESSAGES = 100
DEFAULT_MAX_HISTORY_CHARS = 120_000
DEFAULT_KEEP_LAST_MESSAGES = 10

#: Default tool-narrowing thresholds (G17): they mirror
#: ``puspa.tools.narrowing`` (``DEFAULT_MAX_DIRECT_TOOLS`` etc.)
#: as plain numbers so this config module never imports the tools
#: package. The loop builds a ``NarrowingConfig`` from them.
DEFAULT_MAX_DIRECT_TOOLS = 50
DEFAULT_MAX_TOOLS = 128
DEFAULT_MAX_OFFERED_TOOLS = 10
DEFAULT_SEARCH_RESULT_LIMIT = 10


@dataclass
class TurnConfig:
    """Everything one turn loop needs to know up front.

    ``role`` is the router role for the main chain (an English
    alias such as ``"reader"`` or a catalog name such as
    ``"pembaca"``; aliases are resolved in
    ``puspa.router.chains``). ``fallback_config`` is the configured
    chains (main + auxiliary); when None, the catalog defaults
    from ``puspa.router.chains.default_fallback_chain_config``
    are used for the role. ``temperature`` / ``max_tokens`` are
    passed to the model call.
    """

    max_iterations: int = DEFAULT_MAX_ITERATIONS
    subagent_max_iterations: int = DEFAULT_SUBAGENT_MAX_ITERATIONS
    call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS
    max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES
    max_history_chars: int = DEFAULT_MAX_HISTORY_CHARS
    keep_last_messages: int = DEFAULT_KEEP_LAST_MESSAGES
    role: str = "reader"
    temperature: float = 0.0
    max_tokens: int = 1024
    fallback_config: FallbackChainConfig | None = None
    #: Tool-narrowing thresholds (G17, OpenBot starting points):
    #: past ``max_direct_tools`` the rest wait behind
    #: ``search_tools``; past ``max_tools`` the run is refused;
    #: a successful narrowing offers at most ``max_offered_tools``
    #: catalog tools directly; ``search_result_limit`` caps one
    #: ``search_tools`` call's results.
    max_direct_tools: int = DEFAULT_MAX_DIRECT_TOOLS
    max_tools: int = DEFAULT_MAX_TOOLS
    max_offered_tools: int = DEFAULT_MAX_OFFERED_TOOLS
    search_result_limit: int = DEFAULT_SEARCH_RESULT_LIMIT

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if self.subagent_max_iterations < 1:
            raise ValueError("subagent_max_iterations must be at least 1")
        if self.call_timeout_seconds <= 0:
            raise ValueError("call_timeout_seconds must be positive")
        if self.max_history_messages < 1:
            raise ValueError("max_history_messages must be at least 1")
        if self.max_history_chars < 1:
            raise ValueError("max_history_chars must be at least 1")
        if self.keep_last_messages < 1:
            raise ValueError("keep_last_messages must be at least 1")
        if self.keep_last_messages >= self.max_history_messages:
            raise ValueError("keep_last_messages must be smaller than max_history_messages")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        if self.max_direct_tools < 1:
            raise ValueError("max_direct_tools must be at least 1")
        if self.max_tools <= self.max_direct_tools:
            raise ValueError(
                f"max_tools ({self.max_tools}) must be larger than "
                f"max_direct_tools ({self.max_direct_tools})"
            )
        if self.max_offered_tools < 1:
            raise ValueError("max_offered_tools must be at least 1")
        if self.max_offered_tools > self.max_direct_tools:
            raise ValueError(
                f"max_offered_tools ({self.max_offered_tools}) must not exceed "
                f"max_direct_tools ({self.max_direct_tools})"
            )
        if self.search_result_limit < 1:
            raise ValueError("search_result_limit must be at least 1")

    def effective_fallback_config(self) -> FallbackChainConfig:
        """The fallback chains for this config (defaults resolved)."""
        if self.fallback_config is not None:
            return self.fallback_config
        from puspa.router.chains import default_fallback_chain_config

        return default_fallback_chain_config(self.role)
