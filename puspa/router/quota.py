"""Real-time quota tracking per provider.

Pattern studied, then rewritten as Puspa's own code (see docs/reuse.md):
9Router (decolua/9router, MIT) tracks token consumption per provider,
a reset countdown (5-hour, daily, weekly windows), and a cost estimate
per provider, and routes around a provider whose quota is exhausted.
Only the PATTERN is taken: no 9Router code is copied, and no 9Router
sub-project (RTK, Headroom, Ponytail) is used here. Puspa's quota
store is in-memory and provider-agnostic: a quota is registered
explicitly with the limit and reset window the operator knows from
the provider's plan, or learned from a 429 / rate-limit header. A
provider with no registered quota is treated as available, never as
exhausted: an unknown limit must not block a request.

Money: every cost here is integer Rupiah, the same unit as
``puspa.router.router.compute_cost`` and ``puspa.runtime.budget``.
The cost formula is the same one: ``(in * rp_in + out * rp_out) //
1000``. No second currency is introduced.

Persistence: this store is in-memory. Quota state does not survive
a process restart, and usage recorded here is not yet written to the
ledger. Persisting quota snapshots to Postgres (and reconciling them
with the ledger's per-task cost events) is a follow-up, not done here.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

__all__ = [
    "ProviderQuota",
    "QuotaStore",
]

#: Fallback block length (seconds) when a provider reports that its
#: quota is exhausted but gives no reset time and no Retry-After.
DEFAULT_BLOCK_SECONDS = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cost_rp(rp_per_1k_input: int, rp_per_1k_output: int,
             input_tokens: int, output_tokens: int) -> int:
    """Cost in Rupiah. Same formula as ``router.compute_cost``."""
    return (input_tokens * rp_per_1k_input
            + output_tokens * rp_per_1k_output) // 1000


@dataclass
class ProviderQuota:
    """Quota state for one provider entry (provider + model).

    ``limit_tokens`` is the token allowance for one reset window and
    ``reset_after_seconds`` is the window length in seconds (18000
    for a 5-hour window, 86400 for daily, 604800 for weekly, 0 for
    no automatic reset). ``reset_at`` is the end of the current
    window. ``blocked_until`` is set by a 429 / rate-limit response
    and blocks the provider until that time even inside its window.
    """

    provider_key: str
    tier: str
    limit_tokens: int
    reset_after_seconds: int
    rp_per_1k_input: int
    rp_per_1k_output: int
    reset_at: datetime | None = None
    tokens_used: int = 0
    requests_used: int = 0
    blocked_until: datetime | None = None
    # Last rate-limit signal seen, kept for audit (header values are
    # provider data, never a secret).
    last_rate_limit_reason: str | None = None

    def roll_window(self, now: datetime) -> bool:
        """Advance past windows that already ended; reset usage.

        Returns True when at least one window rolled over. A quota
        with no window (``reset_after_seconds`` 0) never rolls.
        """
        if self.reset_at is None or self.reset_after_seconds <= 0:
            return False
        rolled = False
        window = timedelta(seconds=self.reset_after_seconds)
        while now >= self.reset_at:
            self.reset_at = self.reset_at + window
            self.tokens_used = 0
            self.requests_used = 0
            self.blocked_until = None
            rolled = True
        return rolled

    def is_blocked(self, now: datetime) -> bool:
        return self.blocked_until is not None and now < self.blocked_until

    def is_exhausted(self, now: datetime) -> bool:
        """True when the provider must not receive a request now."""
        self.roll_window(now)
        if self.is_blocked(now):
            return True
        return self.tokens_used >= self.limit_tokens

    def remaining_tokens(self, now: datetime) -> int:
        self.roll_window(now)
        if self.is_blocked(now):
            return 0
        return max(0, self.limit_tokens - self.tokens_used)

    def seconds_until_reset(self, now: datetime) -> int | None:
        """Seconds until the current window ends; None when no window."""
        if self.reset_at is None:
            return None
        self.roll_window(now)
        if self.reset_at is None:
            return None
        return max(0, int((self.reset_at - now).total_seconds()))

    def seconds_until_available(self, now: datetime) -> int:
        """Seconds until the provider can take a request again (0 now)."""
        self.roll_window(now)
        if self.blocked_until is not None and now < self.blocked_until:
            return int((self.blocked_until - now).total_seconds())
        if self.tokens_used >= self.limit_tokens:
            remaining = self.seconds_until_reset(now)
            return remaining if remaining is not None else 0
        return 0

    def estimate_cost_rp(self, input_tokens: int, output_tokens: int) -> int:
        """Estimated Rupiah cost of a call of this size on this entry."""
        return _cost_rp(self.rp_per_1k_input, self.rp_per_1k_output,
                        input_tokens, output_tokens)

    def record_usage(self, input_tokens: int, output_tokens: int,
                     now: datetime) -> int:
        """Record one completed call; return its Rupiah cost.

        Rolls the window first, so usage after a reset starts a fresh
        window instead of piling onto an expired one.
        """
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must not be negative")
        self.roll_window(now)
        self.tokens_used += input_tokens + output_tokens
        self.requests_used += 1
        return self.estimate_cost_rp(input_tokens, output_tokens)

    def mark_rate_limited(self, now: datetime,
                          retry_after_seconds: int | None = None,
                          reason: str | None = None) -> None:
        """Block the provider after a 429 / quota-exhausted response.

        Blocked until Retry-After when given, else until the current
        window resets, else a short default block. The block is a
        fact about the provider, not a guess: it only comes from a
        real provider response (see ``QuotaStore.apply_rate_limit``).
        """
        if retry_after_seconds is not None and retry_after_seconds > 0:
            self.blocked_until = now + timedelta(seconds=retry_after_seconds)
        elif self.reset_at is not None and self.reset_at > now:
            self.blocked_until = self.reset_at
        else:
            self.blocked_until = now + timedelta(seconds=DEFAULT_BLOCK_SECONDS)
        self.last_rate_limit_reason = reason

    def snapshot(self, now: datetime) -> dict:
        """Plain-data view for logs, the dashboard, and tests."""
        self.roll_window(now)
        return {
            "provider_key": self.provider_key,
            "tier": self.tier,
            "limit_tokens": self.limit_tokens,
            "tokens_used": self.tokens_used,
            "tokens_remaining": self.remaining_tokens(now),
            "requests_used": self.requests_used,
            "reset_in_seconds": self.seconds_until_reset(now),
            "blocked_until_in_seconds": (
                max(0, int((self.blocked_until - now).total_seconds()))
                if self.blocked_until is not None and now < self.blocked_until
                else 0
            ),
            "exhausted": self.is_exhausted(now),
            "rp_per_1k_input": self.rp_per_1k_input,
            "rp_per_1k_output": self.rp_per_1k_output,
        }


class QuotaStore:
    """In-memory registry of per-provider quotas.

    ``now_fn`` is an injectable clock (tests pass a fake clock; the
    default is real UTC time). All methods are safe to call from
    several threads: one lock guards every mutation.
    """

    def __init__(self, now_fn=None) -> None:
        self._now_fn = now_fn or _utcnow
        self._quotas: dict[str, ProviderQuota] = {}
        self._lock = threading.Lock()

    def now(self) -> datetime:
        return self._now_fn()

    def register(self, provider_key: str, *, tier: str, limit_tokens: int,
                 reset_after_seconds: int, rp_per_1k_input: int,
                 rp_per_1k_output: int) -> ProviderQuota:
        """Register (or re-register) a quota.

        Limits must come from the provider's actual plan; this
        function takes no defaults, so no limit is ever invented.
        Re-registering keeps the usage counters and only replaces
        the limit, window, rates, and tier.
        """
        if limit_tokens < 0:
            raise ValueError("limit_tokens must not be negative")
        if reset_after_seconds < 0:
            raise ValueError("reset_after_seconds must not be negative")
        if rp_per_1k_input < 0 or rp_per_1k_output < 0:
            raise ValueError("rates must not be negative")
        now = self.now()
        with self._lock:
            existing = self._quotas.get(provider_key)
            if existing is not None:
                existing.tier = tier
                existing.limit_tokens = limit_tokens
                existing.reset_after_seconds = reset_after_seconds
                existing.rp_per_1k_input = rp_per_1k_input
                existing.rp_per_1k_output = rp_per_1k_output
                if reset_after_seconds > 0 and existing.reset_at is None:
                    existing.reset_at = now + timedelta(seconds=reset_after_seconds)
                return existing
            reset_at = (
                now + timedelta(seconds=reset_after_seconds)
                if reset_after_seconds > 0 else None
            )
            quota = ProviderQuota(
                provider_key=provider_key, tier=tier,
                limit_tokens=limit_tokens,
                reset_after_seconds=reset_after_seconds,
                rp_per_1k_input=rp_per_1k_input,
                rp_per_1k_output=rp_per_1k_output,
                reset_at=reset_at,
            )
            self._quotas[provider_key] = quota
            return quota

    def get(self, provider_key: str) -> ProviderQuota | None:
        with self._lock:
            return self._quotas.get(provider_key)

    def is_exhausted(self, provider_key: str) -> bool:
        """False for an unregistered provider: unknown quota is not
        exhaustion, it is just untracked."""
        quota = self.get(provider_key)
        if quota is None:
            return False
        return quota.is_exhausted(self.now())

    def remaining_tokens(self, provider_key: str) -> int | None:
        """Remaining tokens, or None when the provider is unregistered."""
        quota = self.get(provider_key)
        if quota is None:
            return None
        return quota.remaining_tokens(self.now())

    def seconds_until_reset(self, provider_key: str) -> int | None:
        quota = self.get(provider_key)
        if quota is None:
            return None
        return quota.seconds_until_reset(self.now())

    def record_usage(self, provider_key: str, input_tokens: int,
                     output_tokens: int) -> int | None:
        """Record a completed call; return its Rupiah cost, or None
        when the provider has no registered quota (untracked)."""
        quota = self.get(provider_key)
        if quota is None:
            return None
        with self._lock:
            return quota.record_usage(input_tokens, output_tokens, self.now())

    def estimate_cost_rp(self, provider_key: str, input_tokens: int,
                         output_tokens: int) -> int | None:
        """Estimated Rupiah cost on this provider, or None when unknown."""
        quota = self.get(provider_key)
        if quota is None:
            return None
        return quota.estimate_cost_rp(input_tokens, output_tokens)

    def apply_rate_limit(self, provider_key: str, *,
                         retry_after_seconds: int | None = None,
                         reason: str | None = None) -> bool:
        """Mark a provider rate-limited from a real provider response.

        Returns False when the provider is unregistered (nothing to
        mark). Callers pass the Retry-After value when the response
        carried one.
        """
        quota = self.get(provider_key)
        if quota is None:
            return False
        with self._lock:
            quota.mark_rate_limited(self.now(),
                                    retry_after_seconds=retry_after_seconds,
                                    reason=reason)
        return True

    def apply_rate_limit_headers(self, provider_key: str,
                                 headers: dict) -> bool:
        """Update quota state from a provider's rate-limit headers.

        Understood headers (case-insensitive keys):
        - ``retry-after``: seconds until the provider accepts again.
        - ``x-ratelimit-remaining``: 0 means the quota is exhausted;
          the block then runs until the known reset, else 60 seconds.
        - ``x-ratelimit-reset``: epoch seconds of the next reset.
        Unknown or unparsable headers are ignored, never raised.
        Returns False when the provider is unregistered.
        """
        quota = self.get(provider_key)
        if quota is None:
            return False
        lowered = {str(k).lower(): v for k, v in dict(headers).items()}
        now = self.now()
        with self._lock:
            reset_raw = lowered.get("x-ratelimit-reset")
            if reset_raw is not None:
                try:
                    reset_at = datetime.fromtimestamp(
                        int(float(reset_raw)), tz=timezone.utc)
                    if reset_at > now:
                        quota.reset_at = reset_at
                except (TypeError, ValueError, OverflowError):
                    pass
            retry_after = None
            retry_raw = lowered.get("retry-after")
            if retry_raw is not None:
                try:
                    retry_after = int(float(retry_raw))
                except (TypeError, ValueError):
                    retry_after = None
            remaining_raw = lowered.get("x-ratelimit-remaining")
            remaining = None
            if remaining_raw is not None:
                try:
                    remaining = int(float(remaining_raw))
                except (TypeError, ValueError):
                    remaining = None
            if retry_after is not None or remaining == 0:
                quota.mark_rate_limited(
                    now, retry_after_seconds=retry_after,
                    reason="rate_limit_headers")
            return True

    def snapshot(self, provider_key: str) -> dict | None:
        quota = self.get(provider_key)
        if quota is None:
            return None
        return quota.snapshot(self.now())

    def snapshot_all(self) -> list[dict]:
        now = self.now()
        with self._lock:
            quotas = list(self._quotas.values())
        return [quota.snapshot(now) for quota in quotas]
