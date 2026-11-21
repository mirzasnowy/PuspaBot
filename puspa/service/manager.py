"""Local agent service lifecycle: start, stop, restart, status, logs, update.

The local agent service is the PuspaBot gateway (``puspa web``): one
background process whose stdout and stderr go to a log file under the
Puspa home, tracked by a small state file. No Docker, no root, no
systemd: a state file plus ``os.kill`` is enough on a single-user
Linux machine, and the same code runs in tests against a harmless
sleep command.

The command surface mirrors the ``cow`` CLI from CowAgent (MIT):
``start`` / ``stop`` / ``restart`` for control, ``status`` / ``logs``
for inspection, ``update`` to upgrade the installed package and
restart the service. The implementation here is original PuspaBot
code, written for this codebase, not copied from CowAgent.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from puspa.sessions.home import resolve_home

#: Subdirectory of the Puspa home holding service state and logs.
SERVICE_DIR_NAME = "service"

#: State file: pid, host, port, start time, and the spawned command.
STATE_FILE_NAME = "service.json"

#: Log file: the service process's stdout and stderr, appended.
LOG_FILE_NAME = "service.log"

#: How long to wait for a started process to prove it stays up.
START_GRACE_SECONDS = 2.0

#: How long to wait for a graceful stop before forcing the kill.
STOP_TIMEOUT_SECONDS = 5.0

#: PyPI JSON endpoint for the latest released version.
PYPI_JSON_URL = "https://pypi.org/pypi/puspa/json"

#: Package name on PyPI.
PACKAGE_NAME = "puspa"


class ServiceError(Exception):
    """The service could not be started, stopped, or updated."""


@dataclass
class ServiceStatus:
    """One snapshot of the local agent service."""

    running: bool
    pid: int | None = None
    host: str | None = None
    port: int | None = None
    started_at: str | None = None
    uptime_seconds: float | None = None
    log_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        """Plain dict form (for ``--json`` output)."""
        return {
            "running": self.running,
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "started_at": self.started_at,
            "uptime_seconds": self.uptime_seconds,
            "log_path": str(self.log_path) if self.log_path else None,
        }


@dataclass
class StartResult:
    """Outcome of a start: fresh start, or already running."""

    pid: int
    host: str
    port: int
    already_running: bool = False


@dataclass
class StopResult:
    """Outcome of a stop."""

    stopped: bool
    pid: int | None = None
    reason: str = ""


@dataclass
class UpdateResult:
    """Outcome of an update check or upgrade."""

    installed_version: str
    latest_version: str
    update_available: bool
    updated: bool = False
    restarted: bool = False


def _pid_alive(pid: int) -> bool:
    """True when a process with this pid is alive and not a zombie.

    A terminated child that has not been reaped yet is a zombie: it
    still answers signal 0, but it is dead. ``/proc/<pid>/stat`` shows
    that (state ``Z``), so a zombie counts as not alive here.
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            stat = handle.read()
    except OSError:
        pass
    else:
        try:
            state = stat.rsplit(")", 1)[1].split()[0]
        except IndexError:
            return False
        return state != "Z"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _reap(pid: int) -> None:
    """Reap a child process when it is ours (no-op otherwise)."""
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, os.WNOHANG)


def _parse_version(raw: str) -> Version | None:
    """A version string as a comparable version, or None when unparsable."""
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def _default_gateway_command(host: str, port: int) -> list[str]:
    """The command that runs the local gateway in the background."""
    return [
        sys.executable,
        "-m",
        "puspa.cli",
        "web",
        "--host",
        host,
        "--port",
        str(port),
    ]


