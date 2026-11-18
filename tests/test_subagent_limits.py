"""Tests for the lifecycle fail-closed limits (G25).

In-memory only: run with ``pytest tests/test_subagent_limits.py
--noconftest``.
"""

import pytest

from puspa.subagents import (
    LimitExceededError,
    SubagentLifecycle,
    ToolsetRejectedError,
    UnsupportedOptionError,
)
from puspa.tools.registry import ToolRegistry
from puspa.tools.toolsets import default_toolsets


def make_lifecycle(**kwargs):
    registry = ToolRegistry()
    registry.register("plugin_echo", "Echo.", {}, lambda: "echo")
    registry.register("run_code", "Run code.", {}, lambda: "ok")
    return SubagentLifecycle(registry=registry, toolsets=default_toolsets(), **kwargs)


def launch(lifecycle, **kwargs):
    kwargs.setdefault("user_id", "user-1")
    kwargs.setdefault("agent_id", "agent-1")
    kwargs.setdefault("task_id", "task-1")
    kwargs.setdefault("goal", "work")
    kwargs.setdefault("child_fn", lambda ctx: "ok")
    return lifecycle.launch(**kwargs)


class CapturingRunner:
    def __init__(self):
        self.bodies = []

    def __call__(self, record, run_child):
        self.bodies.append(run_child)


def test_oversized_goal_is_refused():
    lifecycle = make_lifecycle()
    with pytest.raises(LimitExceededError, match="goal"):
        launch(lifecycle, goal="g" * 8001)


def test_oversized_context_is_refused():
    lifecycle = make_lifecycle()
    with pytest.raises(LimitExceededError, match="context"):
        launch(lifecycle, context="c" * 32769)


def test_empty_goal_is_refused():
    lifecycle = make_lifecycle()
    with pytest.raises(LimitExceededError, match="goal"):
        launch(lifecycle, goal="")


def test_oversized_metadata_is_refused():
    lifecycle = make_lifecycle()
    with pytest.raises(LimitExceededError, match="metadata"):
        launch(lifecycle, metadata={"blob": "x" * 5000})
    with pytest.raises(LimitExceededError, match="metadata"):
        launch(lifecycle, metadata={f"key-{i}": i for i in range(33)})


def test_working_directory_override_is_refused():
    lifecycle = make_lifecycle()
    with pytest.raises(UnsupportedOptionError, match="working-directory"):
        launch(lifecycle, working_dir="/tmp/elsewhere")


def test_per_tool_timeout_is_refused():
    lifecycle = make_lifecycle()
    with pytest.raises(UnsupportedOptionError, match="per-tool timeout"):
        launch(lifecycle, tool_timeout_s=5)


def test_unknown_toolsets_are_refused():
    lifecycle = make_lifecycle()
    with pytest.raises(ToolsetRejectedError, match="unknown toolset"):
        launch(lifecycle, toolset="nope")
    with pytest.raises(ToolsetRejectedError, match="unknown toolset"):
        launch(lifecycle, parent_toolset="nope")


def test_toolset_naming_an_unregistered_tool_is_refused():
    registry = ToolRegistry()
    registry.register("plugin_echo", "Echo.", {}, lambda: "echo")
    toolsets = default_toolsets()
    toolsets.register_custom("tenant_wide", {"plugin_echo", "ghost_tool"})
    lifecycle = SubagentLifecycle(registry=registry, toolsets=toolsets)
    with pytest.raises(ToolsetRejectedError, match="unregistered tools"):
        launch(lifecycle, toolset="tenant_wide", parent_toolset="tenant_wide")


def test_oversized_steer_text_is_refused():
    lifecycle = make_lifecycle(runner=CapturingRunner())
    handle = launch(lifecycle)
    with pytest.raises(LimitExceededError, match="steered text"):
        lifecycle.steer(handle, "s" * 4001)
    with pytest.raises(LimitExceededError, match="steered text"):
        lifecycle.steer(handle, "")


def test_full_steer_queue_is_refused_not_silently_dropped():
    lifecycle = make_lifecycle(runner=CapturingRunner())
    handle = launch(lifecycle)
    for i in range(16):
        assert lifecycle.steer(handle, f"note {i}") == i + 1
    with pytest.raises(LimitExceededError, match="queue is full"):
        lifecycle.steer(handle, "one too many")
    assert lifecycle.status(handle)["steer_queued"] == 16
