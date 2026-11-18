"""Browser / computer-use session contract (G16) — interface only.

G16 is BIG and deferred until after the MVP: Puspa does NOT
build computer use / live browser automation now. What this
module builds is the contract the post-MVP work must honour,
written down while the principles are fresh:

1. One browser profile per agent. A profile directory belongs
   to exactly one agent id; two agents never share cookies,
   logins, or history.
2. One browser session per task. A session is born for a task
   and dies with it; there is no global browser the bot keeps
   forever. The launcher reaps orphan sessions (no heartbeat
   for ``max_idle_s``).
3. Explicit human takeover for sign-in, captcha, and 2FA. The
   bot never sees a password: when a page needs a human, the
   launcher raises a takeover request and waits; the human
   completes the step in their own browser, and the bot resumes
   after. There is no password field anywhere in this module —
   by design, not by omission.
4. Credentials travel through the broker
   (:mod:`puspa.computer.broker`), never in raw env. A browser
   session receives at most a broker token.

What exists here: the session record, the takeover request, the
launcher ABC, and an in-memory session registry that enforces
rules 1-2 (one live session per agent, reap orphans) so those
rules are testable without a browser. What does NOT exist: CDP
browser driving, a Chromium launch, any live browser. The
default launcher (:class:`UnavailableBrowserLauncher`) raises
:class:`puspa.computer.errors.BrowserUnavailable` on launch
with a message that names the dependency and the follow-up:
a Docker host or VPS plus the human-takeover UI, post-MVP.
See ``docs/computer-use.md`` for the schedule.
"""

from __future__ import annotations

import abc
import enum
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .errors import BrowserSessionBusy, BrowserUnavailable

__all__ = [
    "BrowserLauncher",
    "BrowserSession",
    "HumanTakeover",
    "SessionRegistry",
    "TakeoverReason",
    "TakeoverRequest",
    "TakeoverStatus",
    "UnavailableBrowserLauncher",
    "profile_dir_for",
]

#: Default idle time after which a session with no heartbeat is an orphan.
DEFAULT_MAX_IDLE_S = 900

#: Base directory for per-agent browser profiles (under the operator's data dir).
DEFAULT_PROFILES_BASE = Path("/tmp/puspa-browser-profiles")


def profile_dir_for(agent_id: str, *, base: str | Path = DEFAULT_PROFILES_BASE) -> Path:
    """The browser profile directory for one agent.

    One profile per agent, always: the directory name is the
    agent id, so two agents can never share a profile. Agent ids
    are validated (no path separators, no ``..``) so a crafted id
    cannot escape the profiles base.
    """
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("agent_id must be a non-empty string")
    if "/" in agent_id or "\\" in agent_id or agent_id in (".", ".."):
        raise ValueError(f"agent_id {agent_id!r} is not a safe profile name")
    return Path(base) / agent_id


class TakeoverReason(enum.Enum):
    """Why a human must take over the browser from the bot."""

    SIGN_IN = "sign_in"
    CAPTCHA = "captcha"
    TWO_FA = "two_fa"
    OTHER = "other"


