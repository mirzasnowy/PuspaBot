"""Error types for device pairing.

The pairing surface is the one part of hosting a single user runs
locally, so its error types live here in the public package: the
base type plus the two pairing failures. The server-only error
types (link, seal, relay, and the rest) live in the private
hosted service and subclass the same base type, so one ``except
HostingError`` still catches every hosting failure.
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
