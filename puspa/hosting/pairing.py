"""Device pairing: short-lived code, 30-day session, address lockout.

Pattern (DuskBot ``server/sessions.ts``, rewritten as Puspa code):
a device that cannot do the Google login flow (a headless server, a
second machine) is paired with a short code the user reads off the
already-logged-in app and types into the device.

Rules, all enforced here:

- The pairing code is 6 digits, single-use, and valid for 5 minutes.
- Only the SHA-256 hash of a code is stored; the code itself is
  returned once at issue time and never written anywhere.
- A correct code mints a session token (``secrets.token_urlsafe(32)``)
  valid for 30 days. Only the SHA-256 hash of the token is stored,
  the same pattern as ``puspa.accounts.user_sessions``.
- 5 wrong codes from one address lock that address for 10 minutes.
  While locked, verification refuses immediately without touching
  any code, so a locked address cannot burn other users' codes.
- A successful verification clears the address failure count.

The store is in-memory and process-local: pairing codes and sessions
die with the process. That is deliberate for this iteration — the
durable home for sessions is the Postgres ``sessions`` table owned by
``puspa.accounts`` (Google login sessions already live there), and
moving pairing sessions into it is a follow-up with its own schema
migration. The clock is injectable (``clock`` returns epoch seconds)
so tests can move time without sleeping.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .errors import AddressLockedError, PairingError

__all__ = [
    "PAIRING_CODE_TTL_SECONDS",
    "PAIRING_LOCKOUT_SECONDS",
    "PAIRING_MAX_FAILURES",
    "PAIRING_SESSION_TTL_SECONDS",
    "PairingStore",
]

PAIRING_CODE_TTL_SECONDS = 300  # 5 minutes
PAIRING_SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days
PAIRING_MAX_FAILURES = 5
PAIRING_LOCKOUT_SECONDS = 600  # 10 minutes


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class _CodeRecord:
    user_id: str
    device_label: str
    expires_at: float


@dataclass
class _SessionRecord:
    user_id: str
    device_label: str
    expires_at: float


@dataclass
class _AddressState:
    failures: int = 0
    locked_until: float = 0.0


@dataclass
class PairingStore:
    """In-memory pairing codes, pairing sessions, and address lockouts."""

    clock: Callable[[], float] = field(default=time.time, repr=False)
    _codes: dict[str, _CodeRecord] = field(default_factory=dict, init=False, repr=False)
    _sessions: dict[str, _SessionRecord] = field(default_factory=dict, init=False, repr=False)
    _addresses: dict[str, _AddressState] = field(default_factory=dict, init=False, repr=False)

    def _now(self) -> float:
        return float(self.clock())

    def issue(self, user_id: str, device_label: str) -> dict:
        """Issue a pairing code. Returns ``{"code", "expires_at"}``.

        The code is returned exactly once here; only its hash is kept.
        Raises ``PairingError`` on an empty user id or device label.
        """
        if not isinstance(user_id, str) or not user_id.strip():
            raise PairingError("pairing requires a non-empty user_id")
        if not isinstance(device_label, str) or not device_label.strip():
            raise PairingError("pairing requires a non-empty device_label")
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = self._now() + PAIRING_CODE_TTL_SECONDS
        self._codes[_sha256_hex(code)] = _CodeRecord(
            user_id=user_id, device_label=device_label, expires_at=expires_at
        )
        return {"code": code, "expires_at": expires_at}

    def _address_state(self, address: str) -> _AddressState:
        return self._addresses.setdefault(address, _AddressState())

    def is_locked(self, address: str) -> bool:
        """True when the address is currently locked out."""
        state = self._addresses.get(address)
        if state is None:
            return False
        return self._now() < state.locked_until

    def locked_until(self, address: str) -> float:
        """Epoch seconds until which the address is locked (0 if not locked)."""
        state = self._addresses.get(address)
        return state.locked_until if state is not None else 0.0

    def verify(self, code: str, *, address: str) -> dict:
        """Verify a pairing code; on success mint a 30-day session.

        Returns ``{"session_token", "user_id", "device_label",
        "expires_at"}``. The session token is returned exactly once
        here; only its hash is kept. Raises ``AddressLockedError``
        while the address is locked and ``PairingError`` for an
        unknown, expired, or already-used code. Each wrong code
        counts toward the address lockout.
        """
        if not isinstance(address, str) or not address:
            raise PairingError("pairing verification requires an address")
        now = self._now()
        state = self._address_state(address)
        if now < state.locked_until:
            raise AddressLockedError(
                f"address locked after {PAIRING_MAX_FAILURES} wrong pairing codes",
                locked_until=state.locked_until,
            )
        record = None
        if isinstance(code, str) and code:
            record = self._codes.get(_sha256_hex(code))
        if record is None or now >= record.expires_at:
            if record is not None:
                del self._codes[_sha256_hex(code)]
            self._register_failure(state, now)
            raise PairingError("pairing code unknown, expired, or already used")
        # Single use: consume before minting, so a failure below can
        # never leave a usable code behind.
        del self._codes[_sha256_hex(code)]
        state.failures = 0
        state.locked_until = 0.0
        token = secrets.token_urlsafe(32)
        expires_at = now + PAIRING_SESSION_TTL_SECONDS
        self._sessions[_sha256_hex(token)] = _SessionRecord(
            user_id=record.user_id,
            device_label=record.device_label,
            expires_at=expires_at,
        )
        return {
            "session_token": token,
            "user_id": record.user_id,
            "device_label": record.device_label,
            "expires_at": expires_at,
        }

    def _register_failure(self, state: _AddressState, now: float) -> None:
        state.failures += 1
        if state.failures >= PAIRING_MAX_FAILURES:
            state.locked_until = now + PAIRING_LOCKOUT_SECONDS
            state.failures = 0

    def validate_session(self, token: str) -> dict:
        """Validate a pairing session token.

        Returns ``{"user_id", "device_label", "expires_at"}``. Raises
        ``PairingError`` for an unknown, expired, or revoked token.
        """
        if not isinstance(token, str) or not token:
            raise PairingError("pairing session token unknown or revoked")
        record = self._sessions.get(_sha256_hex(token))
        if record is None:
            raise PairingError("pairing session token unknown or revoked")
        if self._now() >= record.expires_at:
            del self._sessions[_sha256_hex(token)]
            raise PairingError("pairing session expired")
        return {
            "user_id": record.user_id,
            "device_label": record.device_label,
            "expires_at": record.expires_at,
        }

    def revoke_session(self, token: str) -> None:
        """Revoke one pairing session. Idempotent."""
        if isinstance(token, str) and token:
            self._sessions.pop(_sha256_hex(token), None)

    def purge_expired(self) -> dict:
        """Drop expired codes, expired sessions, and spent lockouts.

        Returns counts ``{"codes", "sessions", "locks"}``.
        """
        now = self._now()
        codes = [key for key, rec in self._codes.items() if now >= rec.expires_at]
        for key in codes:
            del self._codes[key]
        sessions = [key for key, rec in self._sessions.items() if now >= rec.expires_at]
        for key in sessions:
            del self._sessions[key]
        locks = [
            addr
            for addr, state in self._addresses.items()
            if state.locked_until and now >= state.locked_until and state.failures == 0
        ]
        for addr in locks:
            del self._addresses[addr]
        return {"codes": len(codes), "sessions": len(sessions), "locks": len(locks)}
