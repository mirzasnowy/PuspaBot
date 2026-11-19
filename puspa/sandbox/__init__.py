"""Sandbox fail-closed (definisi 2.6) + G34 deepening (OpenShell pattern).

G34 adds, on top of the fail-closed Docker runner: persistent
sessions (:mod:`puspa.sandbox.session`), a filesystem API
(:mod:`puspa.sandbox.filesystem`), streaming exec
(:mod:`puspa.sandbox.exec`), policy objects
(:mod:`puspa.sandbox.policy`), resource quotas
(:mod:`puspa.sandbox.quota`), PTY terminals
(:mod:`puspa.sandbox.terminal`), and headless-browser sessions
(:mod:`puspa.sandbox.browser`). All new code is Puspa's own,
written from scratch after studying OpenShell's mechanisms
(see ``docs/sandbox-deepening.md``); no OpenShell code was
copied.

E-03 (plan 4.3) hardens the runner itself: the filtered egress
proxy (:mod:`puspa.sandbox.egress`, HTTP CONNECT + pinned DNS
+ hash-chained egress ledger), seccomp profiles
(:mod:`puspa.sandbox.seccomp`, vendored Docker default + a
strict variant), stronger-isolation runtimes with a spawn
measurement harness (:mod:`puspa.sandbox.runtime`), and a
warm pool that deletes instead of cleaning
(:mod:`puspa.sandbox.warmpool`).
"""

from .allowlist import DEFAULT_ALLOWLIST, Allowlist, is_allowed
from .browser import SandboxBrowserManager, SandboxBrowserSession, SandboxBrowserStatus
from .egress import (
    DEFAULT_DNS_SERVERS,
    DEFAULT_NETWORK_NAME,
    DNSResolutionError,
    EgressError,
    EgressLedger,
    EgressLogEntry,
    EgressLogSink,
    EgressProxy,
    EgressProxyConfig,
    InMemoryEgressSink,
    PinnedDNSResolver,
    is_blocked_ip,
)
from .exec import ExecChunk, ExecRequest, ExecResult, exec_stream, run_exec
from .filesystem import (
    DockerSandboxFilesystem,
    FileEntry,
    LocalSandboxFilesystem,
    SandboxFilesystem,
    filesystem_for_backend,
)
from .policy import (
    FilesystemPolicy,
    NetworkEndpoint,
    NetworkPolicy,
    PolicyError,
    SandboxPolicy,
)
from .quota import (
    DEFAULT_QUOTA,
    QuotaExceeded,
    QuotaRegistry,
    ResourceQuota,
    measure_disk_usage,
)
from .runner import DEFAULT_IMAGE, Sandbox, SandboxUnavailable, audit_docker_flags
from .runtime import (
    DEFAULT_SPAWN_TARGET_S,
    IsolationRuntime,
    IsolationRuntimeError,
    RuntimeConfig,
    SpawnMeasurement,
    docker_spawn_probe,
    evaluate_runtimes,
    list_docker_runtimes,
    measure_spawn,
)
from .seccomp import (
    DEFAULT_PROFILE_FILENAME,
    STRICT_PROFILE_FILENAME,
    STRICT_REMOVED_SYSCALLS,
    SeccompError,
    SeccompProfile,
    default_profile,
    derive_strict,
    load_profile,
    strict_profile,
    validate_profile_data,
)
from .session import SandboxSession, SessionManager, SessionStatus
from .terminal import DEFAULT_COLS, DEFAULT_ROWS, TerminalSession, start_terminal
from .warmpool import (
    DEFAULT_POOL_COMMAND,
    ContainerRuntime,
    DockerContainerRuntime,
    PoolError,
    WarmPool,
    WarmSlot,
)

__all__ = [
    "DEFAULT_ALLOWLIST",
    "DEFAULT_COLS",
    "DEFAULT_DNS_SERVERS",
    "DEFAULT_IMAGE",
    "DEFAULT_NETWORK_NAME",
    "DEFAULT_POOL_COMMAND",
    "DEFAULT_PROFILE_FILENAME",
    "DEFAULT_QUOTA",
    "DEFAULT_ROWS",
    "DEFAULT_SPAWN_TARGET_S",
    "STRICT_PROFILE_FILENAME",
    "STRICT_REMOVED_SYSCALLS",
    "Allowlist",
    "ContainerRuntime",
    "DNSResolutionError",
    "DockerContainerRuntime",
    "DockerSandboxFilesystem",
    "EgressError",
    "EgressLedger",
    "EgressLogEntry",
    "EgressLogSink",
    "EgressProxy",
    "EgressProxyConfig",
    "ExecChunk",
    "ExecRequest",
    "ExecResult",
    "FileEntry",
    "FilesystemPolicy",
    "InMemoryEgressSink",
    "IsolationRuntime",
    "IsolationRuntimeError",
    "LocalSandboxFilesystem",
    "NetworkEndpoint",
    "NetworkPolicy",
    "PinnedDNSResolver",
    "PolicyError",
    "PoolError",
    "QuotaExceeded",
    "QuotaRegistry",
    "ResourceQuota",
    "RuntimeConfig",
    "Sandbox",
    "SandboxBrowserManager",
    "SandboxBrowserSession",
    "SandboxBrowserStatus",
    "SandboxFilesystem",
    "SandboxPolicy",
    "SandboxSession",
    "SandboxUnavailable",
    "SeccompError",
    "SeccompProfile",
    "SessionManager",
    "SessionStatus",
    "SpawnMeasurement",
    "TerminalSession",
    "WarmPool",
    "WarmSlot",
    "audit_docker_flags",
    "default_profile",
    "derive_strict",
    "docker_spawn_probe",
    "evaluate_runtimes",
    "exec_stream",
    "filesystem_for_backend",
    "is_allowed",
    "is_blocked_ip",
    "list_docker_runtimes",
    "load_profile",
    "measure_disk_usage",
    "measure_spawn",
    "run_exec",
    "start_terminal",
    "strict_profile",
    "validate_profile_data",
]
