"""Hierarchical multi-agent runtime (google/adk-python pattern).

Covers: the agent tree (single parent, no cycles), delegation to
the right child by role through the existing handoff contract,
sequential / parallel / loop compositions, session state surviving
across turns with namespaced keys, tool confirmation blocking
unconfirmed impactful calls (in-memory and Gate-backed), memory
slices per node, callbacks, and the planner's hierarchical plans.
In-memory only; no Postgres needed.
"""

import time

import pytest

from puspa.policy import Gate
from puspa.runtime.handoff import HandoffError
from puspa.runtime.hierarchy import (
    AgentNode,
    CallbackRegistry,
    HierarchicalPlan,
    HierarchicalRunner,
    HierarchyError,
    InMemorySessionStore,
    MemoryAccess,
    SessionState,
    ToolConfirmation,
    ToolConfirmationError,
)
from puspa.runtime.planner import Planner, decompose_hierarchical
from puspa.runtime.subagents import SubagentPool
from puspa.runtime.tasks import Step, Task


def _step(name, permission_class="Pembaca"):
    return Step(
        name=name,
        inputs={"type": "request"},
        expected_output={"type": "result"},
        permission_class=permission_class,
        completion_condition=name,
    )


def _team():
    """Root plus researcher / checker / writer children."""
    root = AgentNode(
        name="puspa",
        role="coordinator",
        permission_class="Pelaksana",
        tools=("search_listings", "read_price", "verify_price", "present"),
    )
    researcher = AgentNode(
        name="researcher", role="researcher", tools=("search_listings", "read_price")
    )
    checker = AgentNode(
        name="checker", role="checker", permission_class="Pemeriksa", tools=("verify_price",)
    )
    writer = AgentNode(name="writer", role="writer", tools=("present",))
    root.add_child(researcher)
    root.add_child(checker)
    root.add_child(writer)
    return root, researcher, checker, writer


def _recording_executor(calls=None):
    def executor(node_name, step_name, inputs, state):
        if calls is not None:
            calls.append((node_name, step_name))
        return {"type": "result", "node": node_name, "step": step_name}

    return executor


# ----------------------------------------------------------------------
# Tree rules
# ----------------------------------------------------------------------


def test_single_parent_rule_rejects_second_parent():
    root, researcher, _, _ = _team()
    other = AgentNode(name="other", role="coordinator")
    with pytest.raises(HierarchyError):
        other.add_child(researcher)
    assert researcher.parent is root


def test_cycle_rejected_and_path_reported():
    root, researcher, _, _ = _team()
    with pytest.raises(HierarchyError):
        researcher.add_child(root)
    assert researcher.path() == ["puspa", "researcher"]
    assert root.find_role("checker").name == "checker"
    assert root.find_role("missing") is None


def test_invalid_node_rejected():
    with pytest.raises(HierarchyError):
        AgentNode(name="", role="researcher")
    with pytest.raises(HierarchyError):
        AgentNode(name="x", role="researcher", permission_class="Raja")


# ----------------------------------------------------------------------
# Delegation through the handoff contract
# ----------------------------------------------------------------------


def test_delegation_reaches_the_right_child_by_role():
    root, _, _, _ = _team()
    calls = []
    plan = HierarchicalPlan(
        steps=[_step("search_listings"), _step("verify_price", "Pemeriksa"), _step("present")],
        assignments={
            "search_listings": "researcher",
            "verify_price": "checker",
            "present": "writer",
        },
    )
    runner = HierarchicalRunner(root, executor=_recording_executor(calls))
    result = runner.run(plan, task_id="task-1")
    assert calls == [
        ("researcher", "search_listings"),
        ("checker", "verify_price"),
        ("writer", "present"),
    ]
    assert result.results["search_listings"]["node"] == "researcher"
    assert result.results["present"]["node"] == "writer"
    # Every delegation went through a handoff contract and passed.
    assert len(result.handoffs) == 3
    assert all(h.status == "passed" for h in result.handoffs)
    assert all(h.contract.from_id == "puspa" for h in result.handoffs)
    assert [h.contract.to_id for h in result.handoffs] == ["researcher", "checker", "writer"]
    types = [e["type"] for e in runner.events]
    assert types.count("handoff_proposed") == 3
    assert types.count("handoff_handed_over") == 3


