"""G34: PTY terminal sessions + headless-browser sessions in the sandbox.

OpenShell mechanisms under test (rewritten as Puspa code):
a retained pseudo-terminal with resize (``pty.rs`` /
``sandbox_protocol.proto`` ``Resize``), dedicated process
groups for terminal children, and a browser as a workload
inside the sandbox reached over CDP — with the live browser
honestly unavailable until a container host exists.
"""

import time

import pytest

from puspa.computer import BackendUnavailable, DockerBackend, LocalBackend
from puspa.computer.errors import BrowserSessionBusy, BrowserUnavailable
from puspa.sandbox import (
    SandboxBrowserManager,
    SandboxBrowserStatus,
    SessionManager,
    start_terminal,
)


def _drain(terminal, *, timeout_s: float = 3.0) -> bytes:
    """Read until ``timeout_s`` of quiet, then return all bytes."""
    out = b""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        chunk = terminal.read_output(timeout_s=0.2)
        if chunk:
            out += chunk
            deadline = time.monotonic() + 0.5
    return out


# --- terminal: local backend (real PTY) ---------------------------------------


def test_terminal_echoes_input_via_cat(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    terminal = start_terminal(backend, ["cat"])
    try:
        terminal.send_input(b"hello-terminal\n")
        out = _drain(terminal)
        assert b"hello-terminal" in out
        assert terminal.is_alive
        assert terminal.poll() is None
    finally:
        terminal.close()
    assert terminal.poll() is not None
    terminal.close()  # idempotent


def test_terminal_runs_command_and_reports_exit(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    terminal = start_terminal(backend, ["python3", "-c", "print('pty-ok')"])
    assert terminal.wait(timeout_s=10) == 0
    out = _drain(terminal)
    assert b"pty-ok" in out
    terminal.close()


def test_terminal_resize_and_context_manager(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    with start_terminal(backend, ["sleep", "30"], cols=80, rows=24) as terminal:
        assert (terminal.cols, terminal.rows) == (80, 24)
        terminal.resize(120, 40)
        assert (terminal.cols, terminal.rows) == (120, 40)
        assert terminal.is_alive
        with pytest.raises(ValueError):
            terminal.resize(0, 24)
    assert terminal.poll() is not None


def test_terminal_cwd_stays_in_scope(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    from puspa.harness.sandbox_profiles import ProfileDenied

    with pytest.raises(ProfileDenied, match="outside the sandbox scope"):
        start_terminal(backend, ["pwd"], cwd="..")


def test_terminal_validates_argv(tmp_path):
    backend = LocalBackend(workdir=tmp_path)
    with pytest.raises(ValueError):
        start_terminal(backend, [])
    with pytest.raises(TypeError):
        start_terminal(backend, ["echo", 1])


def test_terminal_docker_fails_closed(tmp_path):
    backend = DockerBackend(workdir=tmp_path)
    with pytest.raises(BackendUnavailable, match="no running container"):
        start_terminal(backend, ["bash"])


# --- browser: contract + bookkeeping, no live browser --------------------------


def test_browser_manager_create_get_list_close(tmp_path):
    sessions = SessionManager(base_dir=tmp_path)
    session = sessions.create("user-a")
    manager = SandboxBrowserManager()
    record = manager.create(session)
    assert record.status is SandboxBrowserStatus.PENDING_HOST
    assert record.cdp_url is None
    assert record.profile_dir == session.workdir / "browser-profile"
    assert record.profile_dir.is_dir()
    assert record.sandbox_session_id == session.session_id
    assert manager.get(record.session_id) is record
    assert manager.get("no-such-id") is None
    assert [r.session_id for r in manager.list_live()] == [record.session_id]
    snapshot = record.to_dict()
    assert snapshot["status"] == "pending_host"
    assert snapshot["cdp_url"] is None
    assert manager.close(record.session_id) is True
    assert record.status is SandboxBrowserStatus.CLOSED
    assert manager.close(record.session_id) is False
    assert manager.list_live() == []
    sessions.kill(session.session_id)


def test_browser_manager_one_live_session_per_sandbox_session(tmp_path):
    sessions = SessionManager(base_dir=tmp_path)
    session = sessions.create("user-a")
    manager = SandboxBrowserManager()
    first = manager.create(session)
    with pytest.raises(BrowserSessionBusy, match="already has live browser session"):
        manager.create(session)
    manager.close(first.session_id)
    second = manager.create(session)
    assert second.session_id != first.session_id
    manager.close(second.session_id)
    sessions.kill(session.session_id)


def test_browser_launch_refuses_without_container_host(tmp_path):
    """No container can run here, so no Chromium can run inside
    a sandbox here. Launch refuses; nothing is faked."""
    sessions = SessionManager(base_dir=tmp_path)
    session = sessions.create("user-a")
    manager = SandboxBrowserManager()
    record = manager.create(session)
    with pytest.raises(BrowserUnavailable, match="no live headless browser"):
        manager.launch(record.session_id)
    # The record is untouched by the refused launch.
    assert record.status is SandboxBrowserStatus.PENDING_HOST
    assert record.cdp_url is None
    manager.close(record.session_id)
    sessions.kill(session.session_id)


def test_browser_launch_unknown_or_closed(tmp_path):
    manager = SandboxBrowserManager()
    with pytest.raises(KeyError, match="unknown browser session"):
        manager.launch("no-such-id")
    sessions = SessionManager(base_dir=tmp_path)
    session = sessions.create("user-a")
    record = manager.create(session)
    manager.close(record.session_id)
    with pytest.raises(BrowserUnavailable, match="is closed"):
        manager.launch(record.session_id)
    sessions.kill(session.session_id)
