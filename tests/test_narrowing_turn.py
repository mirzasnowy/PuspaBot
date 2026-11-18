"""Turn-loop wiring for G17 narrowing (a real run goes through it).

In-memory only: run with ``pytest tests/test_narrowing_turn.py --noconftest -q``.
No live provider, no network: every model call here is an injected
fake, and tool dispatch goes through an in-memory registry.
"""

import json

import pytest

from puspa.tools.narrowing import (
    SEARCH_TOOLS_NAME,
    TOOL_LIMIT_REFUSED_EVENT,
    Skill,
    prepare_run_tools,
    register_search_tools,
)
from puspa.tools.registry import ToolRegistry
from puspa.turns.config import TurnConfig
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply


def _text_reply(text: str = "Done.") -> ModelReply:
    return ModelReply(text=text, input_tokens=10, output_tokens=5)


def _tool_reply(name: str, arguments: dict | None = None) -> ModelReply:
    return ModelReply(
        text="",
        tool_calls=[{"id": "call-1", "name": name, "arguments": arguments or {}}],
        input_tokens=10,
        output_tokens=5,
    )


def _registry_with_tools(names, descriptions=None):
    registry = ToolRegistry()
    for name in names:
        registry.register(
            name,
            (descriptions or {}).get(name, f"Tool {name}."),
            {"type": "object", "properties": {}},
            lambda **kwargs: json.dumps({"ok": True}),
            read_only=True,
        )
    return registry


def _price_skills():
    return [
        Skill(
            id="price-compare",
            description="Compare new product prices across marketplaces.",
            trigger_terms=("price", "cheapest", "compare"),
            tools=("search_listings", "read_price"),
        ),
        Skill(
            id="bill-check",
            description="Check electricity and water bills.",
            trigger_terms=("bill", "pln", "electricity"),
            tools=("check_bill", "read_meter"),
        ),
    ]


def _tool_names(schemas):
    return [schema["function"]["name"] for schema in schemas or []]


def test_real_run_narrows_catalog_for_the_user_message():
    registry = _registry_with_tools(
        ["search_listings", "read_price", "check_bill", "read_meter", "send_message"]
    )
    seen_tools = []

    def fake_model(entry, wire_messages, tools):
        seen_tools.append(_tool_names(tools))
        return _text_reply("The cheapest price is Rp100.000.")

    loop = TurnLoop(
        model_call=fake_model,
        tool_catalog=[registry.get(name) for name in registry.names()],
        narrowing_skills=_price_skills(),
    )
    result = loop.run_turn([InternalMessage.user("what is the cheapest price?")])
    assert result.status == "completed"
    assert result.text == "The cheapest price is Rp100.000."
    assert seen_tools == [["read_price", "search_listings", SEARCH_TOOLS_NAME]]


def test_real_run_fails_open_when_nothing_matches():
    registry = _registry_with_tools(["search_listings", "read_price", "check_bill"])
    seen_tools = []

    def fake_model(entry, wire_messages, tools):
        seen_tools.append(_tool_names(tools))
        return _text_reply("Hi.")

    loop = TurnLoop(
        model_call=fake_model,
        tool_catalog=[registry.get(name) for name in registry.names()],
        narrowing_skills=_price_skills(),
    )
    result = loop.run_turn([InternalMessage.user("tell me a joke")])
    assert result.status == "completed"
    assert seen_tools == [["check_bill", "read_price", "search_listings"]]
    assert SEARCH_TOOLS_NAME not in seen_tools[0]


def test_real_run_over_50_offers_search_tools_not_the_full_list():
    names = [f"tool_{i:03d}" for i in range(60)]
    registry = _registry_with_tools(names)
    seen_tools = []

    def fake_model(entry, wire_messages, tools):
        seen_tools.append(_tool_names(tools))
        return _text_reply("Hi.")

    loop = TurnLoop(
        model_call=fake_model,
        tool_catalog=[registry.get(name) for name in registry.names()],
    )
    result = loop.run_turn([InternalMessage.user("tell me a joke")])
    assert result.status == "completed"
    assert len(seen_tools[0]) == 51
    assert seen_tools[0][-1] == SEARCH_TOOLS_NAME
    assert "tool_059" not in seen_tools[0]


