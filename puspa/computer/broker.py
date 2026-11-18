"""Credential broker interface (G16).

Pattern studied, then rewritten as Puspa's own code (see the G16
section of the repo-comparison report): DuskBot runs a credential
broker on port 18080 — credentials never enter the raw
environment; the broker hands out a token, and the token is
revoked when the process exits. OpenBot had the same hole the
other way round (a bot reading a deployment token from
``/proc/1/environ``) and fixed it with per-computer HMAC tokens.

The rules, which every broker implementation must obey:

1. A credential value NEVER appears in an environment, a log
   line, a ``repr``, or an error message. Only fingerprints
   (first 12 hex chars of the SHA-256) are ever shown.
2. A child process receives only ``PUSPA_BROKER_TOKEN`` — an
   opaque, short-lived, scope-bound token — never the credential.
3. A token dies on first use after expiry, on explicit revoke,
   and when the owning process exits (the broker registers an
   ``atexit`` hook and revokes everything on context exit).
4. Tokens are scoped: a token issued for ``"tokopedia:read"``
   resolves nothing else, and a token for one credential name
   never resolves another.

What this module IS: the interface (:class:`CredentialBroker`),
the token type (:class:`BrokerToken`), and an in-memory test
broker (:class:`InMemoryBroker`) so the rules above are testable
without a service. What it is NOT: a real broker service. The
network broker (HTTP, port 18080 pattern, per-process revocation
on the host) is a post-MVP follow-up — see
``docs/computer-use.md``. Do not mistake the in-memory broker
for a production credential store: it holds values in this
process's memory, which is exactly why it is test-only.

Relation to :mod:`puspa.credentials`: the G22 credential pool
stores keys with per-model cooldown rotation and is the source
of keys. The broker is the separate layer that hands a key to a
spawned process without ever putting the key itself in the
process environment. This module does not duplicate the pool;
a real deployment registers pool entries with the broker by
name and lets the broker issue tokens against them.
"""

from __future__ import annotations

import abc
import atexit
import hashlib
import secrets
import time
from dataclasses import dataclass, field

from .errors import TokenExpired, TokenRevoked, UnknownToken

__all__ = [
    "BrokerToken",
    "CredentialBroker",
    "InMemoryBroker",
    "NoSuchCredential",
]

#: Environment variable a child process receives: the token, never the credential.
BROKER_TOKEN_ENV = "PUSPA_BROKER_TOKEN"


class NoSuchCredential(Exception):
    """No credential is registered under the requested name."""


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class BrokerToken:
    """A short-lived, scope-bound token for one credential.

    ``value`` is opaque: it is not the credential, it does not
    contain the credential, and it cannot be used to derive it.
    ``scope`` names what the token is for (``"tokopedia:read"``).
    ``expires_at`` is a monotonic timestamp. ``repr`` never shows
    the token value — only its id, scope, and expiry.
    """

    token_id: str
    value: str
    scope: str
    expires_at: float
    credential_name: str = field(repr=False)
    credential_fingerprint: str = ""

    def __repr__(self) -> str:  # pragma: no cover - formatting only
        return (
            f"BrokerToken(token_id={self.token_id!r}, scope={self.scope!r}, "
            f"credential_fingerprint={self.credential_fingerprint!r}, "
            f"expires_at={self.expires_at:.0f})"
        )

    def is_expired(self, *, now: float | None = None) -> bool:
        """True when the token's time-to-live has run out."""
        return (now if now is not None else time.monotonic()) >= self.expires_at


class CredentialBroker(abc.ABC):
    """Hands short-lived tokens to processes; never raw credentials.

    Register a credential by name once, issue tokens against it,
    revoke them when the process exits. Implementations must
    guarantee rule 1 of the module docstring (no credential value
    in any env, log, repr, or error) and rule 3 (revoke on process
    exit). Use as a context manager when a process's lifetime is
    known: exiting the context revokes every token it issued.
    """

    def __enter__(self) -> CredentialBroker:
        return self

    def __exit__(self, *exc) -> None:
        self.revoke_all()
        self.close()

    @abc.abstractmethod
    def register(self, name: str, value: str) -> str:
        """Register a credential under ``name``; return its fingerprint."""

    @abc.abstractmethod
    def issue(self, name: str, *, scope: str, ttl_s: int = 300) -> BrokerToken:
        """Issue a token for ``name`` and ``scope`` (default TTL 5 minutes)."""

    @abc.abstractmethod
    def use(self, token: BrokerToken) -> str:
        """Resolve ``token`` to its credential value.

        Raises :class:`puspa.computer.errors.UnknownToken` when the
        token was never issued or was revoked, and
        :class:`puspa.computer.errors.TokenExpired` when its TTL
        ran out. A token resolves at most its own credential: a
        token issued for another name or scope never resolves.
        """

    @abc.abstractmethod
    def revoke(self, token_id: str) -> bool:
        """Revoke one token by id; True when it was live."""

    @abc.abstractmethod
    def revoke_all(self) -> None:
        """Revoke every live token this broker issued."""

    def child_env(self, token: BrokerToken) -> dict[str, str]:
        """The environment a child process receives for ``token``.

        Exactly one entry: ``PUSPA_BROKER_TOKEN``. The credential
        value is never in it — the child presents the token to the
        broker and the broker resolves it.
        """
        return {BROKER_TOKEN_ENV: token.value}

    def close(self) -> None:
        """Release broker resources (default: nothing to release)."""
        return


