"""Public sandbox for single-user local use (split design 2.3).

Permission prompt first, then a workspace-bounded subprocess run
with a timeout, output caps, and secrets stripped from the child
environment. See :mod:`puspa.localexec.executor`.
"""

from .executor import (
    ExecutionResult,
    Executor,
    LocalExecutor,
    OutsideWorkspaceError,
    PermissionDecision,
    PermissionDenied,
    PermissionPrompt,
    ask_permission_interactively,
    scrub_env,
)

__all__ = [
    "ExecutionResult",
    "Executor",
    "LocalExecutor",
    "OutsideWorkspaceError",
    "PermissionDecision",
    "PermissionDenied",
    "PermissionPrompt",
    "ask_permission_interactively",
    "scrub_env",
]
