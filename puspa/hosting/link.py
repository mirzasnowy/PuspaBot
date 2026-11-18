"""Link codes: short codes that bind an app to a server, rate-limited.

Pattern (DuskBot ``apps/link-service/``, rewritten as Puspa code):
when the app cannot reach the server directly it links through a
short code the user reads off one side and types into the other.
The code is 7 characters drawn from an alphabet WITHOUT the easily
confused characters ``l``, ``o``, ``0``, and ``1``, so a code read
aloud or typed by hand survives transcription.

Rules enforced here:

- Codes are single-use and valid for 10 minutes. Only the SHA-256
  hash of a code is stored; the code is returned once at creation.
- Creating a link also mints a 32-byte relay key (AES-256-GCM, see
  ``relay.py``) held ONLY by the app and the server: the server keeps
  it in process memory keyed by the code hash, returns it once on
  redeem, and never logs or persists it. Links die with the process.
- Rate limits on creation: 30 per hour per address, 2,000 per hour
  globally (sliding windows). Both limits are constructor parameters
  with those defaults, so tests can exercise the mechanism with small
  numbers without waiting an hour.
- Redeeming consumes the code. ``is_live`` lets the server check a
  code without consuming it (the relay path validates before it
  forwards anything).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from .errors import LinkError, RateLimitError

__all__ = [
    "LINK_CODE_ALPHABET",
    "LINK_CODE_LENGTH",
    "LINK_CODE_TTL_SECONDS",
    "LINK_CREATE_GLOBAL_PER_HOUR",
    "LINK_CREATE_PER_ADDRESS_PER_HOUR",
    "LinkService",
    "generate_link_code",
]

LINK_CODE_LENGTH = 7

#: Letters without l/o plus digits without 0/1 (32 symbols).
LINK_CODE_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"

LINK_CODE_TTL_SECONDS = 600  # 10 minutes

LINK_CREATE_PER_ADDRESS_PER_HOUR = 30
LINK_CREATE_GLOBAL_PER_HOUR = 2000

_RATE_WINDOW_SECONDS = 3600


def generate_link_code() -> str:
    """Generate one 7-character link code from the unambiguous alphabet."""
    return "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class _LinkRecord:
    user_id: str
    relay_key: bytes
    expires_at: float
    redeemed: bool = False


@dataclass
class LinkService:
    """In-memory link codes + relay keys + creation rate limits."""

    clock: Callable[[], float] = field(default=time.time, repr=False)
    per_address_limit: int = LINK_CREATE_PER_ADDRESS_PER_HOUR
    global_limit: int = LINK_CREATE_GLOBAL_PER_HOUR
    _links: dict[str, _LinkRecord] = field(default_factory=dict, init=False, repr=False)
    _per_address: dict[str, deque] = field(default_factory=dict, init=False, repr=False)
    _global: deque = field(default_factory=deque, init=False, repr=False)

    def _now(self) -> float:
        return float(self.clock())

    def _prune(self, window: deque, now: float) -> None:
        while window and window[0] <= now - _RATE_WINDOW_SECONDS:
            window.popleft()

    def _check_rate_limit(self, address: str, now: float) -> None:
        per_address = self._per_address.setdefault(address, deque())
        self._prune(per_address, now)
        self._prune(self._global, now)
        if len(per_address) >= self.per_address_limit:
            raise RateLimitError(
                f"link creation rate limit: {self.per_address_limit} per hour for this address"
            )
        if len(self._global) >= self.global_limit:
            raise RateLimitError(f"link creation rate limit: {self.global_limit} per hour globally")
        per_address.append(now)
        self._global.append(now)

    def create_link(self, user_id: str, *, address: str) -> dict:
        """Create a link code + relay key. Returns ``{"code",
        "relay_key", "expires_at"}`` — the code and the key are
        returned exactly once here and must be handed to the app
        over a channel the user already trusts (the logged-in app
        itself). Raises ``LinkError`` on bad input and
        ``RateLimitError`` when a limit is hit.
        """
        if not isinstance(user_id, str) or not user_id.strip():
            raise LinkError("link creation requires a non-empty user_id")
        if not isinstance(address, str) or not address:
            raise LinkError("link creation requires an address")
        now = self._now()
        self._check_rate_limit(address, now)
        for _ in range(10):  # collision retry; 32^7 space, never loops long
            code = generate_link_code()
            key = _sha256_hex(code)
            if key not in self._links:
                break
        else:  # pragma: no cover - unreachable in practice (32^7 codes)
            raise LinkError("could not mint a fresh link code")
        expires_at = now + LINK_CODE_TTL_SECONDS
        self._links[key] = _LinkRecord(
            user_id=user_id, relay_key=secrets.token_bytes(32), expires_at=expires_at
        )
        return {"code": code, "relay_key": self._links[key].relay_key, "expires_at": expires_at}

    def is_live(self, code: str) -> bool:
        """True when the code exists, is unredeemed, and is unexpired.

        Does not consume the code. Expired codes are dropped lazily.
        """
        if not isinstance(code, str) or not code:
            return False
        record = self._links.get(_sha256_hex(code))
        if record is None:
            return False
        if self._now() >= record.expires_at or record.redeemed:
            if self._now() >= record.expires_at:
                del self._links[_sha256_hex(code)]
            return False
        return True

    def redeem(self, code: str, *, address: str) -> dict:
        """Redeem a link code; returns ``{"user_id", "relay_key",
        "expires_at"}``. Single-use: the code dies here whether or
        not the caller uses the key. Raises ``LinkError`` for an
        unknown, expired, or already-redeemed code.
        """
        if not isinstance(code, str) or not code:
            raise LinkError("link code unknown, expired, or already redeemed")
        if not isinstance(address, str) or not address:
            raise LinkError("link redemption requires an address")
        record = self._links.pop(_sha256_hex(code), None)
        if record is None or self._now() >= record.expires_at or record.redeemed:
            raise LinkError("link code unknown, expired, or already redeemed")
        return {
            "user_id": record.user_id,
            "relay_key": record.relay_key,
            "expires_at": record.expires_at,
        }

    def purge_expired(self) -> int:
        """Drop expired link codes. Returns the number dropped."""
        now = self._now()
        expired = [key for key, rec in self._links.items() if now >= rec.expires_at]
        for key in expired:
            del self._links[key]
        return len(expired)