def test_unassigned_step_runs_on_root_and_child_output_key_written_to_state():
    root, _, _, _ = _team()
    calls = []
    plan = HierarchicalPlan(
        steps=[_step("search_listings"), _step("gather")],
        assignments={"search_listings": "researcher"},
    )
    state = SessionState(session_id="s-1", user_id="user-1")
    HierarchicalRunner(root, executor=_recording_executor(calls)).run(plan, state=state)
    assert calls == [("researcher", "search_listings"), ("puspa", "gather")]
    assert state.get("researcher")["step"] == "search_listings"
    assert state.get("gather")["step"] == "gather"


def test_schema_mismatch_result_is_rejected_not_accepted():
    root, _, _, _ = _team()

    def bad_executor(node_name, step_name, inputs, state):
        return {"no_type": True}

    plan = HierarchicalPlan(
        steps=[_step("search_listings")],
        assignments={"search_listings": "researcher"},
    )
    runner = HierarchicalRunner(root, executor=bad_executor)
    with pytest.raises(HierarchyError):
        runner.run(plan, task_id="task-1")


def test_handoff_limit_between_agents_is_enforced():
    root, _, _, _ = _team()
    plan = HierarchicalPlan(
        steps=[_step(f"search_listings_{i}") for i in range(4)],
        assignments={f"search_listings_{i}": "researcher" for i in range(4)},
    )
    runner = HierarchicalRunner(root, executor=_recording_executor())
    with pytest.raises(HandoffError):
        runner.run(plan, task_id="task-1")


def test_pool_spawn_records_role_and_caps_still_apply():
    root, _, _, _ = _team()
    pool = SubagentPool()
    state = SessionState(session_id="s-1", user_id="user-1")
    plan = HierarchicalPlan(
        steps=[_step("search_listings"), _step("verify_price", "Pemeriksa")],
        assignments={"search_listings": "researcher", "verify_price": "checker"},
    )
    runner = HierarchicalRunner(root, executor=_recording_executor(), pool=pool)
    runner.run(plan, state=state, task_id="task-1")
    assert [s.role for s in pool.active_by_role("puspa", "researcher")] == ["researcher"]
    assert pool.count_active() == 2


# ----------------------------------------------------------------------
# Compositions
# ----------------------------------------------------------------------


def test_sequential_runs_children_in_plan_order():
    root, _, _, _ = _team()
    calls = []
    plan = HierarchicalPlan(
        steps=[_step("search_listings"), _step("verify_price", "Pemeriksa")],
        assignments={"search_listings": "researcher", "verify_price": "checker"},
        composition="sequential",
    )
    result = HierarchicalRunner(root, executor=_recording_executor(calls)).run(plan, task_id="t")
    assert [name for _, name in calls] == ["search_listings", "verify_price"]
    assert result.iterations == 1


def test_parallel_merges_all_child_results():
    root, _, _, _ = _team()
    calls = []
    plan = HierarchicalPlan(
        steps=[_step("search_listings"), _step("verify_price", "Pemeriksa"), _step("present")],
        assignments={
            "search_listings": "researcher",
            "verify_price": "checker",
            "present": "writer",
        },
        composition="parallel",
    )
    result = HierarchicalRunner(root, executor=_recording_executor(calls)).run(plan, task_id="t")
    assert set(result.results) == {"search_listings", "verify_price", "present"}
    assert result.results["verify_price"]["node"] == "checker"


