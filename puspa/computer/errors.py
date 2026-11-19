"""Errors for the computer-use package (G16).

All errors in :mod:`puspa.computer` live here so backends, the
broker, and the browser contract can raise the same types without
importing each other.
"""

from __future__ import annotations

__all__ = [
    "BackendError",
    "BackendUnavailable",
    "BrowserSessionBusy",
    "BrowserUnavailable",
    "TokenExpired",
    "TokenRevoked",
    "UnknownToken",
]


class BackendError(Exception):
    """A backend failed for a reason that is not unavailability.

    Examples: a command that could not be started, a file sync
    outside the sandbox scope, or a credential-looking value passed
    in a raw environment. The backend itself was reachable; the
    operation was refused or failed.
    """


class BackendUnavailable(Exception):
    """A backend cannot run on this host at all.

    This is the honest fail-closed answer, not a silent fallback:
    callers must not retry the same operation on another backend
    unless the operator explicitly chose that fallback. The Docker
    backend raises this on hosts where containers cannot start
    (this dev VM rejects the ``setns`` syscall); the first live
    Docker check is the CI sandbox lane (.github/workflows/ci.yml).
    """


class BrowserUnavailable(Exception):
    """No live browser is available, by design, until after the MVP.

    Computer use / live browser automation is deferred (G16 is
    BIG, post-MVP). The launcher raises this instead of pretending
    a browser exists. What exists today is the session contract
    and the human-takeover interface in :mod:`puspa.computer.browser`,
    not a driving browser.
    """


class BrowserSessionBusy(Exception):
    """An agent already has a live browser session.

    One agent holds at most one live browser session at a time
    (one browser profile per agent, one session per task). A second
    launch for the same agent is refused until the first session
    closes or is reaped as an orphan.
    """


class UnknownToken(Exception):
    """A broker token was never issued, or was already revoked."""


class TokenExpired(Exception):
    """A broker token was issued but its time-to-live ran out."""


class TokenRevoked(Exception):
    """A broker token was revoked before use.

    Revocation happens when the owning process exits, when its
    lease closes, or when the operator revokes it explicitly.
    """
