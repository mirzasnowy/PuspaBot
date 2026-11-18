"""Fallback chain tests (G21): config-driven chains, 429, auth refresh.

No live provider, no network: every tier call here is an
injected fake. The suite is NOT run by this worker (the central
suite is running); these tests are written for the coordinator
to run.
"""

from __future__ import annotations

import pytest

from puspa.router.chains import (
    FallbackChainConfig,
    ProviderRef,
    chain_entries_for,
    default_fallback_chain_config,
)
from puspa.router.fallback import (
    AllTiersFailed,
    AuthError,
    ProviderError,
    RateLimited,
    classify_failure,
)
from puspa.turns.config import TurnConfig
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply, call_model
from puspa.turns.turn_api_error import wrap_with_auth_refresh


def _reply(text: str = "ok") -> ModelReply:
    return ModelReply(text=text, input_tokens=10, output_tokens=5)


def _two_tier_chain() -> list:
    config = FallbackChainConfig(
        main=[
            ProviderRef("openrouter", "openai/gpt-4o-mini"),
            ProviderRef("openrouter", "meta-llama/llama-3.1-8b-instruct"),
        ]
    )
    entries, _notes = chain_entries_for(config, "main")
    return entries


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_failure_auth_error():
    assert classify_failure(401, "invalid api key") == "auth_error"
    assert classify_failure(401, "") == "auth_error"
    assert classify_failure(403, "invalid api key") == "auth_error"
    assert classify_failure(403, "quota exceeded") == "quota_exhausted"
    assert classify_failure(429, "") == "rate_limited"
    assert classify_failure(400, "bad request") == "non_retryable"


def test_auth_error_is_retryable():
    assert AuthError("bad key", status=401).retryable is True


# ---------------------------------------------------------------------------
# Config-driven chains
# ---------------------------------------------------------------------------


def test_default_main_chain_has_three_tiers():
    config = default_fallback_chain_config("reader")
    entries, _notes = chain_entries_for(config, "main")
    assert [entry.tier for entry in entries] == ["subscription", "cheap", "free"]
    assert len({entry.model for entry in entries}) == 3


def test_auxiliary_chains_are_independent_of_main():
    config = default_fallback_chain_config("reader")
    vision, _ = chain_entries_for(config, "vision")
    title, _ = chain_entries_for(config, "title")
    compression, _ = chain_entries_for(config, "compression")
    assert [e.model for e in vision] != [e.model for e in title]
    assert [e.model for e in compression] != [e.model for e in vision]
    assert all(entry.vision for entry in vision), "the vision chain must be vision models"


def test_config_order_is_respected_not_hardcoded():
    config = FallbackChainConfig(
        main=[
            ProviderRef("openrouter", "meta-llama/llama-3.1-8b-instruct"),
            ProviderRef("openrouter", "openai/gpt-4o-mini"),
        ]
    )
    entries, _notes = chain_entries_for(config, "main")
    assert entries[0].model == "meta-llama/llama-3.1-8b-instruct"
    assert entries[0].tier == "subscription"
    assert entries[1].model == "openai/gpt-4o-mini"
    assert entries[1].tier == "cheap"


def test_refs_beyond_three_tiers_are_dropped_and_reported():
    config = FallbackChainConfig(
        main=[
            ProviderRef("openrouter", "openai/gpt-4o-mini"),
            ProviderRef("openrouter", "meta-llama/llama-3.1-8b-instruct"),
            ProviderRef("openrouter", "qwen/qwen3-32b"),
            ProviderRef("openrouter", "google/gemma-3-4b-it"),
        ]
    )
    entries, notes = chain_entries_for(config, "main")
    assert len(entries) == 3
    assert any("dropped" in note for note in notes)


def test_unlisted_model_cost_is_unknown_not_invented():
    config = FallbackChainConfig(main=[ProviderRef("openrouter", "someone/unknown-model-xyz")])
    entries, notes = chain_entries_for(config, "main")
    assert entries[0].rp_per_1k_input == 0
    assert entries[0].rp_per_1k_output == 0
    assert any("not in the router catalog" in note for note in notes)


def test_empty_config_falls_back_to_catalog_default():
    entries, notes = chain_entries_for(FallbackChainConfig(), "main", role="reader")
    assert len(entries) == 3
    assert any("catalog default" in note for note in notes)


def test_unknown_auxiliary_kind_rejected():
    config = default_fallback_chain_config("reader")
    with pytest.raises(ValueError):
        chain_entries_for(config, "summarise")


# ---------------------------------------------------------------------------
# Fallback execution through the call phase
# ---------------------------------------------------------------------------


