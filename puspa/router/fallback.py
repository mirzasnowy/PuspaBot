"""Smart 3-tier fallback with quota-aware routing.

Pattern studied, then rewritten as Puspa's own code (see docs/reuse.md):
9Router (decolua/9router, MIT) chains providers into a combo ordered
subscription -> cheap -> free, and falls through to the next tier
automatically when the current tier runs out of quota or errors.
Only the PATTERN is taken: no 9Router code is copied, and no 9Router
sub-project is used here. The tiers below are Puspa's own construction
over its router catalog (``puspa/router/router.py``).

Tier order and meaning
-----------------------
- ``subscription``: the role's best model, drained first. 9Router
  drains a subscription first because it is already paid for. Puspa
  has no flat-rate subscription provider wired into its catalog yet
  (no Claude Code / Codex style OAuth subscription); the slot is
  filled by the role's best model, and a real subscription provider
  is a follow-up seam: register it with its allowance as
  ``limit_tokens`` and its billing period as the reset window.
- ``cheap``: a mid-cost model, the paid backup.
- ``free``: the cheapest model in the role's candidate set, the last
  resort. It is near-free (Rp1-3 per 1k tokens), not literally free:
  Puspa has no zero-cost provider wired yet, and this module never
  claims otherwise.

A chain is built from the role's candidate set by cost spread: the
most expensive candidate, the median-cost candidate, and the cheapest
candidate, de-duplicated. The order is always expensive first,
cheapest last, so a fallback never moves to a more expensive tier.

Failure handling (auditable, never silent)
-------------------------------------------
Every tier switch is recorded as a ``FallbackHop`` with the source
tier, the destination tier, the reason, and the provider status.
Every attempt is recorded in ``FallbackAttempt``. When every tier
fails, ``AllTiersFailed`` is raised with the full attempt list in its
message: a silent failure is impossible by construction.

Only retryable failures fall through: 429 (rate limited), a quota
exhaustion signal, 5xx, or a transport error with no status. A
deterministic failure (any other 4xx, for example a bad request)
raises immediately without trying the next tier, because the same
request would fail there too.

Budget (revision 7 section 2.4) is never dodged by a fallback. When
a ``BudgetTracker`` is passed, a successful call's Rupiah cost is
charged to it; when the charge would exceed the per-task ceiling
(Rp2.000 by default), ``BudgetExhausted`` is raised and no cheaper
tier is tried. When the budget is already exhausted before the first
attempt, nothing is called at all. The ceiling itself is not changed
here and no fallback path can raise it.

Transport seam
--------------
``call_with_fallback`` does not call any provider itself. It takes a
``call_fn(entry)`` supplied by the caller: the caller adapts a real
provider call (for example a nanobot provider built by
``router.build_provider``) to this signature. ``call_fn`` returns a
``CallResult`` (value plus token counts) or a plain value (token
counts unknown, cost 0), and raises ``ProviderFailure`` (or an
exception carrying a ``status`` / ``status_code`` attribute) on
failure. Wiring a live nanobot provider call into ``call_fn`` is a
follow-up: the router has no live caller today (nothing in
``puspa/`` calls ``build_provider`` yet), so a live adapter cannot be
proven here without inventing a call path.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .quota import QuotaStore
from .router import CATALOG, ChoiceModel, compute_cost

__all__ = [
    "TIERS",
    "TIER_CHEAP",
    "TIER_FREE",
    "TIER_SUBSCRIPTION",
    "AllTiersFailed",
    "CallResult",
    "ChainEntry",
    "FallbackAttempt",
    "FallbackHop",
    "FallbackResult",
    "NonRetryableError",
    "ProviderError",
    "ProviderFailure",
    "QuotaExhaustedError",
    "RateLimited",
    "build_chain",
    "call_with_fallback",
    "choose_with_quota",
    "classify_failure",
    "seed_chain_quotas",
]

TIER_SUBSCRIPTION = "subscription"
TIER_CHEAP = "cheap"
TIER_FREE = "free"
TIERS = (TIER_SUBSCRIPTION, TIER_CHEAP, TIER_FREE)

#: HTTP statuses that mean "this tier cannot serve the request now".
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


def classify_failure(status: int | None, message: str = "") -> str:
    """Classify a provider failure from its status and message.

    Returns one of ``rate_limited``, ``quota_exhausted``,
    ``provider_error``, ``non_retryable``. The message is only used to
    tell a quota signal apart from a plain rate limit; a status on
    its own decides the retryable / non-retryable split.
    """
    text = (message or "").lower()
    quota_words = ("quota", "insufficient_quota", "exhausted", "allowance", "usage limit", "credit")
    if status == 429:
        if any(word in text for word in quota_words):
            return "quota_exhausted"
        return "rate_limited"
    if status in (402, 403) and any(word in text for word in quota_words):
        return "quota_exhausted"
    if status is None or status >= 500:
        return "provider_error"
    if status == 429:
        return "rate_limited"
    if any(word in text for word in ("quota", "insufficient_quota")):
        return "quota_exhausted"
    if "rate limit" in text or "too many requests" in text:
        return "rate_limited"
    return "non_retryable"


class ProviderFailure(Exception):
    """One tier failed to serve a request.

    ``status`` is the provider HTTP status when known (None for a
    transport error). ``retry_after_seconds`` carries a Retry-After
    value when the provider sent one. ``attempts`` is attached by
    ``call_with_fallback`` to the failure it finally raises.
    """

    def __init__(
        self, message: str, *, status: int | None = None, retry_after_seconds: int | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        self.kind = classify_failure(status, message)
        self.attempts: list[FallbackAttempt] = []

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate_limited", "quota_exhausted", "provider_error")

    def __str__(self) -> str:
        status = f"status {self.status}" if self.status is not None else "no status"
        return f"{self.message} ({status}, {self.kind})"


class RateLimited(ProviderFailure):
    """HTTP 429: the tier is rate limited right now."""

    def __init__(
        self, message: str = "rate limited", *, retry_after_seconds: int | None = None
    ) -> None:
        super().__init__(message, status=429, retry_after_seconds=retry_after_seconds)


class QuotaExhaustedError(ProviderFailure):
    """The tier's quota is exhausted (429/402/403 with a quota signal)."""

    def __init__(
        self,
        message: str = "quota exhausted",
        *,
        status: int | None = 429,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message, status=status, retry_after_seconds=retry_after_seconds)


