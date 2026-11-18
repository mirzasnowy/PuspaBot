"""Tests for the coding harness running behind the backend ABC (G16).

The harness loop keeps calling ``run(code)``; the backend
decides where the code executes. These tests prove the seam
works and that the security rule survives it:

- The local backend preserves :class:`LocalTestExecutor`
  behaviour exactly (the loop repairs itself to green).
- A local-backed executor is refused in production, exactly
  like :class:`LocalTestExecutor`.
- A docker-backed executor is accepted in production at
  construction time (it can never run on this VM — the run
  itself raises BackendUnavailable, fail-closed).
- The production factory is unchanged: sandbox only.

These tests use no Puspa database tables themselves (temp dirs
and subprocesses only). Note: the shared ``tests/conftest.py``
autouse fixture still creates the schemas and truncates tables
in ``puspa_test`` for every test file in this directory,
including this one; that fixture cannot be opted out of from
here.
"""

import pytest

from puspa.computer import BackendUnavailable, DockerBackend, LocalBackend
from puspa.harness import (
    BackendExecutor,
    CodingHarness,
    CodingTask,
    HarnessError,
    LocalTestExecutor,
    SandboxExecutor,
    backend_executor,
    create_production_harness,
)
from puspa.harness.loop import STATUS_PASSED, STATUS_SANDBOX_UNAVAILABLE
from puspa.policy import Gate

TEST_CODE = '\nassert add(2, 3) == 5\nassert add(-1, 1) == 0\nprint("ALL TESTS PASSED")\n'

BUGGY_IMPL = "def add(a, b):\n    return a - b\n"
FIXED_IMPL = "def add(a, b):\n    return a + b\n"


def _program(impl: str) -> str:
    return impl + TEST_CODE


def _task() -> CodingTask:
    return CodingTask(
        task_id="task-add",
        user_id="user-1",
        agent_id="agent-puspa",
        description="implement add(a, b) returning the sum",
        test_code=TEST_CODE,
    )


# --- the seam: executor over a backend -----------------------------------------


def test_backend_executor_runs_code_through_local_backend(tmp_path):
    executor = BackendExecutor(LocalBackend(tmp_path / "work"))
    result = executor.run('print("hello")\n')
    assert result.ok is True
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_backend_executor_failure_is_a_result(tmp_path):
    executor = BackendExecutor(LocalBackend(tmp_path / "work"))
    result = executor.run("assert 1 == 2\n")
    assert result.ok is False
    assert "AssertionError" in result.stderr


def test_backend_executor_write_file_goes_through_sync(tmp_path):
    executor = BackendExecutor(LocalBackend(tmp_path / "work"))
    written = executor.write_file("notes/x.txt", "hello\n")
    assert written.read_text() == "hello\n"
    assert executor.backend.sync.read("notes/x.txt") == "hello\n"


def test_backend_executor_factory(tmp_path):
    executor = backend_executor("local", workdir=tmp_path / "work")
    assert isinstance(executor, BackendExecutor)
    assert executor.backend.name == "local"
    assert executor.test_only is True
    assert executor.production_safe is False
    with pytest.raises(Exception, match="unknown execution backend"):
        backend_executor("no-such-backend")


def test_docker_backend_executor_is_production_safe_but_unavailable(tmp_path):
    executor = BackendExecutor(DockerBackend(workdir=tmp_path / "docker"))
    assert executor.production_safe is True
    assert executor.test_only is False
    with pytest.raises(BackendUnavailable, match="cannot run on this host"):
        executor.run('print("x")\n')


# --- the loop through the seam: same behaviour as LocalTestExecutor --------------


