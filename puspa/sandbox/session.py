"""Persistent sandbox sessions (G34, OpenShell session model, rewritten).

OpenShell treats a sandbox as a long-lived, named resource with
a lifecycle (``SandboxPhase``: provisioning, ready, error,
deleting, stopping, stopped, completed, suspended), a retained
identity across restarts, an idle/orphan rule (sessions with no
heartbeat are reaped), and a recovery timeout after losing
supervision. Exec, file transfer, and terminal attachment all
happen *inside* an existing session; there is no anonymous
one-shot execution path in the public API.

Puspa takes that model and adapts it to the backend ABC Puspa
already has (:mod:`puspa.computer.backends`):

- :class:`SandboxSession` — the record: id, owner, backend,
  workdir, env, quota, network policy, status, lifetime and
  idle timeout. ``READY`` means *admitted*: policy validated,
  quota reserved, backend available. It does not mean a
  container is running — the Docker backend still runs one
  ephemeral container per exec (see :mod:`puspa.sandbox.runner`),
  so a session is a control-plane record plus a workspace, not
  a live process.
- :class:`SessionManager` — create/get/list/kill plus reaping
  of expired and idle sessions. Creation is admission in
  OpenShell's order: validate the policy, reserve the quota,
  then build the backend; any failure releases the reservation
  and leaves no session behind. A Docker session whose backend
  reports unavailable fails admission with
  :class:`puspa.computer.errors.BackendUnavailable` — the
  fail-closed answer, never a silent local fallback.
- Env is scrubbed the same way the local backend scrubs it:
  credential-looking keys are refused outright (credentials
  travel through the broker, never in raw env — see
  :mod:`puspa.computer.broker`).

The manager is in-memory and process-local (like the G16
browser :class:`puspa.computer.browser.SessionRegistry`);
durability across restarts is a later step, not faked here.
"""

from __future__ import annotations

import enum
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .policy import SandboxPolicy
from .quota import DEFAULT_QUOTA, QuotaRegistry, ResourceQuota

__all__ = [
    "CREDENTIAL_ENV_HINTS",
    "SandboxSession",
    "SessionManager",
    "SessionStatus",
]

#: Default session lifetime (1 hour) and idle timeout (15 min,
#: the same default the G16 browser registry uses for orphans).
DEFAULT_LIFETIME_S = 3600
DEFAULT_IDLE_TIMEOUT_S = 900

#: Environment keys that smell like a credential. Mirrors the
#: local backend's rule (:mod:`puspa.computer.local`): any of
#: these in a caller-supplied ``env`` is refused, because a raw
#: credential in a session env is exactly the leak the broker
#: exists to prevent.
CREDENTIAL_ENV_HINTS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE",
    "BEARER",
    "SESSION",
    "COOKIE",
    "AUTH",
)


def _looks_like_credential(key: str) -> bool:
    upper = key.upper()
    return any(hint in upper for hint in CREDENTIAL_ENV_HINTS)


class SessionStatus(enum.Enum):
    """Lifecycle of one sandbox session (OpenShell ``SandboxPhase``, narrowed).

    ``CREATED`` is the record before admission finishes;
    ``READY`` means admitted (policy + quota + backend all
    passed); ``RUNNING`` means an exec is in flight right now;
    ``CLOSED`` is terminal (killed, expired, or reaped).
    ``ERROR`` marks a session whose backend failed after
    admission (the record stays for inspection).
    """

    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    CLOSED = "closed"
    ERROR = "error"


