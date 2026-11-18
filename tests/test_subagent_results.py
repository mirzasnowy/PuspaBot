"""Tests for immutable, idempotent, capped child results (G25).

In-memory only: run with ``pytest tests/test_subagent_results.py
--noconftest``.
"""

import threading
from dataclasses import FrozenInstanceError

import pytest

from puspa.subagents import (
    NotTerminalError,
    SubagentLifecycle,
    SubagentState,
    build_result,
    result_hash,
)
from puspa.tools.registry import ToolRegistry
from puspa.tools.toolsets import default_toolsets


def make_lifecycle(**kwargs):
    registry = ToolRegistry()
    registry.register("plugin_echo", "Echo.", {}, lambda: "echo")
    return SubagentLifecycle(registry=registry, toolsets=default_toolsets(), **kwargs)


def launch(lifecycle, child_fn, **kwargs):
    kwargs.setdefault("user_id", "user-1")
    kwargs.setdefault("agent_id", "agent-1")
    kwargs.setdefault("task_id", "task-1")
    kwargs.setdefault("goal", "work")
    kwargs.setdefault("child_fn", child_fn)
    return lifecycle.launch(**kwargs)


def test_result_is_idempotent_with_a_stable_hash():
    lifecycle = make_lifecycle()
    handle = launch(lifecycle, lambda ctx: "same answer")
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    first = lifecycle.result(handle)
    second = lifecycle.result(handle)
    third = lifecycle.result(handle)
    assert first == second == third
    assert first.result_hash == result_hash("same answer")
    assert first.result_hash == second.result_hash == third.result_hash
    assert not first.truncated


def test_result_is_capped_at_32k_with_stable_hash_of_stored_text():
    lifecycle = make_lifecycle()
    long_text = "x" * 40000
    handle = launch(lifecycle, lambda ctx: long_text)
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    result = lifecycle.result(handle)
    assert result.truncated
    assert len(result.text) == 32768
    assert result.text == long_text[:32768]
    assert result.result_hash == result_hash(result.text)
    # A second call returns the same capped text and hash.
    assert lifecycle.result(handle) == result


def test_result_carries_no_transcript_or_reasoning():
    lifecycle = make_lifecycle()
    handle = launch(lifecycle, lambda ctx: "final only")
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    result = lifecycle.result(handle)
    fields = {field.name for field in result.__dataclass_fields__.values()}
    assert fields == {"state", "text", "result_hash", "truncated", "error"}
    assert "transcript" not in fields
    assert "reasoning" not in fields


def test_result_is_frozen():
    result = build_result(state=SubagentState.SUCCEEDED, text="done")
    with pytest.raises(FrozenInstanceError):
        result.text = "rewritten"


def test_failed_child_result_carries_the_error_not_output():
    lifecycle = make_lifecycle()

    def child(ctx):
        raise RuntimeError("boom in the child")

    handle = launch(lifecycle, child)
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.FAILED
    result = lifecycle.result(handle)
    assert result.state is SubagentState.FAILED
    assert result.text == ""
    assert "boom in the child" in result.error


def test_result_before_terminal_is_refused():
    release = threading.Event()
    lifecycle = make_lifecycle()

    def child(ctx):
        release.wait(timeout=10)
        return "late"

    handle = launch(lifecycle, child)
    with pytest.raises(NotTerminalError) as exc_info:
        lifecycle.result(handle)
    assert exc_info.value.code == "NOT_TERMINAL"
    release.set()
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    assert lifecycle.result(handle).text == "late"


def test_non_string_child_output_is_a_failure_not_a_result():
    lifecycle = make_lifecycle()
    handle = launch(lifecycle, lambda ctx: 42)
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.FAILED
    assert "string" in lifecycle.result(handle).error
