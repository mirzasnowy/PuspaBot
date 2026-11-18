"""Turn loop tests (G21): interrupt, budget, alternation, overflow, recovery.

No live provider, no network: every model call here is an
injected fake. The suite is NOT run by this worker (the central
suite is running); these tests are written for the coordinator
to run.
"""

from __future__ import annotations

import threading
import time

from puspa.runtime.tasks import Step, Task, TaskRunner
from puspa.turns.budget import IterationBudget
from puspa.turns.config import TurnConfig
from puspa.turns.loop import TurnLoop, make_task_executor
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply


def _text_reply(text: str = "Hello from the turn.") -> ModelReply:
    return ModelReply(text=text, input_tokens=10, output_tokens=5)


def _tool_reply(name: str, arguments: dict | None = None) -> ModelReply:
    return ModelReply(
        text="",
        tool_calls=[{"id": "call-1", "name": name, "arguments": arguments or {}}],
        input_tokens=10,
        output_tokens=5,
    )


# ---------------------------------------------------------------------------
# Interruption
# ---------------------------------------------------------------------------


def test_interrupt_mid_call_discards_reply_and_keeps_history_clean():
    entered = threading.Event()
    calls = []

    def slow_model_call(entry, wire_messages, tools):
        entered.set()
        calls.append(entry.model)
        time.sleep(5)  # still running when the interrupt lands
        return _text_reply("this reply must never be seen")

    loop = TurnLoop(model_call=slow_model_call, config=TurnConfig(call_timeout_seconds=30.0))
    history = [InternalMessage.user("Work on this for a while.")]
    outcome = {}

    def run():
        outcome["result"] = loop.run_turn(history)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(timeout=5), "the model call never started"
    loop.request_interrupt()
    thread.join(timeout=5)
    assert not thread.is_alive(), "run_turn did not return after the interrupt"
    result = outcome["result"]
    assert result.status == "interrupted"
    assert result.text == ""
    # The history is exactly what it was: no partial reply in it.
    assert result.messages == history
    assert "discarded" in result.message


def test_interrupt_before_first_iteration():
    def model_call(entry, wire_messages, tools):
        raise AssertionError("no model call may run after an interrupt")

    loop = TurnLoop(model_call=model_call)
    event = threading.Event()
    event.set()
    result = loop.run_turn([InternalMessage.user("Hello")], interrupt_event=event)
    assert result.status == "interrupted"
    assert result.messages == [InternalMessage.user("Hello")]


def test_timeout_ends_turn_failed_with_clean_history():
    def slow_model_call(entry, wire_messages, tools):
        time.sleep(5)
        return _text_reply()

    loop = TurnLoop(model_call=slow_model_call, config=TurnConfig(call_timeout_seconds=0.2))
    history = [InternalMessage.user("Hello")]
    result = loop.run_turn(history)
    assert result.status == "failed"
    assert "timed out" in result.message
    assert result.messages == history


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_iteration_budget_stops_tool_loop_with_clear_message():
    def looping_model_call(entry, wire_messages, tools):
        return _tool_reply("todo", {"action": "list"})

    loop = TurnLoop(model_call=looping_model_call, config=TurnConfig(max_iterations=3))
    result = loop.run_turn([InternalMessage.user("Keep working.")])
    assert result.status == "budget_exceeded"
    assert result.iterations_used == 3
    assert result.iterations_limit == 3
    assert "budget of 3" in result.message
    assert "no further model calls" in result.message


def test_budget_counts_iterations_used_on_success():
    calls = {"count": 0}

    def two_step_model_call(entry, wire_messages, tools):
        calls["count"] += 1
        if calls["count"] == 1:
            return _tool_reply("todo", {"action": "list"})
        return _text_reply("Done.")

    loop = TurnLoop(model_call=two_step_model_call)
    result = loop.run_turn([InternalMessage.user("List my todos.")])
    assert result.status == "completed"
    assert result.text == "Done."
    assert result.iterations_used == 2