def test_loop_repeats_until_exit_condition_then_stops():
    root, _, _, _ = _team()
    seen = {"n": 0}

    def executor(node_name, step_name, inputs, state):
        seen["n"] += 1
        ready = seen["n"] >= 2
        return {"type": "result", "ready": ready}

    plan = HierarchicalPlan(
        steps=[_step("draft")],
        assignments={"draft": "writer"},
        composition="loop",
        max_iterations=3,
        exit_condition=lambda state: bool(state.get("draft", {}).get("ready")),
    )
    result = HierarchicalRunner(root, executor=executor).run(plan, task_id="t")
    assert result.iterations == 2
    assert result.results["draft"]["ready"] is True


def test_loop_stops_at_max_iterations_without_exit():
    root, _, _, _ = _team()
    plan = HierarchicalPlan(
        steps=[_step("draft")],
        assignments={"draft": "writer"},
        composition="loop",
        max_iterations=2,
        exit_condition=lambda state: False,
    )
    result = HierarchicalRunner(root, executor=_recording_executor()).run(plan, task_id="t")
    assert result.iterations == 2


def test_unknown_composition_rejected():
    with pytest.raises(HierarchyError):
        HierarchicalPlan(steps=[_step("gather")], composition="chaos")


# ----------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------


def test_state_scopes_and_temp_keys_never_saved():
    store = InMemorySessionStore()
    state = SessionState(session_id="s-1", user_id="user-1", store=store)
    state.set("last_query", "laptop")
    state.set("user:name", "Nero")
    state.set("app:theme", "dark")
    state.set("temp:draft", "scratch")
    state.save()
    restored = SessionState.load("s-1", user_id="user-1", store=store)
    assert restored.get("last_query") == "laptop"
    assert restored.get("user:name") == "Nero"
    assert restored.get("app:theme") == "dark"
    assert restored.get("temp:draft") is None
    assert restored.keys() == ["last_query"]
    assert restored.keys("user:") == ["user:name"]


def test_state_survives_across_turns_through_the_store():
    store = InMemorySessionStore()
    root, _, _, _ = _team()

    def first_turn(node_name, step_name, inputs, state):
        state.set("user:preference", "tokopedia")
        return {"type": "result"}

    plan = HierarchicalPlan(steps=[_step("gather")])
    runner = HierarchicalRunner(root, executor=first_turn)
    state = SessionState(session_id="s-2", user_id="user-1", store=store)
    runner.run(plan, state=state, task_id="t-1")

    second = SessionState.load("s-2", user_id="user-1", store=store)
    assert second.get("user:preference") == "tokopedia"

    def second_turn(node_name, step_name, inputs, state):
        assert state.get("user:preference") == "tokopedia"
        return {"type": "result"}

    runner2 = HierarchicalRunner(root, executor=second_turn)
    runner2.run(plan, state=second, task_id="t-2")


def test_state_without_store_still_works_in_memory():
    state = SessionState(session_id="s-3")
    state.set("k", 1)
    state.save()  # no store: no-op, never crashes
    assert SessionState.load("s-3").get("k") is None


# ----------------------------------------------------------------------
# Tool confirmation
# ----------------------------------------------------------------------


def test_impactful_tool_blocked_without_confirmation_in_memory():
    confirmation = ToolConfirmation(user_id="user-1", agent_id="puspa")
    ran = []

    def executor(tool, args):
        ran.append(tool)
        return {"type": "result", "sent": True}

    with pytest.raises(ToolConfirmationError):
        confirmation.run("gmail_send", {"to": "a@b.c"}, executor)
    assert ran == []


def test_impactful_tool_runs_after_confirmation_in_memory():
    confirmation = ToolConfirmation(user_id="user-1", agent_id="puspa")
    confirmation_id = confirmation.request("gmail_send", {"to": "a@b.c"})
    assert not confirmation.is_confirmed(confirmation_id)
    confirmation.confirm(confirmation_id, "user-1")
    assert confirmation.is_confirmed(confirmation_id)
    result = confirmation.run(
        "gmail_send",
        {"to": "a@b.c"},
        lambda tool, args: {"type": "result", "sent": True},
        confirmation_id=confirmation_id,
    )
    assert result["sent"] is True


