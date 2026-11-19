"""Headless-browser sessions inside the sandbox (G34) — contract + bookkeeping.

OpenShell has no browser of its own: the sandbox is a runtime
for agents, and a browser is a workload that runs inside it
(a Chromium launched by the agent, reached over its own
DevTools endpoint, with network egress still mediated by the
supervisor's proxy). Puspa's G16 browser contract already
covers the human side of that story — one profile per agent,
one session per task, explicit human takeover for sign-in /
captcha / 2FA, credentials only through the broker
(:mod:`puspa.computer.browser`).

This module is the sandbox side of the same contract, for a
browser that runs *inside* a sandbox session:

- :class:`SandboxBrowserSession` — the record: which sandbox
  session it belongs to, whose profile directory (inside the
  sandbox workdir, never on the host), the planned CDP
  endpoint, and its status. ``PENDING_HOST`` means the record
  exists but no browser is running yet: the CDP endpoint is
  only a plan until a container host exists.
- :class:`SandboxBrowserManager` — one live browser session
  per sandbox session (a second launch raises
  :class:`puspa.computer.errors.BrowserSessionBusy`), plus
  get/list/close. :meth:`SandboxBrowserManager.launch` raises
  :class:`puspa.computer.errors.BrowserUnavailable` until a
  container host can run Chromium: there is no live browser
  on this VM, and this module never fakes one (no local
  Chromium is launched as a stand-in — a host browser would
  not be inside the sandbox at all).

Human takeover, when a page needs a human, goes through the
G16 :class:`puspa.computer.browser.HumanTakeover` interface;
there is no password, token, or secret field anywhere in this
module, by design.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .session import SandboxSession

__all__ = [
    "SandboxBrowserManager",
    "SandboxBrowserSession",
    "SandboxBrowserStatus",
]


class SandboxBrowserStatus(enum.Enum):
    """Lifecycle of a sandboxed headless-browser session."""

    PENDING_HOST = "pending_host"
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass
class SandboxBrowserSession:
    """One headless-browser session inside one sandbox session.

    ``profile_dir`` lives under the sandbox workdir, so the
    browser profile (cookies, logins, history) is scoped to
    the sandbox session and dies with it. ``cdp_url`` is the
    planned Chrome DevTools endpoint: ``None`` while the
    session is ``PENDING_HOST`` (no browser is running yet),
    set only when a real browser starts on a container host.
    """

    session_id: str
    sandbox_session_id: str
    user_id: str
    profile_dir: Path
    status: SandboxBrowserStatus = SandboxBrowserStatus.PENDING_HOST
    cdp_url: str | None = None
    created_at: float = field(default_factory=time.time)
    last_heartbeat_at: float = field(default_factory=time.time)

    def heartbeat(self, *, now: float | None = None) -> None:
        """Record that this browser session is still alive."""
        self.last_heartbeat_at = now if now is not None else time.time()

    def to_dict(self) -> dict:
        """Plain-dict form (for logs and status snapshots)."""
        return {
            "session_id": self.session_id,
            "sandbox_session_id": self.sandbox_session_id,
            "user_id": self.user_id,
            "profile_dir": str(self.profile_dir),
            "status": self.status.value,
            "cdp_url": self.cdp_url,
            "created_at": self.created_at,
        }


class SandboxBrowserManager:
    """Bookkeeping for headless-browser sessions in sandboxes.

    Enforces the contract without a browser: one live browser
    session per sandbox session, profile directories scoped to
    the sandbox workdir, and a ``launch`` that refuses honestly
    until a container host exists. The real launcher (on a
    Docker host or VPS) starts Chromium inside the sandbox
    container with ``--remote-debugging-port`` and records the
    CDP endpoint here; until then the record stays
    ``PENDING_HOST``.
    """

    _MESSAGE = (
        "no live headless browser is available inside the sandbox: "
        "a browser session needs a running container with Chromium "
        "and a CDP endpoint, and containers cannot run on this host "
        "(the kernel rejects the setns syscall). First live check is "
        "on a Docker host or VPS, after the MVP. See "
        "docs/sandbox-deepening.md."
    )

    def __init__(self, *, clock=None) -> None:
        self._clock = clock if clock is not None else time.time
        self._sessions: dict[str, SandboxBrowserSession] = {}

    def _now(self) -> float:
        return float(self._clock())

    def create(self, sandbox_session: SandboxSession) -> SandboxBrowserSession:
        """Open a browser-session record for ``sandbox_session``.

        Raises :class:`BrowserSessionBusy` when the sandbox
        session already has a live browser session. The profile
        directory is created under the sandbox workdir (scoped,
        never on the host outside the session).
        """
        from puspa.computer.errors import BrowserSessionBusy

        for session in self._sessions.values():
            if (
                session.sandbox_session_id == sandbox_session.session_id
                and session.status is not SandboxBrowserStatus.CLOSED
            ):
                raise BrowserSessionBusy(
                    f"sandbox session {sandbox_session.session_id!r} already has "
                    f"live browser session {session.session_id!r}; close it "
                    "before opening another"
                )
        profile_dir = sandbox_session.workdir / "browser-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        record = SandboxBrowserSession(
            session_id=f"sb-browser-{uuid.uuid4().hex[:12]}",
            sandbox_session_id=sandbox_session.session_id,
            user_id=sandbox_session.user_id,
            profile_dir=profile_dir,
            created_at=self._now(),
            last_heartbeat_at=self._now(),
        )
        self._sessions[record.session_id] = record
        return record

    def launch(self, session_id: str) -> SandboxBrowserSession:
        """Start the browser for ``session_id``.

        Always raises :class:`BrowserUnavailable` on this host:
        no container can run here, so no Chromium can run
        inside a sandbox here. The record stays
        ``PENDING_HOST``; nothing is faked.
        """
        from puspa.computer.errors import BrowserUnavailable

        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"unknown browser session {session_id!r}")
        if session.status is SandboxBrowserStatus.CLOSED:
            raise BrowserUnavailable(f"browser session {session_id!r} is closed; create a new one")
        raise BrowserUnavailable(self._MESSAGE)

    def get(self, session_id: str) -> SandboxBrowserSession | None:
        """A browser session by id, or None when unknown."""
        return self._sessions.get(session_id)

    def list_live(self) -> list[SandboxBrowserSession]:
        """Live (not closed) browser sessions, oldest first."""
        return sorted(
            (s for s in self._sessions.values() if s.status is not SandboxBrowserStatus.CLOSED),
            key=lambda s: s.created_at,
        )

    def close(self, session_id: str) -> bool:
        """Close a browser session; True when it was live."""
        session = self._sessions.get(session_id)
        if session is None or session.status is SandboxBrowserStatus.CLOSED:
            return False
        session.status = SandboxBrowserStatus.CLOSED
        return True
