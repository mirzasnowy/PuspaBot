"""Intercepted tool tests (G21): todo, memory, session_search, delegate_task.

Interception happens in the turn loop, before the registry: a
tool the loop intercepts must NEVER reach the registry. Memory
and session search call the REAL stores (in-memory fact store;
SQLite session store on a temp home). The suite is NOT run by
this worker (the central suite is running); these tests are
written for the coordinator to run.
"""

from __future__ import annotations

import pytest

from puspa.memory.facts import MemoryFactStore
from puspa.runtime.subagents import SubagentPool
from puspa.sessions import open_persistence
from puspa.turns.intercepts import INTERCEPTED_TOOLS, InterceptedTools
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply


def _intercepted(**kwargs) -> InterceptedTools:
    return InterceptedTools(user_id="user-1", agent_id="puspa", **kwargs)


# ---------------------------------------------------------------------------
# todo (in-memory per-turn store)
# ---------------------------------------------------------------------------


def test_todo_add_list_complete_remove():
    tools = _intercepted()
    added = tools.try_intercept("todo", {"action": "add", "text": "Buy rice"}, task_id="t1")
    assert added["intercepted"] is True
    item_id = added["result"]["added"]["id"]
    listed = tools.try_intercept("todo", {"action": "list"}, task_id="t1")
    assert [item["text"] for item in listed["result"]["items"]] == ["Buy rice"]
    done = tools.try_intercept("todo", {"action": "complete", "item_id": item_id}, task_id="t1")
    assert done["result"]["completed"]["done"] is True
    open_only = tools.try_intercept("todo", {"action": "list", "open_only": True}, task_id="t1")
    assert open_only["result"]["items"] == []
    removed = tools.try_intercept("todo", {"action": "remove", "item_id": item_id}, task_id="t1")
    assert removed["result"]["removed"] == item_id


def test_todo_unknown_id_is_an_error_result_not_a_crash():
    tools = _intercepted()
    result = tools.try_intercept(
        "todo", {"action": "complete", "item_id": "todo-999"}, task_id="t1"
    )
    assert result["intercepted"] is True
    assert "unknown todo id" in result["error"]


def test_todo_lists_are_scoped_per_task():
    tools = _intercepted()
    tools.try_intercept("todo", {"action": "add", "text": "Task one item"}, task_id="t1")
    tools.try_intercept("todo", {"action": "add", "text": "Task two item"}, task_id="t2")
    listed = tools.try_intercept("todo", {"action": "list"}, task_id="t1")
    assert [item["text"] for item in listed["result"]["items"]] == ["Task one item"]


# ---------------------------------------------------------------------------
# memory (real fact store)
# ---------------------------------------------------------------------------


def test_memory_record_then_recall_real_store():
    tools = _intercepted(memory_store=MemoryFactStore())
    recorded = tools.try_intercept(
        "memory",
        {
            "action": "record",
            "key": "favorite_color",
            "content": "the user's favorite color is blue",
        },
        task_id="t1",
    )
    assert recorded["result"]["recorded"]["stored"] is True
    recalled = tools.try_intercept(
        "memory", {"action": "recall", "query": "favorite color is blue"}, task_id="t1"
    )
    keys = [entry["fact"]["key"] for entry in recalled["result"]["recalled"]]
    assert "favorite_color" in keys
    listed = tools.try_intercept("memory", {"action": "list"}, task_id="t1")
    assert [fact["key"] for fact in listed["result"]["facts"]] == ["favorite_color"]


def test_memory_record_supersedes_same_key():
    tools = _intercepted(memory_store=MemoryFactStore())
    tools.try_intercept(
        "memory",
        {"action": "record", "key": "city", "content": "the user lives in Bandung"},
        task_id="t1",
    )
    tools.try_intercept(
        "memory",
        {"action": "record", "key": "city", "content": "the user lives in Jakarta"},
        task_id="t1",
    )
    listed = tools.try_intercept("memory", {"action": "list"}, task_id="t1")
    active = [fact for fact in listed["result"]["facts"] if fact["status"] == "active"]
    assert len(active) == 1
    assert active[0]["content"] == "the user lives in Jakarta"


