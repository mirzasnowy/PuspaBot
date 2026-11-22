"""Multi-account provider pool with quota-aware failover.

Pattern studied, then rewritten as Puspa's own code (see docs/reuse.md):
azrinaldi/gemini-stealth-bridge (MIT) pools several accounts behind
one provider and routes each request across them: strategies
``rotate`` / ``lru`` / ``fill-first``, quota tracked in a 5-hour and
a 7-day window per account, a quota preflight that skips spent
accounts, failover when a request fails on quota or transport, a
per-account rate limit, and a web control plane that shows live
quota bars. Only the MECHANISM is taken: no gsb code is copied (it
is a Go program; this is a from-scratch Python implementation), and
no gsb sub-system is used here.

NOT taken, on purpose: gsb authenticates with Google session
cookies over a browser-mimicking uTLS transport against the
unofficial Gemini Web endpoints. That breaks Google's platform
rules and is out of scope. This pool serves OFFICIAL providers
only: a BYOK API key stored in the vault (``byok:<provider>``, see
``puspa/pwa/model_settings.py``) or an OAuth subscription login
refreshed by ``puspa/router/oauth.py``. The pool never holds a key
or token value: a ``PoolAccount`` carries only an ``auth_ref`` (a
vault entry name for BYOK, a provider name for OAuth) and the
credential is resolved at call time by the caller. Nothing in this
module ever prints, logs, or returns a credential value.

How it sits in the router
-------------------------
The tiered fallback (``puspa/router/fallback.py``) falls through
MODELS when a tier fails; the pool falls through ACCOUNTS of one
provider when an account fails. The two compose: a pool call
(``call_with_pool``) is what a tier's ``call_fn`` can do, so one
provider's several keys share the load before the chain moves to
the next tier. Quota is tracked per account in the same
``QuotaStore`` the tiered fallback uses (``puspa/router/quota.py``):
each account gets two windows, ``<account>:5h`` (18.000 seconds)
and ``<account>:weekly`` (604.800 seconds), the same two windows
gsb tracks. An account with no registered quota counts as
available: an unknown limit must not block a request.

Strategies (the same three names gsb uses)
------------------------------------------
- ``rotate``: a sticky cursor. Consecutive picks stay on the same
  account while it can serve; the cursor only moves when the
  current account cannot (exhausted, cooling, rate limited). On a
  failure the next pick starts after the failed account.
- ``lru``: the ready account with the fewest failures, then the
  least recently used, then the configured order.
- ``fill-first``: the first ready account in configured order; an
  account that just failed is demoted (cooling) so the next pick
  skips it instead of hammering it.

Failure handling (auditable, never silent)
-------------------------------------------
Every account tried (or skipped) is recorded as a ``PoolAttempt``
and every switch as a ``PoolHop``. When every account fails,
``AllAccountsFailed`` is raised with the full attempt list. Only
retryable failures fall through: 429 (rate limited), a quota
exhaustion signal, 5xx, an authentication failure (401, or 403
with an auth signal), or a transport error with no status. A
deterministic failure (any other 4xx) raises immediately without
trying the next account, because the same request would fail
there too. A quota failure also marks the account's quota
exhausted in the store and starts an escalating cooldown
(60s, 5m, 15m, 1h per strike); a transport failure cools the
account for one fail-cooldown (60 seconds by default, gsb's
fill-first demotion). A success resets the account's failure
and strike counters.

Rate limit per account (gsb's per-account RPM + concurrency)
------------------------------------------------------------
``max_rpm`` caps requests per minute per account (sliding 60-second
window over recorded request times) and ``max_concurrent`` caps
simultaneous in-flight calls per account. Both are 0 (unlimited)
unless the operator sets them. A rate-limited account is skipped
by preflight, never called; the limit is a fact about the pool
configuration, not a guess about the provider.

Quota bars for Settings
-----------------------
``ProviderPool.snapshot`` returns one plain-data dict per account
(the gsb ``/ui`` quota-bar pattern): used fraction and remaining
tokens per window, the reset countdown, and whether the account
is available, cooling, or rate limited. No credential material is
ever in a snapshot. ``puspa/pwa/model_settings.pool_quota_bars``
exposes the same data to the Settings screen next to the model
choice, and ``GET /api/model-settings/pool-quota`` serves it.

What this module does NOT do (honest limits)
--------------------------------------------
- Storage here is IN-MEMORY (the pool registry in
  ``puspa/router/router.py`` and the ``QuotaStore``). Pool quota
  state does not survive a process restart, and usage recorded
  here is not yet written to the ledger. Persisting quota
  snapshots to Postgres is a follow-up, not done here.
- The turn loop DOES call the pool: the routed adapter
  (:func:`puspa.turns.turn_api.make_routed_model_call`) routes a
  subscription-tier call through the pool when the routed
  provider has at least two accounts, so a 429 on one key fails
  over to the next account of the same provider (see
  ``tests/test_provider_pool_wiring.py``). The scheduler does
  NOT call the pool: a scheduled job has no user, so there is no
  per-user vault to discover accounts from, and it keeps the
  default single-key path.
- The claim above that "the router has no live caller today" was
  wrong: :func:`puspa.router.complete.complete_detailed` IS a
  live caller (the turn loop reaches it through
  :func:`puspa.turns.turn_api.make_openrouter_model_call` and
  the routed adapter), and the pool now composes with it: a pool
  call is what a tier's ``call_fn`` does, so one provider's
  several keys share the load before the chain moves to the
  next tier.
"""

