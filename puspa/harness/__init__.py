"""Coding harness (Fase Otak): a real engineering loop.

See :mod:`puspa.harness.loop` for the loop and
:mod:`puspa.harness.executor` for the executors. Production runs
execute generated code ONLY in the Docker sandbox
(:class:`SandboxExecutor`); the local executor is test-only.
"""

from .executor import (
    ExecutionResult,
    Executor,
    LocalTestExecutor,
    SandboxExecutor,
    SandboxUnavailable,
)
from .loop import (
    AttemptRecord,
    CodingHarness,
    CodingTask,
    HarnessError,
    HarnessOutcome,
    create_production_harness,
    default_verdict,
    tool_permission_for,
)

__all__ = [
    "AttemptRecord",
    "CodingHarness",
    "CodingTask",
    "ExecutionResult",
    "Executor",
    "HarnessError",
    "HarnessOutcome",
    "LocalTestExecutor",
    "SandboxExecutor",
    "SandboxUnavailable",
    "create_production_harness",
    "default_verdict",
    "tool_permission_for",
]