class ProviderError(ProviderFailure):
    """A 5xx or transport failure: the tier itself is broken."""

    def __init__(
        self,
        message: str = "provider error",
        *,
        status: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message, status=status, retry_after_seconds=retry_after_seconds)


class NonRetryableError(ProviderFailure):
    """A deterministic failure (bad request, unknown model, ...).

    Falling through to the next tier would fail the same way, so the
    executor raises this immediately instead of trying other tiers.
    """

    def __init__(self, message: str = "request rejected", *, status: int | None = 400) -> None:
        super().__init__(message, status=status)


class AllTiersFailed(Exception):
    """Every tier in the chain failed (or was skipped as exhausted).

    ``attempts`` and ``hops`` carry the full audit trail; the message
    lists each tier with its reason, so the failure is reported, not
    swallowed.
    """

    def __init__(self, attempts: list[FallbackAttempt], hops: list[FallbackHop]) -> None:
        self.attempts = list(attempts)
        self.hops = list(hops)
        lines = ["all tiers failed:"]
        for attempt in attempts:
            lines.append(
                f"  {attempt.tier} ({attempt.provider}/{attempt.model}): "
                f"{attempt.outcome}" + (f" - {attempt.reason}" if attempt.reason else "")
            )
        super().__init__("\n".join(lines))


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainEntry:
    """One tier of a fallback chain: a tier label plus a router choice."""

    tier: str
    provider: str
    model: str
    family: str
    rp_per_1k_input: int
    rp_per_1k_output: int
    vision: bool

    @property
    def provider_key(self) -> str:
        """Quota-store key for this entry: ``provider:model``."""
        return f"{self.provider}:{self.model}"

    @property
    def choice(self) -> ChoiceModel:
        return ChoiceModel(
            provider=self.provider,
            model=self.model,
            family=self.family,
            rp_per_1k_input=self.rp_per_1k_input,
            rp_per_1k_output=self.rp_per_1k_output,
            vision=self.vision,
        )

    def estimate_cost_rp(self, input_tokens: int, output_tokens: int) -> int:
        return compute_cost(self.choice, input_tokens, output_tokens)


