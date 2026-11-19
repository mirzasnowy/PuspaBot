"""Per-user / per-session resource quotas (G34, OpenShell RFC 0004 pattern).

OpenShell RFC 0004 replaced ad-hoc GPU request fields with
typed resource requirements on ``SandboxSpec``: portable
``compute`` requirements (CPU, memory) plus ``device``
requirements (GPUs), used by the gateway to prefilter drivers
and by drivers to provision, with driver-specific settings kept
in a separate escape hatch (``driver_config``) instead of
leaking into the portable shape. The gateway applies the
requirements before provisioning; a request no driver can
serve is refused, not silently downgraded.

Puspa takes that shape, rewritten for the Docker backend Puspa
already has:

- :class:`ResourceQuota` — the portable requirement: CPU
  cores, memory, disk, max processes. Its
  :meth:`ResourceQuota.docker_flags` extends the flags the
  runner already passes (256 MB, 0.5 CPU, 64 processes) rather
  than duplicating them: the runner now builds its flags from
  the quota. GPUs are *declared* (``gpu_count``) and refused:
  no Puspa host provisions a GPU yet, so asking for one is an
  error at quota-build time, not a silent CPU fallback.
- :class:`QuotaRegistry` — per-user limits: one default quota
  plus per-user overrides, a cap on live sessions per user,
  and the reserve/release bookkeeping session creation needs.
  Reserving beyond the cap raises :class:`QuotaExceeded`
  before any backend is built.
- :func:`measure_disk_usage` — disk accounting for a sandbox
  workspace, so the filesystem API can enforce ``disk_mb``
  before a write lands.

All limits are enforced at admission (session creation) or
before the write, never after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_QUOTA",
    "QuotaExceeded",
    "QuotaRegistry",
    "ResourceQuota",
    "measure_disk_usage",
]

#: The quota the runner already enforced before G34
#: (256 MB, 0.5 CPU, 64 processes): the default, so existing
#: behaviour does not change when no quota is given.
DEFAULT_CPU_CORES = 0.5
DEFAULT_MEMORY_MB = 256
DEFAULT_DISK_MB = 256
DEFAULT_MAX_PIDS = 64

#: Hard bounds for one session's quota. The upper bounds are
#: the largest single session a shared host may grant; the
#: lower bounds keep a session usable (below 64 MB of RAM a
#: Python process cannot reliably start).
MIN_CPU_CORES = 0.1
MAX_CPU_CORES = 16.0
MIN_MEMORY_MB = 64
MAX_MEMORY_MB = 16384
MIN_DISK_MB = 16
MAX_DISK_MB = 102400
MIN_MAX_PIDS = 1
MAX_MAX_PIDS = 1024

#: Default cap on live sessions per user (OpenShell's gateway
#: likewise bounds live sandboxes per caller; the number is
#: Puspa's own, chosen so one user cannot exhaust the host).
DEFAULT_MAX_SESSIONS_PER_USER = 5


class QuotaExceeded(ValueError):
    """A quota limit refuses the requested operation.

    Raised before the operation runs: before a session is
    created (too many live sessions for the user), or before a
    file is written (the write would exceed ``disk_mb``).
    """


def measure_disk_usage(root: str | Path) -> int:
    """Bytes used under ``root`` (files only, symlinks not followed).

    Missing directories count as zero. A symlink that points
    outside the root is not followed, so disk accounting can
    never be tricked into counting (or missing) host files.
    """
    base = Path(root)
    if not base.is_dir():
        return 0
    total = 0
    for path in base.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


@dataclass(frozen=True)
class ResourceQuota:
    """Portable resource requirements for one sandbox session.

    ``cpu_cores`` is fractional (``0.5`` = half a core),
    ``memory_mb`` / ``disk_mb`` are mebibytes, ``max_pids`` is
    the process cap. ``gpu_count`` must stay 0: Puspa has no
    GPU provisioning yet, and a GPU request is refused rather
    than silently served on CPU.
    """

    cpu_cores: float = DEFAULT_CPU_CORES
    memory_mb: int = DEFAULT_MEMORY_MB
    disk_mb: int = DEFAULT_DISK_MB
    max_pids: int = DEFAULT_MAX_PIDS
    gpu_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.cpu_cores, (int, float)) or isinstance(self.cpu_cores, bool):
            raise ValueError("cpu_cores must be a number")
        if not MIN_CPU_CORES <= self.cpu_cores <= MAX_CPU_CORES:
            raise ValueError(
                f"cpu_cores must be {MIN_CPU_CORES}-{MAX_CPU_CORES}, got {self.cpu_cores!r}"
            )
        for name, value, low, high in (
            ("memory_mb", self.memory_mb, MIN_MEMORY_MB, MAX_MEMORY_MB),
            ("disk_mb", self.disk_mb, MIN_DISK_MB, MAX_DISK_MB),
            ("max_pids", self.max_pids, MIN_MAX_PIDS, MAX_MAX_PIDS),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ValueError(f"{name} must be an int in {low}-{high}, got {value!r}")
        if not isinstance(self.gpu_count, int) or self.gpu_count < 0:
            raise ValueError(f"gpu_count must be a non-negative int, got {self.gpu_count!r}")
        if self.gpu_count > 0:
            raise ValueError(
                "gpu_count > 0 is not provisioned on any Puspa host yet; "
                "a GPU request is refused, never silently served on CPU"
            )

    def docker_flags(self) -> list[str]:
        """``docker run`` resource flags for this quota.

        These are the flags the runner already passed, now
        derived from the quota instead of hard-coded: memory,
        CPU, and the process cap. Disk is *not* a docker flag
        (there is no volume to cap); disk is enforced by
        :func:`measure_disk_usage` on the workspace instead.
        """
        return [
            "--memory",
            f"{self.memory_mb}m",
            "--cpus",
            str(self.cpu_cores),
            "--pids-limit",
            str(self.max_pids),
        ]

    def to_dict(self) -> dict:
        """Plain-dict form (for session records and logs)."""
        return {
            "cpu_cores": self.cpu_cores,
            "memory_mb": self.memory_mb,
            "disk_mb": self.disk_mb,
            "max_pids": self.max_pids,
            "gpu_count": self.gpu_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ResourceQuota:
        """Build from :meth:`to_dict` output; unknown keys are refused."""
        known = {"cpu_cores", "memory_mb", "disk_mb", "max_pids", "gpu_count"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown ResourceQuota keys: {', '.join(unknown)}")
        return cls(**data)


#: The quota a session gets when the caller does not name one.
DEFAULT_QUOTA = ResourceQuota()


class QuotaRegistry:
    """Per-user quotas and live-session accounting.

    One default quota applies to every user; individual users
    may get an override. The registry also counts live sessions
    per user and refuses a new reservation past
    ``max_sessions_per_user`` — the check session creation runs
    before it builds a backend, so an over-limit user never
    consumes host resources.
    """

    def __init__(
        self,
        default_quota: ResourceQuota | None = None,
        *,
        max_sessions_per_user: int = DEFAULT_MAX_SESSIONS_PER_USER,
    ) -> None:
        if max_sessions_per_user < 1:
            raise ValueError("max_sessions_per_user must be >= 1")
        self._default_quota = default_quota if default_quota is not None else DEFAULT_QUOTA
        self._max_sessions_per_user = max_sessions_per_user
        self._overrides: dict[str, ResourceQuota] = {}
        self._live: dict[str, int] = {}

    @property
    def max_sessions_per_user(self) -> int:
        return self._max_sessions_per_user

    def set_user_quota(self, user_id: str, quota: ResourceQuota) -> None:
        """Override the quota for one user (validated at build)."""
        if not user_id:
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(quota, ResourceQuota):
            raise TypeError("quota must be a ResourceQuota")
        self._overrides[user_id] = quota

    def quota_for(self, user_id: str) -> ResourceQuota:
        """The quota that applies to ``user_id`` (override or default)."""
        return self._overrides.get(user_id, self._default_quota)

    def active_sessions(self, user_id: str) -> int:
        """Live sessions currently reserved for ``user_id``."""
        return self._live.get(user_id, 0)

    def reserve(self, user_id: str) -> None:
        """Reserve one live-session slot for ``user_id``.

        Raises :class:`QuotaExceeded` past the per-user cap.
        Callers must call :meth:`release` when the session ends,
        including when admission fails after reserving.
        """
        if not user_id:
            raise ValueError("user_id must be a non-empty string")
        if self._live.get(user_id, 0) >= self._max_sessions_per_user:
            raise QuotaExceeded(
                f"user {user_id!r} already has {self._max_sessions_per_user} "
                "live sandbox sessions (the per-user cap); close one first"
            )
        self._live[user_id] = self._live.get(user_id, 0) + 1

    def release(self, user_id: str) -> None:
        """Free one live-session slot for ``user_id`` (never below 0)."""
        current = self._live.get(user_id, 0)
        if current <= 1:
            self._live.pop(user_id, None)
        else:
            self._live[user_id] = current - 1

    @staticmethod
    def check_disk(root: str | Path, quota: ResourceQuota, extra_bytes: int) -> int:
        """Check a write of ``extra_bytes`` fits ``quota.disk_mb``.

        Returns the projected usage in bytes. Raises
        :class:`QuotaExceeded` when the write would exceed the
        disk quota; the caller must not write in that case.
        """
        if extra_bytes < 0:
            raise ValueError("extra_bytes must be >= 0")
        used = measure_disk_usage(root)
        limit = quota.disk_mb * 1024 * 1024
        if used + extra_bytes > limit:
            raise QuotaExceeded(
                f"disk quota exceeded: {used + extra_bytes} bytes would exceed "
                f"the {quota.disk_mb} MiB workspace quota "
                f"({used} bytes already used)"
            )
        return used + extra_bytes