from __future__ import annotations

import contextlib
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from puspa.observability import metrics as observability_metrics

from .fallback import (
    AuthError,
    CallResult,
    NonRetryableError,
    ProviderError,
    ProviderFailure,
    QuotaExhaustedError,
    RateLimited,
    classify_failure,
)
from .quota import QuotaStore

__all__ = [
    "ALL_STRATEGIES",
    "DEFAULT_FAIL_COOLDOWN_SECONDS",
    "MAX_EXTRA_BYOK_ACCOUNTS",
    "STRATEGY_FILL_FIRST",
    "STRATEGY_LRU",
    "STRATEGY_ROTATE",
    "WINDOW_5H_SECONDS",
    "WINDOW_WEEKLY_SECONDS",
    "AllAccountsFailed",
    "PoolAccount",
    "PoolAttempt",
    "PoolHop",
    "PoolResult",
    "ProviderPool",
    "build_pool_for_user",
    "call_with_pool",
    "discover_accounts",
    "pool_quota_bars",
    "provider_pool_for_user",
    "reset_provider_pools",
    "resolve_credential",
]

#: The three routing strategies (same names as gsb).
STRATEGY_ROTATE = "rotate"
STRATEGY_LRU = "lru"
STRATEGY_FILL_FIRST = "fill-first"
ALL_STRATEGIES = (STRATEGY_ROTATE, STRATEGY_LRU, STRATEGY_FILL_FIRST)

#: 5-hour quota window, in seconds (gsb's "5h" bucket).
WINDOW_5H_SECONDS = 18_000

#: 7-day quota window, in seconds (gsb's "weekly" bucket).
WINDOW_WEEKLY_SECONDS = 604_800

#: Default demotion after a failed account (gsb's fillFirstFailCooldown).
DEFAULT_FAIL_COOLDOWN_SECONDS = 60

#: Escalating cooldown after repeated quota exhaustion (gsb's
#: quotaExhaustionBackoff shape: 1m, 5m, 15m, 1h, capped).
_QUOTA_COOLDOWN_STEPS = (60, 300, 900, 3600)

#: How many extra numbered BYOK slots are probed per provider
#: (``byok:<provider>:2`` .. ``byok:<provider>:6``). The primary
#: key is slot 1 (``byok:<provider>``); a user with more keys than
#: this simply does not get the extras discovered.
MAX_EXTRA_BYOK_ACCOUNTS = 5

#: Quota-store window suffixes, one entry per account per window.
_WINDOW_5H = "5h"
_WINDOW_WEEKLY = "weekly"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _quota_key(account_id: str, window: str) -> str:
    """Quota-store key for one account's one window."""
    return f"{account_id}:{window}"


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


@dataclass
class PoolAccount:
    """One credential of one provider inside a pool.

    ``account_id`` is stable (``"<provider>:<slot>"``, e.g.
    ``"openrouter:1"``, ``"openrouter:2"``, ``"openrouter:oauth"``).
    ``kind`` is ``"byok"`` (API key) or ``"oauth"`` (subscription
    login). ``label`` is the display name for Settings (never a
    key value). ``auth_ref`` is how the credential is resolved at
    call time: the vault entry name for BYOK (``byok:<provider>``
    or ``byok:<provider>:<n>``), the provider name for OAuth. No
    credential value is ever stored here: ``__repr__`` shows only
    the ref name, never a secret.

    The remaining fields are runtime state, mutated only by the
    pool under its lock: ``fails`` counts consecutive failures,
    ``strikes`` counts consecutive quota exhaustions (drives the
    escalating cooldown), ``last_used`` is the last admission,
    ``cooling_until`` is when a demoted account becomes eligible
    again, ``req_timestamps`` is the sliding 60-second window for
    the per-account RPM limit, and ``active_streams`` is the
    number of calls currently in flight on this account.
    """

    account_id: str
    provider: str
    kind: str
    label: str
    auth_ref: str
    disabled: bool = False
    fails: int = 0
    strikes: int = 0
    last_used: datetime | None = None
    cooling_until: datetime | None = None
    req_timestamps: deque = field(default_factory=deque)
    active_streams: int = 0

    def __repr__(self) -> str:  # never leak a credential value
        return (
            f"PoolAccount(account_id={self.account_id!r}, provider={self.provider!r}, "
            f"kind={self.kind!r}, label={self.label!r}, auth_ref={self.auth_ref!r}, "
            f"disabled={self.disabled!r}, fails={self.fails!r})"
        )


