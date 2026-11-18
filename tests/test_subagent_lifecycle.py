"""Tests for the public subagent lifecycle operations (G25).

In-memory only: run with ``pytest tests/test_subagent_lifecycle.py
--noconftest``. The shared conftest is skipped on purpose — these
tests never touch Postgres, and the conftest's autouse fixture
would connect to it for every test.
"""

import threading
import time

import pytest

from puspa.subagents import (
    LifecycleError,
    NotTerminalError,
    ReconnectUnavailableError,
    SubagentLifecycle,
    SubagentState,
    ToolsetRejectedError,
)
from puspa.tools.registry import ToolRegistry
from puspa.tools.toolsets import default_toolsets


def make_tooling():
    """Fresh tool registry + toolsets with the two known tools."""
    registry = ToolRegistry()
    registry.register("plugin_echo", "Echo.", {}, lambda: "echo")
    registry.register("run_code", "Run code.", {}, lambda: "ok")
    return registry, default_toolsets()


def make_lifecycle(**kwargs):
    registry, toolsets = make_tooling()
    return SubagentLifecycle(registry=registry, toolsets=toolsets, **kwargs)


def launch(lifecycle, child_fn, **kwargs):
    kwargs.setdefault("user_id", "user-1")
    kwargs.setdefault("agent_id", "agent-1")
    kwargs.setdefault("task_id", "task-1")
    kwargs.setdefault("goal", "do the thing")
    kwargs.setdefault("child_fn", child_fn)
    return lifecycle.launch(**kwargs)


class CapturingRunner:
    """Runner that captures the child body instead of starting it."""

    def __init__(self):
        self.bodies = []

    def __call__(self, record, run_child):
        self.bodies.append(run_child)


def test_launch_status_wait_result_happy_path():
    lifecycle = make_lifecycle()
    handle = launch(lifecycle, lambda ctx: "done")
    status = lifecycle.status(handle)
    assert status["state"] in {
        SubagentState.PENDING.value,
        SubagentState.STARTING.value,
        SubagentState.RUNNING.value,
        SubagentState.SUCCEEDED.value,
    }
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    assert lifecycle.status(handle)["state"] == SubagentState.SUCCEEDED.value
    result = lifecycle.result(handle)
    assert result.text == "done"
    assert result.state is SubagentState.SUCCEEDED
    assert result.error == ""
    assert not result.truncated


def test_wait_timeout_never_claims_done():
    started = threading.Event()
    release = threading.Event()

    def child(ctx):
        started.set()
        release.wait(timeout=10)
        return "late"

    lifecycle = make_lifecycle()
    handle = launch(lifecycle, child)
    assert started.wait(timeout=5)
    # The child is still running: wait with a short timeout reports
    # the live state, never a terminal one.
    assert lifecycle.wait(handle, timeout_s=0.2) is SubagentState.RUNNING
    assert lifecycle.status(handle)["state"] == SubagentState.RUNNING.value
    with pytest.raises(NotTerminalError):
        lifecycle.result(handle)
    release.set()
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    assert lifecycle.result(handle).text == "late"


def test_cancel_is_cooperative_and_never_claims_done_early():
    boundaries = []
    release_first = threading.Event()

    def child(ctx):
        for step in range(20):
            ctx.boundary()
            boundaries.append(step)
            if step == 0:
                release_first.set()
            time.sleep(0.1)
        return "finished anyway"

    lifecycle = make_lifecycle()
    handle = launch(lifecycle, child)
    assert release_first.wait(timeout=5)
    state = lifecycle.cancel(handle)
    assert state is SubagentState.CANCEL_REQUESTED
    # Right after cancel returns the child is NOT done yet: the
    # request is in flight until the next boundary observes it.
    assert lifecycle.status(handle)["state"] == SubagentState.CANCEL_REQUESTED.value
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.CANCELLED
    result = lifecycle.result(handle)
    assert result.state is SubagentState.CANCELLED
    assert result.text == ""
    assert "boundary" in result.error
    assert boundaries  # the child did run until the cancel landed
    # Cancelling a terminal child is a no-op that returns its state.
    assert lifecycle.cancel(handle) is SubagentState.CANCELLED


