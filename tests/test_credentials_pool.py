"""Tests for the credential pool with per-model cooldown (G22).

The key test (Nero's PR #146450 area): cooldown is scoped to
the requested MODEL, never global — two different models never
block each other's cooldown, even for the same key. Run
in-memory: ``pytest tests/test_credentials_pool.py --noconftest``.
"""

import pytest

from puspa.credentials.pool import (
    AllKeysCoolingDown,
    AuxiliaryClient,
    AuxiliaryError,
    CredentialPool,
    NoCredentialAvailable,
    RateLimitedError,
)


class FakeClock:
    """Monotonic clock the test advances by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_acquire_returns_first_key_and_skips_cooling_down():
    clock = FakeClock()
    pool = CredentialPool(clock=clock)
    first = pool.add("openai", "gpt-4o-mini", "key-one")
    pool.add("openai", "gpt-4o-mini", "key-two")
    assert pool.acquire("openai", "gpt-4o-mini") is first
    pool.mark_rate_limited(first)
    # The cooled-down key is skipped, not used and failed.
    assert pool.acquire("openai", "gpt-4o-mini").key == "key-two"


def test_cooldown_is_scoped_to_model_not_global():
    """THE key test: same key, two models, independent cooldowns."""
    clock = FakeClock()
    pool = CredentialPool(clock=clock)
    pool.add("openrouter", "model-a", "shared-key")
    pool.add("openrouter", "model-b", "shared-key")
    entry_a = pool.acquire("openrouter", "model-a")
    pool.mark_rate_limited(entry_a)
    assert pool.is_cooling_down("openrouter", "model-a", entry_a.key_id)
    # Model B is untouched by model A's cooldown.
    assert not pool.is_cooling_down("openrouter", "model-b", entry_a.key_id)
    assert pool.acquire("openrouter", "model-b").key == "shared-key"
    # And model A stays blocked until its own cooldown ends.
    with pytest.raises(AllKeysCoolingDown):
        pool.acquire("openrouter", "model-a")


def test_cooldown_expires_and_key_is_usable_again():
    clock = FakeClock()
    pool = CredentialPool(default_cooldown_seconds=60, clock=clock)
    entry = pool.add("openai", "gpt-4o-mini", "key-one")
    pool.mark_rate_limited(entry)
    clock.advance(59)
    with pytest.raises(AllKeysCoolingDown):
        pool.acquire("openai", "gpt-4o-mini")
    clock.advance(2)
    assert pool.acquire("openai", "gpt-4o-mini") is entry


def test_retry_after_overrides_default_cooldown():
    clock = FakeClock()
    pool = CredentialPool(default_cooldown_seconds=60, clock=clock)
    entry = pool.add("openai", "gpt-4o-mini", "key-one")
    pool.mark_rate_limited(entry, retry_after=5)
    assert pool.cooldown_remaining("openai", "gpt-4o-mini", entry.key_id) == pytest.approx(5)
    clock.advance(6)
    assert pool.acquire("openai", "gpt-4o-mini") is entry


def test_all_keys_cooling_down_reports_shortest_wait():
    clock = FakeClock()
    pool = CredentialPool(clock=clock)
    first = pool.add("openai", "gpt-4o-mini", "key-one")
    second = pool.add("openai", "gpt-4o-mini", "key-two")
    pool.mark_rate_limited(first, cooldown_seconds=30)
    pool.mark_rate_limited(second, cooldown_seconds=90)
    with pytest.raises(AllKeysCoolingDown) as excinfo:
        pool.acquire("openai", "gpt-4o-mini")
    assert excinfo.value.retry_after_seconds == pytest.approx(30)


def test_acquire_unknown_pair_raises():
    pool = CredentialPool()
    with pytest.raises(NoCredentialAvailable):
        pool.acquire("openai", "gpt-4o-mini")


def test_auth_failure_cools_down_longer_than_rate_limit():
    clock = FakeClock()
    pool = CredentialPool(default_cooldown_seconds=60, auth_cooldown_seconds=600, clock=clock)
    rate_entry = pool.add("openai", "gpt-4o-mini", "key-one")
    auth_entry = pool.add("openai", "gpt-4o-mini", "key-two")
    pool.mark_rate_limited(rate_entry)
    pool.mark_auth_failed(auth_entry)
    clock.advance(61)
    # Rate-limited key is back; auth-failed key still cooling.
    assert pool.acquire("openai", "gpt-4o-mini") is rate_entry
    assert pool.is_cooling_down("openai", "gpt-4o-mini", auth_entry.key_id)


def test_mark_success_clears_failures():
    pool = CredentialPool()
    entry = pool.add("openai", "gpt-4o-mini", "key-one")
    pool.mark_rate_limited(entry)
    assert entry.failures == 1
    pool.mark_success(entry)
    assert entry.failures == 0


def test_add_same_key_twice_returns_existing_entry():
    pool = CredentialPool()
    first = pool.add("openai", "gpt-4o-mini", "key-one")
    second = pool.add("openai", "gpt-4o-mini", "key-one")
    assert first is second
    assert len(pool.available("openai", "gpt-4o-mini")) == 1


def test_remove_drops_key_and_its_cooldown():
    clock = FakeClock()
    pool = CredentialPool(clock=clock)
    entry = pool.add("openai", "gpt-4o-mini", "key-one")
    pool.mark_rate_limited(entry)
    assert pool.remove("openai", "gpt-4o-mini", "key-one") is True
    assert pool.remove("openai", "gpt-4o-mini", "key-one") is False
    with pytest.raises(NoCredentialAvailable):
        pool.acquire("openai", "gpt-4o-mini")


def test_status_never_shows_key_values():
    pool = CredentialPool()
    pool.add("openai", "gpt-4o-mini", "key-one-secret")
    status = pool.status("openai", "gpt-4o-mini")
    assert len(status) == 1
    assert "key-one-secret" not in repr(status)
    assert status[0]["key_id"]
    assert status[0]["cooling_down"] is False
    assert status[0]["failures"] == 0


def test_auxiliary_client_uses_same_pool_and_rotates_on_rate_limit():
    clock = FakeClock()
    pool = CredentialPool(clock=clock)
    pool.add("openai", "gpt-4o-mini", "key-one")
    pool.add("openai", "gpt-4o-mini", "key-two")
    seen_keys = []

    def call_fn(key, provider, model, prompt):
        seen_keys.append(key)
        if key == "key-one":
            raise RateLimitedError(retry_after_seconds=30)
        return "side answer"

    client = AuxiliaryClient(pool, call_fn, default_provider="openai", default_model="gpt-4o-mini")
    assert client.run("summarize this", purpose="compression") == "side answer"
    assert seen_keys == ["key-one", "key-two"]
    # The limited key is now cooling down for that model.
    assert pool.acquire("openai", "gpt-4o-mini").key == "key-two"


def test_auxiliary_client_rotates_on_auth_failure():
    pool = CredentialPool()
    pool.add("openai", "gpt-4o-mini", "key-one")
    pool.add("openai", "gpt-4o-mini", "key-two")

    class AuthError(Exception):
        status = 401

    def call_fn(key, provider, model, prompt):
        if key == "key-one":
            raise AuthError("bad key")
        return "ok"

    client = AuxiliaryClient(pool, call_fn, default_provider="openai", default_model="gpt-4o-mini")
    assert client.run("title this", purpose="title") == "ok"


def test_auxiliary_client_fails_when_every_key_cooling_down():
    clock = FakeClock()
    pool = CredentialPool(clock=clock)
    pool.add("openai", "gpt-4o-mini", "key-one")

    def call_fn(key, provider, model, prompt):
        raise RateLimitedError()

    client = AuxiliaryClient(pool, call_fn, default_provider="openai", default_model="gpt-4o-mini")
    with pytest.raises(AuxiliaryError):
        client.run("summarize this", purpose="compression")


def test_auxiliary_client_needs_model():
    pool = CredentialPool()
    client = AuxiliaryClient(pool, lambda *a: "x", default_provider="openai")
    with pytest.raises(ValueError):
        client.run("hi", purpose="title")


def test_auxiliary_client_reraises_non_retryable_errors():
    pool = CredentialPool()
    pool.add("openai", "gpt-4o-mini", "key-one")

    def call_fn(key, provider, model, prompt):
        raise ValueError("bad prompt")

    client = AuxiliaryClient(pool, call_fn, default_provider="openai", default_model="gpt-4o-mini")
    with pytest.raises(ValueError):
        client.run("hi", purpose="title")
