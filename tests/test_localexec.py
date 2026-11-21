"""Tests for the public sandbox (puspa/localexec).

Covers the split design 2.3 surface: permission prompt approve /
deny, session approval, workspace-bound enforcement, timeout,
output caps, and environment scrubbing. No database, no network.
"""

import sys
from pathlib import Path

import pytest

from puspa.localexec import (
    ExecutionResult,
    Executor,
    LocalExecutor,
    OutsideWorkspaceError,
    PermissionDecision,
    PermissionDenied,
    scrub_env,
)

APPROVE_ONCE = PermissionDecision.APPROVE_ONCE
APPROVE_SESSION = PermissionDecision.APPROVE_SESSION
DENY = PermissionDecision.DENY


class FakePrompt:
    """A prompt that answers from a queue and records every ask."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.asked = []

    def ask(self, command, cwd):
        self.asked.append((tuple(command), cwd))
        return self.decisions.pop(0)


@pytest.fixture
def workspace(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    return work


@pytest.fixture
def approved(workspace):
    return LocalExecutor(workspace, prompt=FakePrompt([APPROVE_ONCE]))


def test_approve_once_runs_command(approved, workspace):
    result = approved.execute([sys.executable, "-c", "print('halo')"])
    assert isinstance(result, ExecutionResult)
    assert result.returncode == 0
    assert result.stdout.strip() == "halo"
    assert result.timed_out is False
    assert result.truncated is False


def test_string_command_is_split(approved):
    result = approved.execute("echo hello")
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_deny_raises_and_does_not_run(workspace, tmp_path):
    marker = workspace / "ran.txt"
    prompt = FakePrompt([DENY])
    executor = LocalExecutor(workspace, prompt=prompt)
    with pytest.raises(PermissionDenied):
        executor.execute([sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"])
    assert not marker.exists()
    assert len(prompt.asked) == 1


def test_session_approval_prompts_only_once(workspace):
    prompt = FakePrompt([APPROVE_SESSION])
    executor = LocalExecutor(workspace, prompt=prompt)
    first = executor.execute([sys.executable, "-c", "print(1)"])
    second = executor.execute([sys.executable, "-c", "print(2)"])
    assert first.stdout.strip() == "1"
    assert second.stdout.strip() == "2"
    assert len(prompt.asked) == 1


def test_approve_once_prompts_every_time(workspace):
    prompt = FakePrompt([APPROVE_ONCE, APPROVE_ONCE])
    executor = LocalExecutor(workspace, prompt=prompt)
    executor.execute([sys.executable, "-c", "print(1)"])
    executor.execute([sys.executable, "-c", "print(2)"])
    assert len(prompt.asked) == 2


def test_cwd_outside_workspace_is_refused(workspace, tmp_path):
    executor = LocalExecutor(workspace, prompt=FakePrompt([APPROVE_ONCE]))
    with pytest.raises(OutsideWorkspaceError):
        executor.execute([sys.executable, "-c", "print(1)"], cwd=tmp_path)
    with pytest.raises(OutsideWorkspaceError):
        # ".." escape attempt from inside the workspace.
        executor.execute([sys.executable, "-c", "print(1)"], cwd=workspace / "..")


def test_subdirectory_inside_workspace_is_allowed(workspace):
    sub = workspace / "sub"
    sub.mkdir(parents=True)
    executor = LocalExecutor(workspace, prompt=FakePrompt([APPROVE_ONCE]))
    result = executor.execute([sys.executable, "-c", "print('ok')"], cwd=sub)
    assert result.returncode == 0


def test_timeout_kills_and_reports(approved):
    result = approved.execute(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=0.3,
    )
    assert result.timed_out is True
    assert result.returncode is None


def test_output_cap_truncates_stdout(workspace):
    executor = LocalExecutor(workspace, prompt=FakePrompt([APPROVE_ONCE]), max_output_bytes=100)
    result = executor.execute(
        [sys.executable, "-c", "print('x' * 2000)"],
    )
    assert result.truncated is True
    assert len(result.stdout.encode("utf-8")) <= 100
    assert result.returncode == 0


def test_output_cap_truncates_stderr(workspace):
    executor = LocalExecutor(workspace, prompt=FakePrompt([APPROVE_ONCE]), max_output_bytes=50)
    result = executor.execute(
        [sys.executable, "-c", "import sys; sys.stderr.write('e' * 500)"],
    )
    assert result.truncated is True
    assert len(result.stderr.encode("utf-8")) <= 50


def test_env_scrubbing_drops_secret_names():
    env = {
        "PATH": "/usr/bin",
        "PUSPA_VAULT_MASTER_KEY": "rahasia",
        "MY_API_TOKEN": "rahasia",
        "DB_PASSWORD": "rahasia",
        "KEEP_ME": "boleh",
    }
    cleaned = scrub_env(env)
    assert cleaned == {"PATH": "/usr/bin", "KEEP_ME": "boleh"}


def test_child_process_never_sees_secrets(workspace, monkeypatch):
    monkeypatch.setenv("PUSPA_TEST_LOCALEXEC_TOKEN", "rahasia-token")
    monkeypatch.setenv("PUSPA_TEST_LOCALEXEC_KEEP", "boleh")
    executor = LocalExecutor(workspace, prompt=FakePrompt([APPROVE_ONCE]))
    result = executor.execute(
        [
            sys.executable,
            "-c",
            (
                "import os; print(os.environ.get('PUSPA_TEST_LOCALEXEC_TOKEN', 'ABSENT')); "
                "print(os.environ.get('PUSPA_TEST_LOCALEXEC_KEEP', 'ABSENT'))"
            ),
        ]
    )
    assert result.returncode == 0
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "ABSENT"
    assert lines[1] == "boleh"


def test_failing_command_returns_its_code(approved):
    result = approved.execute([sys.executable, "-c", "raise SystemExit(3)"])
    assert result.returncode == 3
    assert result.timed_out is False


def test_empty_command_is_refused(approved):
    with pytest.raises(ValueError):
        approved.execute([])


def test_local_executor_satisfies_the_executor_port(workspace):
    executor = LocalExecutor(workspace, prompt=FakePrompt([APPROVE_ONCE]))
    assert isinstance(executor, Executor)
    assert isinstance(Path.cwd(), Path)
