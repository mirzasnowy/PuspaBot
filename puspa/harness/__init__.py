"""Coding harness (Fase Otak): a real engineering loop.

See :mod:`puspa.harness.loop` for the loop and
:mod:`puspa.harness.executor` for the executors. Production runs
execute generated code ONLY in the Docker sandbox
(:class:`SandboxExecutor`); the local executor is test-only.
Surgical edits, reproduce-first, and sandbox profiles live in
:mod:`puspa.harness.diff`, :mod:`puspa.harness.reproduce`,
:mod:`puspa.harness.repo_map`, and
:mod:`puspa.harness.sandbox_profiles`.
"""

from .diff import (
    DiffApplyError,
    DiffHunk,
    ParsedDiff,
    apply_diff,
    generate_diff,
    is_diff,
    parse_diff,
)
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
from .repo_map import (
    RepoMap,
    RepoMapEntry,
    build_repo_map,
    estimate_tokens,
    render_repo_map,
)
from .reproduce import (
    ReproduceFirst,
    ReproduceFirstError,
    Reproduction,
    build_reproduction,
    generate_reproduction_stub,
)
from .sandbox_profiles import (
    PROFILE_FULL,
    PROFILE_READ_ONLY,
    PROFILE_WORKSPACE_WRITE,
    PROFILES,
    ProfileDenied,
    SandboxProfile,
    check_tool,
    check_write_path,
    effective_tool_level,
    get_profile,
    profile_allows_tool,
    requires_approval,
)

__all__ = [
    "AttemptRecord",
    "CodingHarness",
    "CodingTask",
    "DiffApplyError",
    "DiffHunk",
    "ExecutionResult",
    "Executor",
    "HarnessError",
    "HarnessOutcome",
    "LocalTestExecutor",
    "PROFILES",
    "PROFILE_FULL",
    "PROFILE_READ_ONLY",
    "PROFILE_WORKSPACE_WRITE",
    "ParsedDiff",
    "ProfileDenied",
    "RepoMap",
    "RepoMapEntry",
    "ReproduceFirst",
    "ReproduceFirstError",
    "Reproduction",
    "SandboxExecutor",
    "SandboxProfile",
    "SandboxUnavailable",
    "apply_diff",
    "build_repo_map",
    "build_reproduction",
    "check_tool",
    "check_write_path",
    "create_production_harness",
    "default_verdict",
    "effective_tool_level",
    "estimate_tokens",
    "generate_diff",
    "generate_reproduction_stub",
    "get_profile",
    "is_diff",
    "parse_diff",
    "profile_allows_tool",
    "render_repo_map",
    "requires_approval",
    "tool_permission_for",
]
