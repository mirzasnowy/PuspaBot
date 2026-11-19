"""Exec API: run commands with streaming output (G34, OpenShell exec model).

OpenShell's sandbox protocol (``proto/sandbox_protocol.proto``)
runs a command as an ``Exec`` with an admission deadline, then
streams the result back as bounded output frames (each I/O
payload at most 64 KiB, control messages at most 1 MiB), and
reports the exit status separately from the output: a command
that ran and failed is a result, not a transport failure. The
supervisor caps pending stdin per exec channel, and cancelling
an exec discards pending input. Arguments are passed as an
argv (``SandboxSpec.command`` is a repeated string, never a
shell string), so no shell parsing ever happens.

Puspa takes that shape, rewritten over the backend ABC:

- :class:`ExecRequest` — exactly one of ``argv`` (a command)
  or ``code`` (a Python program), plus working dir, env,
  and timeout. ``cwd`` must stay inside the session scope;
  ``env`` refuses credential-looking keys (broker rule).
- :class:`ExecResult` — ok / stdout / stderr / exit code /
  timed_out / duration, the same shape as
  :class:`puspa.computer.backends.CommandResult` plus timing.
- :func:`run_exec` — one-shot exec through the backend.
- :func:`exec_stream` — streaming exec yielding
  :class:`ExecChunk` values (stdout, stderr, then one exit
  chunk). On the local backend the chunks arrive live from a
  ``Popen`` pipe (bounded per chunk); on the Docker backend
  the container only returns output when it exits, so the
  chunks arrive together after completion — the shape is the
  same, the timing honestly reflects the backend. A Docker
  backend that cannot run here raises
  :class:`puspa.computer.errors.BackendUnavailable` instead
  of streaming host output.

Output is bounded per chunk (64 KiB, OpenShell's payload cap)
and in total (100,000 chars, the existing backend cap).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puspa.computer.backends import ExecutionBackend

__all__ = [
    "ExecChunk",
    "ExecRequest",
    "ExecResult",
    "exec_stream",
    "run_exec",
]

#: One streamed output chunk, at most this many bytes
#: (OpenShell's 64 KiB I/O payload cap).
MAX_CHUNK_BYTES = 64 * 1024

#: Total output cap per stream, matching the backend cap.
MAX_TOTAL_CHARS = 100_000

#: Credential-looking env keys are refused (broker rule; the
#: same list the local backend and the session manager use).
_CREDENTIAL_HINTS = (
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
    return any(hint in upper for hint in _CREDENTIAL_HINTS)


@dataclass(frozen=True)
class ExecRequest:
    """One command to run inside a sandbox session.

    Exactly one of ``argv`` / ``code`` must be given. ``argv``
    is a command line (no shell parsing); ``code`` is a Python
    program run as ``python -`` style input. ``cwd`` is
    sandbox-relative (``None`` = the session workdir).
    """

    argv: tuple[str, ...] | None = None
    code: str | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_s: int = 30

    def __post_init__(self) -> None:
        if (self.argv is None) == (self.code is None):
            raise ValueError("exactly one of argv / code must be given")
        if self.argv is not None:
            if not self.argv:
                raise ValueError("argv must be a non-empty list of strings")
            if not all(isinstance(part, str) for part in self.argv):
                raise TypeError("every argv part must be str")
        if self.code is not None and not isinstance(self.code, str):
            raise TypeError("code must be str")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        for key, value in self.env.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("env keys and values must be str")
            if _looks_like_credential(key):
                raise ValueError(
                    f"refusing credential-looking env key {key!r}: "
                    "credentials travel through the broker, never in raw env"
                )


@dataclass(frozen=True)
class ExecResult:
    """Outcome of one exec (a failed program is a result, not a raise)."""

    ok: bool
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False
    duration_s: float = 0.0


@dataclass(frozen=True)
class ExecChunk:
    """One streamed piece of an exec: output, or the final exit.

    ``kind`` is ``"stdout"``, ``"stderr"``, or ``"exit"``.
    The exit chunk carries ``exit_code`` / ``timed_out`` and
    empty ``data``; it is always the last chunk.
    """

    kind: str
    data: str = ""
    exit_code: int | None = None
    timed_out: bool = False


def run_exec(backend: ExecutionBackend, request: ExecRequest) -> ExecResult:
    """Run ``request`` on ``backend`` and return its result.

    Env from the request is merged over the session env by the
    caller (the backend applies its own scrubbing on top).
    Backend unavailability raises :class:`BackendUnavailable`;
    a program that ran and failed returns ``ok=False``.
    """
    from puspa.computer.errors import BackendUnavailable

    started = time.monotonic()
    if request.argv is not None:
        result = backend.run_command(
            list(request.argv),
            cwd=request.cwd,
            timeout_s=request.timeout_s,
            env=request.env or None,
        )
    else:
        assert request.code is not None
        if request.cwd is not None or request.env:
            raise BackendUnavailable(
                "run_python takes no cwd/env on this backend: use argv "
                "exec for a working directory or environment"
            )
        result = backend.run_python(request.code, timeout_s=request.timeout_s)
    return ExecResult(
        ok=result.ok,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_s=time.monotonic() - started,
    )


def exec_stream(backend: ExecutionBackend, request: ExecRequest) -> Iterator[ExecChunk]:
    """Stream ``request`` on ``backend`` as output + exit chunks.

    The local backend streams live: chunks arrive as the child
    writes them. Any other backend falls back to a one-shot run
    whose output is chunked afterwards (the Docker backend only
    returns output when its container exits, so live streaming
    is impossible there — the chunked shape is identical, the
    timing honestly reflects the backend).
    """
    if backend.name == "local":
        yield from _stream_local(backend, request)
        return
    result = run_exec(backend, request)
    yield from _chunk_result(result)


def _chunk_result(result: ExecResult) -> Iterator[ExecChunk]:
    """Split a finished result into bounded chunks + one exit chunk."""
    for kind, text in (("stdout", result.stdout), ("stderr", result.stderr)):
        for start in range(0, len(text), MAX_CHUNK_BYTES):
            yield ExecChunk(kind=kind, data=text[start : start + MAX_CHUNK_BYTES])
    yield ExecChunk(
        kind="exit",
        exit_code=result.exit_code,
        timed_out=result.timed_out,
    )


def _stream_local(backend: ExecutionBackend, request: ExecRequest) -> Iterator[ExecChunk]:
    """Live streaming exec on the local backend (subprocess + pipes).

    Runs inside the backend scope (the jail): ``cwd`` resolves
    under the scope root and is refused outside it. The child
    environment is scrubbed the same way the local backend
    scrubs it (``PATH``/``HOME`` only, no ``PYTHONPATH``, no
    user site), plus the request env. On timeout the whole
    process group is killed and a timed-out exit chunk ends
    the stream.
    """
    import select

    run_dir = _resolve_cwd(backend, request.cwd)
    child_env = _scrubbed_env(request.env)
    if request.argv is not None:
        argv = list(request.argv)
    else:
        program = backend.scope.root / "exec_stream.py"
        program.write_text(request.code or "", encoding="utf-8")
        argv = [sys.executable, str(program)]
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(run_dir),
            env=child_env,
            start_new_session=True,
        )
    except OSError as exc:
        from puspa.computer.errors import BackendError

        raise BackendError(f"local backend could not start {argv[0]!r}: {exc}") from exc
    assert proc.stdout is not None and proc.stderr is not None
    deadline = started + request.timeout_s
    stdout_total = stderr_total = 0
    timed_out = False
    try:
        import os as _os

        _os.set_blocking(proc.stdout.fileno(), False)
        _os.set_blocking(proc.stderr.fileno(), False)
        open_fds = {proc.stdout.fileno(): "stdout", proc.stderr.fileno(): "stderr"}
        while open_fds:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            ready, _, _ = select.select(list(open_fds), [], [], min(remaining, 0.5))
            for fd in ready:
                kind = open_fds[fd]
                try:
                    data = _os.read(fd, MAX_CHUNK_BYTES)
                except OSError:
                    data = b""
                if not data:
                    open_fds.pop(fd, None)
                    continue
                text = data.decode("utf-8", "replace")
                if kind == "stdout":
                    stdout_total += len(text)
                    if stdout_total > MAX_TOTAL_CHARS:
                        text = text[: max(0, MAX_TOTAL_CHARS - (stdout_total - len(text)))]
                else:
                    stderr_total += len(text)
                    if stderr_total > MAX_TOTAL_CHARS:
                        text = text[: max(0, MAX_TOTAL_CHARS - (stderr_total - len(text)))]
                if text:
                    yield ExecChunk(kind=kind, data=text)
            if proc.poll() is not None and not ready:
                # Child exited; drain what is left, then stop.
                for fd, kind in list(open_fds.items()):
                    try:
                        data = _os.read(fd, MAX_CHUNK_BYTES)
                    except OSError:
                        data = b""
                    if data:
                        yield ExecChunk(kind=kind, data=data.decode("utf-8", "replace"))
                    open_fds.pop(fd, None)
                break
    finally:
        if timed_out:
            _kill_process_group(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            proc.wait(timeout=5)
    exit_code = None if timed_out else proc.returncode
    yield ExecChunk(kind="exit", exit_code=exit_code, timed_out=timed_out)


def _resolve_cwd(backend: ExecutionBackend, cwd: str | None) -> Path:
    from puspa.harness.sandbox_profiles import ProfileDenied

    if cwd is None:
        return backend.scope.root
    target = Path(cwd)
    if not target.is_absolute():
        target = backend.scope.root / target
    resolved = target.resolve()
    try:
        resolved.relative_to(backend.scope.root)
    except ValueError:
        raise ProfileDenied(
            f"cwd {str(cwd)!r} is outside the sandbox scope {str(backend.scope.root)!r}"
        ) from None
    return resolved


def _scrubbed_env(env: dict[str, str]) -> dict[str, str]:
    """Scrubbed child env: PATH/HOME only, plus the request env."""
    base = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if "TMPDIR" in os.environ:
        base["TMPDIR"] = os.environ["TMPDIR"]
    base.update(env)
    return base


def _kill_process_group(proc: subprocess.Popen) -> None:
    import contextlib
    import signal

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        with contextlib.suppress(OSError):
            proc.kill()