class InMemoryBroker(CredentialBroker):
    """Test-only broker: tokens live in this process's memory.

    DO NOT USE IN PRODUCTION. The values sit in this process's
    memory, so any code in the same process could read them —
    which is exactly what the real (network) broker prevents by
    living in a separate process. This broker exists so the token
    rules (issue, expiry, revoke, revoke-on-exit, no credential
    in env) are testable without a service.
    """

    def __init__(self, *, clock=None) -> None:
        self._clock = clock if clock is not None else time.monotonic
        self._values: dict[str, str] = {}
        self._fingerprints: dict[str, str] = {}
        # token value -> (token_id, credential name, scope, expires_at)
        self._live: dict[str, tuple[str, str, str, float]] = {}
        self._revoked: set[str] = set()
        self._closed = False
        self._atexit_registered = False
        self._register_atexit()

    # -- registration ------------------------------------------------------

    def register(self, name: str, value: str) -> str:
        """Register ``value`` under ``name``; return its fingerprint."""
        self._require_open()
        if not isinstance(name, str) or not name:
            raise ValueError("credential name must be a non-empty string")
        if not isinstance(value, str) or not value:
            raise ValueError("credential value must be a non-empty string")
        self._values[name] = value
        fingerprint = _fingerprint(value)
        self._fingerprints[name] = fingerprint
        return fingerprint

    def fingerprint(self, name: str) -> str:
        """Fingerprint of the registered credential (never the value)."""
        try:
            return self._fingerprints[name]
        except KeyError:
            raise NoSuchCredential(f"no credential registered as {name!r}") from None

    # -- tokens --------------------------------------------------------------

    def issue(self, name: str, *, scope: str, ttl_s: int = 300) -> BrokerToken:
        """Issue a token for ``name`` and ``scope``."""
        self._require_open()
        if name not in self._values:
            raise NoSuchCredential(f"no credential registered as {name!r}")
        if not isinstance(scope, str) or not scope:
            raise ValueError("scope must be a non-empty string")
        if ttl_s <= 0:
            raise ValueError("ttl_s must be > 0")
        token_id = secrets.token_hex(8)
        value = secrets.token_urlsafe(32)
        expires_at = float(self._clock()) + float(ttl_s)
        self._live[value] = (token_id, name, scope, expires_at)
        return BrokerToken(
            token_id=token_id,
            value=value,
            scope=scope,
            expires_at=expires_at,
            credential_name=name,
            credential_fingerprint=self._fingerprints[name],
        )

    def use(self, token: BrokerToken) -> str:
        """Resolve ``token`` to its credential value, or raise."""
        self._require_open()
        record = self._live.get(token.value)
        if record is None:
            if token.token_id in self._revoked:
                raise TokenRevoked(f"token {token.token_id!r} was revoked")
            raise UnknownToken(f"token {token.token_id!r} was never issued by this broker")
        token_id, name, scope, expires_at = record
        if token.token_id != token_id:
            raise UnknownToken("token id does not match the issued token")
        if token.scope != scope:
            raise UnknownToken("token scope does not match the issued token")
        if float(self._clock()) >= expires_at:
            del self._live[token.value]
            raise TokenExpired(f"token {token.token_id!r} expired")
        return self._values[name]

    def revoke(self, token_id: str) -> bool:
        """Revoke one token by id; True when it was live."""
        for value, (live_id, _name, _scope, _exp) in list(self._live.items()):
            if live_id == token_id:
                del self._live[value]
                self._revoked.add(token_id)
                return True
        return False

    def revoke_all(self) -> None:
        """Revoke every live token this broker issued."""
        for token_id, _name, _scope, _exp in self._live.values():
            self._revoked.add(token_id)
        self._live.clear()

    def live_token_count(self) -> int:
        """Live tokens right now (test/diagnostics helper)."""
        return len(self._live)

    def close(self) -> None:
        """Revoke everything and stop issuing (idempotent)."""
        if self._closed:
            return
        self.revoke_all()
        self._closed = True

    # -- internals -----------------------------------------------------------

    def _require_open(self) -> None:
        if self._closed:
            raise TokenRevoked("broker is closed; all tokens were revoked")

    def _register_atexit(self) -> None:
        if self._atexit_registered:
            return
        self._atexit_registered = True
        atexit.register(self.revoke_all)
