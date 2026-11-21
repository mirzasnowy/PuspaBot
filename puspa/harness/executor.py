"""Code execution backends for the coding harness.

Two executors exist, and the difference between them is the whole
security point of this module:

- :class:`SandboxExecutor` is the ONLY production executor. It runs
  generated code through the bound :class:`ExecPort
  <puspa.localexec.Executor>` — in a local install that is the
  public local runner (:class:`puspa.localexec.LocalExecutor`:
  permission prompt first, then a subprocess on the user's own
  machine, bounded to its workspace); on the hosted server the
  same port is bound to the Docker sandbox. When the bound
  executor cannot run, :class:`SandboxExecutor` raises
  :class:`SandboxUnavailable` and NO code runs anywhere. There is
  no fallback to another executor in this module, in the loop, or
  anywhere else in the production path.
- :class:`LocalTestExecutor` is a TEST-ONLY executor. It runs the
  generated code in a throwaway temp directory on this host with
  the host Python, so the harness loop itself can be tested end to
  end without a permission prompt. It exists so tests can exercise
  the loop, the repair cycle, and the attempt/budget ceilings. It
  must NEVER be used in production: the harness constructor
  rejects it when ``production=True``, and
  :func:`create_production_harness` only ever builds a
  :class:`SandboxExecutor`.

:class:`BackendExecutor` is the seam between the loop and a
backend object (anything with ``run_python`` / ``sync`` /
``name``). The hosted computer backends plug in here; the local
install builds one over the local runner through
:func:`backend_executor`. A backend that cannot run raises its own
``BackendUnavailable``, which subclasses the harness-level one
(one error identity across the boundary); :class:`BackendExecutor`
lets it propagate unchanged, and the loop ends the run with status
``sandbox_unavailable``.

Both executors share one interface: ``run(code, timeout_s=30)``
takes a complete program text (implementation plus its tests) and
returns an :class:`ExecutionResult`. The production executor has
no persistent filesystem between runs, so the loop always passes
the full program text on every attempt instead of relying on
files written by an earlier attempt.

File writes go through ``write_file`` on the executor, enforced by
the sandbox profile (see :mod:`puspa.harness.sandbox_profiles`):
``read_only`` refuses every write, ``workspace_write`` and ``full``
allow writes only under the executor workdir and never inside
protected paths. The production executor has no writable
filesystem, so its ``write_file`` always refuses: edits in
production run through ``run()`` only.
"""

from __future__ import annotations

import abc
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from puspa.localexec import (
    ExecutionResult as LocalExecutionResult,
)
from puspa.localexec import Executor as ExecPort
from puspa.localexec import (
    LocalExecutor,
    OutsideWorkspaceError,
    PermissionDenied,
)

from .sandbox_profiles import ProfileDenied, check_write_path

__all__ = [
    "BackendError",
    "BackendExecutor",
    "BackendUnavailable",
    "ExecutionResult",
    "Executor",
    "LocalTestExecutor",
    "ProfileDenied",
    "SandboxExecutor",
    "SandboxUnavailable",
    "backend_executor",
]

_MAX_OUTPUT_CHARS = 100_000


class SandboxUnavailable(Exception):
    """The bound execution backend cannot run code at all.

    Fail-closed: callers must not retry the same code on another
    executor. In the local install this is what a denied permission
    prompt or an unusable workspace becomes; on the hosted server
    it is what a down Docker sandbox becomes.
    """


class BackendUnavailable(Exception):
    """A backend cannot run on this host at all.

    This is the honest fail-closed answer, not a silent fallback:
    callers must not retry the same operation on another backend
    unless the operator explicitly chose that fallback.
    """