# ---------------------------------------------------------------------------
# Results / failures
# ---------------------------------------------------------------------------


@dataclass
class PoolAttempt:
    """One account tried (or skipped) during a pool run."""

    account_id: str
    provider: str
    outcome: str  # "succeeded" | "failed" | "skipped"
    reason: str | None = None
    status: int | None = None
    cost_rp: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class PoolHop:
    """One recorded account switch: source account, destination, why."""

    from_account_id: str
    to_account_id: str
    reason: str
    status: int | None = None


@dataclass
class PoolResult:
    """A completed pool run: value, account used, full audit trail."""

    value: object
    account_used: PoolAccount
    attempts: list[PoolAttempt] = field(default_factory=list)
    hops: list[PoolHop] = field(default_factory=list)
    total_cost_rp: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    @property
    def accounts_tried(self) -> list[str]:
        return [attempt.account_id for attempt in self.attempts]


class AllAccountsFailed(Exception):
    """Every account in the pool failed (or was skipped as unusable).

    ``attempts`` and ``hops`` carry the full audit trail; the
    message lists each account with its reason, so the failure is
    reported, not swallowed.
    """

    def __init__(self, attempts: list[PoolAttempt], hops: list[PoolHop]) -> None:
        self.attempts = list(attempts)
        self.hops = list(hops)
        lines = ["all pool accounts failed:"]
        for attempt in attempts:
            lines.append(
                f"  {attempt.account_id}: {attempt.outcome}"
                + (f" - {attempt.reason}" if attempt.reason else "")
            )
        super().__init__("\n".join(lines))


# ---------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------


