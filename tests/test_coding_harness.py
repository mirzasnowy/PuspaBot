"""Tests for the coding harness (Fase Otak) + per-tool permissions.

Mapping: opencode / claude code loop and per-tool permission model,
SWE-bench task shape (fixed FAIL_TO_PASS tests), Voyager
self-verification (generated code is run before it is trusted).
See docs/brain-repo-list.md and docs/reuse-fase-otak-harness.md.

These tests use no Puspa database tables themselves (in-memory
stubs, a counting executor, and temp dirs only). Note: the shared
``tests/conftest.py`` autouse fixture still creates the schemas and
truncates tables in ``puspa_test`` for every test file in this
directory, including this one; that fixture cannot be opted out of
from here.
"""

import time

import pytest

from puspa.harness import (
    CodingHarness,
    CodingTask,
    HarnessError,
    LocalTestExecutor,
    SandboxExecutor,
    create_production_harness,
    tool_permission_for,
)
from puspa.harness.executor import ExecutionResult, Executor
from puspa.harness.loop import (
    STATUS_ATTEMPT_LIMIT,
    STATUS_BUDGET_EXHAUSTED,
    STATUS_PASSED,
    STATUS_PENDING_APPROVAL,
    STATUS_SANDBOX_UNAVAILABLE,
)
from puspa.policy import Gate, GateDenied, TokenError
from puspa.policy.tokens import _issued, _revoked, is_subset, issue_token, tool_permission
from puspa.sandbox import Sandbox, SandboxUnavailable

HOST_PROBE = "/tmp/puspa-harness-host-probe.txt"

TEST_CODE = (
    "\nassert add(2, 3) == 5\n"
    "assert add(-1, 1) == 0\n"
    "assert add(0, 0) == 0\n"
    'print("ALL TESTS PASSED")\n'
)

BUGGY_IMPL = "def add(a, b):\n    return a - b\n"
FIXED_IMPL = "def add(a, b):\n    return a + b\n"


def _program(impl: str) -> str:
    return impl + TEST_CODE


def _task(**overrides) -> CodingTask:
    task = CodingTask(
        task_id="task-add",
        user_id="user-1",
        agent_id="agent-puspa",
        description="implement add(a, b) returning the sum",
        test_code=TEST_CODE,
    )
    for key, value in overrides.items():
        setattr(task, key, value)
    return task


