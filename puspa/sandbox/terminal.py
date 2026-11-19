"""Terminal sessions: PTY-like exec with input/output streams (G34).

OpenShell runs its canonical main process in a retained
pseudo-terminal (a login shell when no command is given) and
supports ``Exec`` with a TTY plus a ``Resize`` message for
window changes; the PTY setup gives the child a dedicated
process group and a controlling terminal
(``crates/openshell-sandbox/src/pty.rs``). The supervisor
caps pending stdin per exec channel, and closing input is not
the same as sending Ctrl-D.

Puspa takes that shape for the local backend, where a real PTY
exists on this host:

- :class:`TerminalSession` — a command running under a PTY:
  :meth:`TerminalSession.send_input` writes bytes to the
  child, :meth:`TerminalSession.read_output` drains what the
  child printed, :meth:`TerminalSession.resize` changes the
  window size (``TIOCSWINSZ`` + ``SIGWINCH``), and
  :meth:`TerminalSession.close` kills the whole process group.
  The child runs inside the backend scope (the jail): its cwd
  resolves under the scope root, and its env is scrubbed the
  same way the local backend scrubs it.
- :func:`start_terminal` — the entry point; only the local
  backend supports a live PTY here. The Docker backend raises
  :class:`puspa.computer.errors.BackendUnavailable`: a PTY
  inside a container needs a running container, and containers
  cannot start on this host. That refusal is the honest
  answer, not a fake terminal.

PTYs are Unix-only (``pty``, ``fcntl``, ``termios``); on a
platform without them the import raises
:class:`BackendUnavailable` with a clear reason.
"""

from __future__ import annotations

import contextlib
import os
import select
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puspa.computer.backends import ExecutionBackend

from .exec import _resolve_cwd, _scrubbed_env

__all__ = ["DEFAULT_COLS", "DEFAULT_ROWS", "TerminalSession", "start_terminal"]

#: Default terminal size (OpenShell's sandbox docs use the
#: same 80x24-style default for a fresh terminal).
DEFAULT_COLS = 80
DEFAULT_ROWS = 24

#: How long :meth:`TerminalSession.read_output` waits for output
#: when the caller passes no timeout.
DEFAULT_READ_TIMEOUT_S = 0.5


class TerminalSession:
    """One command running under a PTY inside a sandbox session.

    ``send_input`` / ``read_output`` are the input/output
    streams; ``resize`` changes the window; ``poll`` reports
    the exit code once the child is done (``None`` while it
    runs); ``close`` kills the process group and closes the
    master fd. Closing is idempotent.
    """

    def __init__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
    ) -> None:
        if cols <= 0 or rows <= 0:
            raise ValueError("cols and rows must be > 0")
        try:
            import pty
        except ImportError as exc:
            from puspa.computer.errors import BackendUnavailable

            raise BackendUnavailable(
                "PTY terminals need a Unix host with the pty module; this platform has none"
            ) from exc
        self.argv = list(argv)
        self.cols = cols
        self.rows = rows
        self._master_fd, slave_fd = pty.openpty()
        self._set_winsize(self._master_fd, cols, rows)
        try:
            self._proc = subprocess.Popen(
                self.argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(cwd),
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            from puspa.computer.errors import BackendError

            os.close(self._master_fd)
            os.close(slave_fd)
            raise BackendError(f"terminal could not start {self.argv[0]!r}: {exc}") from exc
        os.close(slave_fd)
        self._closed = False
        self._exit_code: int | None = None

    # -- streams -----------------------------------------------------------

    def send_input(self, data: bytes | str) -> int:
        """Write ``data`` to the child's stdin; return bytes written."""
        if self._closed:
            from puspa.computer.errors import BackendError

            raise BackendError("terminal session is closed")
        payload = data.encode("utf-8") if isinstance(data, str) else data
        return os.write(self._master_fd, payload)

    def read_output(self, timeout_s: float = DEFAULT_READ_TIMEOUT_S) -> bytes:
        """Drain output available within ``timeout_s`` (b"" when none)."""
        if self._closed:
            return b""
        ready, _, _ = select.select([self._master_fd], [], [], max(0.0, timeout_s))
        if not ready:
            return b""
        try:
            return os.read(self._master_fd, 64 * 1024)
        except OSError:
            return b""

    def resize(self, cols: int, rows: int) -> None:
        """Change the window size and notify the child (SIGWINCH)."""
        if cols <= 0 or rows <= 0:
            raise ValueError("cols and rows must be > 0")
        if self._closed:
            from puspa.computer.errors import BackendError

            raise BackendError("terminal session is closed")
        self.cols, self.rows = cols, rows
        self._set_winsize(self._master_fd, cols, rows)
        with contextlib.suppress(OSError, ProcessLookupError):
            os.killpg(self._proc.pid, signal.SIGWINCH)

    # -- lifecycle -----------------------------------------------------------

    def poll(self) -> int | None:
        """Exit code once the child is done, else None."""
        if self._exit_code is None:
            self._exit_code = self._proc.poll()
        return self._exit_code

    @property
    def is_alive(self) -> bool:
        """True while the child is still running."""
        return self.poll() is None

    def wait(self, timeout_s: float = 30) -> int:
        """Wait for the child to exit; return its exit code."""
        try:
            self._exit_code = self._proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.close()
            raise
        return self._exit_code

    def close(self) -> None:
        """Kill the process group and close the master fd (idempotent)."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(OSError, ProcessLookupError):
            os.killpg(self._proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            self._proc.wait(timeout=5)
        with contextlib.suppress(OSError):
            os.close(self._master_fd)

    def __enter__(self) -> TerminalSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _set_winsize(fd: int, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios

        packed = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


def start_terminal(
    backend: ExecutionBackend,
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
) -> TerminalSession:
    """Start a PTY terminal session on ``backend``.

    Only the local backend supports a live PTY here: it runs
    the command under a PTY inside its scope (the jail). The
    Docker backend raises :class:`BackendUnavailable` — a PTY
    inside a container needs a running container, and
    containers cannot start on this host.
    """
    if not isinstance(argv, (list, tuple)) or not argv:
        raise ValueError("argv must be a non-empty list of strings")
    if not all(isinstance(part, str) for part in argv):
        raise TypeError("every argv part must be str")
    if backend.name != "local":
        from puspa.computer.errors import BackendUnavailable

        raise BackendUnavailable(
            f"terminal sessions need a live PTY on backend {backend.name!r}; "
            "only the local backend provides one here, and the Docker "
            "backend has no running container on this host "
            "(containers cannot start here; first live check is on a "
            "Docker host or VPS)"
        )
    run_dir = _resolve_cwd(backend, cwd)
    child_env = _scrubbed_env(env or {})
    return TerminalSession(
        list(argv),
        cwd=run_dir,
        env=child_env,
        cols=cols,
        rows=rows,
    )
