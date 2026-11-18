"""Computer use / per-bot sandbox (G16) — principles first.

G16 is BIG and deferred until after the MVP: Puspa does NOT
build computer use / live browser automation now. What this
package builds is the principles plus the real seams, so the
post-MVP work plugs into tested interfaces instead of starting
from nothing:

- :mod:`puspa.computer.backends` — the execution-backend ABC
  plus the name -> variant registry (``local`` + ``docker``),
  with one conformance suite run against every backend.
- :mod:`puspa.computer.local` — subprocess backend (test/dev
  only, not a security boundary).
- :mod:`puspa.computer.docker` — the hardened Docker sandbox
  backend (production; honestly unavailable on this VM).
- :mod:`puspa.computer.scope` + :mod:`puspa.computer.sync` —
  the sandbox scope and file sync, separated from execution.
- :mod:`puspa.computer.config` — backend selection by config.
- :mod:`puspa.computer.broker` — the credential-broker
  interface (credentials never in raw env) plus an in-memory
  test broker. No real broker service yet.
- :mod:`puspa.computer.browser` — the browser-session contract
  (one profile per agent, one session per task, explicit human
  takeover, launcher that reaps orphans). Interface only: no
  CDP driving, no live browser.

See ``docs/computer-use.md`` for the principles, the backend
table, the broker rules, the browser-session contract, the
honest limits, and the post-MVP follow-up schedule.
"""

from .backends import (
    BACKENDS,
    CommandResult,
    ExecutionBackend,
    get_backend,
    list_backends,
    register_backend,
)
from .broker import (
    BROKER_TOKEN_ENV,
    BrokerToken,
    CredentialBroker,
    InMemoryBroker,
    NoSuchCredential,
)
from .browser import (
    BrowserLauncher,
    BrowserSession,
    HumanTakeover,
    SessionRegistry,
    TakeoverReason,
    TakeoverRequest,
    TakeoverStatus,
    UnavailableBrowserLauncher,
    profile_dir_for,
)
from .config import ENV_BACKEND, ComputerConfig
from .docker import DockerBackend
from .errors import (
    BackendError,
    BackendUnavailable,
    BrowserSessionBusy,
    BrowserUnavailable,
    TokenExpired,
    TokenRevoked,
    UnknownToken,
)
from .local import LocalBackend
from .scope import SandboxScope
from .sync import FileSync, LocalFileSync, NoFileSync

__all__ = [
    "BACKENDS",
    "BROKER_TOKEN_ENV",
    "ENV_BACKEND",
    "BackendError",
    "BackendUnavailable",
    "BrokerToken",
    "BrowserLauncher",
    "BrowserSession",
    "BrowserSessionBusy",
    "BrowserUnavailable",
    "CommandResult",
    "ComputerConfig",
    "CredentialBroker",
    "DockerBackend",
    "ExecutionBackend",
    "FileSync",
    "HumanTakeover",
    "InMemoryBroker",
    "LocalBackend",
    "LocalFileSync",
    "NoFileSync",
    "NoSuchCredential",
    "SandboxScope",
    "SessionRegistry",
    "TakeoverReason",
    "TakeoverRequest",
    "TakeoverStatus",
    "TokenExpired",
    "TokenRevoked",
    "UnavailableBrowserLauncher",
    "UnknownToken",
    "get_backend",
    "list_backends",
    "profile_dir_for",
    "register_backend",
]