class TakeoverStatus(enum.Enum):
    """Lifecycle of one human-takeover request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    COMPLETED = "completed"


@dataclass
class TakeoverRequest:
    """A request for a human to take over the browser.

    Carries the session, the reason, and the status — and nothing
    else. In particular there is no password, token, or secret
    field: the human signs in in their own browser, the bot
    never sees the credential, and the request only records that
    the human step happened.
    """

    request_id: str
    session_id: str
    agent_id: str
    reason: TakeoverReason
    status: TakeoverStatus = TakeoverStatus.PENDING
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None

    def resolve(self, status: TakeoverStatus) -> None:
        """Mark the request approved/denied/completed (once)."""
        if self.status is not TakeoverStatus.PENDING:
            raise ValueError(f"takeover request {self.request_id!r} is already {self.status}")
        if status is TakeoverStatus.PENDING:
            raise ValueError("cannot resolve a takeover request back to pending")
        self.status = status
        self.resolved_at = time.time()


@dataclass
class BrowserSession:
    """One browser session for one task of one agent.

    ``profile_dir`` is the agent's profile (one per agent, see
    :func:`profile_dir_for`). ``last_heartbeat_at`` is refreshed
    by the launcher while the session is alive; a session with
    no heartbeat for ``max_idle_s`` is an orphan and gets reaped.
    """

    session_id: str
    agent_id: str
    task_id: str
    profile_dir: Path
    created_at: float = field(default_factory=time.time)
    last_heartbeat_at: float = field(default_factory=time.time)
    closed: bool = False

    def heartbeat(self, *, now: float | None = None) -> None:
        """Record that this session is still alive."""
        self.last_heartbeat_at = now if now is not None else time.time()

    def is_orphan(self, *, max_idle_s: int = DEFAULT_MAX_IDLE_S, now: float | None = None) -> bool:
        """True when idle too long (or already closed)."""
        if self.closed:
            return True
        at = now if now is not None else time.time()
        return at - self.last_heartbeat_at > max_idle_s


class SessionRegistry:
    """In-memory bookkeeping for live browser sessions.

    Enforces the contract without a browser: one live session
    per agent (a second launch for the same agent raises
    :class:`puspa.computer.errors.BrowserSessionBusy`), one
    session per task, and reaping of orphans. The real launcher
    (post-MVP) owns one of these and drives a Chromium instance
    per registered session; until then the registry is what the
    contract tests exercise.
    """

    def __init__(self, *, clock=None) -> None:
        self._clock = clock if clock is not None else time.time
        self._sessions: dict[str, BrowserSession] = {}

    def _now(self) -> float:
        return float(self._clock())

    def register(
        self, agent_id: str, task_id: str, *, base=DEFAULT_PROFILES_BASE
    ) -> BrowserSession:
        """Open a session record for ``agent_id``/``task_id``.

        Raises :class:`puspa.computer.errors.BrowserSessionBusy`
        when the agent already has a live session.
        """
        for session in self._sessions.values():
            if session.agent_id == agent_id and not session.closed:
                raise BrowserSessionBusy(
                    f"agent {agent_id!r} already has live browser session "
                    f"{session.session_id!r} for task {session.task_id!r}; "
                    "close it (or let the launcher reap it) before launching another"
                )
        session = BrowserSession(
            session_id=f"browser-{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            task_id=task_id,
            profile_dir=profile_dir_for(agent_id, base=base),
            created_at=self._now(),
            last_heartbeat_at=self._now(),
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> BrowserSession | None:
        """A session by id, or None when unknown."""
        return self._sessions.get(session_id)

    def list_live(self) -> list[BrowserSession]:
        """Live (not closed) sessions, oldest first."""
        return sorted(
            (s for s in self._sessions.values() if not s.closed),
            key=lambda s: s.created_at,
        )

    def close(self, session_id: str) -> bool:
        """Close a session; True when it was live."""
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return False
        session.closed = True
        return True

    def reap_orphans(self, *, max_idle_s: int = DEFAULT_MAX_IDLE_S) -> list[str]:
        """Close every orphan session; return their ids.

        A session is an orphan when it has had no heartbeat for
        ``max_idle_s``. Reaping closes the record; the real
        launcher additionally kills the Chromium process.
        """
        now = self._now()
        reaped = []
        for session in self._sessions.values():
            if not session.closed and session.is_orphan(max_idle_s=max_idle_s, now=now):
                session.closed = True
                reaped.append(session.session_id)
        return reaped


class HumanTakeover(abc.ABC):
    """Interface for asking a human to take over the browser.

    The bot calls :meth:`request` when a page needs a human
    (sign-in, captcha, 2FA) and waits for the request to resolve.
    The human completes the step in their own browser; the bot
    never sees the password. Implementations must never accept
    or store a password.
    """

    @abc.abstractmethod
    def request(self, session: BrowserSession, reason: TakeoverReason) -> TakeoverRequest:
        """Open a takeover request for ``session``."""


class BrowserLauncher(abc.ABC):
    """Launches and reaps per-task browser sessions.

    The launcher owns a :class:`SessionRegistry` and, post-MVP,
    one Chromium instance per live session. Until then the only
    implementation is :class:`UnavailableBrowserLauncher`, which
    raises on launch. The contract methods below are what the
    post-MVP launcher must implement.
    """

    def __init__(self, registry: SessionRegistry | None = None) -> None:
        self.registry = registry if registry is not None else SessionRegistry()

    @abc.abstractmethod
    def launch(self, agent_id: str, task_id: str) -> BrowserSession:
        """Start (or reuse) a browser for ``agent_id``/``task_id``.

        Registers the session, starts Chromium with the agent's
        profile, and returns the session. One live session per
        agent: a second launch raises
        :class:`puspa.computer.errors.BrowserSessionBusy`.
        """

    @abc.abstractmethod
    def close(self, session_id: str) -> bool:
        """Close a session and its browser; True when it was live."""

    @abc.abstractmethod
    def request_takeover(self, session_id: str, reason: TakeoverReason) -> TakeoverRequest:
        """Ask a human to take over ``session_id`` for ``reason``."""

    def list_sessions(self) -> list[BrowserSession]:
        """Live sessions, oldest first."""
        return self.registry.list_live()

    def reap_orphans(self, *, max_idle_s: int = DEFAULT_MAX_IDLE_S) -> list[str]:
        """Close orphan sessions (no heartbeat for ``max_idle_s``)."""
        return self.registry.reap_orphans(max_idle_s=max_idle_s)


class UnavailableBrowserLauncher(BrowserLauncher):
    """The only launcher until after the MVP: no live browser.

    ``launch`` and ``request_takeover`` raise
    :class:`puspa.computer.errors.BrowserUnavailable` with a
    message that names the dependency (a Docker host or VPS
    plus the human-takeover UI) and the follow-up schedule,
    instead of pretending a browser exists. ``close`` and
    ``reap_orphans`` still work on the (empty) registry so the
    contract stays testable.
    """

    _MESSAGE = (
        "no live browser is available: computer use is deferred until after the MVP (G16). "
        "Follow-up needs a Docker host or VPS (containers cannot run on this VM) "
        "plus the human-takeover UI for sign-in/captcha/2FA. "
        "See docs/computer-use.md."
    )

    def launch(self, agent_id: str, task_id: str) -> BrowserSession:
        raise BrowserUnavailable(self._MESSAGE)

    def close(self, session_id: str) -> bool:
        return self.registry.close(session_id)

    def request_takeover(self, session_id: str, reason: TakeoverReason) -> TakeoverRequest:
        raise BrowserUnavailable(self._MESSAGE)