def _harness_payload(**overrides) -> dict:
    now = int(time.time())
    payload = {
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
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _reset_revocation():
    """Isolation: token revocation is in-memory global state."""
    _revoked.clear()
    _issued.clear()
    yield
    _revoked.clear()
    _issued.clear()


@pytest.fixture
def stub_log():
    events = []

    def log(event_type, content):
        events.append((event_type, content))

    log.events = events
    return log


class _CountingExecutor(Executor):
    """Wraps another executor and counts run() calls."""

    def __init__(self, inner: Executor) -> None:
        self.inner = inner
        self.calls = 0
        self.codes = []

    def run(self, code: str, *, timeout_s: int = 30) -> ExecutionResult:
        self.calls += 1
        self.codes.append(code)
        return self.inner.run(code, timeout_s=timeout_s)


class _DeadSandbox:
    """Stand-in for a sandbox that cannot run (Docker down)."""

    def run(self, code, *, timeout_s=30):
        raise SandboxUnavailable("stub: docker unavailable")


# --- end to end: loop repairs itself until green ---------------------------


def test_e2e_loop_to_green(tmp_path):
    """The loop runs the buggy code, reads the failure, fixes it,
    and ends green in 2 attempts (local test executor only)."""
    generated = []

    def generator(task, attempts):
        if attempts and "AssertionError" in attempts[-1].result.stderr:
            generated.append("fixed")
            return _program(FIXED_IMPL)
        generated.append("buggy")
        return _program(BUGGY_IMPL)

    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=generator,
        max_attempts=5,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_PASSED
    assert generated == ["buggy", "fixed"]
    assert len(outcome.attempts) == 2
    assert outcome.attempts[0].passed is False
    assert "AssertionError" in outcome.attempts[0].result.stderr
    assert outcome.attempts[1].passed is True
    assert "ALL TESTS PASSED" in outcome.attempts[1].result.stdout
    assert outcome.final_code == _program(FIXED_IMPL)
    assert outcome.test_only is True
    event_types = [event["type"] for event in harness.event_log()]
    assert "harness_started" in event_types
    assert "harness_passed" in event_types


def test_local_executor_timeout_is_a_result_not_a_crash(tmp_path):
    executor = LocalTestExecutor(tmp_path / "work")
    result = executor.run("while True:\n    pass\n", timeout_s=1)
    assert result.ok is False
    assert result.timed_out is True
    assert result.exit_code is None


# --- fail-closed: no host execution in the production path -----------------


def test_production_harness_rejects_local_executor(tmp_path):
    """A local (host) executor can never be a production harness."""
    with pytest.raises(HarnessError):
        CodingHarness(
            executor=LocalTestExecutor(tmp_path / "work"),
            generator=lambda task, attempts: "",
            production=True,
        )


def test_production_factory_builds_sandbox_only():
    """The production factory only ever builds a SandboxExecutor."""
    harness = create_production_harness(
        generator=lambda task, attempts: "",
        gate=Gate(),
        token_payload=_harness_payload(),
    )
    assert harness.production is True
    assert isinstance(harness.executor, SandboxExecutor)
    assert harness.test_only is False


def test_sandbox_unavailable_stops_loop_without_executing(tmp_path):
    """Sandbox down: the loop stops with sandbox_unavailable, the
    code never runs anywhere (no host probe file appears), and the
    generator is not asked for a second attempt."""
    import os

    probe = tmp_path / "host-probe.txt"
    code = f"open({str(probe)!r}, 'w').write('host-executed')\n"
    calls = {"n": 0}

    def generator(task, attempts):
        calls["n"] += 1
        return code

    harness = CodingHarness(
        executor=SandboxExecutor(_DeadSandbox()),
        generator=generator,
        max_attempts=3,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_SANDBOX_UNAVAILABLE
    assert outcome.attempts == []
    assert calls["n"] == 1
    assert not probe.exists()
    assert not os.path.exists(HOST_PROBE)


def test_real_sandbox_never_executes_on_host(tmp_path):
    """With the real sandbox: either it raises SandboxUnavailable
    (this VM: containers cannot start) or it runs inside the
    container. In both cases the host probe file must not appear."""
    probe = tmp_path / "host-probe.txt"
    code = f"open({str(probe)!r}, 'w').write('host-executed')\n"
    executor = SandboxExecutor(Sandbox())
    try:
        executor.run(code, timeout_s=30)
    except SandboxUnavailable:
        pass
    assert not probe.exists()


# --- attempt limit + cost ceiling ------------------------------------------


def test_attempt_limit(tmp_path):
    """A generator that never fixes the bug stops exactly at the
    attempt limit."""
    calls = {"n": 0}

    def generator(task, attempts):
        calls["n"] += 1
        return _program(BUGGY_IMPL)

    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=generator,
        max_attempts=3,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_ATTEMPT_LIMIT
    assert len(outcome.attempts) == 3
    assert calls["n"] == 3
    assert outcome.final_code is None
    assert "3 attempts" in outcome.reason


def test_budget_ceiling_stops_loop(tmp_path):
    """Cost ceiling (existing BudgetTracker pattern): the second
    attempt would exceed Rp1000, so the loop stops budget_exhausted
    after exactly one executed attempt."""
    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=lambda task, attempts: _program(BUGGY_IMPL),
        max_attempts=5,
        cost_ceiling_rp=1000,
        cost_per_attempt_rp=600,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_BUDGET_EXHAUSTED
    assert len(outcome.attempts) == 1
    assert harness.budget.rupiah_used == 600


def test_harness_constructor_rejects_bad_limits(tmp_path):
    with pytest.raises(HarnessError):
        CodingHarness(
            executor=LocalTestExecutor(tmp_path),
            generator=lambda task, attempts: "",
            max_attempts=0,
        )
    with pytest.raises(HarnessError):
        CodingHarness(
            executor=LocalTestExecutor(tmp_path),
            generator=lambda task, attempts: "",
            cost_ceiling_rp=-1,
        )


# --- per-tool permissions (opencode / claude code model) -------------------


def test_tool_permission_levels():
    payload = _harness_payload(
        tool_permissions={"run_tests": "ask", "deploy": "deny"}
    )
    assert tool_permission(payload, "write_code") == "allow"
    assert tool_permission(payload, "run_tests") == "ask"
    assert tool_permission(payload, "deploy") == "deny"
    assert tool_permission(payload, "unknown_tool") == "deny"
    assert tool_permission_for(payload, "run_tests") == "ask"
    assert tool_permission_for(None, "run_tests") == "deny"


def test_tool_permission_explicit_level_beats_tools_list():
    """An explicit 'ask' wins over membership in the tools list."""
    payload = _harness_payload(tool_permissions={"write_code": "ask"})
    assert tool_permission(payload, "write_code") == "ask"


def test_tool_permissions_validated_on_issue(ephemeral_keys):
    sk, _pk = ephemeral_keys
    with pytest.raises(TokenError):
        issue_token(_harness_payload(tool_permissions={"run_tests": "maybe"}), sk)
    token = issue_token(_harness_payload(tool_permissions={"run_tests": "ask"}), sk)
    assert token.count(".") == 1


def test_is_subset_per_tool_cannot_widen():
    parent = _harness_payload(tool_permissions={"run_tests": "ask"})
    # Narrowing ask -> deny is fine.
    assert is_subset(_harness_payload(tool_permissions={"run_tests": "deny"}), parent) is True
    # Widening ask -> allow is not a subset.
    assert is_subset(_harness_payload(tool_permissions={"run_tests": "allow"}), parent) is False
    # Granting allow for a tool the parent denies is not a subset
    # (child tools list matches the parent here, so only the
    # per-tool levels decide).
    parent_denied = _harness_payload(tool_permissions={"deploy": "deny"})
    child_widens = _harness_payload(tool_permissions={"deploy": "allow"})
    assert is_subset(child_widens, parent_denied) is False
    # Old behaviour unchanged when no tool_permissions are present.
    assert is_subset(_harness_payload(tools=["write_code"]), _harness_payload()) is True
    assert is_subset(_harness_payload(tools=["write_code", "delete_all"]), _harness_payload()) is False


def test_gate_authorize_tool(stub_log):
    gate = Gate(log=stub_log)
    payload = _harness_payload(tool_permissions={"run_tests": "ask", "deploy": "deny"})
    assert gate.authorize_tool(payload, "write_code") == "allow"
    assert gate.authorize_tool(payload, "run_tests") == "ask"
    with pytest.raises(GateDenied):
        gate.authorize_tool(payload, "deploy")
    with pytest.raises(GateDenied):
        gate.authorize_tool(payload, "unknown_tool")


def test_gate_deploy_kind(ephemeral_keys, stub_log):
    sk, pk = ephemeral_keys
    gate = Gate(log=stub_log, public_key=pk)
    token = issue_token(_harness_payload(), sk)
    request = gate.request_approval(
        user_id="user-1", agent_id="agent-puspa", token=token,
        kind="deploy", content={"task_id": "task-add"},
    )
    assert request.kind == "deploy"
    assert request.status == "requested"
    # A token without the deploy tool cannot even ask for deploy.
    token_no_deploy = issue_token(_harness_payload(tools=["write_code", "run_tests"]), sk)
    with pytest.raises(GateDenied):
        gate.request_approval(
            user_id="user-1", agent_id="agent-puspa", token=token_no_deploy,
            kind="deploy", content={"task_id": "task-add"},
        )


def test_gate_deploy_ask_level_can_request_approval(ephemeral_keys, stub_log):
    """A tool at level 'ask' may request approval (that IS the ask
    flow); it just cannot run before the user approves."""
    sk, pk = ephemeral_keys
    gate = Gate(log=stub_log, public_key=pk)
    token = issue_token(
        _harness_payload(
            tools=["write_code", "run_tests"],
            tool_permissions={"deploy": "ask"},
        ),
        sk,
    )
    request = gate.request_approval(
        user_id="user-1", agent_id="agent-puspa", token=token,
        kind="deploy", content={"task_id": "task-add"},
    )
    assert request.status == "requested"


# --- harness + gate: denied / ask tools, deploy approval -------------------


def test_harness_denied_tool_runs_nothing(tmp_path):
    """A denied tool stops the run before any code is generated or
    executed."""
    executor = _CountingExecutor(LocalTestExecutor(tmp_path / "work"))
    calls = {"n": 0}

    def generator(task, attempts):
        calls["n"] += 1
        return _program(FIXED_IMPL)

    harness = CodingHarness(
        executor=executor,
        generator=generator,
        gate=Gate(),
        token_payload=_harness_payload(tool_permissions={"write_code": "deny"}),
    )
    with pytest.raises(GateDenied):
        harness.run(_task())
    assert calls["n"] == 0
    assert executor.calls == 0


def test_harness_ask_tool_pending_approval(tmp_path):
    """An 'ask' tool stops the run as pending_approval without
    running anything."""
    executor = _CountingExecutor(LocalTestExecutor(tmp_path / "work"))
    harness = CodingHarness(
        executor=executor,
        generator=lambda task, attempts: _program(FIXED_IMPL),
        gate=Gate(),
        token_payload=_harness_payload(tool_permissions={"run_tests": "ask"}),
    )
    outcome = harness.run(_task())
    assert outcome.status == STATUS_PENDING_APPROVAL
    assert outcome.attempts == []
    assert executor.calls == 0


def test_deploy_needs_user_approval(tmp_path, stub_log):
    """Deploy after a green run: no approval -> no deploy; approve
    -> deploy runs exactly once (gate idempotency)."""
    gate = Gate(log=stub_log)
    payload = _harness_payload()
    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=lambda task, attempts: _program(FIXED_IMPL),
        gate=gate,
        token_payload=payload,
    )
    outcome = harness.run(_task())
    assert outcome.status == STATUS_PASSED

    request = harness.request_deploy(outcome)
    assert request.kind == "deploy"
    assert request.status == "requested"

    calls = []
    with pytest.raises(GateDenied):
        harness.deploy(request.request_id, executor=lambda content: calls.append(content))
    assert calls == []

    gate.approve(request.request_id, "user-1")
    result = harness.deploy(
        request.request_id,
        executor=lambda content: {"deployed_task": content["task_id"]},
    )
    assert result == {"deployed_task": "task-add"}
    # Second deploy call returns the same result without re-running.
    again = harness.deploy(
        request.request_id,
        executor=lambda content: calls.append(content),
    )
    assert again == {"deployed_task": "task-add"}
    assert calls == []


def test_request_deploy_rejects_non_passed_outcome(tmp_path):
    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=lambda task, attempts: _program(BUGGY_IMPL),
        gate=Gate(),
        token_payload=_harness_payload(),
        max_attempts=1,
    )
    outcome = harness.run(_task())
    assert outcome.status == STATUS_ATTEMPT_LIMIT
    with pytest.raises(HarnessError):
        harness.request_deploy(outcome)


def test_request_deploy_without_gate_refused(tmp_path):
    """No gate configured means no approval path, so deploy is
    refused rather than silently allowed."""
    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=lambda task, attempts: _program(FIXED_IMPL),
    )
    outcome = harness.run(_task())
    assert outcome.status == STATUS_PASSED
    with pytest.raises(HarnessError):
        harness.request_deploy(outcome)