# ---------------------------------------------------------------------------
# session_search (real session store)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_session_search_real_store(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session(title="groceries")
        gateway.append_message(session["id"], "user", "buy rice and cooking oil")
        gateway.append_message(session["id"], "assistant", "noted, rice and oil")
        tools = _intercepted(session_gateway=gateway)
        result = tools.try_intercept("session_search", {"query": "rice"}, task_id="t1")
        assert result["intercepted"] is True
        assert result.get("error") is None
        assert len(result["result"]["matches"]) >= 1
        assert any("rice" in match["content"] for match in result["result"]["matches"])


def test_session_search_without_gateway_is_an_error_result():
    tools = _intercepted()
    result = tools.try_intercept("session_search", {"query": "rice"}, task_id="t1")
    assert result["intercepted"] is True
    assert "no session store wired" in result["error"]


# ---------------------------------------------------------------------------
# delegate_task
# ---------------------------------------------------------------------------


def test_delegate_task_via_callable():
    calls = []

    def delegate(*, purpose, permission_class, task_id):
        calls.append((purpose, permission_class))
        return {"subagent_id": "sub-abc", "status": "active"}

    tools = _intercepted(delegate=delegate)
    result = tools.try_intercept(
        "delegate_task",
        {"purpose": "Research rice prices", "permission_class": "Pembaca"},
        task_id="t1",
    )
    assert result["result"]["delegated"]["subagent_id"] == "sub-abc"
    assert calls == [("Research rice prices", "Pembaca")]


def test_delegate_task_via_real_pool(token_payloads):
    pool = SubagentPool()
    tools = _intercepted(
        subagent_pool=pool,
        agent_token=token_payloads["agents"],
        subagent_token=token_payloads["sub"],
    )
    result = tools.try_intercept(
        "delegate_task",
        {"purpose": "Research rice prices", "permission_class": "Pembaca"},
        task_id="task-delegate-1",
    )
    delegated = result["result"]["delegated"]
    assert delegated["subagent_id"].startswith("sub-")
    assert delegated["status"] == "active"
    assert pool.count_active() == 1


def test_delegate_task_without_wiring_is_an_error_result():
    tools = _intercepted()
    result = tools.try_intercept("delegate_task", {"purpose": "Research rice prices"}, task_id="t1")
    assert result["intercepted"] is True
    assert "no subagent pool wired" in result["error"]


# ---------------------------------------------------------------------------
# Interception happens before the registry
# ---------------------------------------------------------------------------


def test_intercepted_tools_never_reach_the_registry():
    assert set(INTERCEPTED_TOOLS) == {
        "todo",
        "memory",
        "session_search",
        "delegate_task",
        "check_before_buy",
    }
    registry_calls = []

    def registry(name, arguments):
        registry_calls.append(name)
        return {"registry": True}

    calls = {"count": 0}

    def model_call(entry, wire_messages, tools):
        calls["count"] += 1
        if calls["count"] == 1:
            return ModelReply(
                text="",
                tool_calls=[{"id": "call-1", "name": "todo", "arguments": {"action": "list"}}],
                input_tokens=10,
                output_tokens=5,
            )
        return ModelReply(text="No todos.", input_tokens=10, output_tokens=5)

    loop = TurnLoop(
        model_call=model_call,
        tool_call=registry,
        intercepts=_intercepted(),
    )
    result = loop.run_turn([InternalMessage.user("List my todos.")])
    assert result.status == "completed"
    assert registry_calls == [], "an intercepted tool must never reach the registry"
    tool_messages = [m for m in result.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    assert "intercepted" in tool_messages[0].text


def test_non_intercepted_tool_reaches_the_registry():
    registry_calls = []

    def registry(name, arguments):
        registry_calls.append((name, arguments))
        return {"price": 15000}

    def model_call(entry, wire_messages, tools):
        if not registry_calls:
            return ModelReply(
                text="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "check_price",
                        "arguments": {"item": "rice"},
                    }
                ],
                input_tokens=10,
                output_tokens=5,
            )
        return ModelReply(text="Rice costs Rp15.000.", input_tokens=10, output_tokens=5)

    loop = TurnLoop(model_call=model_call, tool_call=registry)
    result = loop.run_turn([InternalMessage.user("What is the price of rice?")])
    assert result.status == "completed"
    assert result.text == "Rice costs Rp15.000."
    assert registry_calls == [("check_price", {"item": "rice"})]


def test_unknown_tool_name_is_not_intercepted():
    tools = _intercepted()
    assert tools.try_intercept("check_price", {"item": "rice"}, task_id="t1") is None
