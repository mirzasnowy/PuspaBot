"""Error types for multi-tenant hosting auth, pairing, and relay.

Every failure in ``puspa.hosting`` raises one of these (never a raw
library exception), so callers can fail closed on a single base type
and tests can assert the exact refusal reason.
"""

from __future__ import annotations


class HostingError(Exception):
    """Base type for all hosting auth/relay failures."""


class PairingError(HostingError):
    """A pairing code or pairing session is unknown, expired, or used."""


class AddressLockedError(PairingError):
    """An address is locked after too many wrong pairing codes.

    Carries ``locked_until`` (epoch seconds) so the caller can report
    the remaining lockout without re-deriving it.
    """

    def __init__(self, message: str, locked_until: float) -> None:
        super().__init__(message)
        self.locked_until = locked_until


class LinkError(HostingError):
    """A link code is unknown, expired, or already redeemed."""


class RateLimitError(HostingError):
    """A link-creation rate limit was hit (per address or global)."""


class SealError(HostingError):
    """A card seal failed to verify or open (bad signature, wrong key)."""


class IdentityKeyError(HostingError):
    """The server identity key could not be loaded, created, or trusted."""


class RelayError(HostingError):
    """A relay frame is malformed, oversized, unauthentic, or undeliverable."""


class AllRoutesFailedError(HostingError):
    """No connection route worked (direct, tunnel, and relay all failed).

    Carries ``attempts``: the ordered list of ``RouteAttempt`` records
    from ``puspa.hosting.fallback`` so the caller can log each failure.
    """

    def __init__(self, message: str, attempts) -> None:
        super().__init__(message)
        self.attempts = list(attempts)
