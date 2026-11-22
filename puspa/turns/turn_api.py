"""Turn phase: the model call (G21).

One function, :func:`call_model`, does the whole call phase:

1. Convert the internal history to the provider wire format
   (chat completions — the router's live path).
2. Run the request through the configured fallback chain
   (``puspa.router.fallback.call_with_fallback``): tiers are
   tried in order, 429/5xx/auth failures fall through, and the
   full attempt/hop audit trail comes back.
3. Wrap each tier call with refresh-first auth handling
   (``puspa.turns.turn_api_error``).
4. Run the whole chain inside an interruptible call
   (``puspa.turns.interrupt``): an interrupt abandons the
   in-flight request and its result is discarded — no partial
   reply ever reaches the caller.

The actual HTTP call is the injected ``model_call`` seam:
``model_call(entry, wire_messages, tools) -> ModelReply``.
The default adapter (:func:`make_openrouter_model_call`) calls
``puspa.router.complete.complete_messages`` — the router's live
chat-completions caller — resolving its key per call, so a
refreshed OAuth token is picked up by the auth retry. Tests
inject a fake ``model_call``; nothing here touches the network
unless the caller supplies the live adapter.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from puspa.observability import tracing as observability_tracing
from puspa.router.chains import chain_entries_for
from puspa.router.complete import CompletionError, complete_messages
from puspa.router.fallback import (
    AuthError,
    CallResult,
    ChainEntry,
    NonRetryableError,
    ProviderError,
    ProviderFailure,
    QuotaExhaustedError,
    RateLimited,
    call_with_fallback,
    classify_failure,
)

from .config import TurnConfig
from .interrupt import run_interruptible
from .messages import InternalMessage, to_chat_completions
from .turn_api_error import wrap_with_auth_refresh

__all__ = [
    "ModelCallOutcome",
    "ModelReply",
    "call_model",
    "make_openrouter_model_call",
    "make_routed_model_call",
]


@dataclass
class ModelReply:
    """What one model call returned (already provider-neutral)."""

    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None
    provider: str = ""
    model: str = ""


@dataclass
class ModelCallOutcome:
    """A completed model call: the reply plus the audit trail."""

    reply: ModelReply
    tier_used: ChainEntry
    attempts: list = field(default_factory=list)
    hops: list = field(default_factory=list)
    total_cost_rp: int = 0
    chain_notes: list[str] = field(default_factory=list)

    def as_assistant_message(self) -> InternalMessage:
        """The reply as an internal assistant message."""
        return InternalMessage.assistant(self.reply.text, tool_calls=list(self.reply.tool_calls))


def _completion_error_to_failure(exc: CompletionError) -> ProviderFailure:
    """Map a router ``CompletionError`` onto a fallback failure."""
    kind = classify_failure(exc.status, str(exc))
    if kind == "auth_error":
        return AuthError(str(exc), status=exc.status)
    if kind == "rate_limited":
        return RateLimited(str(exc))
    if kind == "quota_exhausted":
        return QuotaExhaustedError(str(exc), status=exc.status)
    if kind == "non_retryable":
        return NonRetryableError(str(exc), status=exc.status)
    return ProviderError(str(exc), status=exc.status)


def make_openrouter_model_call(
    *,
    key_resolver=None,
    transport=None,
    client=None,
    base_url: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    timeout: float = 60.0,
):
    """Build the default ``model_call`` adapter (live, OpenRouter).

    ``model_call(entry, wire_messages, tools)`` calls
    ``complete_messages`` with the chain entry's model. The API
    key is resolved on EVERY call (``key_resolver`` or the BYOK
    path), so the auth refresh-then-retry in the call phase picks
    up a refreshed token. ``transport`` / ``client`` are the
    httpx seams (tests pass a ``MockTransport``). The adapter
    never invents a key: a missing key raises ``KeyError``,
    loudly, instead of calling with a fake one.
    """

    def model_call(entry: ChainEntry, wire_messages: list[dict], tools: list[dict] | None):
        try:
            result = complete_messages(
                wire_messages,
                model=entry.model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                base_url=base_url,
                transport=transport,
                client=client,
                key_resolver=key_resolver,
                tools=tools,
            )
        except CompletionError as exc:
            raise _completion_error_to_failure(exc) from exc
        return ModelReply(
            text=result.text,
            tool_calls=list(result.tool_calls),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            finish_reason=result.finish_reason,
            provider=result.provider,
            model=result.model,
        )

    return model_call


def make_routed_model_call(
    *,
    user_id,
    dsn=None,
    key_resolver=None,
    transport=None,
    client=None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    timeout: float = 60.0,
):
    """Build the per-capability ``model_call`` adapter (COW-08).

    The drop-in replacement for :func:`make_openrouter_model_call`
    when the turn loop knows the user: the chat capability's route
    (see :mod:`puspa.pwa.capability_routing`) decides the primary
    tier. The route is resolved on EVERY call, so a Settings
    change takes effect on the very next turn without rebuilding
    anything.

    - Primary tier (``"subscription"``): the routed chat provider
      and model, with the vault key for that provider. With no
      stored chat route the user's model selection is the route
      (hosted means this tier behaves exactly like the OpenRouter
      adapter: the chain entry's model on OpenRouter). When the
      routed provider has at least two accounts (several BYOK keys
      or a key plus a subscription login), the call goes through
      the provider pool (see :func:`_call_subscription_via_pool`):
      a quota or transport failure on one account fails over to
      the next instead of killing the turn.
    - Cheap/free tiers: exactly the OpenRouter adapter (the chain
      entry's model), so a routed provider that 429s or fails
      auth still falls through to the existing safety net instead
      of retrying the same failing endpoint.
    - A routed provider with no model is refused loudly
      (``ValueError`` naming the fix) instead of sending the
      chain's OpenRouter model id to the wrong endpoint.

    The adapter never invents a key: a missing vault key raises
    ``KeyError``, loudly, instead of calling with a fake one.
    """
    from puspa.pwa.capability_routing import resolve_chat_kwargs

    def model_call(entry: ChainEntry, wire_messages: list[dict], tools: list[dict] | None):
        if entry.tier == "subscription":
            routed = resolve_chat_kwargs(user_id, dsn=dsn)
            if routed:
                model = routed.get("model")
                if not model:
                    raise ValueError(
                        f"chat is routed to provider {routed.get('provider')!r} with no model: "
                        "pick a model in Settings first"
                    )
                pool = _pool_for_provider(routed.get("provider"), user_id, dsn=dsn)
                if pool is not None:
                    return _call_subscription_via_pool(
                        pool,
                        wire_messages=wire_messages,
                        model=model,
                        routed=routed,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=timeout,
                        transport=transport,
                        client=client,
                        user_id=user_id,
                        dsn=dsn,
                        tools=tools,
                    )
                try:
                    result = complete_messages(
                        wire_messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=timeout,
                        base_url=routed.get("base_url"),
                        api_key=routed.get("api_key"),
                        transport=transport,
                        client=client,
                        key_resolver=key_resolver,
                        tools=tools,
                        provider=routed.get("provider"),
                    )
                except CompletionError as exc:
                    raise _completion_error_to_failure(exc) from exc
                return _reply_from_result(result)
        return make_openrouter_model_call(
            key_resolver=key_resolver,
            transport=transport,
            client=client,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )(entry, wire_messages, tools)

    return model_call


def _reply_from_result(result) -> ModelReply:
    """Build a :class:`ModelReply` from a router ``CompletionResult``."""

    return ModelReply(
        text=result.text,
        tool_calls=list(result.tool_calls),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        finish_reason=result.finish_reason,
        provider=result.provider,
        model=result.model,
    )


def _pool_for_provider(provider, user_id, dsn=None):
    """The pool for a routed provider, or None when pooling adds nothing.

    Only a pool with at least two accounts routes traffic: with one
    account there is nothing to fail over to, and the direct call
    stays bit-identical. A pool-build failure also falls back to
    the direct call, so the pool can never break a completion that
    would have worked. The failure modes the build can hit here
    are a missing or corrupt vault entry (``KeyError`` /
    ``ValueError``) and an OS-level failure reading the vault
    (``OSError``); anything else propagates, as before.
    """

    if not provider:
        return None
    try:
        from puspa.router.pool import provider_pool_for_user

        pool = provider_pool_for_user(provider, user_id, dsn=dsn)
    except (KeyError, ValueError, OSError):
        return None
    if pool is None or len(pool.accounts) < 2:
        return None
    return pool


def _call_subscription_via_pool(
    pool,
    *,
    wire_messages,
    model,
    routed,
    temperature,
    max_tokens,
    timeout,
    transport,
    client,
    user_id,
    dsn,
    tools,
) -> ModelReply:
    """Run one subscription-tier call through ``pool`` (V2-40 wiring).

    Each pool attempt resolves its own account's credential at call
    time and calls :func:`complete_messages` with it, so a quota or
    transport failure on one key fails over to the next account of
    the same provider instead of killing the turn. The tiered
    fallback above still sees the outcome: a non-retryable failure
    raises immediately, and when every account fails the tier
    raises a retryable :class:`ProviderError`, so the chain falls
    through to the cheap/free safety net.

    The caller's ``key_resolver`` is deliberately NOT forwarded
    here: it would hand the same key to every account and defeat
    the failover. Per-account credentials come from the vault
    (BYOK) or the OAuth refresher, resolved at call time.
    """

    from puspa.router.pool import AllAccountsFailed, call_with_pool, resolve_credential

    def _attempt(account):
        key = resolve_credential(account, user_id=user_id, dsn=dsn)
        try:
            result = complete_messages(
                wire_messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                base_url=routed.get("base_url"),
                api_key=key,
                transport=transport,
                client=client,
                tools=tools,
                provider=routed.get("provider"),
            )
        except CompletionError as exc:
            raise _completion_error_to_failure(exc) from exc
        return CallResult(
            value=_reply_from_result(result),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    try:
        reply = call_with_pool(pool, _attempt).value
    except AllAccountsFailed as exc:
        raise ProviderError(str(exc)) from exc
    if not isinstance(reply, ModelReply):
        raise ProviderError(f"pool returned {type(reply).__name__}, not a ModelReply")
    return reply


def call_model(
    messages: list[InternalMessage],
    *,
    config: TurnConfig | None = None,
    chain: list[ChainEntry] | None = None,
    model_call=None,
    tools: list[dict] | None = None,
    interrupt_event: threading.Event | None = None,
    refresher=None,
    quota_store=None,
    budget=None,
) -> ModelCallOutcome:
    """Call the model once, through the fallback chain, interruptibly.

    ``messages`` are internal messages (already alternation-
    enforced by the prepare phase). ``chain`` overrides the
    config's main chain; ``model_call`` overrides the default
    OpenRouter adapter (tests inject a fake). ``budget`` is a
    ``puspa.runtime.budget.BudgetTracker``: the Rupiah ceiling
    is enforced by the fallback executor, never dodged.

    Raises :class:`TurnInterrupted` when the user interrupts
    (the in-flight reply is discarded), :class:`TurnTimeout`
    when the call runs past ``config.call_timeout_seconds``,
    :class:`AllTiersFailed` when every tier fails, or the
    original ``ProviderFailure`` for a deterministic failure.
    """
    config = config or TurnConfig()
    if chain is None:
        fallback_config = config.effective_fallback_config()
        chain, chain_notes = chain_entries_for(fallback_config, "main", role=config.role)
    else:
        chain_notes = []
    if not chain:
        raise ValueError("fallback chain is empty: no provider is configured for this turn")
    wire = to_chat_completions(messages)
    if wire.dropped:
        # A dropped field here means the wire format lost part of
        # the history. That is reported to the caller through the
        # outcome notes path, not hidden: the loop records it.
        chain_notes = [*chain_notes, *[f"wire conversion dropped: {drop}" for drop in wire.dropped]]
    call = model_call or make_openrouter_model_call(
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.call_timeout_seconds,
    )

    def tier_call(entry: ChainEntry) -> CallResult:
        reply = call(entry, wire.value, tools)
        if not isinstance(reply, ModelReply):
            raise ProviderError(
                f"model_call for {entry.provider}/{entry.model} returned "
                f"{type(reply).__name__}, not a ModelReply"
            )
        return CallResult(
            value=reply,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
        )

    wrapped_call = wrap_with_auth_refresh(
        tier_call, refresher, provider_of=lambda entry: entry.provider
    )

    # E-18: the chain runs on the ``turn-model-call`` thread, and
    # contextvars do not cross threads — so the trace id is
    # captured on THIS thread and re-set on the worker thread, or
    # the router spans would land under trace id "unknown".
    trace_id = observability_tracing.current_trace_id()

    def run_chain():
        token = None
        if trace_id is not None:
            token = observability_tracing.start_trace(trace_id)
        try:
            return call_with_fallback(chain, wrapped_call, quota_store=quota_store, budget=budget)
        finally:
            if token is not None:
                observability_tracing.end_trace(token)

    result = run_interruptible(
        run_chain,
        interrupt_event=interrupt_event,
        timeout=config.call_timeout_seconds,
    )
    reply = result.value
    if not isinstance(reply, ModelReply):  # pragma: no cover - defensive
        raise ProviderError("fallback chain returned a value that is not a ModelReply")
    return ModelCallOutcome(
        reply=reply,
        tier_used=result.tier_used,
        attempts=list(result.attempts),
        hops=list(result.hops),
        total_cost_rp=result.total_cost_rp,
        chain_notes=chain_notes,
    )
