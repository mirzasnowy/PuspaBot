"""Tests for the host finish path: hooks, memory, cleanup, cost (G25).

In-memory only: run with ``pytest tests/test_subagent_host.py
--noconftest``.
"""

import threading

from puspa.runtime.budget import BudgetTracker
from puspa.runtime.subagents import SubagentPool
from puspa.subagents import SubagentLifecycle, SubagentState
from puspa.subagents.toolsets import resolved_tools
from puspa.tools.registry import ToolRegistry
from puspa.tools.toolsets import default_toolsets


def make_tooling():
    registry = ToolRegistry()
    registry.register("plugin_echo", "Echo.", {}, lambda: "echo")
    registry.register("run_code", "Run code.", {}, lambda: "ok")
    return registry, default_toolsets()


def make_lifecycle(**kwargs):
    registry, toolsets = make_tooling()
    return SubagentLifecycle(registry=registry, toolsets=toolsets, **kwargs)


def finished_report(lifecycle, handle):
    for event in lifecycle.event_log():
        if event["type"] == "subagent_finished":
            return event["content"]
    raise AssertionError("no subagent_finished event")


def launch(lifecycle, child_fn, **kwargs):
    kwargs.setdefault("user_id", "user-1")
    kwargs.setdefault("agent_id", "agent-1")
    kwargs.setdefault("task_id", "task-1")
    kwargs.setdefault("goal", "work")
    kwargs.setdefault("child_fn", child_fn)
    return lifecycle.launch(**kwargs)


def test_stop_hooks_run_serially_in_order_and_isolate_failures():
    calls = []

    def first(event, payload):
        calls.append("first")

    def broken(event, payload):
        calls.append("broken")
        raise RuntimeError("hook blew up")

    def last(event, payload):
        calls.append("last")

    lifecycle = make_lifecycle(stop_hooks=[first, broken, last])
    handle = launch(lifecycle, lambda ctx: "ok")
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    assert calls == ["first", "broken", "last"]
    report = finished_report(lifecycle, handle)
    assert [entry["hook"] for entry in report["stop_hooks"]] == ["first", "broken", "last"]
    assert [entry["ok"] for entry in report["stop_hooks"]] == [True, False, True]
    assert "hook blew up" in report["stop_hooks"][1]["error"]


def test_memory_notify_and_pool_cleanup_and_parent_tools_restored():
    notified = []
    pool = SubagentPool()
    registry, toolsets = make_tooling()
    parent_before = resolved_tools("default", toolsets=toolsets)
    lifecycle = SubagentLifecycle(
        pool=pool,
        registry=registry,
        toolsets=toolsets,
        memory_notify=notified.append,
    )
    release = threading.Event()

    def child(ctx):
        release.wait(timeout=10)
        return "ok"

    handle = launch(lifecycle, child)
    assert pool.count_active() == 1
    release.set()
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    # Pool entry cleaned up, memory notified, parent tools unchanged.
    assert pool.count_active() == 0
    assert len(notified) == 1
    assert notified[0]["subagent_id"] == lifecycle.status(handle)["subagent_id"]
    assert notified[0]["state"] == SubagentState.SUCCEEDED.value
    assert notified[0]["result_hash"] == lifecycle.result(handle).result_hash
    assert resolved_tools("default", toolsets=toolsets) == parent_before
    report = finished_report(lifecycle, handle)
    assert report["memory_notified"] is True
    assert report["pool_completed"] is True
    assert report["parent_tools_restored"] is True


def test_child_cost_rolls_up_into_the_parent_budget():
    budget = BudgetTracker(rupiah_max=2000)
    registry, toolsets = make_tooling()
    lifecycle = SubagentLifecycle(registry=registry, toolsets=toolsets, budget=budget)

    def child(ctx):
        ctx.record_cost(150)
        return "ok"

    handle = launch(lifecycle, child)
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    assert lifecycle.status(handle)["cost_rp"] == 150
    assert budget.rupiah_used == 150
    report = finished_report(lifecycle, handle)
    assert report["cost_rollup_rp"] == 150
    assert report["cost_rollup_error"] == ""


def test_cost_rollup_failure_is_recorded_not_fatal():
    budget = BudgetTracker(rupiah_max=100)
    registry, toolsets = make_tooling()
    lifecycle = SubagentLifecycle(registry=registry, toolsets=toolsets, budget=budget)

    def child(ctx):
        ctx.record_cost(150)
        return "ok"

    handle = launch(lifecycle, child)
    # The child itself succeeds; the parent budget cannot absorb
    # its cost, so the rollup failure is recorded on the report.
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    report = finished_report(lifecycle, handle)
    assert report["cost_rollup_rp"] == 0
    assert "plafon" in report["cost_rollup_error"]
    assert budget.rupiah_used == 0


def test_failing_memory_notify_is_recorded_not_fatal():
    def broken_notify(payload):
        raise RuntimeError("memory is down")

    registry, toolsets = make_tooling()
    lifecycle = SubagentLifecycle(registry=registry, toolsets=toolsets, memory_notify=broken_notify)
    handle = launch(lifecycle, lambda ctx: "ok")
    assert lifecycle.wait(handle, timeout_s=5) is SubagentState.SUCCEEDED
    report = finished_report(lifecycle, handle)
    assert report["memory_notified"] is False
    assert "memory is down" in report["memory_error"]
