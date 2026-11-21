"""Tests for ``puspa service``: lifecycle of the local agent service.

The lifecycle tests run the manager against a harmless stand-in
command (a Python one-liner that prints two lines, then sleeps), so
they exercise the real process handling — spawn, pid file, signal,
log tail — without starting the gateway. One end-to-end test starts
the real gateway on a free localhost port and checks it answers HTTP
before stopping it. Update tests use injected version/install fakes,
so no network or pip run happens here.
"""

from __future__ import annotations

import json
import socket
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest

from puspa.cli import build_parser, main
from puspa.service.cli import run_service
from puspa.service.manager import ServiceManager

#: Stand-in service command: prints two lines, then sleeps. The
#: manager treats it exactly like the gateway (same spawn, pid file,
#: log file, and signals).
FAKE_COMMAND = [
    sys.executable,
    "-c",
    (
        "import sys, time; "
        "print('service ready', flush=True); "
        "print('second line', flush=True); "
        "time.sleep(120)"
    ),
]


def _free_port() -> int:
    """An unused localhost port (closed right away; the test binds it next)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def service_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary Puspa home, so tests never touch the live one."""
    home = tmp_path / "puspa-home"
    monkeypatch.setenv("PUSPA_HOME", str(home))
    return home


@pytest.fixture
def manager_factory(service_home: Path):
    """A factory building managers that spawn the stand-in command."""

    def factory(home=None):
        return ServiceManager(home=home or service_home, command=FAKE_COMMAND)

    return factory


@pytest.fixture
def manager(manager_factory, service_home: Path):
    """A manager on the temporary home; stops any leftover service after."""
    created = manager_factory(service_home)
    yield created
    created.stop()


def _run(argv: list[str], manager_factory, out: list[str]) -> int:
    """Parse ``puspa service ...`` argv and run it, capturing prints."""
    args = build_parser().parse_args(argv)
    return run_service(args, manager_factory=manager_factory, print_fn=out.append)


def test_start_then_status_running(manager, manager_factory) -> None:
    out: list[str] = []
    assert _run(["service", "start"], manager_factory, out) == 0
    assert any("started" in line for line in out)
    current = manager.status()
    assert current.running is True
    assert current.pid is not None and current.pid > 0
    assert current.host == "127.0.0.1"
    assert current.port == 8000
    assert current.started_at is not None
    assert current.uptime_seconds is not None and current.uptime_seconds >= 0


def test_start_twice_keeps_same_pid(manager, manager_factory) -> None:
    out: list[str] = []
    assert _run(["service", "start"], manager_factory, out) == 0
    first_pid = manager.status().pid
    out.clear()
    assert _run(["service", "start"], manager_factory, out) == 0
    assert any("already running" in line for line in out)
    assert manager.status().pid == first_pid


def test_stop_then_status_stopped(manager, manager_factory) -> None:
    out: list[str] = []
    assert _run(["service", "start"], manager_factory, out) == 0
    assert _run(["service", "stop"], manager_factory, out) == 0
    assert any("stopped" in line for line in out)
    assert manager.status().running is False
    assert manager.status().pid is None


def test_stop_when_stopped_returns_1(manager_factory) -> None:
    out: list[str] = []
    assert _run(["service", "stop"], manager_factory, out) == 1
    assert any("not running" in line for line in out)


def test_restart_changes_pid_and_stays_running(manager, manager_factory) -> None:
    out: list[str] = []
    assert _run(["service", "start"], manager_factory, out) == 0
    first_pid = manager.status().pid
    assert _run(["service", "restart"], manager_factory, out) == 0
    assert any("restarted" in line for line in out)
    current = manager.status()
    assert current.running is True
    assert current.pid is not None and current.pid != first_pid


def test_restart_when_stopped_starts(manager, manager_factory) -> None:
    out: list[str] = []
    assert _run(["service", "restart"], manager_factory, out) == 0
    assert manager.status().running is True


def test_status_reports_stopped_and_json(manager_factory) -> None:
    out: list[str] = []
    assert _run(["service", "status"], manager_factory, out) == 0
    assert out == ["Service is stopped."]
    out.clear()
    assert _run(["service", "status", "--json"], manager_factory, out) == 0
    payload = json.loads("\n".join(out))
    assert payload["running"] is False
    assert payload["pid"] is None


def test_logs_show_service_output(manager, manager_factory) -> None:
    out: list[str] = []
    assert _run(["service", "start"], manager_factory, out) == 0
    # The stand-in prints on start; wait for the flush to land.
    deadline = time.monotonic() + 5
    while "service ready" not in manager.logs() and time.monotonic() < deadline:
        time.sleep(0.1)
    out.clear()
    assert _run(["service", "logs"], manager_factory, out) == 0
    assert "service ready" in out
    assert "second line" in out
    out.clear()
    assert _run(["service", "logs", "--lines", "1"], manager_factory, out) == 0
    assert out == ["second line"]


def test_logs_without_log_file_returns_1(manager_factory) -> None:
    out: list[str] = []
    assert _run(["service", "logs"], manager_factory, out) == 1
    assert any("no service log yet" in line for line in out)