def test_cancel_pending_child_never_runs():
    ran = []
    runner = CapturingRunner()
    lifecycle = make_lifecycle(runner=runner)
    handle = launch(lifecycle, lambda ctx: ran.append(True) or "x")
    assert lifecycle.status(handle)["state"] == SubagentState.PENDING.value
    assert lifecycle.cancel(handle) is SubagentState.CANCELLED
    assert lifecycle.result(handle).state is SubagentState.CANCELLED
    assert ran == []
    # Even if the runner starts the body later, nothing runs.
    runner.bodies[0]()
    assert ran == []
    assert lifecycle.status(handle)["state"] == SubagentState.CANCELLED.value


def test_steer_queues_text_for_the_next_boundary():
    seen = []
    go = threading.Event()

    def child(ctx):
        go.wait(timeout=10)
        for _ in range(3):
            ctx.boundary()
            seen.extend(ctx.drain_steer())
            time.sleep(0.02)
        return "steered: " + "|".join(seen)

    lifecycle = make_lifecycle()
    handle = launch(lifecycle, child)
    assert lifecycle.steer(handle, "first") == 1
    assert lifecycle.steer(handle, "second") == 2
    go.set()
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    assert lifecycle.result(handle).text == "steered: first|second"
    with pytest.raises(LifecycleError, match="terminal"):
        lifecycle.steer(handle, "too late")


def test_reconnect_same_process_works_other_generation_refused():
    lifecycle = make_lifecycle()
    handle = launch(lifecycle, lambda ctx: "ok")
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    snapshot = lifecycle.reconnect(handle)
    assert snapshot["state"] == SubagentState.SUCCEEDED.value
    # A fresh lifecycle is a new process generation: the same
    # handle can never reconnect, and never starts a replacement.
    other = make_lifecycle()
    with pytest.raises(ReconnectUnavailableError) as exc_info:
        other.reconnect(handle)
    assert exc_info.value.code == "RECONNECT_UNAVAILABLE"


def test_shutdown_marks_live_children_interrupted():
    release = threading.Event()

    def child(ctx):
        release.wait(timeout=10)
        return "never"

    lifecycle = make_lifecycle()
    handle = launch(lifecycle, child)
    assert lifecycle.wait(handle, timeout_s=0.2) is SubagentState.RUNNING
    affected = lifecycle.shutdown()
    assert [item["state"] for item in affected] == [SubagentState.INTERRUPTED.value]
    assert lifecycle.status(handle)["state"] == SubagentState.INTERRUPTED.value
    assert lifecycle.result(handle).error == "host shutdown"
    release.set()


def test_pool_full_refuses_launch():
    runner = CapturingRunner()
    lifecycle = make_lifecycle(runner=runner)
    handles = [launch(lifecycle, lambda ctx: "x", task_id=f"task-{i}") for i in range(4)]
    assert len(handles) == 4
    with pytest.raises(LifecycleError, match="pool is full"):
        launch(lifecycle, lambda ctx: "x", task_id="task-5")
    lifecycle.shutdown()


def test_child_toolset_must_fit_parent_toolset_and_token():
    lifecycle = make_lifecycle()
    # Coding child under a coding parent is fine.
    handle = launch(
        lifecycle,
        lambda ctx: "ok",
        toolset="coding",
        parent_toolset="coding",
        task_id="task-coding",
    )
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    # Coding child under a default parent widens the parent: refused.
    with pytest.raises(ToolsetRejectedError, match="widens"):
        launch(lifecycle, lambda ctx: "x", toolset="coding", parent_toolset="default")
    # A child toolset wider than the parent *token* tools is refused.
    parent_token = {
        "user_id": "user-1",
        "agent_id": "agent-1",
        "tools": ["plugin_echo"],
        "domains": [],
        "connectors": {},
        "budget": {"step_max": 25, "token_max": 20000, "rupiah_max": 2000},
        "exp": 9999999999,
    }
    with pytest.raises(LifecycleError, match="outside the parent token"):
        launch(
            lifecycle,
            lambda ctx: "x",
            toolset="coding",
            parent_toolset="coding",
            parent_token=parent_token,
            task_id="task-token",
        )
    # The same child fits when the token allows both tools.
    parent_token["tools"] = ["plugin_echo", "run_code"]
    handle = launch(
        lifecycle,
        lambda ctx: "ok",
        toolset="coding",
        parent_toolset="coding",
        parent_token=parent_token,
        task_id="task-token-ok",
    )
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