def test_readonly_tool_needs_no_confirmation():
    confirmation = ToolConfirmation(user_id="user-1", agent_id="puspa")
    result = confirmation.run("search_listings", {}, lambda tool, args: {"type": "result"})
    assert result["type"] == "result"


def test_unknown_confirmation_cannot_be_confirmed():
    confirmation = ToolConfirmation(user_id="user-1", agent_id="puspa")
    with pytest.raises(ToolConfirmationError):
        confirmation.confirm("confirm-nope", "user-1")


def _gate_payload():
    now = int(time.time())
    return {
        "user_id": "user-1",
        "agent_id": "puspa",
        "subagent_id": None,
        "tools": ["send", "gmail.kirim", "search_listings"],
        "domains": [],
        "connectors": {"gmail": ["read", "send"]},
        "budget": {"step_max": 25, "token_max": 100000, "rupiah_max": 0},
        "exp": now + 3600,
        "iat": now,
        "parent": None,
    }


def test_gate_backed_confirmation_blocks_then_runs_exactly_once():
    gate = Gate(log=lambda event_type, content: None, spending=None)
    confirmation = ToolConfirmation(
        user_id="user-1", agent_id="puspa", token=_gate_payload(), gate=gate
    )
    ran = []

    def executor(tool, args):
        ran.append(tool)
        return {"type": "result", "sent": True}

    confirmation_id = confirmation.request("gmail_send", {"to": "a@b.c"})
    with pytest.raises(ToolConfirmationError):
        confirmation.run("gmail_send", {"to": "a@b.c"}, executor, confirmation_id=confirmation_id)
    assert ran == []
    confirmation.confirm(confirmation_id, "user-1")
    first = confirmation.run(
        "gmail_send", {"to": "a@b.c"}, executor, confirmation_id=confirmation_id
    )
    second = confirmation.run(
        "gmail_send", {"to": "a@b.c"}, executor, confirmation_id=confirmation_id
    )
    assert first["sent"] is True and second["sent"] is True
    assert ran == ["gmail_send"]  # Gate executes exactly once


def test_runner_blocks_impactful_step_without_confirmation():
    root, _, _, _ = _team()
    confirmation = ToolConfirmation(user_id="user-1", agent_id="puspa")
    plan = HierarchicalPlan(
        steps=[_step("prepare_payment", "Pelaksana")],
        assignments={"prepare_payment": "executor"},
    )
    # No "executor" child exists, so the root runs it; the tool
    # resolver maps the step to the impactful payment tool.
    runner = HierarchicalRunner(
        root,
        executor=_recording_executor(),
        confirmation=confirmation,
        tool_resolver=lambda name: "prepare_payment",
    )
    with pytest.raises(ToolConfirmationError):
        runner.run(plan, task_id="t")


# ----------------------------------------------------------------------
# Memory access through slice grants
# ----------------------------------------------------------------------


def test_memory_slice_uses_grant_and_needs_token_ref():
    _root, researcher, _, _ = _team()
    seen = {}

    def fake_grant(user_id, agent_id, subagent_id, query, columns, token_ref):
        seen.update(
            user_id=user_id,
            agent_id=agent_id,
            subagent_id=subagent_id,
            query=query,
            columns=columns,
            token_ref=token_ref,
        )
        return {"profile": {"name": "Nero"}, "episodic": []}

    memory = MemoryAccess("user-1", "puspa", grant=fake_grant)
    data = memory.read(researcher, "laptop", ("profile",), token_ref="tok-1")
    assert data["profile"]["name"] == "Nero"
    assert seen == {
        "user_id": "user-1",
        "agent_id": "puspa",
        "subagent_id": "researcher",
        "query": "laptop",
        "columns": ("profile",),
        "token_ref": "tok-1",
    }
    with pytest.raises(HierarchyError):
        memory.read(researcher, "laptop", token_ref="")


# ----------------------------------------------------------------------
# Callbacks
# ----------------------------------------------------------------------