def test_subagent_budget_is_capped_by_parent_remaining():
    def looping_model_call(entry, wire_messages, tools):
        return _tool_reply("todo", {"action": "list"})

    parent_budget = IterationBudget(max_iterations=10)
    parent_budget.consume(7)  # 3 remaining: the subagent cap is 3, not 50
    loop = TurnLoop(
        model_call=looping_model_call,
        config=TurnConfig(subagent_max_iterations=50),
    )
    result = loop.run_subagent_turn(
        [InternalMessage.user("Keep working.")], parent_budget=parent_budget
    )
    assert result.status == "budget_exceeded"
    assert result.iterations_used == 3
    assert result.iterations_limit == 3
    assert result.subagent_iterations_used == 3
    assert parent_budget.used == 10, "the parent pays for every child iteration"


# ---------------------------------------------------------------------------
# Alternation + overflow + recovery
# ---------------------------------------------------------------------------


def test_loop_repairs_double_user_message_before_calling():
    seen_wire = {}

    def capturing_model_call(entry, wire_messages, tools):
        seen_wire["messages"] = wire_messages
        return _text_reply("Merged and answered.")

    loop = TurnLoop(model_call=capturing_model_call)
    history = [
        InternalMessage.user("First question."),
        InternalMessage.user("Second question."),
    ]
    result = loop.run_turn(history)
    assert result.status == "completed"
    user_messages = [m for m in seen_wire["messages"] if m["role"] == "user"]
    assert len(user_messages) == 1, "the model must see one merged user message"
    assert "First question." in user_messages[0]["content"]
    assert "Second question." in user_messages[0]["content"]
    assert result.repairs, "the repair must be recorded on the result"


def test_loop_truncates_overflow_keeping_system_and_tail():
    def model_call(entry, wire_messages, tools):
        return _text_reply("Answered with a short history.")

    config = TurnConfig(max_history_messages=6, keep_last_messages=2)
    loop = TurnLoop(model_call=model_call, config=config)
    history = [InternalMessage.system("Be helpful.")]
    for index in range(5):
        history.append(InternalMessage.user(f"Question {index}."))
        history.append(InternalMessage.assistant(f"Answer {index}."))
    result = loop.run_turn(history)
    assert result.status == "completed"
    texts = [message.text for message in result.messages]
    assert texts[0] == "Be helpful.", "the system block must survive truncation"
    assert "Question 0." not in texts, "the oldest turns must be cut"
    assert "Answer 4." in texts, "the protected tail must survive"
    assert any("truncated" in note for note in result.notes)


def test_all_tiers_failing_ends_turn_failed_with_clean_history():
    from puspa.router.fallback import ProviderError

    def failing_model_call(entry, wire_messages, tools):
        raise ProviderError("provider is down", status=503)

    config = TurnConfig()
    loop = TurnLoop(model_call=failing_model_call, config=config)
    history = [InternalMessage.user("Hello")]
    result = loop.run_turn(history)
    assert result.status == "failed"
    assert "503" in result.message or "provider_error" in result.message
    assert result.messages == history, "no partial reply may enter the history"


# ---------------------------------------------------------------------------
# TaskRunner wiring (the split loop, not a second loop)
# ---------------------------------------------------------------------------


def test_make_task_executor_runs_turn_inside_task_runner():
    def model_call(entry, wire_messages, tools):
        return _text_reply("Turn reply via TaskRunner.")

    executor = make_task_executor(model_call=model_call)
    runner = TaskRunner(executor=executor)
    task = Task(
        task_id="task-turn-1",
        user_id="user-1",
        agent_id="puspa",
        purpose="Say hello to the user.",
        plan=[
            Step(
                name="respond",
                inputs={"type": "respond", "text": "Say hello."},
                expected_output={"type": "answer"},
            )
        ],
    )
    finished = runner.run(task)
    assert finished.status == "completed"
    assert finished.result["respond"]["text"] == "Turn reply via TaskRunner."


def test_make_task_executor_failure_notice_is_honest():
    from puspa.router.fallback import ProviderError

    def failing_model_call(entry, wire_messages, tools):
        raise ProviderError("provider is down", status=503)

    executor = make_task_executor(model_call=failing_model_call)
    runner = TaskRunner(executor=executor)
    task = Task(
        task_id="task-turn-2",
        user_id="user-1",
        agent_id="puspa",
        purpose="Say hello to the user.",
        plan=[
            Step(
                name="respond",
                inputs={"type": "respond", "text": "Say hello."},
                expected_output={"type": "answer"},
            )
        ],
    )
    finished = runner.run(task)
    assert finished.status == "completed"
    assert "could not answer" in finished.result["respond"]["text"]
