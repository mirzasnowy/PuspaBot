"""Tests for smart 3-tier fallback + real-time quota tracking
(puspa/router/fallback.py, puspa/router/quota.py).

Pattern studied, then rewritten as Puspa's own code: 9Router
(decolua/9router, MIT) chains subscription -> cheap -> free providers
and falls through automatically when a tier runs out of quota or
errors, while tracking token use, reset countdowns, and cost per
provider. These tests prove the mechanism with SIMULATED providers
(no live provider is ever called):

- a 429 on the first tier falls through to the second and the
  request completes, with the tier used and the hop recorded;
- a 5xx on the first tier falls through the same way;
- an exhausted tier is skipped without being called, and the router
  never picks an exhausted tier while another tier still has quota;
- when every tier fails, the error names every tier and its reason
  instead of failing silently;
- a deterministic failure (400) does NOT fall through;
- quota tracking computes remaining tokens and the reset countdown
  correctly, including the rollover after a reset;
- cost estimates use the same Rupiah formula as the Puspa budget
  (``router.compute_cost``), and the per-task budget ceiling is never
  dodged by falling back to a cheaper tier;
- every tier switch is audited (reason + source tier + destination
  tier).

In-memory only: this module shadows the conftest autouse Postgres
reset fixture (``_reset_tables``) with a no-op, so no test here
touches the puspa_test database. Time is a fake clock: a mutable
holder the QuotaStore reads through ``now_fn``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from puspa.router.fallback import (
    AllTiersFailed,
    CallResult,
    NonRetryableError,
    ProviderError,
    QuotaExhaustedError,
    RateLimited,
    build_chain,
    call_with_fallback,
    choose_with_quota,
    classify_failure,
    seed_chain_quotas,
)
from puspa.router.quota import QuotaStore
from puspa.router.router import (
    choose,
    choose_with_quota as router_choose_with_quota,
    compute_cost,
    default_chain,
)
from puspa.runtime.budget import BudgetExhausted, BudgetTracker

TIER_SUBSCRIPTION = "subscription"
TIER_CHEAP = "cheap"
TIER_FREE = "free"


@pytest.fixture(autouse=True)
def _reset_tables():
    """Shadow the conftest autouse Postgres reset: in-memory only."""
    yield


# ---------------------------------------------------------------------------
# Helpers: fake clock + scripted fake providers
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    """Mutable clock: the store reads the current fake time."""

    def __init__(self, start: datetime = BASE_TIME) -> None:
        self.now_value = start

    def now(self) -> datetime:
        return self.now_value

    def advance(self, **kwargs) -> None:
        self.now_value = self.now_value + timedelta(**kwargs)


def make_store(clock: FakeClock) -> QuotaStore:
    return QuotaStore(now_fn=clock.now)


def scripted_provider(script):
    """Build a call_fn from {model: outcome | [outcome, ...]}.

    An outcome is a CallResult, a plain value, or an exception to
    raise. A list is consumed in order (one entry per call). Calls
    are recorded on the returned function as ``.calls`` (models).
    """
    remaining = {model: (list(v) if isinstance(v, list) else [v])
                 for model, v in script.items()}
    calls: list[str] = []

    def call_fn(entry):
        calls.append(entry.model)
        outcomes = remaining.get(entry.model, [CallResult("default-ok")])
        outcome = outcomes.pop(0) if outcomes else CallResult("default-ok")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    call_fn.calls = calls
    return call_fn


# ---------------------------------------------------------------------------
# Chain shape: subscription -> cheap -> free, expensive first
# ---------------------------------------------------------------------------

def test_chain_order_and_distinct_models():
    chain = build_chain("pembaca")
    assert [entry.tier for entry in chain] == [
        TIER_SUBSCRIPTION, TIER_CHEAP, TIER_FREE]
    models = [entry.model for entry in chain]
    assert len(set(models)) == 3
    totals = [e.rp_per_1k_input + e.rp_per_1k_output for e in chain]
    assert totals == sorted(totals, reverse=True)


def test_chain_for_planner_uses_reference_passed_models():
    from puspa.router.router import CATALOG

    chain = build_chain("perencana")
    assert len(chain) == 3
    for entry in chain:
        row = next(b for b in CATALOG if b[1] == entry.model)
        assert row[6] is True  # passed the 10 reference tasks


def test_verifier_chain_excludes_creator_family():
    chain = build_chain("pemeriksa", creator_family="deepseek")
    assert all(entry.family != "deepseek" for entry in chain)
    assert [entry.tier for entry in chain] == [
        TIER_SUBSCRIPTION, TIER_CHEAP, TIER_FREE]


def test_router_default_chain_wiring_matches_fallback():
    assert [e.model for e in default_chain("pembaca")] == [
        e.model for e in build_chain("pembaca")]


def test_empty_chain_rejected():
    with pytest.raises(ValueError):
        call_with_fallback([], lambda entry: "ok")


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        build_chain("raja")


# ---------------------------------------------------------------------------
# Fallback really switches tiers on 429 / 5xx / quota exhaustion
# ---------------------------------------------------------------------------

def test_first_tier_429_then_second_succeeds():
    chain = build_chain("pembaca")
    first, second = chain[0].model, chain[1].model
    call_fn = scripted_provider({
        first: RateLimited("slow down", retry_after_seconds=30),
        second: CallResult("done-by-cheap", 1000, 500),
    })
    result = call_with_fallback(chain, call_fn)
    assert result.value == "done-by-cheap"
    assert result.tier_used.model == second
    assert result.tier_used.tier == TIER_CHEAP
    assert [a.tier for a in result.attempts] == [
        TIER_SUBSCRIPTION, TIER_CHEAP]
    assert result.attempts[0].outcome == "failed"
    assert result.attempts[0].reason == "rate_limited"
    assert result.attempts[0].status == 429
    assert result.attempts[1].outcome == "succeeded"
    assert len(result.hops) == 1
    hop = result.hops[0]
    assert hop.from_tier == TIER_SUBSCRIPTION
    assert hop.to_tier == TIER_CHEAP
    assert hop.reason == "rate_limited"
    assert hop.status == 429
    assert call_fn.calls == [first, second]


def test_first_tier_5xx_then_second_succeeds():
    chain = build_chain("pembaca")
    first, second = chain[0].model, chain[1].model
    call_fn = scripted_provider({
        first: ProviderError("upstream down", status=503),
        second: CallResult("done-by-cheap", 200, 100),
    })
    result = call_with_fallback(chain, call_fn)
    assert result.value == "done-by-cheap"
    assert result.tier_used.tier == TIER_CHEAP
    assert result.hops[0].reason == "provider_error"
    assert result.hops[0].status == 503


def test_quota_exhausted_signal_falls_through():
    chain = build_chain("pembaca")
    first, second = chain[0].model, chain[1].model
    call_fn = scripted_provider({
        first: QuotaExhaustedError("insufficient_quota", status=429),
        second: CallResult("done-by-cheap", 100, 50),
    })
    result = call_with_fallback(chain, call_fn)
    assert result.tier_used.tier == TIER_CHEAP
    assert result.attempts[0].reason == "quota_exhausted"
    assert result.hops[0].reason == "quota_exhausted"


def test_429_marks_provider_blocked_in_quota_store():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    seed_chain_quotas(store, chain, limit_tokens=100000,
                      reset_after_seconds=18000)
    first = chain[0].model
    call_fn = scripted_provider({
        first: RateLimited("slow down", retry_after_seconds=120),
        chain[1].model: CallResult("ok", 10, 5),
    })
    call_with_fallback(chain, call_fn, quota_store=store)
    assert store.is_exhausted(chain[0].provider_key) is True
    assert store.remaining_tokens(chain[0].provider_key) == 0
    clock.advance(seconds=121)
    assert store.is_exhausted(chain[0].provider_key) is False


def test_exhausted_tier_skipped_without_being_called():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    seed_chain_quotas(store, chain, limit_tokens=100,
                      reset_after_seconds=18000)
    # Drain the subscription tier's whole quota.
    store.record_usage(chain[0].provider_key, 60, 40)
    assert store.is_exhausted(chain[0].provider_key) is True
    call_fn = scripted_provider({
        chain[1].model: CallResult("done-by-cheap", 10, 5),
    })
    result = call_with_fallback(chain, call_fn, quota_store=store)
    assert chain[0].model not in call_fn.calls
    assert result.tier_used.tier == TIER_CHEAP
    assert result.attempts[0].outcome == "skipped"
    assert result.attempts[0].reason == "quota_exhausted"
    assert len(result.hops) == 1
    assert result.hops[0].from_tier == TIER_SUBSCRIPTION
    assert result.hops[0].to_tier == TIER_CHEAP
    assert result.hops[0].reason == "quota_exhausted"


def test_all_tiers_fail_reports_every_tier_and_reason():
    chain = build_chain("pembaca")
    call_fn = scripted_provider({
        chain[0].model: RateLimited("slow down"),
        chain[1].model: ProviderError("upstream down", status=503),
        chain[2].model: QuotaExhaustedError("insufficient_quota"),
    })
    with pytest.raises(AllTiersFailed) as excinfo:
        call_with_fallback(chain, call_fn)
    error = excinfo.value
    assert len(error.attempts) == 3
    assert [a.outcome for a in error.attempts] == ["failed"] * 3
    assert [a.reason for a in error.attempts] == [
        "rate_limited", "provider_error", "quota_exhausted"]
    assert len(error.hops) == 2
    assert (error.hops[0].from_tier, error.hops[0].to_tier) == (
        TIER_SUBSCRIPTION, TIER_CHEAP)
    assert (error.hops[1].from_tier, error.hops[1].to_tier) == (
        TIER_CHEAP, TIER_FREE)
    message = str(error)
    for entry in chain:
        assert entry.tier in message
        assert entry.model in message


def test_deterministic_400_does_not_fall_through():
    chain = build_chain("pembaca")
    call_fn = scripted_provider({
        chain[0].model: NonRetryableError("bad request", status=400),
        chain[1].model: CallResult("must-not-run", 1, 1),
    })
    with pytest.raises(NonRetryableError) as excinfo:
        call_with_fallback(chain, call_fn)
    assert excinfo.value.status == 400
    assert excinfo.value.retryable is False
    assert len(excinfo.value.attempts) == 1
    assert chain[1].model not in call_fn.calls
    assert chain[2].model not in call_fn.calls


def test_plain_value_result_costs_zero_and_succeeds():
    chain = build_chain("pembaca")
    call_fn = scripted_provider({chain[0].model: "plain-ok"})
    result = call_with_fallback(chain, call_fn)
    assert result.value == "plain-ok"
    assert result.total_cost_rp == 0
    assert result.tier_used.tier == TIER_SUBSCRIPTION


def test_classify_failure_table():
    assert classify_failure(429, "") == "rate_limited"
    assert classify_failure(429, "insufficient_quota") == "quota_exhausted"
    assert classify_failure(429, "Quota exceeded for this month") == "quota_exhausted"
    assert classify_failure(402, "insufficient_quota") == "quota_exhausted"
    assert classify_failure(503, "") == "provider_error"
    assert classify_failure(None, "connection reset") == "provider_error"
    assert classify_failure(400, "bad request") == "non_retryable"
    assert classify_failure(404, "model not found") == "non_retryable"


# ---------------------------------------------------------------------------
# Quota tracking: remaining, reset countdown, rollover, headers
# ---------------------------------------------------------------------------

def test_quota_remaining_and_cost_after_usage():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    entry = chain[0]
    seed_chain_quotas(store, chain, limit_tokens=1000,
                      reset_after_seconds=18000)
    assert store.remaining_tokens(entry.provider_key) == 1000
    assert store.seconds_until_reset(entry.provider_key) == 18000
    cost = store.record_usage(entry.provider_key, 400, 100)
    assert cost == compute_cost(entry.choice, 400, 100)
    assert store.remaining_tokens(entry.provider_key) == 500
    assert store.seconds_until_reset(entry.provider_key) == 18000
    snapshot = store.snapshot(entry.provider_key)
    assert snapshot["tokens_used"] == 500
    assert snapshot["tokens_remaining"] == 500
    assert snapshot["requests_used"] == 1
    assert snapshot["exhausted"] is False


def test_quota_reset_countdown_ticks_and_rolls_over():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    entry = chain[0]
    seed_chain_quotas(store, chain, limit_tokens=1000,
                      reset_after_seconds=18000)
    store.record_usage(entry.provider_key, 1000, 0)
    assert store.is_exhausted(entry.provider_key) is True
    assert store.remaining_tokens(entry.provider_key) == 0
    clock.advance(seconds=9000)
    assert store.seconds_until_reset(entry.provider_key) == 9000
    assert store.is_exhausted(entry.provider_key) is True
    clock.advance(seconds=9000)  # window ends exactly at reset
    assert store.is_exhausted(entry.provider_key) is False
    assert store.remaining_tokens(entry.provider_key) == 1000
    assert store.seconds_until_reset(entry.provider_key) == 18000
    assert store.snapshot(entry.provider_key)["tokens_used"] == 0
    # Usage after the reset counts in the fresh window.
    store.record_usage(entry.provider_key, 100, 50)
    assert store.remaining_tokens(entry.provider_key) == 850


def test_quota_multiple_windows_roll_forward():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    entry = chain[0]
    seed_chain_quotas(store, chain, limit_tokens=100,
                      reset_after_seconds=3600)
    store.record_usage(entry.provider_key, 100, 0)
    clock.advance(seconds=3600 * 3 + 10)  # three whole windows pass
    assert store.is_exhausted(entry.provider_key) is False
    assert store.remaining_tokens(entry.provider_key) == 100
    assert 0 < store.seconds_until_reset(entry.provider_key) <= 3600


def test_quota_no_window_never_resets():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    entry = chain[0]
    store.register(entry.provider_key, tier=entry.tier, limit_tokens=100,
                   reset_after_seconds=0,
                   rp_per_1k_input=entry.rp_per_1k_input,
                   rp_per_1k_output=entry.rp_per_1k_output)
    assert store.seconds_until_reset(entry.provider_key) is None
    store.record_usage(entry.provider_key, 100, 0)
    clock.advance(days=30)
    assert store.is_exhausted(entry.provider_key) is True


def test_rate_limit_headers_block_then_release():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    entry = chain[0]
    seed_chain_quotas(store, chain, limit_tokens=100000,
                      reset_after_seconds=18000)
    assert store.apply_rate_limit_headers(
        entry.provider_key, {"Retry-After": "90"}) is True
    assert store.is_exhausted(entry.provider_key) is True
    clock.advance(seconds=89)
    assert store.is_exhausted(entry.provider_key) is True
    clock.advance(seconds=2)
    assert store.is_exhausted(entry.provider_key) is False
    # Remaining 0 with no Retry-After blocks until the window reset.
    assert store.apply_rate_limit_headers(
        entry.provider_key, {"X-RateLimit-Remaining": "0"}) is True
    assert store.is_exhausted(entry.provider_key) is True
    # Unknown headers are ignored, never raised.
    assert store.apply_rate_limit_headers(
        entry.provider_key, {"X-Weird": "nonsense"}) is True
    # An unregistered provider reports False, never raises.
    assert store.apply_rate_limit_headers(
        "openrouter:no-such-model", {"Retry-After": "5"}) is False


def test_rate_limit_reset_header_moves_reset():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    entry = chain[0]
    seed_chain_quotas(store, chain, limit_tokens=1000,
                      reset_after_seconds=18000)
    reset_epoch = int((BASE_TIME + timedelta(seconds=600)).timestamp())
    store.apply_rate_limit_headers(
        entry.provider_key, {"X-RateLimit-Reset": str(reset_epoch)})
    assert store.seconds_until_reset(entry.provider_key) == 600


def test_unregistered_provider_is_available_not_exhausted():
    store = make_store(FakeClock())
    assert store.is_exhausted("openrouter:never-registered") is False
    assert store.remaining_tokens("openrouter:never-registered") is None
    assert store.record_usage("openrouter:never-registered", 10, 5) is None
    assert store.estimate_cost_rp(
        "openrouter:never-registered", 10, 5) is None
    assert store.snapshot("openrouter:never-registered") is None


def test_register_rejects_negative_values():
    store = make_store(FakeClock())
    with pytest.raises(ValueError):
        store.register("openrouter:x", tier="cheap", limit_tokens=-1,
                       reset_after_seconds=60, rp_per_1k_input=1,
                       rp_per_1k_output=1)
    with pytest.raises(ValueError):
        store.register("openrouter:x", tier="cheap", limit_tokens=10,
                       reset_after_seconds=-5, rp_per_1k_input=1,
                       rp_per_1k_output=1)


# ---------------------------------------------------------------------------
# Cost estimates: same Rupiah unit as the Puspa budget
# ---------------------------------------------------------------------------

def test_cost_estimate_matches_router_compute_cost():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    seed_chain_quotas(store, chain, limit_tokens=100000,
                      reset_after_seconds=18000)
    for entry in chain:
        assert store.estimate_cost_rp(entry.provider_key, 1000, 500) == \
            compute_cost(entry.choice, 1000, 500)
        assert entry.estimate_cost_rp(1000, 500) == \
            compute_cost(entry.choice, 1000, 500)


def test_successful_call_charges_quota_and_reports_cost():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    seed_chain_quotas(store, chain, limit_tokens=100000,
                      reset_after_seconds=18000)
    entry = chain[0]
    call_fn = scripted_provider({
        entry.model: CallResult("ok", 1000, 500),
    })
    result = call_with_fallback(chain, call_fn, quota_store=store)
    expected = compute_cost(entry.choice, 1000, 500)
    assert result.total_cost_rp == expected
    assert result.total_input_tokens == 1000
    assert result.total_output_tokens == 500
    assert store.remaining_tokens(entry.provider_key) == 100000 - 1500
    assert store.snapshot(entry.provider_key)["requests_used"] == 1


# ---------------------------------------------------------------------------
# Quota-aware routing: never pick an exhausted tier while one remains
# ---------------------------------------------------------------------------

def test_quota_aware_routing_skips_exhausted_tier():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    seed_chain_quotas(store, chain, limit_tokens=100,
                      reset_after_seconds=18000)
    assert choose_with_quota("pembaca", store).tier == TIER_SUBSCRIPTION
    store.record_usage(chain[0].provider_key, 100, 0)
    picked = choose_with_quota("pembaca", store)
    assert picked is not None
    assert picked.tier == TIER_CHEAP
    assert picked.model == chain[1].model
    store.record_usage(chain[1].provider_key, 100, 0)
    picked = choose_with_quota("pembaca", store)
    assert picked.tier == TIER_FREE
    store.record_usage(chain[2].provider_key, 100, 0)
    assert choose_with_quota("pembaca", store) is None


def test_quota_aware_routing_recovers_after_reset():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    seed_chain_quotas(store, chain, limit_tokens=100,
                      reset_after_seconds=3600)
    for entry in chain:
        store.record_usage(entry.provider_key, 100, 0)
    assert choose_with_quota("pembaca", store) is None
    clock.advance(seconds=3600)
    picked = choose_with_quota("pembaca", store)
    assert picked is not None
    assert picked.tier == TIER_SUBSCRIPTION


def test_router_wiring_choose_with_quota_matches():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    seed_chain_quotas(store, chain, limit_tokens=100,
                      reset_after_seconds=18000)
    store.record_usage(chain[0].provider_key, 100, 0)
    picked = router_choose_with_quota("pembaca", store)
    assert picked is not None
    assert picked.model == chain[1].model
    # With no quota registered at all, the router picks the FIRST chain
    # tier (subscription, the most expensive model), NOT the cheapest
    # model plain choose() returns: quota-aware routing drains the
    # subscription tier first, per the 9Router rule.
    fresh = make_store(FakeClock())
    assert router_choose_with_quota("pembaca", fresh).model == \
        chain[0].model
    assert router_choose_with_quota("pembaca", fresh).model != \
        choose("pembaca").model


# ---------------------------------------------------------------------------
# Budget ceiling: fallback must never dodge it
# ---------------------------------------------------------------------------

def test_budget_exhausted_before_any_tier_calls_nothing():
    chain = build_chain("pembaca")
    budget = BudgetTracker(rupiah_max=2000)
    budget.record_cost(2000)
    call_fn = scripted_provider({
        chain[0].model: CallResult("must-not-run", 1, 1),
    })
    with pytest.raises(BudgetExhausted):
        call_with_fallback(chain, call_fn, budget=budget)
    assert call_fn.calls == []


def test_budget_exceeded_by_success_does_not_fall_back():
    chain = build_chain("pembaca")
    # The cheapest reader model costs Rp1/1k in + Rp2/1k out. A call
    # of 1000 in + 500 out on the subscription tier (claude-haiku,
    # Rp16/1k in + Rp80/1k out) costs Rp56; a Rp50 ceiling is exceeded
    # by the first tier, and the cheap tier must NOT be tried to dodge it.
    budget = BudgetTracker(rupiah_max=50)
    call_fn = scripted_provider({
        chain[0].model: CallResult("expensive-ok", 1000, 500),
        chain[1].model: CallResult("must-not-run", 1, 1),
    })
    with pytest.raises(BudgetExhausted):
        call_with_fallback(chain, call_fn, budget=budget)
    assert call_fn.calls == [chain[0].model]
    assert budget.rupiah_used == 0  # the over-ceiling charge was refused


def test_budget_charge_recorded_on_success_within_ceiling():
    chain = build_chain("pembaca")
    budget = BudgetTracker(rupiah_max=2000)
    entry = chain[0]
    call_fn = scripted_provider({
        entry.model: CallResult("ok", 1000, 500),
    })
    result = call_with_fallback(chain, call_fn, budget=budget)
    assert budget.rupiah_used == result.total_cost_rp
    assert budget.rupiah_used == compute_cost(entry.choice, 1000, 500)
    assert budget.remaining_rupiah == 2000 - result.total_cost_rp


def test_failed_tiers_cost_nothing_to_budget_or_quota():
    clock = FakeClock()
    store = make_store(clock)
    chain = build_chain("pembaca")
    seed_chain_quotas(store, chain, limit_tokens=100000,
                      reset_after_seconds=18000)
    budget = BudgetTracker(rupiah_max=2000)
    call_fn = scripted_provider({
        chain[0].model: RateLimited("slow down"),
        chain[1].model: CallResult("ok", 1000, 500),
    })
    result = call_with_fallback(chain, call_fn, quota_store=store,
                                budget=budget)
    assert budget.rupiah_used == result.total_cost_rp
    # The failed tier consumed no tokens in the quota store.
    assert store.snapshot(chain[0].provider_key)["tokens_used"] == 0
    assert store.snapshot(chain[1].provider_key)["tokens_used"] == 1500


# ---------------------------------------------------------------------------
# Audit trail completeness
# ---------------------------------------------------------------------------

def test_every_hop_has_reason_source_and_destination():
    chain = build_chain("pembaca")
    call_fn = scripted_provider({
        chain[0].model: RateLimited("slow down"),
        chain[1].model: ProviderError("down", status=502),
        chain[2].model: CallResult("done-by-free", 10, 5),
    })
    result = call_with_fallback(chain, call_fn)
    assert result.tier_used.tier == TIER_FREE
    assert len(result.hops) == 2
    for hop in result.hops:
        assert hop.from_tier in (TIER_SUBSCRIPTION, TIER_CHEAP, TIER_FREE)
        assert hop.to_tier in (TIER_SUBSCRIPTION, TIER_CHEAP, TIER_FREE)
        assert hop.from_tier != hop.to_tier
        assert hop.reason
    assert [h.from_tier for h in result.hops] == [
        TIER_SUBSCRIPTION, TIER_CHEAP]
    assert [h.to_tier for h in result.hops] == [TIER_CHEAP, TIER_FREE]
    assert result.tiers_tried == [TIER_SUBSCRIPTION, TIER_CHEAP, TIER_FREE]
