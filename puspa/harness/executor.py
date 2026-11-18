"""Code execution backends for the coding harness.

Two executors exist, and the difference between them is the whole
security point of this module:

- :class:`SandboxExecutor` is the ONLY production executor. It runs
  generated code through :class:`puspa.sandbox.Sandbox` (a throwaway
  Docker container, no network). When the sandbox cannot run, it
  raises :class:`puspa.sandbox.SandboxUnavailable` and NO code runs
  anywhere. There is no host fallback in this module, in the loop,
  or anywhere else in the production path.
- :class:`LocalTestExecutor` is a TEST-ONLY executor. It runs the
  generated code in a throwaway temp directory on this host with the
  host Python, so the harness loop itself can be tested end to end
  on a machine where Docker containers cannot start (this dev VM
  rejects the ``setns`` syscall, so the real sandbox is unavailable
  here). It exists so tests can exercise the loop, the repair cycle,
  and the attempt/budget ceilings. It must NEVER be used in
  production: the harness constructor rejects it when
  ``production=True``, and :func:`create_production_harness` only
  ever builds a :class:`SandboxExecutor`.

Both executors share one interface: ``run(code, timeout_s=30)`` takes
a complete program text (implementation plus its tests) and returns
an :class:`ExecutionResult`. The sandbox has no persistent filesystem
between runs, so the loop always passes the full program text on
every attempt instead of relying on files written by an earlier
attempt.

File writes go through ``write_file`` on the executor, enforced by
the sandbox profile (see :mod:`puspa.harness.sandbox_profiles`):
``read_only`` refuses every write, ``workspace_write`` and ``full``
allow writes only under the executor workdir and never inside
protected paths. The sandbox executor has no persistent filesystem,
so its ``write_file`` always refuses: edits in production run
through ``run()`` only.
"""

from __future__ import annotations

import abc
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from puspa.sandbox import Sandbox, SandboxUnavailable

from .sandbox_profiles import ProfileDenied, check_write_path

__all__ = [
    "ExecutionResult",
    "Executor",
    "LocalTestExecutor",
    "ProfileDenied",
    "SandboxExecutor",
    "SandboxUnavailable",
]

_MAX_OUTPUT_CHARS = 100_000


@dataclass
class ExecutionResult:
    """Outcome of one program execution, sandbox or local."""

    ok: bool
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False


class Executor(abc.ABC):
    """Runs one complete program text and returns its result."""

    @abc.abstractmethod
    def run(self, code: str, *, timeout_s: int = 30) -> ExecutionResult:
        """Execute ``code`` and return an :class:`ExecutionResult`.

        Raises :class:`SandboxUnavailable` when the backend cannot
        run code at all (sandbox down). A program that runs and fails
        (non-zero exit, assertion error) returns a result with
        ``ok=False`` instead of raising: a failing test is a result,
        not a backend failure.
        """

    def write_file(self, path: str | Path, content: str, *, profile: str = "workspace_write") -> Path:
        """Write ``content`` to ``path`` under this executor.

        The default refuses: only executors with a writable
        filesystem implement this. The profile is enforced by the
        implementing executor through
        :func:`puspa.harness.sandbox_profiles.check_write_path`.
        """
        raise NotImplementedError(
            f"{type(self).__name__} has no writable filesystem; "
            "edits run through run() only"
        )


class SandboxExecutor(Executor):
    """Production executor: generated code runs only in the sandbox."""

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self.sandbox = sandbox if sandbox is not None else Sandbox()

    def run(self, code: str, *, timeout_s: int = 30) -> ExecutionResult:
        # Sandbox.run raises SandboxUnavailable when Docker cannot
        # execute. That exception propagates untouched: fail-closed,
        # no host fallback, no retry on the host.
        result = self.sandbox.run(code, timeout_s=timeout_s)
        return ExecutionResult(
            ok=bool(result["ok"]),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            exit_code=result.get("exit_code"),
            timed_out=bool(result.get("timed_out", False)),
        )


class LocalTestExecutor(Executor):
    """TEST-ONLY executor: runs generated code on this host.

    DO NOT USE IN PRODUCTION. This executor exists for one purpose:
    testing the harness loop (generate, run, read the failure, fix,
    repeat) on machines where the Docker sandbox cannot start. The
    code under test runs in a fresh temp directory with a scrubbed
    environment (no ``PYTHONPATH``, no user site packages) and a
    timeout, but it still runs ON THE HOST, so it is not a security
    boundary. Production code must use :class:`SandboxExecutor`.

    Each ``run`` writes the program to ``attempt.py`` inside the
    executor temp directory and executes it with the host Python.
    """

    def __init__(self, workdir: str | Path | None = None) -> None:
        if workdir is None:
            workdir = tempfile.mkdtemp(prefix="puspa-harness-test-")
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

    def _clean_env(self) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if "TMPDIR" in os.environ:
            env["TMPDIR"] = os.environ["TMPDIR"]
        return env

    def write_file(self, path: str | Path, content: str, *, profile: str = "workspace_write") -> Path:
        """Write a file under the executor workdir, profile-enforced.

        ``read_only`` refuses every write with :class:`ProfileDenied`;
        other profiles allow writes only under ``self.workdir`` and
        never inside protected paths (``.git`` and friends). A path
        escaping the workdir (absolute or ``..``) is refused.
        """
        resolved = check_write_path(profile, path, workspace_root=self.workdir)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return resolved

    def run(self, code: str, *, timeout_s: int = 30) -> ExecutionResult:
        if not isinstance(code, str):
            raise TypeError("code must be str")
        program = self.workdir / "attempt.py"
        program.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(program)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(self.workdir),
                env=self._clean_env(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return ExecutionResult(
                ok=False,
                stdout=stdout[:_MAX_OUTPUT_CHARS],
                stderr=(stderr or "timeout: execution exceeded the limit")[:_MAX_OUTPUT_CHARS],
                exit_code=None,
                timed_out=True,
            )
        return ExecutionResult(
            ok=proc.returncode == 0,
            stdout=proc.stdout[:_MAX_OUTPUT_CHARS],
            stderr=proc.stderr[:_MAX_OUTPUT_CHARS],
            exit_code=proc.returncode,
            timed_out=False,
        )
