"""Tests for subagent capability handles (G25).

In-memory only: run with ``pytest tests/test_subagent_handles.py
--noconftest``.
"""

import base64
import json

import pytest

from puspa.subagents import (
    SubagentHandle,
    SubagentLifecycle,
    UnknownHandleError,
    mint_handle,
)
from puspa.tools.registry import ToolRegistry
from puspa.tools.toolsets import default_toolsets


def make_lifecycle(**kwargs):
    registry = ToolRegistry()
    registry.register("plugin_echo", "Echo.", {}, lambda: "echo")
    return SubagentLifecycle(registry=registry, toolsets=default_toolsets(), **kwargs)


def test_handle_round_trip_and_repr_hides_token():
    handle = mint_handle(subagent_id="sub-abc", generation="gen-1")
    parsed = SubagentHandle.parse(handle.serialize())
    assert parsed == handle
    assert "tok" not in repr(handle)
    assert handle.token not in repr(handle)
    assert parsed.token_hash() == handle.token_hash()


def test_malformed_handles_are_unknown():
    for raw in ("", "not-base64!!!", "aGVsbG8=", "e30=", "WzFd"):
        with pytest.raises(UnknownHandleError) as exc_info:
            SubagentHandle.parse(raw)
        assert exc_info.value.code == "UNKNOWN_HANDLE"


def test_wrong_version_handle_is_unknown():
    payload = {"v": 99, "id": "sub-abc", "gen": "gen-1", "tok": "secret"}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(UnknownHandleError):
        SubagentHandle.parse(raw)


def test_forged_handle_cannot_reach_a_child():
    lifecycle = make_lifecycle()
    real = lifecycle.launch(
        user_id="user-1",
        agent_id="agent-1",
        task_id="task-1",
        goal="work",
        child_fn=lambda ctx: "ok",
    )
    assert lifecycle.wait(real, timeout_s=5)
    forged = mint_handle(subagent_id="sub-deadbeef0000", generation=lifecycle.generation)
    with pytest.raises(UnknownHandleError):
        lifecycle.status(forged.serialize())
    with pytest.raises(UnknownHandleError):
        lifecycle.status("sub-000000000000")


def test_right_id_wrong_token_is_unknown():
    lifecycle = make_lifecycle()
    real = lifecycle.launch(
        user_id="user-1",
        agent_id="agent-1",
        task_id="task-1",
        goal="work",
        child_fn=lambda ctx: "ok",
    )
    parsed = SubagentHandle.parse(real.serialize())
    impostor = SubagentHandle(
        subagent_id=parsed.subagent_id,
        generation=parsed.generation,
        token="a-different-token",
    )
    with pytest.raises(UnknownHandleError):
        lifecycle.status(impostor.serialize())
    assert lifecycle.wait(real, timeout_s=5)


def test_handle_object_and_serialized_form_are_both_accepted():
    lifecycle = make_lifecycle()
    handle = lifecycle.launch(
        user_id="user-1",
        agent_id="agent-1",
        task_id="task-1",
        goal="work",
        child_fn=lambda ctx: "ok",
    )
    assert (
        lifecycle.status(handle)["subagent_id"]
        == lifecycle.status(handle.serialize())["subagent_id"]
    )
    assert lifecycle.wait(handle, timeout_s=5)