def _default_version_checker() -> str:
    """The latest ``puspa`` release on PyPI (network call)."""
    request = urllib.request.Request(PYPI_JSON_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return str(payload["info"]["version"])


def _default_installer() -> tuple[int, str]:
    """Upgrade the installed package; returns (exit code, output)."""
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    return completed.returncode, completed.stdout + completed.stderr


class ServiceManager:
    """Owns the local agent service process and its state files."""

    def __init__(
        self,
        home: str | Path | None = None,
        *,
        command: list[str] | None = None,
        version_checker: Callable[[], str] | None = None,
        installer: Callable[[], tuple[int, str]] | None = None,
    ) -> None:
        """Build a manager for one Puspa home.

        ``command`` overrides the spawned command (the gateway by
        default); tests pass a harmless long-running command instead.
        ``version_checker`` and ``installer`` are the seams ``update``
        uses, so tests can answer without touching the network or pip.
        """
        self.home = resolve_home(home=home)
        self.service_dir = self.home / SERVICE_DIR_NAME
        self.state_path = self.service_dir / STATE_FILE_NAME
        self.log_path = self.service_dir / LOG_FILE_NAME
        self._command = command
        self._version_checker = version_checker or _default_version_checker
        self._installer = installer or _default_installer

    # -- state ---------------------------------------------------------
    def _read_state(self) -> dict[str, Any] | None:
        """The recorded state, or None when there is no state file."""
        try:
            raw = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            return None
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _write_state(self, pid: int, host: str, port: int, command: list[str]) -> None:
        """Record a freshly started service."""
        self.service_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": pid,
            "host": host,
            "port": port,
            "started_at": datetime.now(UTC).isoformat(),
            "command": command,
        }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _clear_state(self) -> None:
        """Forget the recorded state (best effort)."""
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    # -- status --------------------------------------------------------
    def status(self) -> ServiceStatus:
        """Current state: running with pid/host/port, or stopped."""
        state = self._read_state()
        if state is None:
            return ServiceStatus(running=False, log_path=self.log_path)
        pid = state.get("pid")
        if not isinstance(pid, int) or not _pid_alive(pid):
            # Stale state: the process is gone, so the service is
            # stopped; drop the stale record so the next start is clean.
            self._clear_state()
            return ServiceStatus(running=False, log_path=self.log_path)
        started_at = state.get("started_at")
        uptime: float | None = None
        if isinstance(started_at, str):
            try:
                started = datetime.fromisoformat(started_at)
                uptime = (datetime.now(UTC) - started).total_seconds()
            except ValueError:
                uptime = None
        host = state.get("host") if isinstance(state.get("host"), str) else None
        port = state.get("port") if isinstance(state.get("port"), int) else None
        return ServiceStatus(
            running=True,
            pid=pid,
            host=host,
            port=port,
            started_at=started_at if isinstance(started_at, str) else None,
            uptime_seconds=uptime,
            log_path=self.log_path,
        )

    # -- start / stop / restart -----------------------------------------
    def start(self, host: str = "127.0.0.1", port: int = 8000) -> StartResult:
        """Start the service in the background (no-op when running)."""
        current = self.status()
        if current.running and current.pid is not None:
            return StartResult(
                pid=current.pid,
                host=current.host or host,
                port=current.port or port,
                already_running=True,
            )
        command = list(self._command) if self._command else _default_gateway_command(host, port)
        self.service_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.log_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log_file.close()
        pid = process.pid
        self._write_state(pid, host, port, command)
        deadline = time.monotonic() + START_GRACE_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self._clear_state()
                tail = self.logs(lines=10)
                raise ServiceError(
                    f"service exited immediately (exit code {process.returncode}); "
                    f"last log lines:\n" + "\n".join(tail)
                )
            time.sleep(0.1)
        return StartResult(pid=pid, host=host, port=port)

    def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> StopResult:
        """Stop the service gracefully, then forcefully when needed."""
        current = self.status()
        if not current.running or current.pid is None:
            return StopResult(stopped=False, reason="service is not running")
        pid = current.pid
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            self._clear_state()
            return StopResult(stopped=False, pid=pid, reason="service is not running")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                _reap(pid)
                self._clear_state()
                return StopResult(stopped=True, pid=pid)
            time.sleep(0.1)
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not _pid_alive(pid):
                _reap(pid)
                self._clear_state()
                return StopResult(stopped=True, pid=pid)
            time.sleep(0.1)
        return StopResult(stopped=False, pid=pid, reason="service did not stop")

    def restart(self, host: str | None = None, port: int | None = None) -> StartResult:
        """Stop the service (when running) and start it again."""
        current = self.status()
        if current.running:
            stopped = self.stop()
            if not stopped.stopped:
                raise ServiceError(f"could not stop the service: {stopped.reason}")
        return self.start(
            host=host or current.host or "127.0.0.1",
            port=port or current.port or 8000,
        )

    # -- logs ------------------------------------------------------------
    def logs(self, lines: int = 50) -> list[str]:
        """The last ``lines`` lines of the service log."""
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise ServiceError(f"no service log yet ({self.log_path})") from None
        return text.splitlines()[-lines:] if lines > 0 else []

    # -- update ------------------------------------------------------------
    def installed_version(self) -> str:
        """The installed ``puspa`` version, or ``"unknown"`` in a checkout."""
        try:
            return importlib_metadata.version(PACKAGE_NAME)
        except importlib_metadata.PackageNotFoundError:
            return "unknown"

    def update(self, *, check_only: bool = False) -> UpdateResult:
        """Upgrade the package to the latest release, then restart.

        With ``check_only`` this only compares versions and installs
        nothing. When the service is running and the upgrade succeeds,
        it is restarted on the same host and port.
        """
        installed = self.installed_version()
        latest = self._version_checker()
        installed_parsed = _parse_version(installed) if installed != "unknown" else None
        latest_parsed = _parse_version(latest)
        if installed_parsed is not None and latest_parsed is not None:
            available = latest_parsed > installed_parsed
        else:
            available = latest != installed
        result = UpdateResult(
            installed_version=installed,
            latest_version=latest,
            update_available=available,
        )
        if not available or check_only:
            return result
        current = self.status()
        code, output = self._installer()
        if code != 0:
            tail = "\n".join(output.splitlines()[-10:])
            raise ServiceError(f"upgrade failed (exit code {code}):\n{tail}")
        result.updated = True
        if current.running:
            self.restart(host=current.host, port=current.port)
            result.restarted = True
        return result