def test_first_tier_429_falls_through_to_second():
    chain = _two_tier_chain()
    seen = []

    def model_call(entry, wire_messages, tools):
        seen.append(entry.model)
        if entry.model == "openai/gpt-4o-mini":
            raise RateLimited("rate limited")
        return _reply("second tier answered")

    outcome = call_model(
        [InternalMessage.user("Hello")],
        config=TurnConfig(),
        chain=chain,
        model_call=model_call,
    )
    assert outcome.reply.text == "second tier answered"
    assert outcome.tier_used.tier == "cheap"
    assert seen == ["openai/gpt-4o-mini", "meta-llama/llama-3.1-8b-instruct"]
    assert [attempt.outcome for attempt in outcome.attempts] == ["failed", "succeeded"]
    assert len(outcome.hops) == 1
    assert outcome.hops[0].reason == "rate_limited"


def test_5xx_falls_through_to_second():
    chain = _two_tier_chain()

    def model_call(entry, wire_messages, tools):
        if entry.model == "openai/gpt-4o-mini":
            raise ProviderError("bad gateway", status=502)
        return _reply("second tier answered")

    outcome = call_model(
        [InternalMessage.user("Hello")],
        config=TurnConfig(),
        chain=chain,
        model_call=model_call,
    )
    assert outcome.reply.text == "second tier answered"
    assert outcome.tier_used.tier == "cheap"


def test_all_tiers_failing_raises_with_audit_trail():
    chain = _two_tier_chain()

    def model_call(entry, wire_messages, tools):
        raise ProviderError("provider is down", status=503)

    with pytest.raises(AllTiersFailed) as excinfo:
        call_model(
            [InternalMessage.user("Hello")],
            config=TurnConfig(),
            chain=chain,
            model_call=model_call,
        )
    assert len(excinfo.value.attempts) == 2
    assert len(excinfo.value.hops) == 1


def test_deterministic_400_does_not_fall_through():
    from puspa.router.fallback import NonRetryableError

    chain = _two_tier_chain()
    seen = []

    def model_call(entry, wire_messages, tools):
        seen.append(entry.model)
        raise NonRetryableError("bad request", status=400)

    with pytest.raises(NonRetryableError):
        call_model(
            [InternalMessage.user("Hello")],
            config=TurnConfig(),
            chain=chain,
            model_call=model_call,
        )
    assert seen == ["openai/gpt-4o-mini"], "a 400 must not try the next tier"


# ---------------------------------------------------------------------------
# Auth refresh-first
# ---------------------------------------------------------------------------


class _FakeRefresher:
    def __init__(self, *, fail_refresh: bool = False) -> None:
        self.refresh_calls: list[str] = []
        self.fail_refresh = fail_refresh

    def get_valid_token(self, provider: str) -> str:
        self.refresh_calls.append(provider)
        if self.fail_refresh:
            from puspa.router.oauth import TokenRefreshError

            raise TokenRefreshError(provider, "invalid_grant")
        return "fresh-token"


def test_auth_error_refreshes_first_then_retries_same_tier():
    chain = _two_tier_chain()
    refresher = _FakeRefresher()
    attempts = {"count": 0}

    def model_call(entry, wire_messages, tools):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise AuthError("invalid api key", status=401)
        return _reply("same tier answered after refresh")

    outcome = call_model(
        [InternalMessage.user("Hello")],
        config=TurnConfig(),
        chain=chain,
        model_call=model_call,
        refresher=refresher,
    )
    assert outcome.reply.text == "same tier answered after refresh"
    assert outcome.tier_used.tier == "subscription"
    assert refresher.refresh_calls == ["openrouter"], "refresh must run before moving on"
    assert attempts["count"] == 2, "the same tier is retried once after refresh"
    assert outcome.hops == [], "no tier hop when the refresh fixes it"


def test_auth_error_after_failed_refresh_moves_to_next_tier():
    chain = _two_tier_chain()
    refresher = _FakeRefresher(fail_refresh=True)

    def model_call(entry, wire_messages, tools):
        if entry.model == "openai/gpt-4o-mini":
            raise AuthError("invalid api key", status=401)
        return _reply("second tier answered")

    outcome = call_model(
        [InternalMessage.user("Hello")],
        config=TurnConfig(),
        chain=chain,
        model_call=model_call,
        refresher=refresher,
    )
    assert outcome.reply.text == "second tier answered"
    assert outcome.tier_used.tier == "cheap"
    assert refresher.refresh_calls == ["openrouter"]


def test_wrap_with_auth_refresh_passes_non_auth_failures_through():
    calls = {"count": 0}

    def call_fn(entry):
        calls["count"] += 1
        raise RateLimited("rate limited")

    wrapped = wrap_with_auth_refresh(call_fn, _FakeRefresher(), provider="openrouter")
    with pytest.raises(RateLimited):
        wrapped(object())
    assert calls["count"] == 1, "no refresh and no retry for a non-auth failure"