class BackendError(Exception):
    """A backend failed for a reason that is not unavailability.

    Examples: an unknown backend name, or a command that could not
    be started. The backend itself was reachable; the operation
    was refused or failed.
    """


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
        run code at all (sandbox down, permission denied). A program
        that runs and fails (non-zero exit, assertion error) returns
        a result with ``ok=False`` instead of raising: a failing
        test is a result, not a backend failure.
        """

    def write_file(
        self, path: str | Path, content: str, *, profile: str = "workspace_write"
    ) -> Path:
        """Write ``content`` to ``path`` under this executor.

        The default refuses: only executors with a writable
        filesystem implement this. The profile is enforced by the
        implementing executor through
        :func:`puspa.harness.sandbox_profiles.check_write_path`.
        """
        raise NotImplementedError(
            f"{type(self).__name__} has no writable filesystem; edits run through run() only"
        )


class BackendExecutor(Executor):
    """Harness executor that runs through a backend object.

    The seam between the coding harness and an execution backend:
    the loop keeps calling ``run(code)``, and the backend decides
    where the code actually executes. ``production_safe`` is True
    only for a backend named ``"docker"``; ``test_only`` is True
    for the local backend, which preserves
    :class:`LocalTestExecutor` behaviour exactly (subprocess on
    this host, scrubbed env, no security boundary). A
    ``BackendExecutor`` over the local backend is refused by the
    harness constructor when ``production=True``, exactly like
    :class:`LocalTestExecutor`.
    """

    def __init__(self, backend) -> None:
        self.backend = backend

    @property
    def production_safe(self) -> bool:
        """True only when the backend is Docker (the production backend)."""
        return self.backend.name == "docker"

    @property
    def test_only(self) -> bool:
        """True for the local backend (test/dev only, like LocalTestExecutor)."""
        return self.backend.name == "local"

    def run(self, code: str, *, timeout_s: int = 30) -> ExecutionResult:
        # A backend that cannot run raises its own BackendUnavailable.
        # That class subclasses the harness-level BackendUnavailable
        # (one error identity across the boundary: the private
        # backends inherit it from here), so it propagates unchanged
        # and the loop still ends the run with sandbox_unavailable.
        # No host fallback, no retry on the host.
        result = self.backend.run_python(code, timeout_s=timeout_s)
        return ExecutionResult(
            ok=bool(result.ok),
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            timed_out=bool(result.timed_out),
        )

    def write_file(
        self, path: str | Path, content: str, *, profile: str = "workspace_write"
    ) -> Path:
        """Write through the backend's file sync (scope-enforced).

        Backends without a persistent filesystem (Docker) refuse
        with BackendError: edits in production run through run()
        only, exactly like :class:`SandboxExecutor`.
        """
        return self.backend.sync.write(path, content, profile=profile)


class _LocalExecSync:
    """File sync for the local-runner backend: plain files under the workdir."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def write(self, path: str | Path, content: str, *, profile: str = "workspace_write") -> Path:
        """Write ``content`` under the workdir, profile-enforced."""
        resolved = check_write_path(profile, path, workspace_root=self.workdir)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return resolved

    def read(self, path: str | Path) -> str:
        """Read a file previously written under the workdir."""
        resolved = check_write_path("workspace_write", path, workspace_root=self.workdir)
        return resolved.read_text(encoding="utf-8")


class _LocalExecBackend:
    """The local runner, shaped as a backend for :class:`BackendExecutor`.

    Runs programs through a :class:`puspa.localexec.LocalExecutor`
    (permission prompt first, workspace-bounded subprocess). This
    is the public replacement for the old local computer backend:
    same shape (``name`` / ``run_python`` / ``sync`` /
    ``check_available``), test/dev only, never a security boundary.
    """

    name = "local"

    def __init__(self, workdir: str | Path, executor: ExecPort | None = None) -> None:
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.executor = executor if executor is not None else LocalExecutor(self.workdir)
        self.sync = _LocalExecSync(self.workdir)

    def check_available(self) -> bool:
        """The local runner is always available as a mechanism."""
        return True

    def run_command(
        self, argv: list[str], *, timeout_s: int = 30, cwd: str | Path | None = None
    ) -> LocalExecutionResult:
        """Run one argv through the local runner."""
        return self.executor.execute(argv, cwd=cwd or self.workdir, timeout=timeout_s)

    def run_python(self, code: str, *, timeout_s: int = 30) -> ExecutionResult:
        """Write ``code`` to the workdir and run it with the host Python."""
        program = self.workdir / "attempt.py"
        program.write_text(code, encoding="utf-8")
        try:
            result = self.executor.execute(
                [sys.executable, str(program)], cwd=self.workdir, timeout=timeout_s
            )
        except PermissionDenied as exc:
            raise BackendUnavailable(f"execution denied at the permission prompt: {exc}") from exc
        except OutsideWorkspaceError as exc:
            raise BackendUnavailable(str(exc)) from exc
        return ExecutionResult(
            ok=result.returncode == 0 and not result.timed_out,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            timed_out=result.timed_out,
        )


