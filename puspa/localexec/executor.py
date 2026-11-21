"""Public sandbox for single-user local use (split design 2.3).

The local runner: a permission prompt first (approve once, approve
for the session, or deny — the Claude Code model), then a direct
subprocess run on the user's own machine, bounded to the user's
workspace directory, with a timeout, stdout/stderr size caps, and
secrets stripped from the child process environment.

This is a SEPARATE implementation written fresh for the public repo.
It contains zero private-sandbox machinery: no kernel-level syscall
filters, no shared warm process pools, no cross-tenant quotas, no
multi-tenant isolation, no container backends, no network egress
rules for shared infra. The public core calls it through the
:class:`Executor` protocol; the hosted server binds its own sandbox
behind the same protocol.
"""

from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404 - running local commands is this module's job
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

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

#: Default per-run timeout (seconds) when the caller passes none.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Default cap on captured stdout / stderr each (bytes).
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000

#: Environment variable name fragments treated as secrets. A child
#: process never sees a variable whose upper-cased name contains one
#: of these. The vault master key (``PUSPA_VAULT_MASTER_KEY``) is
#: caught by the ``KEY`` fragment.
SECRET_ENV_FRAGMENTS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "AUTH",
    "CREDENTIAL",
    "PRIVATE",
    "BEARER",
    "SESSION",
    "COOKIE",
    "SIGNING",
)


class PermissionDecision(StrEnum):
    """What the permission prompt answered for one command."""

    APPROVE_ONCE = "once"
    APPROVE_SESSION = "session"
    DENY = "deny"


class PermissionDenied(Exception):
    """The user denied the command at the permission prompt."""


class OutsideWorkspaceError(ValueError):
    """The requested working directory is outside the workspace."""


class PermissionPrompt(Protocol):
    """Ask the user whether a command may run. Returns a decision."""

    def ask(self, command: str | Sequence[str], cwd: Path) -> PermissionDecision: ...


def ask_permission_interactively(command: str | Sequence[str], cwd: Path) -> PermissionDecision:
    """Default prompt: read the answer from stdin (fail-closed).

    ``y`` runs this command once, ``a`` approves every later command
    in this session, anything else (including an empty answer) denies.
    """
    shown = command if isinstance(command, str) else shlex.join(str(part) for part in command)
    print(f"Run in {cwd}?\n  {shown}")
    try:
        answer = input("[y] once / [a] always this session / [n] deny: ").strip().lower()
    except EOFError:
        return PermissionDecision.DENY
    if answer in {"y", "yes"}:
        return PermissionDecision.APPROVE_ONCE
    if answer in {"a", "always"}:
        return PermissionDecision.APPROVE_SESSION
    return PermissionDecision.DENY


def scrub_env(env: Mapping[str, str]) -> dict[str, str]:
    """Copy ``env`` without secret-bearing variables.

    A variable is dropped when its upper-cased name contains any
    fragment in :data:`SECRET_ENV_FRAGMENTS`. The connection string
    (``PUSPA_DSN`` / ``PUSPA_TEST_DSN``) is deliberately kept: it
    points at the user's own local database, and DB-backed tools
    read it from the environment.
    """
    return {
        key: value
        for key, value in env.items()
        if not any(fragment in key.upper() for fragment in SECRET_ENV_FRAGMENTS)
    }


@dataclass(frozen=True)
class ExecutionResult:
    """The outcome of one :meth:`Executor.execute` call."""

    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


@runtime_checkable
class Executor(Protocol):
    """The execution port: run a command, return its captured result.

    ``command`` is a shell-free argv list (or a string, split with
    :func:`shlex.split`); ``cwd`` defaults to the workspace root;
    ``timeout`` is seconds (``None`` means the executor's default).
    """

    def execute(
        self,
        command: str | Sequence[str],
        cwd: str | Path | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult: ...


class LocalExecutor:
    """Run commands on the user's own machine, after a prompt.

    The workspace bound is enforced on the resolved working
    directory: a ``cwd`` outside the workspace is refused before
    the prompt is even asked.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        prompt: PermissionPrompt | None = None,
        default_timeout: float | None = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self._prompt = prompt
        self._default_timeout = default_timeout
        self._max_output_bytes = max_output_bytes
        self._session_approved = False

    def _resolve_cwd(self, cwd: str | Path | None) -> Path:
        target = Path(cwd).expanduser().resolve() if cwd is not None else self.workspace
        if target != self.workspace and self.workspace not in target.parents:
            raise OutsideWorkspaceError(
                f"working directory {target} is outside the workspace {self.workspace}"
            )
        return target

    def _ask(self, argv: tuple[str, ...], cwd: Path) -> PermissionDecision:
        if self._session_approved:
            return PermissionDecision.APPROVE_ONCE
        prompt = self._prompt
        if prompt is None:
            return ask_permission_interactively(argv, cwd)
        if callable(prompt):
            return prompt(argv, cwd)
        return prompt.ask(argv, cwd)

    @staticmethod
    def _cap(data: bytes, limit: int) -> tuple[str, bool]:
        truncated = len(data) > limit
        return data[:limit].decode("utf-8", "replace"), truncated

    def execute(
        self,
        command: str | Sequence[str],
        cwd: str | Path | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Prompt first, then run the command inside the workspace."""
        target = self._resolve_cwd(cwd)
        argv = tuple(
            shlex.split(command) if isinstance(command, str) else (str(p) for p in command)
        )
        if not argv:
            raise ValueError("command must not be empty")
        decision = self._ask(argv, target)
        if decision is PermissionDecision.DENY:
            raise PermissionDenied(f"denied by the user: {shlex.join(argv)}")
        if decision is PermissionDecision.APPROVE_SESSION:
            self._session_approved = True
        effective_timeout = timeout if timeout is not None else self._default_timeout
        # nosec B603 - argv list, shell=False: no shell is ever involved.
        proc = subprocess.Popen(  # nosec B603
            list(argv),
            cwd=str(target),
            env=scrub_env(os.environ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        try:
            out, err = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            stdout, out_cut = self._cap(out, self._max_output_bytes)
            stderr, err_cut = self._cap(err, self._max_output_bytes)
            return ExecutionResult(
                command=argv,
                returncode=None,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                truncated=out_cut or err_cut,
            )
        stdout, out_cut = self._cap(out, self._max_output_bytes)
        stderr, err_cut = self._cap(err, self._max_output_bytes)
        return ExecutionResult(
            command=argv,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            truncated=out_cut or err_cut,
        )
