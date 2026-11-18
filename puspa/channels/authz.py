"""Channel authorisation: allowlist + DM pairing (G14).

A channel answers nobody by default. A chat is authorised in
exactly one of two ways:

1. **Allowlist** — the operator lists ``(platform, chat_id)`` pairs
   (from config/env). Used for the owner's own chats.
2. **DM pairing** — a user who is already logged in to the PWA asks
   for a pairing code, reads it off the PWA, and sends it to the
   bot in a DM. A correct code binds that chat to the user.

Pairing follows the ``puspa.hosting.pairing`` pattern, rewritten
for channels: the code is 6 digits, single-use, valid for 5
minutes; only its SHA-256 hash is stored and the code itself is
returned once at issue time and never written anywhere; 5 wrong
codes from one chat lock that chat for 10 minutes. The binding it
mints is ``platform:chat_id -> user_id`` and is what the inbound
pipeline resolves a session key through.

The store is in-memory and process-local, like the hosting
pairing store: pairing codes and bindings die with the process.
That is deliberate for this iteration — the durable home for
channel bindings is a follow-up with its own schema migration
(the delivery ledger already has a Postgres table; bindings join
it there). The clock is injectable so tests can move time without
sleeping.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .errors import PairingError, UnauthorizedChatError

__all__ = [
    "PAIRING_CODE_TTL_SECONDS",
    "PAIRING_LOCKOUT_SECONDS",
    "PAIRING_MAX_FAILURES",
    "Allowlist",
    "ChannelPairing",
    "resolve_session_key",
]

PAIRING_CODE_TTL_SECONDS = 300  # 5 minutes
PAIRING_MAX_FAILURES = 5
PAIRING_LOCKOUT_SECONDS = 600  # 10 minutes


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def resolve_session_key(platform: str, chat_id: str) -> str:
    """Canonical session key for a channel chat (``platform:chat_id``)."""
    return f"{platform}:{chat_id}"


@dataclass(frozen=True)
class Allowlist:
    """The set of (platform, chat_id) pairs the gateway answers.

    Empty by default: with no allowlist and no pairing, the gateway
    answers nobody. ``parse`` reads the ``platform:chat_id`` pairs
    from one comma-separated string (the env/config form).
    """

    entries: frozenset[tuple[str, str]] = frozenset()

    @classmethod
    def parse(cls, raw: str | None) -> Allowlist:
        if not raw:
            return cls()
        entries = set()
        for raw_item in raw.split(","):
            item = raw_item.strip()
            if not item:
                continue
            platform, _, chat_id = item.partition(":")
            if not platform or not chat_id:
                raise ValueError(f"bad allowlist entry: {item!r} (want platform:chat_id)")
            entries.add((platform.strip(), chat_id.strip()))
        return cls(frozenset(entries))

    def allows(self, platform: str, chat_id: str) -> bool:
        return (platform, chat_id) in self.entries


@dataclass
class _CodeRecord:
    user_id: str
    expires_at: float


@dataclass
class _ChatState:
    failures: int = 0
    locked_until: float = 0.0


@dataclass
class ChannelPairing:
    """In-memory DM pairing codes and chat -> user bindings."""

    clock: Callable[[], float] = field(default=time.time, repr=False)
    _codes: dict[str, _CodeRecord] = field(default_factory=dict, init=False, repr=False)
    _bindings: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _chats: dict[str, _ChatState] = field(default_factory=dict, init=False, repr=False)

    def _now(self) -> float:
        return float(self.clock())

    # -- issuing ----------------------------------------------------------

    def issue(self, user_id: str) -> dict:
        """Issue a pairing code for a user. Returns ``{"code", "expires_at"}``.

        The code is returned exactly once here; only its hash is
        kept. Raises ``PairingError`` on an empty user id.
        """
        if not isinstance(user_id, str) or not user_id.strip():
            raise PairingError("pairing requires a non-empty user_id")
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = self._now() + PAIRING_CODE_TTL_SECONDS
        self._codes[_sha256_hex(code)] = _CodeRecord(user_id=user_id, expires_at=expires_at)
        return {"code": code, "expires_at": expires_at}

    # -- verifying / binding ----------------------------------------------

    def _chat_state(self, chat_key: str) -> _ChatState:
        return self._chats.setdefault(chat_key, _ChatState())

    def is_locked(self, platform: str, chat_id: str) -> bool:
        state = self._chats.get(resolve_session_key(platform, chat_id))
        return state is not None and self._now() < state.locked_until

    def verify(self, code: str, *, platform: str, chat_id: str) -> str:
        """Verify a code sent in a DM; bind the chat to the code's user.

        Returns the bound ``user_id``. The code is single-use: a
        correct code is consumed even when the binding already
        exists. Raises ``PairingError`` on a wrong, expired, or
        already-used code, and while the chat is locked out.
        """
        chat_key = resolve_session_key(platform, chat_id)
        state = self._chat_state(chat_key)
        if self._now() < state.locked_until:
            raise PairingError("chat is locked out after too many wrong codes")
        record = self._codes.pop(_sha256_hex(code), None)
        if record is None:
            state.failures += 1
            if state.failures >= PAIRING_MAX_FAILURES:
                state.locked_until = self._now() + PAIRING_LOCKOUT_SECONDS
                state.failures = 0
            raise PairingError("wrong pairing code")
        if self._now() > record.expires_at:
            raise PairingError("pairing code expired")
        state.failures = 0
        self._bindings[chat_key] = record.user_id
        return record.user_id

    # -- authorisation ------------------------------------------------------

    def bound_user(self, platform: str, chat_id: str) -> str | None:
        """The user id a chat is paired to, or None when unpaired."""
        return self._bindings.get(resolve_session_key(platform, chat_id))

    def unbind(self, platform: str, chat_id: str) -> bool:
        """Drop a chat binding. Returns True when one existed."""
        return self._bindings.pop(resolve_session_key(platform, chat_id), None) is not None

    def authorize(
        self, platform: str, chat_id: str, *, allowlist: Allowlist | None = None
    ) -> str | None:
        """Return the user id for an authorised chat, else raise.

        An allowlisted chat is authorised but has no bound user
        (returns None — the caller treats it as the operator's own
        chat). A paired chat returns its bound user id. Anything
        else raises :class:`UnauthorizedChatError`.
        """
        if allowlist is not None and allowlist.allows(platform, chat_id):
            return None
        user_id = self.bound_user(platform, chat_id)
        if user_id is not None:
            return user_id
        raise UnauthorizedChatError(f"chat {platform}:{chat_id} is neither allowlisted nor paired")

    def is_authorized(
        self, platform: str, chat_id: str, *, allowlist: Allowlist | None = None
    ) -> bool:
        try:
            self.authorize(platform, chat_id, allowlist=allowlist)
        except UnauthorizedChatError:
            return False
        return True

    def bindings(self) -> dict[str, str]:
        """A copy of the current chat -> user bindings (for inspection)."""
        return dict(self._bindings)


def parse_allowlist_env(raw: str | None) -> Allowlist:
    """Parse the ``PUSPA_CHANNEL_ALLOWLIST`` env form (``platform:chat_id,...``)."""
    return Allowlist.parse(raw)


def authorized_user_ids(
    pairing: ChannelPairing, allowlist: Allowlist, chats: Iterable[tuple[str, str]]
) -> dict[tuple[str, str], str | None]:
    """Authorisation for a batch of chats (helper for admin tooling)."""
    result = {}
    for platform, chat_id in chats:
        try:
            result[(platform, chat_id)] = pairing.authorize(platform, chat_id, allowlist=allowlist)
        except UnauthorizedChatError:
            result[(platform, chat_id)] = None
    return result
