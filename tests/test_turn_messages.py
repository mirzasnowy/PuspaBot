"""Turn message format tests (G21): three API modes, one internal format.

No live provider, no network: every converter here is pure
format translation. Round-trips must preserve text and tool
calls; losses must be reported, never silent.
"""

from __future__ import annotations

import pytest

from puspa.turns.messages import (
    InternalMessage,
    enforce_alternation,
    from_anthropic,
    from_chat_completions,
    from_responses,
    to_anthropic,
    to_chat_completions,
    to_responses,
    validate_alternation,
)


def _sample_history() -> list[InternalMessage]:
    return [
        InternalMessage.system("You are Puspa, a helpful agent."),
        InternalMessage.user("What is the price of rice?"),
        InternalMessage.assistant(
            "",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "check_price",
                    "arguments": {"item": "rice"},
                }
            ],
        ),
        InternalMessage.tool_result("call-1", '{"price": 15000}'),
        InternalMessage.assistant("Rice costs Rp15.000 per kg."),
    ]


def test_chat_completions_round_trip():
    history = _sample_history()
    wire = to_chat_completions(history)
    assert wire.dropped == []
    back = from_chat_completions(wire.value)
    assert back.dropped == []
    assert [m.role for m in back.value] == [m.role for m in history]
    assert back.value[0].text == "You are Puspa, a helpful agent."
    assert back.value[1].text == "What is the price of rice?"
    assert back.value[2].tool_calls[0]["name"] == "check_price"
    assert back.value[2].tool_calls[0]["arguments"] == {"item": "rice"}
    assert back.value[3].tool_result_block["content"] == '{"price": 15000}'
    assert back.value[4].text == "Rice costs Rp15.000 per kg."


def test_chat_completions_tool_arguments_json_string():
    wire = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-9",
                    "type": "function",
                    "function": {"name": "todo", "arguments": '{"action": "list"}'},
                }
            ],
        }
    ]
    back = from_chat_completions(wire)
    assert back.value[0].tool_calls[0]["arguments"] == {"action": "list"}


def test_chat_completions_unparsable_tool_arguments_reported():
    wire = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-9",
                    "type": "function",
                    "function": {"name": "todo", "arguments": "{not json"},
                }
            ],
        }
    ]
    back = from_chat_completions(wire)
    assert back.value[0].tool_calls[0]["arguments"] == {}
    assert back.dropped, "unparsable arguments must be reported, not silent"


def test_anthropic_round_trip():
    history = _sample_history()
    anthropic = to_anthropic(history)
    assert anthropic.dropped == []
    assert anthropic.value["system"] == "You are Puspa, a helpful agent."
    back = from_anthropic(anthropic.value)
    assert [m.role for m in back.value] == [m.role for m in history]
    assert back.value[1].text == "What is the price of rice?"
    assert back.value[2].tool_calls[0]["name"] == "check_price"
    assert back.value[2].tool_calls[0]["arguments"] == {"item": "rice"}
    assert back.value[4].text == "Rice costs Rp15.000 per kg."


def test_anthropic_multiple_system_messages_merged_and_noted():
    history = [
        InternalMessage.system("First instruction."),
        InternalMessage.system("Second instruction."),
        InternalMessage.user("Hello"),
    ]
    anthropic = to_anthropic(history)
    assert "First instruction." in anthropic.value["system"]
    assert "Second instruction." in anthropic.value["system"]
    assert anthropic.notes, "the merge must be noted"


def test_responses_round_trip():
    history = _sample_history()
    responses = to_responses(history)
    assert responses.dropped == []
    assert responses.value["input"][0]["role"] == "system"
    back = from_responses(responses.value)
    assert [m.role for m in back.value] == [m.role for m in history]
    assert back.value[1].text == "What is the price of rice?"
    assert back.value[2].tool_calls[0]["name"] == "check_price"
    assert back.value[2].tool_calls[0]["arguments"] == {"item": "rice"}
    assert back.value[3].tool_result_block["content"] == '{"price": 15000}'
    assert back.value[4].text == "Rice costs Rp15.000 per kg."


