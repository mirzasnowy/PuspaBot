"""Router package: model choice, fallback, quota, live completion.

``complete`` is the first live caller of the router: it picks the
cheapest model for a role and calls it over an OpenRouter-compatible
chat-completions endpoint. See ``puspa.router.complete``.

Note on names: the completion function itself is called
``complete`` and lives in the ``puspa.router.complete`` module, so
this package deliberately does NOT re-export it under that bare
name: doing so would shadow the submodule attribute
(``puspa.router.complete`` would become the function instead of
the module). Import the function from its module::

    from puspa.router.complete import complete

Everything else is exported here.
"""

from .chains import (
    AUXILIARY_KINDS,
    DEFAULT_AUXILIARY_ROLES,
    FallbackChainConfig,
    ProviderRef,
    chain_entries_for,
    default_fallback_chain_config,
)
from .complete import (
    CompletionError,
    CompletionResult,
    complete_detailed,
    complete_messages,
    default_llm_complete,
    make_call_fn,
    resolve_api_key,
    resolve_llm_complete,
    resolve_role,
)

__all__ = [
    "AUXILIARY_KINDS",
    "DEFAULT_AUXILIARY_ROLES",
    "CompletionError",
    "CompletionResult",
    "FallbackChainConfig",
    "ProviderRef",
    "chain_entries_for",
    "complete_detailed",
    "complete_messages",
    "default_fallback_chain_config",
    "default_llm_complete",
    "make_call_fn",
    "resolve_api_key",
    "resolve_llm_complete",
    "resolve_role",
]
