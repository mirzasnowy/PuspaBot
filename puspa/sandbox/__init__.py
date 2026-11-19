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
"""

from .allowlist import DEFAULT_ALLOWLIST, Allowlist, is_allowed
from .browser import SandboxBrowserManager, SandboxBrowserSession, SandboxBrowserStatus
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
from .runner import DEFAULT_IMAGE, Sandbox, SandboxUnavailable
from .session import SandboxSession, SessionManager, SessionStatus
from .terminal import DEFAULT_COLS, DEFAULT_ROWS, TerminalSession, start_terminal

__all__ = [
    "DEFAULT_ALLOWLIST",
    "DEFAULT_COLS",
    "DEFAULT_IMAGE",
    "DEFAULT_QUOTA",
    "DEFAULT_ROWS",
    "Allowlist",
    "DockerSandboxFilesystem",
    "ExecChunk",
    "ExecRequest",
    "ExecResult",
    "FileEntry",
    "FilesystemPolicy",
    "LocalSandboxFilesystem",
    "NetworkEndpoint",
    "NetworkPolicy",
    "PolicyError",
    "QuotaExceeded",
    "QuotaRegistry",
    "ResourceQuota",
    "Sandbox",
    "SandboxBrowserManager",
    "SandboxBrowserSession",
    "SandboxBrowserStatus",
    "SandboxFilesystem",
    "SandboxPolicy",
    "SandboxSession",
    "SandboxUnavailable",
    "SessionManager",
    "SessionStatus",
    "TerminalSession",
    "exec_stream",
    "filesystem_for_backend",
    "is_allowed",
    "measure_disk_usage",
    "run_exec",
    "start_terminal",
]
