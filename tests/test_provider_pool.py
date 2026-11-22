"""Tests for the multi-account provider pool (puspa/router/pool.py).

Pattern studied, then rewritten as Puspa's own code:
azrinaldi/gemini-stealth-bridge (MIT) pools several accounts behind
one provider with rotate / lru / fill-first strategies, 5-hour and
7-day quota tracking per account, a quota preflight that skips spent
accounts, failover on quota or transport errors, per-account rate
limits, and live quota bars. These tests prove the mechanism with
SIMULATED accounts (no live provider is ever called, no cookie or
uTLS code exists anywhere in the implementation — the pool serves
official providers only: BYOK vault keys and OAuth logins):

- two accounts for one provider rotate and fail over on a quota
  error, with the account used and the hop recorded;
- a spent account is skipped by preflight (5h window and weekly
  window), and the pool never picks an exhausted account while
  another still has quota;
- rotate stays on one account while it can serve and only moves
  when it cannot; lru picks the least recently used; fill-first
  takes the first ready account and demotes one that just failed;
- a transport error fails over the same way, while a deterministic
  failure (400) does NOT fall through;
- when every account fails, the error names every account and its
  reason instead of failing silently;
- per-account rate limits (RPM sliding window, max concurrent)
  make the pool skip a limited account instead of calling it;
- quota usage is recorded in both windows, the snapshot reports
  used fractions and reset countdowns for the Settings quota bars,
  and an account with no registered quota is untracked but usable;
- the per-task budget ceiling is never dodged by falling over to
  another account;
- every account switch is audited (reason + source + destination).

In-memory only: this module shadows the conftest autouse Postgres
reset fixture (``_reset_tables``) with a no-op, so no test here
touches the puspa_test database. Time is a fake clock: a mutable
holder the pool and its QuotaStore read through ``now_fn``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from puspa.router.fallback import CallResult, NonRetryableError, ProviderError, QuotaExhaustedError
from puspa.router.pool import (
    AllAccountsFailed,
    PoolAccount,
    ProviderPool,
    call_with_pool,
)
from puspa.router.quota import QuotaStore
from puspa.runtime.budget import BudgetExhausted, BudgetTracker


@pytest.fixture(autouse=True)
def _reset_tables():
    """Shadow the conftest autouse Postgres reset: in-memory only."""
    yield


# ---------------------------------------------------------------------------
# Helpers: fake clock + scripted fake accounts
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """Mutable clock: the pool reads the current fake time."""

    def __init__(self, start: datetime = BASE_TIME) -> None:
        self.now_value = start

    def now(self) -> datetime:
        return self.now_value

    def advance(self, **kwargs) -> None:
        self.now_value = self.now_value + timedelta(**kwargs)


def make_account(account_id: str, provider: str = "openrouter", kind: str = "byok") -> PoolAccount:
    return PoolAccount(
        account_id=account_id,
        provider=provider,
        kind=kind,
        label=f"{provider} {account_id}",
        auth_ref=f"byok:{provider}" if kind == "byok" else provider,
    )


def make_pool(
    clock: FakeClock,
    account_ids=("openrouter:1", "openrouter:2"),
    *,
    strategy: str = "rotate",
    max_rpm: int = 0,
    max_concurrent: int = 0,
) -> ProviderPool:
    return ProviderPool(
        "openrouter",
        [make_account(account_id) for account_id in account_ids],
        strategy=strategy,
        quota_store=QuotaStore(now_fn=clock.now),
        now_fn=clock.now,
        max_rpm=max_rpm,
        max_concurrent=max_concurrent,
    )


def scripted_call(script):
    """Build a call_fn from {account_id: outcome | [outcome, ...]}.

    An outcome is a CallResult, a plain value, or an exception to
    raise. A list is consumed in order (one entry per call). Calls
    are recorded on the returned function as ``.calls``.
    """
    remaining = {k: (list(v) if isinstance(v, list) else [v]) for k, v in script.items()}
    calls: list[str] = []

    def call_fn(account):
        calls.append(account.account_id)
        outcomes = remaining.get(account.account_id, [CallResult("default-ok")])
        outcome = outcomes.pop(0) if outcomes else CallResult("default-ok")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    call_fn.calls = calls
    return call_fn


# ---------------------------------------------------------------------------
# Rotation + failover (the done criteria)
# ---------------------------------------------------------------------------


def test_two_accounts_rotate_and_fail_over_on_quota_error():
    clock = FakeClock()
    pool = make_pool(clock, strategy="rotate")
    call_fn = scripted_call(
        {
            "openrouter:1": [QuotaExhaustedError("quota exhausted", status=429)],
            "openrouter:2": [CallResult("second-ok", 10, 5)],
        }
    )
    result = call_with_pool(pool, call_fn)
    assert result.value == "second-ok"
    assert result.account_used.account_id == "openrouter:2"
    assert result.accounts_tried == ["openrouter:1", "openrouter:2"]
    assert [a.outcome for a in result.attempts] == ["failed", "succeeded"]
    assert result.attempts[0].reason == "quota_exhausted"
    assert len(result.hops) == 1
    assert result.hops[0].from_account_id == "openrouter:1"
    assert result.hops[0].to_account_id == "openrouter:2"
    assert call_fn.calls == ["openrouter:1", "openrouter:2"]


def test_rotate_stays_on_one_account_while_it_can_serve():
    clock = FakeClock()
    pool = make_pool(clock, strategy="rotate")
    first = pool.select()
    second = pool.select()
    third = pool.select()
    assert first is not None and second is not None and third is not None
    assert first.account_id == second.account_id == third.account_id == "openrouter:1"


def test_rotate_moves_when_the_current_account_is_exhausted():
    clock = FakeClock()
    pool = make_pool(clock, strategy="rotate")
    pool.register_quota("openrouter:1", five_hour_tokens=10)
    pool.quota_store.record_usage("openrouter:1:5h", 10, 0)
    assert pool.select().account_id == "openrouter:2"
    # Still on the second account while it can serve.
    assert pool.select().account_id == "openrouter:2"


def test_spent_account_is_skipped_by_preflight():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    pool.register_quota("openrouter:1", five_hour_tokens=100)
    pool.register_quota("openrouter:2", five_hour_tokens=100)
    pool.quota_store.record_usage("openrouter:1:5h", 100, 0)
    call_fn = scripted_call({"openrouter:2": [CallResult("ok", 1, 1)]})
    result = call_with_pool(pool, call_fn)
    assert result.account_used.account_id == "openrouter:2"
    assert result.attempts[0].account_id == "openrouter:1"
    assert result.attempts[0].outcome == "skipped"
    assert result.attempts[0].reason == "quota_exhausted"
    assert call_fn.calls == ["openrouter:2"]


def test_weekly_window_exhaustion_also_skips_the_account():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    pool.register_quota("openrouter:1", weekly_tokens=50)
    pool.quota_store.record_usage("openrouter:1:weekly", 50, 0)
    assert pool.skip_reason(pool.accounts[0]) == "quota_exhausted"
    assert pool.select().account_id == "openrouter:2"


def test_quota_failure_marks_the_store_exhausted_for_the_next_pick():
    clock = FakeClock()
    pool = make_pool(clock, strategy="rotate")
    pool.register_quota("openrouter:1", five_hour_tokens=1000)
    call_fn = scripted_call(
        {
            "openrouter:1": [QuotaExhaustedError("quota exhausted", status=429)],
            "openrouter:2": [CallResult("ok")],
        }
    )
    call_with_pool(pool, call_fn)
    # The failed account is now blocked in the store, so the next
    # pick skips it even though its window still has tokens left.
    assert pool.quota_store.is_exhausted("openrouter:1:5h")
    assert pool.select().account_id == "openrouter:2"


def test_transport_error_fails_over():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    call_fn = scripted_call(
        {
            "openrouter:1": [ProviderError("connection reset")],
            "openrouter:2": [CallResult("ok")],
        }
    )
    result = call_with_pool(pool, call_fn)
    assert result.account_used.account_id == "openrouter:2"
    assert result.attempts[0].reason == "provider_error"


def test_all_accounts_failed_names_every_account():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    call_fn = scripted_call(
        {
            "openrouter:1": [QuotaExhaustedError("quota exhausted", status=429)],
            "openrouter:2": [ProviderError("boom")],
        }
    )
    with pytest.raises(AllAccountsFailed) as excinfo:
        call_with_pool(pool, call_fn)
    assert "openrouter:1" in str(excinfo.value)
    assert "openrouter:2" in str(excinfo.value)
    assert [a.outcome for a in excinfo.value.attempts] == ["failed", "failed"]
    assert call_fn.calls == ["openrouter:1", "openrouter:2"]


def test_all_accounts_exhausted_reports_skips_not_silence():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    pool.register_quota("openrouter:1", five_hour_tokens=10)
    pool.register_quota("openrouter:2", five_hour_tokens=10)
    pool.quota_store.record_usage("openrouter:1:5h", 10, 0)
    pool.quota_store.record_usage("openrouter:2:5h", 10, 0)
    call_fn = scripted_call({})
    with pytest.raises(AllAccountsFailed) as excinfo:
        call_with_pool(pool, call_fn)
    assert [a.outcome for a in excinfo.value.attempts] == ["skipped", "skipped"]
    assert call_fn.calls == []


def test_deterministic_failure_does_not_fall_through():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    call_fn = scripted_call({"openrouter:1": [NonRetryableError("bad request", status=400)]})
    with pytest.raises(NonRetryableError):
        call_with_pool(pool, call_fn)
    assert call_fn.calls == ["openrouter:1"]
    assert pool.accounts[0].fails == 1
    assert pool.accounts[1].fails == 0


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def test_lru_picks_the_least_recently_used_account():
    clock = FakeClock()
    pool = make_pool(clock, strategy="lru")
    # Use account 1 once through the executor, so it is "recent".
    call_with_pool(pool, scripted_call({"openrouter:1": [CallResult("ok")]}))
    # LRU now prefers account 2 (never used).
    assert pool.select().account_id == "openrouter:2"
    call_with_pool(pool, scripted_call({"openrouter:2": [CallResult("ok")]}))
    # Both used once; account 1 was used earlier, so it is least recent.
    assert pool.select().account_id == "openrouter:1"


def test_lru_prefers_fewer_failures_over_recency():
    clock = FakeClock()
    pool = make_pool(clock, strategy="lru")
    pool.accounts[0].fails = 2
    pool.accounts[0].last_used = BASE_TIME - timedelta(hours=1)
    pool.accounts[1].fails = 0
    pool.accounts[1].last_used = BASE_TIME
    assert pool.select().account_id == "openrouter:2"


def test_fill_first_takes_the_first_ready_account():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    assert pool.select().account_id == "openrouter:1"
    assert pool.select().account_id == "openrouter:1"


def test_fill_first_demotes_a_failed_account():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    call_fn = scripted_call(
        {
            "openrouter:1": [ProviderError("boom")],
            "openrouter:2": [CallResult("ok")],
        }
    )
    call_with_pool(pool, call_fn)
    # Account 1 cools for 60 seconds; the next pick skips it.
    assert pool.select().account_id == "openrouter:2"
    clock.advance(seconds=61)
    assert pool.select().account_id == "openrouter:1"


def test_quota_cooldown_escalates_with_repeated_strikes():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    # No quota registered here on purpose: a registered window
    # would stay store-blocked until its reset, so the second
    # strike could never happen; the cooldown alone governs.
    script = {
        "openrouter:1": [QuotaExhaustedError("quota exhausted", status=429)],
        "openrouter:2": [CallResult("ok")],
    }
    call_with_pool(pool, scripted_call(script))
    first_cooling = pool.accounts[0].cooling_until
    assert first_cooling is not None
    assert first_cooling - clock.now() == timedelta(seconds=60)
    # Second strike, after the first cooldown lapses: 5 minutes.
    clock.advance(seconds=61)
    call_with_pool(pool, scripted_call(script))
    second_cooling = pool.accounts[0].cooling_until
    assert second_cooling is not None
    assert second_cooling - clock.now() == timedelta(seconds=300)
    assert pool.accounts[0].strikes == 2


def test_success_resets_failures_and_strikes():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    call_fn = scripted_call(
        {
            "openrouter:1": [ProviderError("boom"), CallResult("ok", 1, 1)],
            "openrouter:2": [CallResult("ok")],
        }
    )
    call_with_pool(pool, call_fn)
    assert pool.accounts[0].fails == 1
    clock.advance(seconds=61)
    call_with_pool(pool, scripted_call({"openrouter:1": [CallResult("ok", 1, 1)]}))
    assert pool.accounts[0].fails == 0
    assert pool.accounts[0].strikes == 0
    assert pool.accounts[0].cooling_until is None


# ---------------------------------------------------------------------------
# Per-account rate limits
# ---------------------------------------------------------------------------


def test_rpm_limit_skips_the_limited_account():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first", max_rpm=1)
    call_with_pool(pool, scripted_call({"openrouter:1": [CallResult("ok")]}))
    # Account 1 used its one request this minute; the next pick moves on.
    assert pool.select().account_id == "openrouter:2"
    clock.advance(seconds=61)
    assert pool.select().account_id == "openrouter:1"


def test_select_alone_consumes_no_rpm_budget():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first", max_rpm=1)
    assert pool.select().account_id == "openrouter:1"
    assert pool.select().account_id == "openrouter:1"
    assert pool.select().account_id == "openrouter:1"


def test_max_concurrent_skips_a_busy_account():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first", max_concurrent=1)
    pool.accounts[0].active_streams = 1
    assert pool.skip_reason(pool.accounts[0]) == "rate_limited"
    assert pool.select().account_id == "openrouter:2"
    pool.accounts[0].active_streams = 0
    assert pool.select().account_id == "openrouter:1"


def test_active_streams_are_released_after_the_call():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first", max_concurrent=1)
    call_with_pool(pool, scripted_call({"openrouter:1": [CallResult("ok")]}))
    assert pool.accounts[0].active_streams == 0
    call_with_pool(pool, scripted_call({"openrouter:1": [ProviderError("boom")]}))
    assert pool.accounts[0].active_streams == 0


# ---------------------------------------------------------------------------
# Quota tracking + snapshot (Settings quota bars)
# ---------------------------------------------------------------------------


def test_usage_is_recorded_in_both_windows():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    pool.register_quota("openrouter:1", five_hour_tokens=1000, weekly_tokens=5000)
    call_with_pool(pool, scripted_call({"openrouter:1": [CallResult("ok", 100, 50)]}))
    assert pool.quota_store.get("openrouter:1:5h").tokens_used == 150
    assert pool.quota_store.get("openrouter:1:weekly").tokens_used == 150


def test_snapshot_reports_used_fractions_and_reset_countdowns():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    pool.register_quota("openrouter:1", five_hour_tokens=1000, weekly_tokens=7000)
    pool.quota_store.record_usage("openrouter:1:5h", 250, 0)
    bars = pool.snapshot()
    assert len(bars) == 2
    first = bars[0]
    assert first["account_id"] == "openrouter:1"
    assert first["available"] is True
    assert first["skip_reason"] is None
    windows = {w["window"]: w for w in first["windows"]}
    assert windows["5h"]["used_fraction"] == pytest.approx(0.25)
    assert windows["5h"]["tokens_remaining"] == 750
    assert windows["5h"]["reset_in_seconds"] == 18_000
    assert windows["5h"]["exhausted"] is False
    assert windows["weekly"]["used_fraction"] == pytest.approx(0.0)
    assert windows["weekly"]["reset_in_seconds"] == 604_800
    # The second account has no registered quota: untracked, usable.
    second = bars[1]
    assert second["windows"][0]["used_fraction"] is None
    assert second["windows"][0]["limit_tokens"] is None
    assert second["available"] is True


def test_snapshot_marks_an_exhausted_account_unavailable():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    pool.register_quota("openrouter:1", five_hour_tokens=10)
    pool.quota_store.record_usage("openrouter:1:5h", 10, 0)
    first = pool.snapshot()[0]
    assert first["available"] is False
    assert first["skip_reason"] == "quota_exhausted"
    assert first["windows"][0]["exhausted"] is True
    assert first["windows"][0]["tokens_remaining"] == 0


def test_snapshot_never_contains_credential_values():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    rendered = repr(pool.snapshot())
    assert "byok:" not in rendered
    assert "sk-" not in rendered


def test_disabled_account_is_skipped_and_can_be_re_enabled():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    pool.disable("openrouter:1")
    assert pool.skip_reason(pool.accounts[0]) == "disabled"
    assert pool.select().account_id == "openrouter:2"
    pool.enable("openrouter:1")
    assert pool.select().account_id == "openrouter:1"


def test_select_returns_none_when_nothing_is_ready():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    pool.disable("openrouter:1")
    pool.disable("openrouter:2")
    assert pool.select() is None
    assert pool.available_accounts() == []


# ---------------------------------------------------------------------------
# Budget + validation
# ---------------------------------------------------------------------------


def test_budget_ceiling_is_never_dodged_by_failing_over():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    pool.register_quota(
        "openrouter:1",
        five_hour_tokens=1000,
        weekly_tokens=1000,
        rp_per_1k_input=100,
        rp_per_1k_output=100,
    )
    pool.register_quota(
        "openrouter:2",
        five_hour_tokens=1000,
        weekly_tokens=1000,
        rp_per_1k_input=100,
        rp_per_1k_output=100,
    )
    budget = BudgetTracker(rupiah_max=50)
    call_fn = scripted_call({"openrouter:1": [CallResult("ok", 1000, 0)]})
    # The call costs Rp100 on account 1; the ceiling is Rp50, so the
    # run fails instead of retrying on the cheaper-looking account 2.
    with pytest.raises(BudgetExhausted):
        call_with_pool(pool, call_fn, budget=budget)
    assert call_fn.calls == ["openrouter:1"]
    assert budget.rupiah_used == 0


def test_budget_exhausted_before_any_try_calls_nothing():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    budget = BudgetTracker(rupiah_max=0)
    call_fn = scripted_call({"openrouter:1": [CallResult("ok")]})
    with pytest.raises(BudgetExhausted):
        call_with_pool(pool, call_fn, budget=budget)
    assert call_fn.calls == []


def test_pool_rejects_bad_construction():
    clock = FakeClock()
    with pytest.raises(ValueError, match="at least one account"):
        ProviderPool("openrouter", [], now_fn=clock.now)
    with pytest.raises(ValueError, match="unknown pool strategy"):
        ProviderPool(
            "openrouter", [make_account("openrouter:1")], strategy="random", now_fn=clock.now
        )
    with pytest.raises(ValueError, match="must be unique"):
        ProviderPool(
            "openrouter",
            [make_account("openrouter:1"), make_account("openrouter:1")],
            now_fn=clock.now,
        )
    with pytest.raises(ValueError, match="belongs to provider"):
        ProviderPool(
            "openrouter",
            [make_account("openai:1", provider="openai")],
            now_fn=clock.now,
        )
    with pytest.raises(KeyError, match="no pool account"):
        make_pool(clock).disable("openrouter:9")


def test_pool_method_call_matches_the_module_function():
    clock = FakeClock()
    pool = make_pool(clock, strategy="fill-first")
    result = pool.call(scripted_call({"openrouter:1": [CallResult("via-method")]}))
    assert result.value == "via-method"
    assert result.account_used.account_id == "openrouter:1"