def _role_candidates(role: str, creator_family: str | None = None):
    """Candidate catalog rows for a role, mirroring ``router.choose``."""
    if role in ("perencana", "penyaji"):
        return [row for row in CATALOG if row[6]]
    if role in ("pembaca", "pencari"):
        return list(CATALOG)
    if role == "pemeriksa":
        rows = (
            [row for row in CATALOG if row[2] != creator_family]
            if creator_family
            else list(CATALOG)
        )
        return rows or list(CATALOG)
    if role == "vision":
        return [row for row in CATALOG if row[5]]
    raise ValueError(f"unknown role: {role!r}")


def build_chain(role: str, *, creator_family: str | None = None) -> list[ChainEntry]:
    """Build the fallback chain for a role, ordered subscription first.

    Takes the role's candidate models, sorts them by total rate
    (input + output Rp per 1k tokens) most expensive first, and picks
    three spread positions: first, median, last. The result is always
    ordered expensive -> cheap, distinct models only, and at most one
    entry per tier. A role with fewer than three candidates yields a
    shorter chain; the tier labels still follow the same order.
    """
    candidates = _role_candidates(role, creator_family)
    if not candidates:
        if role == "vision":
            return []
        raise ValueError(f"no candidates for role {role!r}")
    by_cost = sorted(
        candidates,
        key=lambda row: row[3] + row[4],
        reverse=True,
    )
    positions = [0, len(by_cost) // 2, len(by_cost) - 1]
    seen: set[str] = set()
    picked: list[tuple] = []
    for position in positions:
        row = by_cost[position]
        if row[1] not in seen:
            seen.add(row[1])
            picked.append(row)
    chain: list[ChainEntry] = []
    for tier, row in zip(TIERS, picked):
        chain.append(
            ChainEntry(
                tier=tier,
                provider=row[0],
                model=row[1],
                family=row[2],
                rp_per_1k_input=row[3],
                rp_per_1k_output=row[4],
                vision=row[5],
            )
        )
    return chain


def choose_with_quota(
    role: str, quota_store: QuotaStore, *, creator_family: str | None = None
) -> ChainEntry | None:
    """Quota-aware routing: first chain tier whose quota is not exhausted.

    A tier with no registered quota counts as available (unknown is
    not exhaustion). Returns None only when every tier is exhausted.
    """
    for entry in build_chain(role, creator_family=creator_family):
        if not quota_store.is_exhausted(entry.provider_key):
            return entry
    return None


def seed_chain_quotas(
    quota_store: QuotaStore, chain: list[ChainEntry], *, limit_tokens: int, reset_after_seconds: int
) -> None:
    """Register the same quota window for every entry of a chain.

    The limit and window must come from the provider's actual plan;
    they are required arguments precisely so no limit is invented.
    """
    for entry in chain:
        quota_store.register(
            entry.provider_key,
            tier=entry.tier,
            limit_tokens=limit_tokens,
            reset_after_seconds=reset_after_seconds,
            rp_per_1k_input=entry.rp_per_1k_input,
            rp_per_1k_output=entry.rp_per_1k_output,
        )


# ---------------------------------------------------------------------------
# Execution with fallback
# ---------------------------------------------------------------------------


@dataclass
class CallResult:
    """What ``call_fn`` returns on success: a value plus token counts."""

    value: object
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class FallbackAttempt:
    """One tier tried (or skipped) during a fallback run."""

    tier: str
    provider: str
    model: str
    outcome: str  # "succeeded" | "failed" | "skipped"
    reason: str | None = None
    status: int | None = None
    cost_rp: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class FallbackHop:
    """One recorded tier switch: source tier, destination tier, why."""

    from_tier: str
    to_tier: str
    reason: str
    status: int | None = None


@dataclass
class FallbackResult:
    """A completed fallback run: value, tier used, full audit trail."""

    value: object
    tier_used: ChainEntry
    attempts: list[FallbackAttempt] = field(default_factory=list)
    hops: list[FallbackHop] = field(default_factory=list)
    total_cost_rp: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    @property
    def tiers_tried(self) -> list[str]:
        return [attempt.tier for attempt in self.attempts]


def _failure_from_exception(exc: BaseException) -> ProviderFailure:
    """Normalize any ``call_fn`` failure into a ``ProviderFailure``.

    A ``ProviderFailure`` passes through. Any other exception with a
    ``status`` / ``status_code`` attribute is classified from it. Any
    other exception is treated as a transport-level provider error:
    the call never reached a model that could reject it
    deterministically, so falling through is safe.
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
        return ProviderError(str(exc), status=status)
    return ProviderError(f"{type(exc).__name__}: {exc}")


def call_with_fallback(
    chain: list[ChainEntry], call_fn, *, quota_store: QuotaStore | None = None, budget=None
) -> FallbackResult:
    """Run one request through the chain, falling through on failure.

    ``call_fn(entry)`` is the caller-supplied provider call (see the
    module docstring). ``quota_store``, when given, is consulted
    before each tier (exhausted tiers are skipped, never called) and
    updated after each success and each rate-limit failure.
    ``budget`` is a ``puspa.runtime.budget.BudgetTracker``; its
    ceiling is enforced, never dodged (see the module docstring).

    Raises ``AllTiersFailed`` when no tier succeeds, the original
    ``ProviderFailure`` when a tier fails deterministically, or
    ``BudgetExhausted`` when the task budget runs out.
    """
    from puspa.runtime.budget import BudgetExhausted

    if not chain:
        raise ValueError("fallback chain is empty")
    if budget is not None and budget.remaining_rupiah <= 0:
        raise BudgetExhausted(
            f"budget exhausted before any tier was tried (ceiling Rp{budget.rupiah_max})"
        )

    attempts: list[FallbackAttempt] = []
    hops: list[FallbackHop] = []
    total_cost = 0
    total_in = 0
    total_out = 0

    for index, entry in enumerate(chain):
        next_entry = chain[index + 1] if index + 1 < len(chain) else None

        if quota_store is not None and quota_store.is_exhausted(entry.provider_key):
            attempts.append(
                FallbackAttempt(
                    tier=entry.tier,
                    provider=entry.provider,
                    model=entry.model,
                    outcome="skipped",
                    reason="quota_exhausted",
                )
            )
            if next_entry is not None:
                hops.append(
                    FallbackHop(
                        from_tier=entry.tier,
                        to_tier=next_entry.tier,
                        reason="quota_exhausted",
                    )
                )
            continue

        try:
            raw = call_fn(entry)
        except BaseException as exc:  # failure of this tier, not of the run
            failure = _failure_from_exception(exc)
            attempts.append(
                FallbackAttempt(
                    tier=entry.tier,
                    provider=entry.provider,
                    model=entry.model,
                    outcome="failed",
                    reason=failure.kind,
                    status=failure.status,
                )
            )
            if failure.kind in ("rate_limited", "quota_exhausted") and quota_store is not None:
                quota_store.apply_rate_limit(
                    entry.provider_key,
                    retry_after_seconds=failure.retry_after_seconds,
                    reason=failure.kind,
                )
            if not failure.retryable:
                failure.attempts = list(attempts)
                raise failure from exc
            if next_entry is not None:
                hops.append(
                    FallbackHop(
                        from_tier=entry.tier,
                        to_tier=next_entry.tier,
                        reason=failure.kind,
                        status=failure.status,
                    )
                )
            continue

        if isinstance(raw, CallResult):
            value = raw.value
            input_tokens, output_tokens = raw.input_tokens, raw.output_tokens
        else:
            value = raw
            input_tokens, output_tokens = 0, 0
        cost_rp = entry.estimate_cost_rp(input_tokens, output_tokens)
        attempts.append(
            FallbackAttempt(
                tier=entry.tier,
                provider=entry.provider,
                model=entry.model,
                outcome="succeeded",
                cost_rp=cost_rp,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        if quota_store is not None:
            quota_store.record_usage(entry.provider_key, input_tokens, output_tokens)
        total_cost += cost_rp
        total_in += input_tokens
        total_out += output_tokens
        if budget is not None:
            # Charged after the call, from measured tokens. A charge
            # that would exceed the ceiling raises BudgetExhausted
            # and the run stops here: no cheaper tier is tried to
            # dodge the ceiling.
            budget.record_cost(cost_rp, input_tokens + output_tokens)
        return FallbackResult(
            value=value,
            tier_used=entry,
            attempts=attempts,
            hops=hops,
            total_cost_rp=total_cost,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
        )

    raise AllTiersFailed(attempts, hops)
