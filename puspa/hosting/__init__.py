"""Device pairing for the self-host product.

A device proves it is in front of the operator by exchanging a short
pairing code; the paired device may then act for the user on this
machine. Pairing is the only part of the hosting surface a single
user runs locally: the multi-tenant server path lives elsewhere.
"""

from __future__ import annotations

from .errors import AddressLockedError, HostingError, PairingError
from .pairing import (
    PAIRING_CODE_TTL_SECONDS,
    PAIRING_LOCKOUT_SECONDS,
    PAIRING_MAX_FAILURES,
    PAIRING_SESSION_TTL_SECONDS,
    PairingStore,
)

__all__ = [
    "PAIRING_CODE_TTL_SECONDS",
    "PAIRING_LOCKOUT_SECONDS",
    "PAIRING_MAX_FAILURES",
    "PAIRING_SESSION_TTL_SECONDS",
    "AddressLockedError",
    "HostingError",
    "PairingError",
    "PairingStore",
]