def test_responses_output_items_round_trip():
    payload = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Checking the price."}],
            },
            {
                "type": "function_call",
                "call_id": "call-2",
                "name": "check_price",
                "arguments": '{"item": "rice"}',
            },
        ]
    }
    back = from_responses(payload)
    assert len(back.value) == 1
    assert back.value[0].role == "assistant"
    assert back.value[0].text == "Checking the price."
    assert back.value[0].tool_calls[0]["name"] == "check_price"


def test_responses_reasoning_items_dropped_and_reported():
    payload = {
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Done."}],
            },
        ]
    }
    back = from_responses(payload)
    assert len(back.value) == 1
    assert back.value[0].text == "Done."
    assert back.dropped, "reasoning items must be reported as dropped"


def test_responses_payload_without_items_rejected():
    with pytest.raises(ValueError):
        from_responses({"id": "resp-1"})


def test_validate_alternation_clean_history():
    assert validate_alternation(_sample_history()) == []


def test_validate_alternation_flags_double_user():
    history = [
        InternalMessage.user("First question."),
        InternalMessage.user("Second question."),
    ]
    violations = validate_alternation(history)
    assert violations, "two user messages in a row must be a violation"
    assert "two user messages" in violations[0]


def test_validate_alternation_flags_orphan_tool_result():
    history = [
        InternalMessage.user("Hello"),
        InternalMessage.tool_result("call-missing", "some result"),
    ]
    violations = validate_alternation(history)
    assert violations, "an orphan tool result must be a violation"


def test_validate_alternation_flags_late_system():
    history = [
        InternalMessage.user("Hello"),
        InternalMessage.system("Late instruction."),
    ]
    violations = validate_alternation(history)
    assert violations, "a system message after the start must be a violation"


def test_enforce_alternation_merges_double_user():
    history = [
        InternalMessage.user("First question."),
        InternalMessage.user("Second question."),
        InternalMessage.assistant("Answer."),
    ]
    fixed = enforce_alternation(history)
    assert [m.role for m in fixed.value] == ["user", "assistant"]
    assert "First question." in fixed.value[0].text
    assert "Second question." in fixed.value[0].text
    assert fixed.notes, "the merge must be reported"
    assert validate_alternation(fixed.value) == []


def test_enforce_alternation_merges_double_assistant_keeping_tool_calls():
    history = [
        InternalMessage.user("Do it."),
        InternalMessage.assistant("Working on it."),
        InternalMessage.assistant(
            "",
            tool_calls=[{"id": "call-1", "name": "todo", "arguments": {"action": "list"}}],
        ),
        InternalMessage.tool_result("call-1", "[]"),
    ]
    fixed = enforce_alternation(history)
    assert validate_alternation(fixed.value) == []
    assistant = fixed.value[1]
    assert assistant.text == "Working on it."
    assert assistant.tool_calls[0]["name"] == "todo"


def test_enforce_alternation_orphan_tool_result_becomes_user_message():
    history = [
        InternalMessage.user("Hello"),
        InternalMessage.tool_result("call-missing", "orphan content"),
    ]
    fixed = enforce_alternation(history)
    # Merged into the previous user message (a second user message
    # in a row would still violate alternation).
    assert [m.role for m in fixed.value] == ["user"]
    assert "Hello" in fixed.value[0].text
    assert "orphan content" in fixed.value[0].text
    assert fixed.notes, "the repair must be reported"
    assert validate_alternation(fixed.value) == []


def test_enforce_alternation_does_not_mutate_input():
    history = [
        InternalMessage.user("First."),
        InternalMessage.user("Second."),
    ]
    original = [message.to_dict() for message in history]
    enforce_alternation(history)
    assert [message.to_dict() for message in history] == original


def test_enforce_alternation_empty_history():
    fixed = enforce_alternation([])
    assert fixed.value == []


def test_internal_message_unknown_role_rejected():
    with pytest.raises(ValueError):
        InternalMessage(role="pirate", blocks=[])