def backend_executor(backend_name: str = "local", **kwargs) -> BackendExecutor:
    """Build a :class:`BackendExecutor` for the named backend.

    ``backend_name`` is a registered backend name. The local
    install has exactly one: ``"local"`` (the local runner, built
    over :class:`puspa.localexec.LocalExecutor`); ``kwargs`` go to
    the backend constructor (``workdir`` for the local backend,
    plus ``prompt`` / ``executor`` to control the permission
    prompt — tests pass an auto-approving prompt, interactive use
    leaves the default). ``"docker"`` is honestly unavailable here — the Docker sandbox
    lives in the hosted service, not in the local install — so it
    raises :class:`BackendUnavailable` instead of faking a
    backend. Any other name raises :class:`BackendError`.
    """
    if backend_name == "local":
        workdir = kwargs.pop("workdir", None)
        prompt = kwargs.pop("prompt", None)
        executor = kwargs.pop("executor", None)
        if workdir is None:
            workdir = tempfile.mkdtemp(prefix="puspa-backend-local-")
        if kwargs:
            raise BackendError(f"unknown options for the local backend: {sorted(kwargs)}")
        if executor is None:
            executor = LocalExecutor(workdir, prompt=prompt)
        return BackendExecutor(_LocalExecBackend(workdir, executor=executor))
    if backend_name == "docker":
        raise BackendUnavailable(
            "no Docker backend in the local install; the Docker sandbox "
            "lives in the hosted service, not on this machine"
        )
    raise BackendError(f"unknown execution backend {backend_name!r}; registered backends: local")


class SandboxExecutor(Executor):
    """Production executor: generated code runs only through the bound port.

    The executor is a :class:`puspa.localexec.Executor`. With none
    given, a :class:`puspa.localexec.LocalExecutor` is built over a
    fresh workdir, so a local install runs the code on the user's
    own machine after the permission prompt, bounded to that
    workdir. The hosted server passes its own executor, bound to
    the Docker sandbox, behind the same port. Either way the code
    runs in exactly one place — through the bound executor — and
    nowhere else.

    A denied prompt (or an unusable workspace) raises
    :class:`SandboxUnavailable`: fail-closed, no host fallback, no
    retry on another executor.
    """

    def __init__(
        self,
        executor: ExecPort | None = None,
        *,
        workdir: str | Path | None = None,
    ) -> None:
        if executor is None:
            self.workdir = (
                Path(workdir)
                if workdir is not None
                else Path(tempfile.mkdtemp(prefix="puspa-harness-"))
            )
            self.workdir.mkdir(parents=True, exist_ok=True)
            self.executor: ExecPort = LocalExecutor(self.workdir)
        else:
            self.executor = executor
            if workdir is not None:
                self.workdir = Path(workdir)
                self.workdir.mkdir(parents=True, exist_ok=True)
            else:
                workspace = getattr(executor, "workspace", None)
                base = Path(workspace) if workspace is not None else Path.cwd()
                base.mkdir(parents=True, exist_ok=True)
                self.workdir = Path(tempfile.mkdtemp(prefix="puspa-harness-", dir=str(base)))

    def run(self, code: str, *, timeout_s: int = 30) -> ExecutionResult:
        # Write the program, then run it through the bound executor
        # only. A denial or an unusable workspace becomes
        # SandboxUnavailable and propagates untouched: fail-closed,
        # no host fallback, no retry on the host.
        program = self.workdir / "attempt.py"
        program.write_text(code, encoding="utf-8")
        try:
            result = self.executor.execute(
                [sys.executable, str(program)], cwd=self.workdir, timeout=timeout_s
            )
        except (PermissionDenied, OutsideWorkspaceError) as exc:
            raise SandboxUnavailable(str(exc)) from exc
        return ExecutionResult(
            ok=result.returncode == 0 and not result.timed_out,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            timed_out=result.timed_out,
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

    def write_file(
        self, path: str | Path, content: str, *, profile: str = "workspace_write"
    ) -> Path:
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
                check=False,
                env=self._clean_env(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (
                exc.stdout.decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr = (
                exc.stderr.decode("utf-8", "replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
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