@dataclass
class SandboxSession:
    """One persistent sandbox session for one user.

    ``workdir`` is the session workspace on this host (the
    scope root for the local backend; the recorded workspace
    for the Docker backend, whose containers have no mounts).
    ``env`` is the scrubbed environment merged into every exec
    in this session. ``last_activity_at`` is refreshed by
    :meth:`SessionManager.heartbeat` and by every exec, so idle
    reaping measures real use, not creation time.
    """

    session_id: str
    user_id: str
    backend_name: str
    workdir: Path
    env: dict[str, str] = field(default_factory=dict)
    quota: ResourceQuota = field(default_factory=lambda: DEFAULT_QUOTA)
    policy: SandboxPolicy = field(default_factory=SandboxPolicy)
    status: SessionStatus = SessionStatus.CREATED
    created_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    lifetime_s: int = DEFAULT_LIFETIME_S
    idle_timeout_s: int = DEFAULT_IDLE_TIMEOUT_S

    def touch(self, *, now: float | None = None) -> None:
        """Record activity (resets the idle clock)."""
        self.last_activity_at = now if now is not None else time.time()

    def is_expired(self, *, now: float | None = None) -> bool:
        """True when the session outlived ``lifetime_s``."""
        at = now if now is not None else time.time()
        return at - self.created_at >= self.lifetime_s

    def is_idle(self, *, now: float | None = None) -> bool:
        """True when idle longer than ``idle_timeout_s`` (or closed)."""
        if self.status is SessionStatus.CLOSED:
            return True
        at = now if now is not None else time.time()
        return at - self.last_activity_at > self.idle_timeout_s

    def to_dict(self) -> dict:
        """Plain-dict form (for logs and status snapshots)."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "backend_name": self.backend_name,
            "workdir": str(self.workdir),
            "status": self.status.value,
            "quota": self.quota.to_dict(),
            "network_mode": self.policy.network.docker_network_mode(),
            "created_at": self.created_at,
            "last_activity_at": self.last_activity_at,
            "lifetime_s": self.lifetime_s,
            "idle_timeout_s": self.idle_timeout_s,
        }


class SessionManager:
    """Create, track, and reap sandbox sessions.

    ``backend_factory`` builds the backend for a session
    (``(backend_name, workdir) -> ExecutionBackend``); the
    default builds the registered backends from
    :mod:`puspa.computer.backends`. ``clock`` is an injectable
    time source (tests pass a fake clock; production uses
    :func:`time.time`).
    """

    def __init__(
        self,
        base_dir: str | Path | None = None,
        *,
        backend_factory: Callable | None = None,
        registry: QuotaRegistry | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if base_dir is None:
            base_dir = Path(tempfile.mkdtemp(prefix="puspa-sandbox-sessions-"))
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._backend_factory = backend_factory or _default_backend_factory
        self._registry = registry if registry is not None else QuotaRegistry()
        self._clock = clock if clock is not None else time.time
        self._sessions: dict[str, SandboxSession] = {}
        self._backends: dict[str, object] = {}

    def _now(self) -> float:
        return float(self._clock())

    # -- creation (admission, OpenShell Attach + Confirm order) ----------

    def create(
        self,
        user_id: str,
        *,
        backend_name: str = "local",
        quota: ResourceQuota | None = None,
        policy: SandboxPolicy | None = None,
        env: dict[str, str] | None = None,
        lifetime_s: int = DEFAULT_LIFETIME_S,
        idle_timeout_s: int = DEFAULT_IDLE_TIMEOUT_S,
    ) -> SandboxSession:
        """Create and admit a session.

        Admission order: (1) validate the policy, (2) reserve
        the per-user quota slot, (3) build the backend and
        check it is available. Any failure releases the
        reservation and leaves no session behind. A Docker
        backend that cannot run here fails step (3) with
        :class:`BackendUnavailable` — fail-closed, never a
        silent fallback to the local backend.
        """
        if not user_id:
            raise ValueError("user_id must be a non-empty string")
        if lifetime_s <= 0 or idle_timeout_s <= 0:
            raise ValueError("lifetime_s and idle_timeout_s must be > 0")
        checked_policy = policy if policy is not None else SandboxPolicy()
        checked_policy.validate()
        checked_quota = quota if quota is not None else self._registry.quota_for(user_id)
        clean_env = self._scrub_env(env)
        self._registry.reserve(user_id)
        session_id = f"sandbox-{uuid.uuid4().hex[:12]}"
        workdir = self.base_dir / session_id
        try:
            workdir.mkdir(parents=True, exist_ok=True)
            backend = self._backend_factory(backend_name, workdir)
            if not backend.check_available():
                from puspa.computer.errors import BackendUnavailable

                raise BackendUnavailable(
                    f"backend {backend_name!r} cannot run on this host; "
                    "session admission refused (fail-closed, no fallback "
                    "to another backend)"
                )
        except Exception:
            self._registry.release(user_id)
            raise
        session = SandboxSession(
            session_id=session_id,
            user_id=user_id,
            backend_name=backend_name,
            workdir=workdir,
            env=clean_env,
            quota=checked_quota,
            policy=checked_policy,
            status=SessionStatus.READY,
            created_at=self._now(),
            last_activity_at=self._now(),
            lifetime_s=lifetime_s,
            idle_timeout_s=idle_timeout_s,
        )
        self._sessions[session_id] = session
        self._backends[session_id] = backend
        return session

    # -- lookup ------------------------------------------------------------

    def get(self, session_id: str) -> SandboxSession | None:
        """A session by id, or None when unknown."""
        return self._sessions.get(session_id)

    def backend_for(self, session_id: str):
        """The backend built for ``session_id`` (None when unknown)."""
        return self._backends.get(session_id)

    def list_sessions(
        self,
        user_id: str | None = None,
        *,
        status: SessionStatus | None = None,
    ) -> list[SandboxSession]:
        """Sessions, oldest first, optionally filtered."""
        sessions = sorted(self._sessions.values(), key=lambda s: s.created_at)
        if user_id is not None:
            sessions = [s for s in sessions if s.user_id == user_id]
        if status is not None:
            sessions = [s for s in sessions if s.status is status]
        return sessions

    def heartbeat(self, session_id: str, *, now: float | None = None) -> bool:
        """Refresh a session's idle clock; False when unknown/closed."""
        session = self._sessions.get(session_id)
        if session is None or session.status is SessionStatus.CLOSED:
            return False
        session.touch(now=now if now is not None else self._now())
        return True

    # -- teardown ------------------------------------------------------------

    def kill(self, session_id: str, *, delete_workdir: bool = False) -> bool:
        """Close a session and free its quota slot.

        True when the session was live. The workspace is kept
        by default (for inspection after the session ends);
        pass ``delete_workdir=True`` to remove it. Killing an
        already-closed session is a no-op that returns False.
        """
        session = self._sessions.get(session_id)
        if session is None or session.status is SessionStatus.CLOSED:
            return False
        session.status = SessionStatus.CLOSED
        backend = self._backends.pop(session_id, None)
        if backend is not None:
            close = getattr(backend, "close", None)
            if callable(close):
                close()
        self._registry.release(session.user_id)
        if delete_workdir:
            _remove_tree(session.workdir)
        return True

    def reap_expired(self, *, now: float | None = None) -> list[str]:
        """Close every expired or idle session; return their ids.

        A session is reaped when its lifetime ran out or it has
        had no activity for ``idle_timeout_s`` — OpenShell's
        orphan rule, applied to Puspa sessions. Reaping closes
        the record and frees the quota slot; the workspace is
        kept (see :meth:`kill`).
        """
        at = now if now is not None else self._now()
        reaped = []
        for session in list(self._sessions.values()):
            if session.status is SessionStatus.CLOSED:
                continue
            if (session.is_expired(now=at) or session.is_idle(now=at)) and self.kill(
                session.session_id
            ):
                reaped.append(session.session_id)
        return reaped

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _scrub_env(env: dict[str, str] | None) -> dict[str, str]:
        """Validate session env: no credential-looking keys, str values."""
        if not env:
            return {}
        clean: dict[str, str] = {}
        for key, value in env.items():
            if not isinstance(key, str) or not key:
                raise ValueError("env keys must be non-empty strings")
            if not isinstance(value, str):
                raise ValueError(f"env value for {key!r} must be str")
            if _looks_like_credential(key):
                raise ValueError(
                    f"refusing credential-looking env key {key!r}: "
                    "credentials travel through the broker, never in raw env"
                )
            clean[key] = value
        return clean


def _default_backend_factory(backend_name: str, workdir: Path):
    """Build a registered backend for a session workspace."""
    from puspa.computer.backends import get_backend

    return get_backend(backend_name, workdir=workdir)


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
