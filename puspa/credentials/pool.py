"""Credential pool with per-model cooldown rotation (G22).

Pattern studied, then rewritten as Puspa's own code (see the
G22 section of the repo-comparison report): hermes-agent keeps
a credential pool per model with rotation cooldown
(``agent/credential_pool*.py``). No hermes-agent code is copied.

The rule this module exists for (Nero's own PR #146450 area):
cooldown is scoped to the MODEL that was requested, never
global. The same key used for two models has a separate
cooldown per model, so two different models never block each
other's cooldown. Concretely the cooldown key is
``(provider, model, key fingerprint)``.

Behaviour
---------
- :class:`CredentialPool` holds keys per ``(provider, model)``.
  :meth:`CredentialPool.acquire` returns the first key that is
  not cooling down for that exact pair, skipping cooled-down
  keys instead of using them and failing.
- :meth:`CredentialPool.mark_rate_limited` puts the key on
  cooldown for that ``(provider, model)`` only, for
  ``cooldown_seconds`` (default 60s) or the provider's
  ``Retry-After`` value when given. A rate limit on model A
  leaves model B untouched, even for the same key.
- :meth:`CredentialPool.mark_auth_failed` puts the key on a
  longer cooldown (default 600s): an auth failure means the key
  itself is bad for that pair, so it is not retried immediately.
- :meth:`CredentialPool.mark_success` clears the failure count
  for the key/pair.
- The clock is injectable (``clock`` returns monotonic seconds)
  so tests can advance time without sleeping.

Keys are never logged or printed: entries carry only a
fingerprint (first 12 hex chars of the SHA-256 of the key).
:meth:`CredentialPool.status` reports fingerprints, cooldown
state, and failure counts — never key values.

Auxiliary client
----------------
:class:`AuxiliaryClient` is the second LLM client for side
tasks (vision, compression, titles). It draws from the SAME
pool, not from a separate key: a side task that hits a rate
limit rotates to the next key in the pool and marks the limited
one as cooling down, exactly like a main call. Its ``call_fn``
is injected (``call_fn(key, provider, model, prompt) -> str``),
so no live call is made here.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

__all__ = [
    "AllKeysCoolingDown",
    "AuxiliaryClient",
    "AuxiliaryError",
    "CredentialEntry",
    "CredentialPool",
    "NoCredentialAvailable",
    "RateLimitedError",
]


class NoCredentialAvailable(Exception):
    """No key is registered for the requested (provider, model)."""


class AllKeysCoolingDown(Exception):
    """Keys exist for the pair, but every one is cooling down.

    ``retry_after_seconds`` is the shortest remaining cooldown,
    so the caller knows how long to wait.
    """

    def __init__(self, provider, model, retry_after_seconds):
        super().__init__(
            f"all keys for {provider}/{model} are cooling down; retry in {retry_after_seconds:.0f}s"
        )
        self.provider = provider
        self.model = model
        self.retry_after_seconds = retry_after_seconds


class RateLimitedError(Exception):
    """A call failed with a rate limit (HTTP 429 equivalent).

    ``retry_after_seconds`` carries the provider's Retry-After
    value when one was given.
    """

    def __init__(self, message="rate limited", *, retry_after_seconds=None):
        super().__init__(message)
        self.message = message
        self.status = 429
        self.retry_after_seconds = retry_after_seconds


class AuxiliaryError(Exception):
    """An auxiliary call failed after trying every usable key."""


def _fingerprint(key: str) -> str:
    return hashlib.sha256(str(key).encode()).hexdigest()[:12]


@dataclass
class CredentialEntry:
    """One key in the pool, for one ``(provider, model)`` pair.

    ``key_id`` is the fingerprint, never the key. ``failures``
    counts consecutive failures for this pair (reset on
    success). ``repr`` never shows the key.
    """

    provider: str
    model: str
    key: str
    key_id: str = field(init=False)
    failures: int = 0

    def __post_init__(self):
        self.key_id = _fingerprint(self.key)

    def __repr__(self) -> str:  # pragma: no cover - formatting only
        return (
            f"CredentialEntry(provider={self.provider!r}, "
            f"model={self.model!r}, key_id={self.key_id!r}, "
            f"failures={self.failures})"
        )


class CredentialPool:
    """Pool of keys per ``(provider, model)`` with cooldown rotation.

    ``default_cooldown_seconds`` applies to rate limits;
    ``auth_cooldown_seconds`` applies to auth failures.
    ``clock`` is a ``() -> float`` monotonic clock (default
    ``time.monotonic``); inject a fake clock in tests.
    """

    def __init__(
        self,
        *,
        default_cooldown_seconds: float = 60.0,
        auth_cooldown_seconds: float = 600.0,
        clock=None,
    ):
        if default_cooldown_seconds <= 0:
            raise ValueError("default_cooldown_seconds must be positive")
        if auth_cooldown_seconds <= 0:
            raise ValueError("auth_cooldown_seconds must be positive")
        self.default_cooldown_seconds = float(default_cooldown_seconds)
        self.auth_cooldown_seconds = float(auth_cooldown_seconds)
        self._clock = clock if clock is not None else time.monotonic
        # (provider, model) -> [CredentialEntry, ...] in add order.
        self._entries: dict[tuple, list[CredentialEntry]] = {}
        # (provider, model, key_id) -> monotonic timestamp when the
        # cooldown ends. Scoped per model by construction: the same
        # key used for two models has two independent entries here.
        self._cooldown_until: dict[tuple, float] = {}

    # -- registration -------------------------------------------------

    def add(self, provider, model, key) -> CredentialEntry:
        """Add one key for ``(provider, model)``; return its entry.

        Adding the same key twice for the same pair returns the
        existing entry instead of duplicating it.
        """
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("key must be a non-empty string")
        model = model.strip()
        pair = (provider, model)
        entry = CredentialEntry(provider=provider, model=model, key=key)
        existing = self._entries.setdefault(pair, [])
        for known in existing:
            if known.key_id == entry.key_id:
                return known
        existing.append(entry)
        return entry

    def add_many(self, provider, model, keys) -> list[CredentialEntry]:
        """Add several keys for one pair; return their entries."""
        return [self.add(provider, model, key) for key in keys]

    def remove(self, provider, model, key) -> bool:
        """Remove one key from a pair; True when it was present."""
        pair = (provider, model)
        entries = self._entries.get(pair, [])
        key_id = _fingerprint(key)
        kept = [e for e in entries if e.key_id != key_id]
        if len(kept) == len(entries):
            return False
        self._entries[pair] = kept
        self._cooldown_until.pop((provider, model, key_id), None)
        return True

    # -- cooldown ------------------------------------------------------

    def _now(self) -> float:
        return float(self._clock())

    def is_cooling_down(self, provider, model, key_id, *, now: float | None = None) -> bool:
        """True when this key is still cooling down for this pair."""
        until = self._cooldown_until.get((provider, model, key_id))
        if until is None:
            return False
        return (now if now is not None else self._now()) < until

    def cooldown_remaining(self, provider, model, key_id, *, now: float | None = None) -> float:
        """Seconds left on the cooldown (0 when not cooling down)."""
        until = self._cooldown_until.get((provider, model, key_id))
        if until is None:
            return 0.0
        return max(0.0, until - (now if now is not None else self._now()))

    def mark_rate_limited(self, entry, *, cooldown_seconds=None, retry_after=None) -> float:
        """Cool ``entry`` down for its ``(provider, model)`` only.

        ``retry_after`` (the provider's Retry-After, when given)
        wins over ``cooldown_seconds``; otherwise the pool
        default applies. Returns the cooldown in seconds. The
        same key used for another model is NOT affected.
        """
        seconds = (
            retry_after
            if retry_after is not None
            else (
                cooldown_seconds if cooldown_seconds is not None else self.default_cooldown_seconds
            )
        )
        if seconds is None or float(seconds) <= 0:
            raise ValueError("cooldown must be positive")
        self._cooldown_until[(entry.provider, entry.model, entry.key_id)] = self._now() + float(
            seconds
        )
        entry.failures += 1
        return float(seconds)

    def mark_auth_failed(self, entry, *, cooldown_seconds=None) -> float:
        """Cool ``entry`` down longer: its key is bad for this pair.

        Auth failures use ``auth_cooldown_seconds`` by default
        (600s), because retrying a rejected key immediately only
        burns the request. Scoped to ``(provider, model)`` like
        every other cooldown.
        """
        seconds = cooldown_seconds if cooldown_seconds is not None else self.auth_cooldown_seconds
        if float(seconds) <= 0:
            raise ValueError("cooldown must be positive")
        self._cooldown_until[(entry.provider, entry.model, entry.key_id)] = self._now() + float(
            seconds
        )
        entry.failures += 1
        return float(seconds)

    def mark_success(self, entry) -> None:
        """A call with ``entry`` worked: clear its failure count."""
        entry.failures = 0

    # -- acquisition ---------------------------------------------------

    def available(self, provider, model, *, now: float | None = None) -> list[CredentialEntry]:
        """Usable entries for the pair (cooling-down keys skipped)."""
        now = now if now is not None else self._now()
        return [
            entry
            for entry in self._entries.get((provider, model), [])
            if not self.is_cooling_down(provider, model, entry.key_id, now=now)
        ]

    def acquire(self, provider, model, *, now: float | None = None) -> CredentialEntry:
        """Return the first usable key for ``(provider, model)``.

        Keys on cooldown for this pair are skipped, never used
        and failed. Raises :class:`NoCredentialAvailable` when
        no key is registered for the pair, and
        :class:`AllKeysCoolingDown` when keys exist but every one
        is cooling down (with the shortest remaining cooldown).
        """
        entries = self._entries.get((provider, model), [])
        if not entries:
            raise NoCredentialAvailable(f"no credential registered for {provider}/{model}")
        now = now if now is not None else self._now()
        for entry in entries:
            if not self.is_cooling_down(provider, model, entry.key_id, now=now):
                return entry
        shortest = min(
            self.cooldown_remaining(provider, model, entry.key_id, now=now) for entry in entries
        )
        raise AllKeysCoolingDown(provider, model, shortest)

    def status(self, provider, model, *, now: float | None = None) -> list[dict]:
        """Pool status for a pair: fingerprints, never key values."""
        now = now if now is not None else self._now()
        return [
            {
                "key_id": entry.key_id,
                "provider": provider,
                "model": model,
                "cooling_down": self.is_cooling_down(provider, model, entry.key_id, now=now),
                "cooldown_remaining_seconds": self.cooldown_remaining(
                    provider, model, entry.key_id, now=now
                ),
                "failures": entry.failures,
            }
            for entry in self._entries.get((provider, model), [])
        ]


class AuxiliaryClient:
    """Second LLM client for side tasks, sharing the pool.

    Side tasks (vision, compression, titles) run through the
    same :class:`CredentialPool` as main calls — never a
    separate key. On a rate limit the client marks the key as
    cooling down and rotates to the next usable key for the
    same ``(provider, model)``; on an auth failure it cools
    the key down longer and rotates too. ``call_fn`` is the
    injected transport: ``call_fn(key, provider, model, prompt)
    -> str``. It must raise :class:`RateLimitedError` (or any
    exception with ``status == 429``) for a rate limit, and an
    exception with ``status == 401`` or ``403`` for an auth
    failure; anything else is re-raised immediately.
    """

    def __init__(self, pool, call_fn, *, default_provider="openrouter", default_model=None):
        if pool is None or call_fn is None:
            raise ValueError("pool and call_fn are required")
        self.pool = pool
        self.call_fn = call_fn
        self.default_provider = default_provider
        self.default_model = default_model

    def run(self, prompt, *, purpose, provider=None, model=None) -> str:
        """Run one side task, rotating keys on rate limit / auth.

        ``purpose`` names the side task (``"vision"``,
        ``"compression"``, ``"title"``, ...) and is carried in
        the error when every key fails. Returns the model's text.
        Raises :class:`AuxiliaryError` when no usable key remains
        or every key failed.
        """
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(purpose, str) or not purpose:
            raise ValueError("purpose must be a non-empty string")
        provider = provider or self.default_provider
        model = model or self.default_model
        if not model:
            raise ValueError(
                "auxiliary call needs a model (pass model= or set default_model on the client)"
            )
        tried = 0
        last_error = None
        while True:
            try:
                entry = self.pool.acquire(provider, model)
            except (NoCredentialAvailable, AllKeysCoolingDown) as exc:
                raise AuxiliaryError(
                    f"auxiliary task {purpose!r} for {provider}/{model} "
                    f"failed after {tried} key(s): {exc}"
                ) from exc
            tried += 1
            try:
                text = self.call_fn(entry.key, provider, model, prompt)
            except Exception as exc:
                status = getattr(exc, "status", None)
                if status == 429 or isinstance(exc, RateLimitedError):
                    retry_after = getattr(exc, "retry_after_seconds", None)
                    self.pool.mark_rate_limited(entry, retry_after=retry_after)
                    last_error = exc
                    continue
                if status in (401, 403):
                    self.pool.mark_auth_failed(entry)
                    last_error = exc
                    continue
                raise
            self.pool.mark_success(entry)
            return text
        # Unreachable: the loop exits by return or by raise.
        raise AuxiliaryError(  # pragma: no cover - defensive
            f"auxiliary task {purpose!r} failed: {last_error}"
        )