def test_real_run_over_128_is_refused_before_the_model_is_called():
    names = [f"tool_{i:03d}" for i in range(129)]
    registry = _registry_with_tools(names)
    calls = []

    def fake_model(entry, wire_messages, tools):
        calls.append(tools)
        return _text_reply("Hi.")

    loop = TurnLoop(
        model_call=fake_model,
        tool_catalog=[registry.get(name) for name in registry.names()],
    )
    result = loop.run_turn([InternalMessage.user("tell me a joke")])
    assert result.status == "failed"
    assert "129 tools exceed the 128 tool limit" in result.message
    assert calls == []
    refused = [event for event in loop.event_memory() if event["type"] == TOOL_LIMIT_REFUSED_EVENT]
    assert len(refused) == 1
    assert refused[0]["content"]["tool_count"] == 129
    assert refused[0]["content"]["limit"] == 128


def test_real_run_can_call_search_tools_to_reach_a_hidden_tool():
    registry = _registry_with_tools(
        ["search_listings", "read_price", "check_bill", "read_meter", "send_message"],
        descriptions={
            "check_bill": "Check an electricity bill.",
            "read_meter": "Read the electricity meter.",
        },
    )
    register_search_tools(registry)
    replies = iter(
        [
            _tool_reply(SEARCH_TOOLS_NAME, {"query": "electricity bill"}),
            _text_reply("Your bill is Rp150.000."),
        ]
    )

    def fake_model(entry, wire_messages, tools):
        return next(replies)

    loop = TurnLoop(
        model_call=fake_model,
        tool_call=registry.handle_function_call,
        tool_catalog=[registry.get(name) for name in registry.names()],
        narrowing_skills=_price_skills(),
    )
    result = loop.run_turn([InternalMessage.user("what is the cheapest price?")])
    assert result.status == "completed"
    assert result.text == "Your bill is Rp150.000."
    tool_messages = [m for m in result.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0].blocks[0]["content"])
    assert payload["status"] == "ok"
    assert payload["scope"] == "hidden"
    assert [item["name"] for item in payload["results"]] == ["check_bill", "read_meter"]


def test_explicit_tools_win_over_narrowing():
    registry = _registry_with_tools(["search_listings", "read_price", "check_bill"])
    seen_tools = []

    def fake_model(entry, wire_messages, tools):
        seen_tools.append(_tool_names(tools))
        return _text_reply("Hi.")

    loop = TurnLoop(
        model_call=fake_model,
        tool_catalog=[registry.get(name) for name in registry.names()],
        narrowing_skills=_price_skills(),
    )
    explicit = [registry.get("check_bill").to_schema()]
    result = loop.run_turn([InternalMessage.user("cheapest price?")], tools=explicit)
    assert result.status == "completed"
    assert seen_tools == [["check_bill"]]


def test_turn_config_thresholds_are_configurable():
    config = TurnConfig(max_direct_tools=3, max_tools=5, max_offered_tools=2)
    assert config.max_direct_tools == 3
    assert config.max_tools == 5
    names = [f"tool_{i:03d}" for i in range(6)]
    registry = _registry_with_tools(names)
    loop = TurnLoop(
        model_call=lambda entry, wire, tools: _text_reply("Hi."),
        config=config,
        tool_catalog=[registry.get(name) for name in registry.names()],
    )
    result = loop.run_turn([InternalMessage.user("hi")])
    assert result.status == "failed"
    assert "6 tools exceed the 5 tool limit" in result.message


def test_turn_config_rejects_bad_thresholds():
    with pytest.raises(ValueError, match=r"max_tools .* must be larger"):
        TurnConfig(max_direct_tools=50, max_tools=50)
    with pytest.raises(ValueError, match=r"max_offered_tools .* must not exceed"):
        TurnConfig(max_direct_tools=5, max_offered_tools=6)


def test_prepare_run_tools_agrees_with_the_loop():
    # The loop's narrowing is the same function a caller can use
    # directly: same message, same catalog, same offered set.
    registry = _registry_with_tools(
        ["search_listings", "read_price", "check_bill", "read_meter", "send_message"]
    )
    catalog = [registry.get(name) for name in registry.names()]
    direct = prepare_run_tools("what is the cheapest price?", catalog, skills=_price_skills())
    seen_tools = []

    def fake_model(entry, wire_messages, tools):
        seen_tools.append(_tool_names(tools))
        return _text_reply("Hi.")

    loop = TurnLoop(model_call=fake_model, tool_catalog=catalog, narrowing_skills=_price_skills())
    loop.run_turn([InternalMessage.user("what is the cheapest price?")])
    assert seen_tools == [direct.offered_names]