def test_stale_state_file_is_stopped_and_start_recovers(
    manager, manager_factory, service_home: Path
) -> None:
    state_dir = service_home / "service"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "service.json").write_text(
        json.dumps(
            {
                "pid": 999999,
                "host": "127.0.0.1",
                "port": 8000,
                "started_at": "2026-01-01T00:00:00+00:00",
                "command": FAKE_COMMAND,
            }
        ),
        encoding="utf-8",
    )
    assert manager.status().running is False
    out: list[str] = []
    assert _run(["service", "start"], manager_factory, out) == 0
    assert manager.status().running is True


def test_start_failing_command_reports_log(manager_factory, service_home: Path) -> None:
    def failing_factory(home=None):
        return ServiceManager(
            home=home or service_home,
            command=[sys.executable, "-c", "import sys; print('boom', flush=True); sys.exit(3)"],
        )

    out: list[str] = []
    assert _run(["service", "start"], failing_factory, out) == 1
    assert any("exited immediately" in line for line in out)
    assert any("boom" in line for line in out)


def _update_factory(service_home: Path, *, latest: str, installer=None):
    """A factory whose managers answer update questions from fakes."""

    def factory(home=None):
        return ServiceManager(
            home=home or service_home,
            command=FAKE_COMMAND,
            version_checker=lambda: latest,
            installer=installer,
        )

    return factory


def _recording_installer(
    calls: list[str], *, code: int = 0, output: str = ""
) -> Callable[[], tuple[int, str]]:
    """An installer fake that records it ran and returns ``code``."""

    def install() -> tuple[int, str]:
        calls.append("installed")
        return code, output

    return install


def test_update_check_only_newer_available(manager_factory, service_home: Path) -> None:
    calls: list[str] = []
    factory = _update_factory(
        service_home,
        latest="9.9.9",
        installer=_recording_installer(calls),
    )
    out: list[str] = []
    assert _run(["service", "update", "--check"], factory, out) == 0
    assert any("Update available" in line for line in out)
    assert any("9.9.9" in line for line in out)
    assert calls == []


def test_update_check_only_up_to_date(service_home: Path) -> None:
    probe = ServiceManager(home=service_home, command=FAKE_COMMAND)
    factory = _update_factory(service_home, latest=probe.installed_version())
    out: list[str] = []
    assert _run(["service", "update", "--check"], factory, out) == 0
    assert any("Already up to date" in line for line in out)


def test_update_installs_and_restarts_running_service(service_home: Path) -> None:
    calls: list[str] = []
    factory = _update_factory(
        service_home,
        latest="9.9.9",
        installer=_recording_installer(calls, output="ok"),
    )
    out: list[str] = []
    assert _run(["service", "start"], factory, out) == 0
    first_pid = ServiceManager(home=service_home, command=FAKE_COMMAND).status().pid
    out.clear()
    assert _run(["service", "update"], factory, out) == 0
    assert calls == ["installed"]
    assert any("Updated puspa" in line for line in out)
    assert any("restarted" in line.lower() for line in out)
    current = ServiceManager(home=service_home, command=FAKE_COMMAND).status()
    assert current.running is True
    assert current.pid is not None and current.pid != first_pid
    ServiceManager(home=service_home, command=FAKE_COMMAND).stop()


def test_update_failing_installer_keeps_service_running(service_home: Path) -> None:
    factory = _update_factory(
        service_home,
        latest="9.9.9",
        installer=lambda: (1, "pip broke"),
    )
    out: list[str] = []
    assert _run(["service", "start"], factory, out) == 0
    first_pid = ServiceManager(home=service_home, command=FAKE_COMMAND).status().pid
    out.clear()
    assert _run(["service", "update"], factory, out) == 1
    assert any("upgrade failed" in line for line in out)
    current = ServiceManager(home=service_home, command=FAKE_COMMAND).status()
    assert current.running is True
    assert current.pid == first_pid
    ServiceManager(home=service_home, command=FAKE_COMMAND).stop()


def test_service_without_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["service"]) == 0
    assert "service" in capsys.readouterr().out


def test_main_service_status_via_default_factory(
    monkeypatch: pytest.MonkeyPatch, service_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The top-level entry point reaches the service group."""
    import puspa.service.cli as service_cli

    def fake_factory(home=None):
        return ServiceManager(home=home or service_home, command=FAKE_COMMAND)

    monkeypatch.setattr(service_cli, "_default_factory", fake_factory)
    assert main(["service", "--home", str(service_home), "status"]) == 0
    assert "stopped" in capsys.readouterr().out


@pytest.mark.unit
def test_real_gateway_start_status_stop(service_home: Path) -> None:
    """End to end: the default command starts the real gateway."""
    port = _free_port()
    manager = ServiceManager(home=service_home)
    try:
        result = manager.start(port=port)
        assert result.pid > 0
        deadline = time.monotonic() + 30
        body = ""
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    body = response.read().decode("utf-8", errors="replace")
                break
            except OSError:
                time.sleep(0.5)
        assert manager.status().running is True
        assert "Puspa" in body
    finally:
        manager.stop()
    assert manager.status().running is False