def test_before_tool_callback_can_block_a_step():
    root, _, _, _ = _team()
    callbacks = CallbackRegistry()
    callbacks.before_tool.append(lambda node, tool, args: tool != "verify_price")
    plan = HierarchicalPlan(
        steps=[_step("search_listings"), _step("verify_price", "Pemeriksa")],
        assignments={"search_listings": "researcher", "verify_price": "checker"},
    )
    result = HierarchicalRunner(root, executor=_recording_executor(), callbacks=callbacks).run(
        plan, task_id="t"
    )
    assert result.blocked == ["verify_price"]
    assert result.results["verify_price"]["blocked"] is True
    assert result.results["search_listings"]["node"] == "researcher"


def test_after_tool_callback_can_replace_a_result():
    root, _, _, _ = _team()
    callbacks = CallbackRegistry()
    callbacks.after_tool.append(lambda node, tool, args, result: {"type": "result", "fixed": True})
    plan = HierarchicalPlan(
        steps=[_step("search_listings")], assignments={"search_listings": "researcher"}
    )
    result = HierarchicalRunner(root, executor=_recording_executor(), callbacks=callbacks).run(
        plan, task_id="t"
    )
    assert result.results["search_listings"] == {"type": "result", "fixed": True}


def test_model_callbacks_run_around_execution_without_breaking_it():
    root, _, _, _ = _team()
    seen = []
    callbacks = CallbackRegistry()
    callbacks.before_model.append(lambda node, prompt: seen.append(("before", node)))
    callbacks.after_model.append(lambda node, response: seen.append(("after", node)))
    runner = HierarchicalRunner(root, executor=_recording_executor(), callbacks=callbacks)
    runner.callbacks.run_before_model("puspa", "hello")
    runner.callbacks.run_after_model("puspa", "hi")
    assert seen == [("before", "puspa"), ("after", "puspa")]


# ----------------------------------------------------------------------
# Planner integration (old behavior preserved)
# ----------------------------------------------------------------------


def test_decompose_hierarchical_assigns_roles_by_step():
    plan = decompose_hierarchical("compare laptop prices across stores")
    assert plan.composition == "sequential"
    assert plan.assignments["search_listings"] == "researcher"
    assert plan.assignments["read_price_tokopedia"] == "researcher"
    assert plan.assignments["verify_price"] == "checker"
    assert plan.assignments["present_comparison"] == "writer"
    assert [s.name for s in plan.steps] == [
        "search_listings",
        "read_price_tokopedia",
        "read_price_shopee",
        "read_price_blibli",
        "verify_price",
        "present_comparison",
    ]


def test_planner_run_hierarchical_delegates_and_old_run_still_works():
    root, _, _, _ = _team()
    calls = []

    def executor(name, inputs):
        calls.append(name)
        return {"type": "result"}

    planner = Planner(executor=executor)
    task = Task(
        task_id="task-1",
        user_id="user-1",
        agent_id="puspa",
        purpose="research and summarize 5 articles",
    )
    result = planner.run_hierarchical(task, root)
    assert task.status == "completed"
    assert set(result.results) == {"search_artikel", "read_artikel", "summarize"}
    assert len(result.handoffs) == 3  # one handoff per delegated step
    # Flat path untouched: same planner still runs a flat plan.
    calls.clear()
    flat = Task(
        task_id="task-2",
        user_id="user-1",
        agent_id="puspa",
        purpose="compare laptop prices across stores",
    )
    flat_result = planner.run(flat)
    assert flat_result.status == "completed"
    assert calls[0] == "search_listings"


def test_run_task_folds_results_into_task():
    root, _, _, _ = _team()
    plan = HierarchicalPlan(
        steps=[_step("search_listings")], assignments={"search_listings": "researcher"}
    )
    runner = HierarchicalRunner(root, executor=_recording_executor())
    task = Task(
        task_id="task-9",
        user_id="user-1",
        agent_id="puspa",
        purpose="compare laptop prices across stores",
    )
    task, result = runner.run_task(task, plan)
    assert task.status == "completed"
    assert task.result["search_listings"]["node"] == "researcher"
    assert len(result.handoffs) == 1