class ProviderPool:
    """Several accounts/keys for ONE provider, with quota-aware routing.

    ``now_fn`` is an injectable clock (tests pass a fake clock; the
    default is real UTC time). ``quota_store`` is the shared
    ``QuotaStore`` (a fresh one is created when none is given).
    ``max_rpm`` / ``max_concurrent`` are the per-account rate
    limits (0 = unlimited). ``fail_cooldown_seconds`` is the
    demotion after a transport failure. All methods are safe to
    call from several threads: one lock guards every mutation.
    """

    def __init__(
        self,
        provider: str,
        accounts: list[PoolAccount],
        *,
        strategy: str = STRATEGY_ROTATE,
        quota_store: QuotaStore | None = None,
        now_fn=None,
        max_rpm: int = 0,
        max_concurrent: int = 0,
        fail_cooldown_seconds: int = DEFAULT_FAIL_COOLDOWN_SECONDS,
    ) -> None:
        if not provider:
            raise ValueError("provider must not be empty")
        if strategy not in ALL_STRATEGIES:
            raise ValueError(
                f"unknown pool strategy {strategy!r} (known: {', '.join(ALL_STRATEGIES)})"
            )
        if not accounts:
            raise ValueError("a pool needs at least one account")
        if len({account.account_id for account in accounts}) != len(accounts):
            raise ValueError("pool account ids must be unique")
        for account in accounts:
            if account.provider != provider:
                raise ValueError(
                    f"account {account.account_id!r} belongs to provider "
                    f"{account.provider!r}, not {provider!r}"
                )
            if account.kind not in ("byok", "oauth"):
                raise ValueError(
                    f"account {account.account_id!r} has unknown kind {account.kind!r}"
                )
        if max_rpm < 0 or max_concurrent < 0:
            raise ValueError("max_rpm and max_concurrent must not be negative")
        if fail_cooldown_seconds < 0:
            raise ValueError("fail_cooldown_seconds must not be negative")
        self.provider = provider
        self.accounts = list(accounts)
        self.strategy = strategy
        self.quota_store = quota_store if quota_store is not None else QuotaStore(now_fn=now_fn)
        self._now_fn = now_fn or _utcnow
        self.max_rpm = max_rpm
        self.max_concurrent = max_concurrent
        self.fail_cooldown_seconds = fail_cooldown_seconds
        self._lock = threading.Lock()
        # Sticky rotation cursor (rotate strategy): the account id
        # the next pick starts walking from.
        self._rotate_cursor_id: str | None = None

    def __repr__(self) -> str:
        with self._lock:
            ids = [account.account_id for account in self.accounts]
        return (
            f"ProviderPool(provider={self.provider!r}, strategy={self.strategy!r}, "
            f"accounts={ids!r})"
        )

    def now(self) -> datetime:
        return self._now_fn()

    # -- quota registration --------------------------------------------

    def register_quota(
        self,
        account_id: str,
        *,
        five_hour_tokens: int | None = None,
        weekly_tokens: int | None = None,
        rp_per_1k_input: int = 0,
        rp_per_1k_output: int = 0,
    ) -> None:
        """Register quota windows for one account.

        Limits must come from the provider's actual plan; they are
        required per window precisely so no limit is invented. A
        window left as None stays unregistered (untracked, and the
        account is treated as available in that window). Rates
        default to 0 (cost unknown) — pass the catalog rates when
        the account's model is known.
        """
        account = self._account_or_raise(account_id)
        if five_hour_tokens is not None:
            self.quota_store.register(
                _quota_key(account.account_id, _WINDOW_5H),
                tier="pool",
                limit_tokens=five_hour_tokens,
                reset_after_seconds=WINDOW_5H_SECONDS,
                rp_per_1k_input=rp_per_1k_input,
                rp_per_1k_output=rp_per_1k_output,
            )
        if weekly_tokens is not None:
            self.quota_store.register(
                _quota_key(account.account_id, _WINDOW_WEEKLY),
                tier="pool",
                limit_tokens=weekly_tokens,
                reset_after_seconds=WINDOW_WEEKLY_SECONDS,
                rp_per_1k_input=rp_per_1k_input,
                rp_per_1k_output=rp_per_1k_output,
            )

    # -- eligibility (preflight) ----------------------------------------

    def skip_reason(self, account: PoolAccount, now: datetime | None = None) -> str | None:
        """Why ``account`` cannot serve a request now, or None if ready.

        The preflight: disabled, cooling after a failure, quota
        exhausted in either window, or over the per-account rate
        limit. An account with no registered quota is ready in
        that window (unknown is not exhaustion).
        """
        moment = now or self.now()
        if account.disabled:
            return "disabled"
        if account.cooling_until is not None and moment < account.cooling_until:
            return "cooling"
        for window in (_WINDOW_5H, _WINDOW_WEEKLY):
            if self.quota_store.is_exhausted(_quota_key(account.account_id, window)):
                return "quota_exhausted"
        if self._rate_limited_locked(account, moment):
            return "rate_limited"
        return None

    def is_available(self, account: PoolAccount, now: datetime | None = None) -> bool:
        """True when preflight passes for ``account``."""
        return self.skip_reason(account, now) is None

    def available_accounts(self) -> list[PoolAccount]:
        """Accounts that pass preflight right now, in pool order."""
        now = self.now()
        with self._lock:
            return [a for a in self.accounts if self.skip_reason(a, now) is None]

    # -- selection ------------------------------------------------------

    def select(self) -> PoolAccount | None:
        """Pick the next account per the pool strategy, or None.

        Preflight runs first: only ready accounts are candidates.
        Rotate stays on the sticky cursor while it is ready; lru
        takes the fewest failures then the least recently used;
        fill-first takes the first ready account in pool order.
        A rotate pick moves the cursor to the picked account.
        Selection alone records no usage and consumes no RPM
        budget; admission happens in ``call_with_pool``.
        """
        now = self.now()
        with self._lock:
            ready = [a for a in self.accounts if self.skip_reason(a, now) is None]
            if not ready:
                return None
            if self.strategy == STRATEGY_ROTATE:
                picked = self._pick_rotate_locked(ready)
            elif self.strategy == STRATEGY_LRU:
                picked = self._pick_lru_locked(ready)
            else:  # fill-first
                picked = ready[0]
            if self.strategy == STRATEGY_ROTATE and picked is not None:
                self._rotate_cursor_id = picked.account_id
            return picked

    def _pick_rotate_locked(self, ready: list[PoolAccount]) -> PoolAccount:
        """Walk from the sticky cursor; first ready account wins."""
        if self._rotate_cursor_id is not None:
            start = next(
                (i for i, a in enumerate(self.accounts) if a.account_id == self._rotate_cursor_id),
                0,
            )
        else:
            start = 0
        ordered = [
            self.accounts[(start + i) % len(self.accounts)] for i in range(len(self.accounts))
        ]
        for account in ordered:
            if account in ready:
                return account
        return ready[0]

    def _pick_lru_locked(self, ready: list[PoolAccount]) -> PoolAccount:
        """Fewest failures, then least recently used, then pool order."""

        def key(item: tuple[int, PoolAccount]) -> tuple:
            index, account = item
            last = account.last_used or datetime.min.replace(tzinfo=UTC)
            return (account.fails, last, index)

        return min(enumerate(ready), key=key)[1]

    # -- admission + rate limit ------------------------------------------

    def _rate_limited_locked(self, account: PoolAccount, now: datetime) -> bool:
        """True when the per-account RPM / concurrency limit is hit.

        Pure check: records nothing. The sliding 60-second window
        prunes timestamps older than a minute first.
        """
        if self.max_concurrent > 0 and account.active_streams >= self.max_concurrent:
            return True
        if self.max_rpm <= 0:
            return False
        cutoff = now - timedelta(seconds=60)
        return sum(1 for ts in account.req_timestamps if ts > cutoff) >= self.max_rpm

    def _admit_locked(self, account: PoolAccount, now: datetime) -> bool:
        """Record one request on ``account``; False when rate limited.

        Called by the executor just before the call, so the RPM
        budget and the in-flight count reflect real calls, not
        selections. The caller releases the stream in a finally.
        """
        if self._rate_limited_locked(account, now):
            return False
        account.req_timestamps.append(now)
        account.active_streams += 1
        account.last_used = now
        return True

    def _release_locked(self, account: PoolAccount) -> None:
        account.active_streams = max(0, account.active_streams - 1)

    def _requests_last_minute_locked(self, account: PoolAccount, now: datetime) -> int:
        cutoff = now - timedelta(seconds=60)
        return sum(1 for ts in account.req_timestamps if ts > cutoff)

    # -- failure bookkeeping ---------------------------------------------

    def _note_failure_locked(
        self, account: PoolAccount, failure: ProviderFailure, now: datetime
    ) -> None:
        """Cool a failed account and record its strike (quota only)."""
        account.fails += 1
        if failure.kind == "quota_exhausted":
            account.strikes += 1
            step = _QUOTA_COOLDOWN_STEPS[min(account.strikes - 1, len(_QUOTA_COOLDOWN_STEPS) - 1)]
            account.cooling_until = now + timedelta(seconds=step)
        else:
            account.cooling_until = now + timedelta(seconds=self.fail_cooldown_seconds)

    def _note_success_locked(self, account: PoolAccount) -> None:
        account.fails = 0
        account.strikes = 0
        account.cooling_until = None

    def _record_usage_locked(
        self, account: PoolAccount, input_tokens: int, output_tokens: int
    ) -> int:
        """Record one completed call in both windows; return its Rp cost."""
        cost = 0
        for window in (_WINDOW_5H, _WINDOW_WEEKLY):
            recorded = self.quota_store.record_usage(
                _quota_key(account.account_id, window), input_tokens, output_tokens
            )
            if recorded is not None:
                cost = recorded
        return cost

    # -- enable / disable -------------------------------------------------

    def disable(self, account_id: str) -> None:
        """Take an account out of rotation (operator action). Idempotent."""
        with self._lock:
            self._account_or_raise(account_id).disabled = True

    def enable(self, account_id: str) -> None:
        """Put an account back into rotation. Idempotent."""
        with self._lock:
            self._account_or_raise(account_id).disabled = False

    def _account_or_raise(self, account_id: str) -> PoolAccount:
        for account in self.accounts:
            if account.account_id == account_id:
                return account
        raise KeyError(f"no pool account {account_id!r} for provider {self.provider!r}")

    # -- quota bars (Settings) --------------------------------------------

    def _window_view(self, account: PoolAccount, window: str, now: datetime) -> dict:
        """One window's quota-bar data; untracked when unregistered."""
        quota = self.quota_store.get(_quota_key(account.account_id, window))
        if quota is None:
            return {
                "window": window,
                "limit_tokens": None,
                "tokens_used": None,
                "tokens_remaining": None,
                "used_fraction": None,
                "reset_in_seconds": None,
                "exhausted": False,
            }
        snapshot = quota.snapshot(now)
        limit = snapshot["limit_tokens"]
        used = snapshot["tokens_used"]
        return {
            "window": window,
            "limit_tokens": limit,
            "tokens_used": used,
            "tokens_remaining": snapshot["tokens_remaining"],
            "used_fraction": (used / limit) if limit else 0.0,
            "reset_in_seconds": snapshot["reset_in_seconds"],
            "exhausted": snapshot["exhausted"],
        }

    def snapshot_account(self, account: PoolAccount) -> dict:
        """Plain-data view of one account for Settings (no secrets)."""
        now = self.now()
        with self._lock:
            reason = self.skip_reason(account, now)
            return {
                "account_id": account.account_id,
                "provider": account.provider,
                "kind": account.kind,
                "label": account.label,
                "disabled": account.disabled,
                "available": reason is None,
                "skip_reason": reason,
                "rate_limited": self._rate_limited_locked(account, now),
                "cooling_until_in_seconds": (
                    max(0, int((account.cooling_until - now).total_seconds()))
                    if account.cooling_until is not None and now < account.cooling_until
                    else 0
                ),
                "fails": account.fails,
                "strikes": account.strikes,
                "last_used": account.last_used.isoformat() if account.last_used else None,
                "requests_last_minute": self._requests_last_minute_locked(account, now),
                "max_rpm": self.max_rpm,
                "max_concurrent": self.max_concurrent,
                "active_streams": account.active_streams,
                "windows": [
                    self._window_view(account, _WINDOW_5H, now),
                    self._window_view(account, _WINDOW_WEEKLY, now),
                ],
            }

    def snapshot(self) -> list[dict]:
        """One quota-bar dict per account, in pool order."""
        return [self.snapshot_account(account) for account in self.accounts]

    # -- execution ---------------------------------------------------------

    def call(self, call_fn, *, budget=None) -> PoolResult:
        """Run one request through the pool, failing over on retryable errors.

        ``call_fn(account)`` is the caller-supplied provider call
        (see the module docstring). It returns a ``CallResult``
        (value plus token counts) or a plain value (token counts
        unknown, cost 0), and raises ``ProviderFailure`` (or an
        exception carrying a ``status`` / ``status_code`` attribute)
        on failure. ``budget`` is a ``puspa.runtime.budget.
        BudgetTracker``; its ceiling is enforced, never dodged.

        Raises ``AllAccountsFailed`` when no account succeeds, the
        original ``ProviderFailure`` when an account fails
        deterministically, or ``BudgetExhausted`` when the task
        budget runs out.
        """
        return call_with_pool(self, call_fn, budget=budget)