def test_harness_repairs_to_green_through_local_backend(tmp_path):
    """The loop, the repair cycle, and the verdict work unchanged
    when execution goes through the backend ABC (local backend)."""
    generated = []

    def generator(task, attempts):
        if attempts and "AssertionError" in attempts[-1].result.stderr:
            generated.append("fixed")
            return _program(FIXED_IMPL)
        generated.append("buggy")
        return _program(BUGGY_IMPL)

    harness = CodingHarness(
        executor=BackendExecutor(LocalBackend(tmp_path / "work")),
        generator=generator,
        max_attempts=5,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_PASSED
    assert generated == ["buggy", "fixed"]
    assert len(outcome.attempts) == 2
    assert outcome.attempts[1].passed is True
    assert "ALL TESTS PASSED" in outcome.attempts[1].result.stdout
    assert outcome.test_only is True
    assert harness.test_only is True


def test_docker_backend_unavailable_stops_loop_without_executing(tmp_path):
    """Docker down: the loop stops with sandbox_unavailable, the
    code never runs anywhere (no host probe file appears)."""
    probe = tmp_path / "host-probe.txt"
    code = f"open({str(probe)!r}, 'w').write('host-executed')\n"
    calls = {"n": 0}

    def generator(task, attempts):
        calls["n"] += 1
        return code

    harness = CodingHarness(
        executor=BackendExecutor(DockerBackend(workdir=tmp_path / "docker")),
        generator=generator,
        max_attempts=3,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_SANDBOX_UNAVAILABLE
    assert outcome.attempts == []
    assert calls["n"] == 1
    assert not probe.exists()


def test_docker_backend_unavailable_in_reproduce_probe_stops_loop(tmp_path):
    """Same fail-closed rule inside the reproduce-first starter
    probe: a dead backend there stops the run with
    sandbox_unavailable instead of raising out of harness.run
    (the probe previously had no executor-exception catch)."""
    probe = tmp_path / "host-probe.txt"
    starter = f"open({str(probe)!r}, 'w').write('host-executed')\n"
    calls = {"n": 0}

    def generator(task, attempts):
        calls["n"] += 1
        return _program(FIXED_IMPL)

    task = _task()
    task.starter_code = starter
    harness = CodingHarness(
        executor=BackendExecutor(DockerBackend(workdir=tmp_path / "docker")),
        generator=generator,
        max_attempts=3,
        reproduce_first=True,
    )
    outcome = harness.run(task)

    assert outcome.status == STATUS_SANDBOX_UNAVAILABLE
    assert outcome.attempts == []
    assert calls["n"] == 0
    assert not probe.exists()


# --- production gate: unchanged rule, new executor shape ------------------------


def test_production_harness_rejects_local_backend_executor(tmp_path):
    with pytest.raises(HarnessError, match="production harness requires"):
        CodingHarness(
            executor=BackendExecutor(LocalBackend(tmp_path / "work")),
            generator=lambda task, attempts: "",
            production=True,
        )


def test_production_harness_accepts_docker_backend_executor(tmp_path):
    """Construction only: the run itself is unavailable on this VM,
    but the gate accepts the docker-backed shape for production."""
    harness = CodingHarness(
        executor=BackendExecutor(DockerBackend(workdir=tmp_path / "docker")),
        generator=lambda task, attempts: "",
        production=True,
    )
    assert harness.production is True
    assert harness.test_only is False


def test_production_harness_still_rejects_local_test_executor(tmp_path):
    with pytest.raises(HarnessError, match="production harness requires"):
        CodingHarness(
            executor=LocalTestExecutor(tmp_path / "work"),
            generator=lambda task, attempts: "",
            production=True,
        )


def test_production_factory_still_builds_sandbox_only():
    import time

    now = int(time.time())
    harness = create_production_harness(
        generator=lambda task, attempts: "",
        gate=Gate(),
        token_payload={
            "user_id": "user-1",
            "agent_id": "agent-puspa",
            "subagent_id": None,
            "tools": ["write_code", "run_tests", "deploy"],
            "domains": [],
            "connectors": {},
            "budget": {"step_max": 25, "token_max": 100000, "rupiah_max": 2000},
            "exp": now + 3600,
            "iat": now,
            "parent": None,
        },
    )
    assert harness.production is True
    assert isinstance(harness.executor, SandboxExecutor)
    assert harness.test_only is False