def _failure_from_exception(exc: BaseException) -> ProviderFailure:
    """Normalize any ``call_fn`` failure into a ``ProviderFailure``.

    Same classification as the tiered fallback: a ``ProviderFailure``
    passes through, any other exception with a ``status`` /
    ``status_code`` attribute is classified from it, and any other
    exception is a transport-level provider error.
    """
    if isinstance(exc, ProviderFailure):
        return exc
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        kind = classify_failure(status, str(exc))
        if kind == "non_retryable":
            return NonRetryableError(str(exc), status=status)
        if kind == "quota_exhausted":
            return QuotaExhaustedError(str(exc), status=status)
        if kind == "rate_limited":
            return RateLimited(str(exc))
        if kind == "auth_error":
            return AuthError(str(exc), status=status)
        return ProviderError(str(exc), status=status)
    return ProviderError(f"{type(exc).__name__}: {exc}")


def _observe_pool(provider: str, hops: int) -> None:
    """Record one pool run in the observability metrics (never raises)."""
    with contextlib.suppress(Exception):
        observability_metrics.get_metrics().record_fallback(f"pool:{provider}", hops=hops)


def call_with_pool(pool: ProviderPool, call_fn, *, budget=None) -> PoolResult:
    """Run one request through ``pool``, failing over on retryable errors.

    See :meth:`ProviderPool.call` for the contract. The executor
    preflights before every pick (an account that went exhausted
    since the last pick is skipped, never called) and admits the
    picked account (RPM timestamp + in-flight count) just before
    the call. A quota failure also marks the account's quota
    exhausted in the store, so the next preflight skips it even
    after its cooldown ends, until the window resets.
    """
    from puspa.runtime.budget import BudgetExhausted

    if budget is not None and budget.remaining_rupiah <= 0:
        raise BudgetExhausted(
            f"budget exhausted before any pool account was tried (ceiling Rp{budget.rupiah_max})"
        )

    attempts: list[PoolAttempt] = []
    hops: list[PoolHop] = []
    total_cost = 0
    total_in = 0
    total_out = 0
    tried: set[str] = set()

    def _hop(from_id: str, to_id: str, reason: str, status: int | None = None) -> None:
        if from_id != to_id:
            hops.append(
                PoolHop(from_account_id=from_id, to_account_id=to_id, reason=reason, status=status)
            )

    # Run-start preflight (mirrors the tiered fallback): every
    # account that cannot serve is recorded as skipped with its
    # own reason, and the chain hops through them in pool order,
    # so a run that never reaches a live account still has a
    # complete audit trail instead of silence.
    now = pool.now()
    previous_id: str | None = None
    previous_reason = "failover"
    with pool._lock:
        for account in pool.accounts:
            reason = pool.skip_reason(account, now)
            if reason is None:
                continue
            attempts.append(
                PoolAttempt(
                    account_id=account.account_id,
                    provider=account.provider,
                    outcome="skipped",
                    reason=reason,
                )
            )
            if previous_id is not None:
                _hop(previous_id, account.account_id, reason)
            previous_id = account.account_id
            previous_reason = reason

    while True:
        now = pool.now()
        with pool._lock:
            remaining = [
                a
                for a in pool.accounts
                if a.account_id not in tried and pool.skip_reason(a, now) is None
            ]
            if not remaining:
                break
            if pool.strategy == STRATEGY_ROTATE:
                picked = pool._pick_rotate_locked(remaining)
            elif pool.strategy == STRATEGY_LRU:
                picked = pool._pick_lru_locked(remaining)
            else:  # fill-first
                picked = remaining[0]
            if pool.strategy == STRATEGY_ROTATE:
                pool._rotate_cursor_id = picked.account_id
            admitted = pool._admit_locked(picked, now)
            if not admitted:  # lost the admission race: skip, try the next
                tried.add(picked.account_id)
                attempts.append(
                    PoolAttempt(
                        account_id=picked.account_id,
                        provider=picked.provider,
                        outcome="skipped",
                        reason="rate_limited",
                    )
                )
                if previous_id is not None:
                    _hop(previous_id, picked.account_id, "rate_limited")
                previous_id = picked.account_id
                previous_reason = "rate_limited"
                continue
        tried.add(picked.account_id)
        if previous_id is not None:
            _hop(previous_id, picked.account_id, previous_reason)
        previous_id = picked.account_id
        previous_reason = "failover"

        try:
            raw = call_fn(picked)
        except BaseException as exc:  # failure of this account, not of the run
            failure = _failure_from_exception(exc)
            now = pool.now()
            with pool._lock:
                pool._release_locked(picked)
                pool._note_failure_locked(picked, failure, now)
                if failure.kind in ("rate_limited", "quota_exhausted"):
                    for window in (_WINDOW_5H, _WINDOW_WEEKLY):
                        pool.quota_store.apply_rate_limit(
                            _quota_key(picked.account_id, window),
                            retry_after_seconds=failure.retry_after_seconds,
                            reason=failure.kind,
                        )
            attempts.append(
                PoolAttempt(
                    account_id=picked.account_id,
                    provider=picked.provider,
                    outcome="failed",
                    reason=failure.kind,
                    status=failure.status,
                )
            )
            if not failure.retryable:
                failure.pool_attempts = list(attempts)  # type: ignore[attr-defined]
                _observe_pool(pool.provider, len(hops))
                raise failure from exc
            previous_reason = failure.kind
            continue

        if isinstance(raw, CallResult):
            value = raw.value
            input_tokens, output_tokens = raw.input_tokens, raw.output_tokens
        else:
            value = raw
            input_tokens, output_tokens = 0, 0
        with pool._lock:
            cost_rp = pool._record_usage_locked(picked, input_tokens, output_tokens)
            pool._note_success_locked(picked)
            pool._release_locked(picked)
        attempts.append(
            PoolAttempt(
                account_id=picked.account_id,
                provider=picked.provider,
                outcome="succeeded",
                cost_rp=cost_rp,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        total_cost += cost_rp
        total_in += input_tokens
        total_out += output_tokens
        if budget is not None:
            budget.record_cost(cost_rp, input_tokens + output_tokens)
        _observe_pool(pool.provider, len(hops))
        return PoolResult(
            value=value,
            account_used=picked,
            attempts=attempts,
            hops=hops,
            total_cost_rp=total_cost,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
        )

    _observe_pool(pool.provider, len(hops))
    raise AllAccountsFailed(attempts, hops)


# ---------------------------------------------------------------------------
# Credential resolution + per-user discovery
# ---------------------------------------------------------------------------


def resolve_credential(account: PoolAccount, *, user_id, dsn=None, oauth_refresher=None) -> str:
    """Resolve the credential for one pool account, at call time only.

    BYOK: read the vault entry named by ``account.auth_ref``
    (``byok:<provider>`` or a numbered slot). OAuth: load the
    user's token set for the provider from the vault-backed
    ``VaultTokenStore`` and return a valid access token through
    the shared refresher (proactive refresh, same as
    ``puspa/router/router.get_valid_oauth_token``). Raises
    ``KeyError`` when no BYOK key is stored and
    ``TokenRefreshError`` when no usable OAuth token exists. The
    returned value is for the provider call only: never log it,
    never store it, never put it in a snapshot.
    """
    if account.kind == "byok":
        from puspa.accounts.vault import get as vault_get

        try:
            return vault_get(user_id, account.auth_ref, dsn=dsn)
        except KeyError as exc:
            raise KeyError(
                f"no BYOK key stored for pool account {account.account_id!r}: "
                "save one in Settings first (it lives in the vault only)"
            ) from exc
    if account.kind == "oauth":
        from .provider_login import VaultTokenStore

        refresher = oauth_refresher if oauth_refresher is not None else _shared_refresher()
        stored = VaultTokenStore(dsn=dsn).load(user_id, account.provider)
        if stored is not None and refresher.store.get(account.provider) is None:
            refresher.store.put(account.provider, stored)
        return refresher.get_valid_token(account.provider)
    raise ValueError(f"unknown pool account kind {account.kind!r}")


def _shared_refresher():
    """The router's shared OAuth refresher (same one as router.py)."""
    from .router import oauth_refresher

    return oauth_refresher()


def discover_accounts(provider: str, user_id, dsn=None) -> list[PoolAccount]:
    """Discover a user's credentials for ``provider`` as pool accounts.

    Sources, in pool order (official providers only):

    - the primary BYOK key (vault ``byok:<provider>``) -> slot 1;
    - numbered extra BYOK keys (vault ``byok:<provider>:2`` ..
      ``byok:<provider>:6``) -> slots 2..6;
    - an OAuth subscription login (vault ``byok:oauth:<provider>``,
      via ``VaultTokenStore``) -> the ``oauth`` slot.

    Only existence is probed here (a vault read that raises
    ``KeyError`` means "no credential"); no key or token value is
    ever read into this module. Returns an empty list when the
    user has no credential for the provider at all.
    """
    from puspa.accounts.vault import get as vault_get

    from .provider_login import VaultTokenStore

    provider = provider.strip().lower()
    if not provider:
        raise ValueError("provider must not be empty")
    accounts: list[PoolAccount] = []

    def _has_vault_entry(name: str) -> bool:
        try:
            vault_get(user_id, name, dsn=dsn)
        except (KeyError, Exception):
            return False
        return True

    if _has_vault_entry(f"byok:{provider}"):
        accounts.append(
            PoolAccount(
                account_id=f"{provider}:1",
                provider=provider,
                kind="byok",
                label=f"{provider} key 1",
                auth_ref=f"byok:{provider}",
            )
        )
    for slot in range(2, 2 + MAX_EXTRA_BYOK_ACCOUNTS):
        if _has_vault_entry(f"byok:{provider}:{slot}"):
            accounts.append(
                PoolAccount(
                    account_id=f"{provider}:{slot}",
                    provider=provider,
                    kind="byok",
                    label=f"{provider} key {slot}",
                    auth_ref=f"byok:{provider}:{slot}",
                )
            )
    if VaultTokenStore(dsn=dsn).load(user_id, provider) is not None:
        accounts.append(
            PoolAccount(
                account_id=f"{provider}:oauth",
                provider=provider,
                kind="oauth",
                label=f"{provider} subscription",
                auth_ref=provider,
            )
        )
    return accounts


def build_pool_for_user(
    provider: str,
    user_id,
    *,
    strategy: str = STRATEGY_ROTATE,
    dsn=None,
    quota_store: QuotaStore | None = None,
    now_fn=None,
    max_rpm: int = 0,
    max_concurrent: int = 0,
) -> ProviderPool | None:
    """Build a user's pool for ``provider`` from discovered credentials.

    Returns None when the user has no credential for the provider
    (no account invented). Quota limits are NOT registered here:
    they must come from the provider's actual plan via
    ``ProviderPool.register_quota``; until then the accounts are
    untracked and treated as available.
    """
    accounts = discover_accounts(provider, user_id, dsn=dsn)
    if not accounts:
        return None
    return ProviderPool(
        provider.strip().lower(),
        accounts,
        strategy=strategy,
        quota_store=quota_store,
        now_fn=now_fn,
        max_rpm=max_rpm,
        max_concurrent=max_concurrent,
    )


# ---------------------------------------------------------------------------
# Process-wide pool registry (Settings reads the same pool the router
# would use, so quota state is shared, not rebuilt per request)
# ---------------------------------------------------------------------------

#: (user_id, provider, strategy) -> pool. Same singleton shape as
#: the shared OAuth refresher in ``puspa/router/router.py``.
_POOLS: dict[tuple[str, str, str], ProviderPool] = {}
_POOLS_LOCK = threading.Lock()


def provider_pool_for_user(
    provider: str,
    user_id,
    *,
    strategy: str = STRATEGY_ROTATE,
    dsn=None,
    quota_store: QuotaStore | None = None,
    now_fn=None,
    max_rpm: int = 0,
    max_concurrent: int = 0,
) -> ProviderPool | None:
    """The cached pool for one user + provider (built on first use).

    Returns None when the user has no credential for the provider.
    The cached pool keeps its quota state across calls, so a quota
    bar read in Settings reflects the same usage the router sees.
    """
    key = (str(user_id), provider.strip().lower(), strategy)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is not None:
            return pool
    pool = build_pool_for_user(
        provider,
        user_id,
        strategy=strategy,
        dsn=dsn,
        quota_store=quota_store,
        now_fn=now_fn,
        max_rpm=max_rpm,
        max_concurrent=max_concurrent,
    )
    if pool is None:
        return None
    with _POOLS_LOCK:
        return _POOLS.setdefault(key, pool)


def pool_quota_bars(provider: str, user_id, *, dsn=None) -> dict:
    """Quota-bar data for Settings: one dict per account of the provider.

    Returns ``{"provider", "strategy", "accounts": [...]}`` where
    each account dict is ``ProviderPool.snapshot_account`` output
    (used fraction and remaining tokens per 5h / weekly window,
    reset countdown, availability). Empty ``accounts`` when the
    user has no credential for the provider. No credential
    material is ever in the result.
    """
    pool = provider_pool_for_user(provider, user_id, dsn=dsn)
    if pool is None:
        return {
            "provider": provider.strip().lower(),
            "strategy": STRATEGY_ROTATE,
            "accounts": [],
        }
    return {"provider": pool.provider, "strategy": pool.strategy, "accounts": pool.snapshot()}


def reset_provider_pools() -> None:
    """Drop every cached pool (tests; a fresh process starts empty)."""
    with _POOLS_LOCK:
        _POOLS.clear()
