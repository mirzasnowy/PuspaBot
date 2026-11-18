"""Per-run skill narrowing + search_tools over the registry (G17).

In-memory only: run with ``pytest tests/test_narrowing.py --noconftest -q``.
"""

import json

import pytest

from puspa.tools.narrowing import (
    DEFAULT_MAX_DIRECT_TOOLS,
    DEFAULT_MAX_OFFERED_TOOLS,
    DEFAULT_MAX_TOOLS,
    SEARCH_TOOLS_NAME,
    TOOL_LIMIT_REFUSED_EVENT,
    KeywordSelector,
    NarrowingConfig,
    Skill,
    ToolLimitError,
    current_hidden_tools,
    hidden_tools_context,
    prepare_run_tools,
    register_search_tools,
    search_tools,
    search_tools_definition,
)
from puspa.tools.registry import ToolRegistry


def _tool(name, description, *, registry):
    registry.register(
        name,
        description,
        {"type": "object", "properties": {}},
        lambda **kwargs: json.dumps({"ok": True}),
        read_only=True,
    )
    return registry.get(name)


def _catalog(registry):
    price = Skill(
        id="price-compare",
        description="Compare new product prices across marketplaces.",
        trigger_terms=("price", "cheapest", "compare"),
        tools=("search_listings", "read_price"),
    )
    bills = Skill(
        id="bill-check",
        description="Check electricity and water bills.",
        trigger_terms=("bill", "pln", "electricity"),
        tools=("check_bill", "read_meter"),
    )
    tools = [
        _tool(
            "search_listings", "Search product listings for the cheapest price.", registry=registry
        ),
        _tool("read_price", "Read the price of one product.", registry=registry),
        _tool("check_bill", "Check an electricity bill.", registry=registry),
        _tool("read_meter", "Read the electricity meter.", registry=registry),
        _tool("send_message", "Send a chat message.", registry=registry),
    ]
    return [price, bills], tools


@pytest.fixture()
def registry():
    return ToolRegistry()


def test_narrowing_picks_matching_skill_tools_only(registry):
    skills, tools = _catalog(registry)
    result = prepare_run_tools("what is the cheapest price?", tools, skills=skills)
    assert result.narrowed is True
    assert result.fail_open is False
    assert result.selected_skill_ids == ["price-compare"]
    assert result.offered_names == ["search_listings", "read_price", SEARCH_TOOLS_NAME]
    assert result.hidden_names == ["check_bill", "read_meter", "send_message"]
    assert result.search_tool_included is True
    assert any(a["event"] == "narrowing_selected" for a in result.audit)


def test_no_match_fails_open_to_whole_catalog(registry):
    skills, tools = _catalog(registry)
    result = prepare_run_tools("tell me a joke", tools, skills=skills)
    assert result.narrowed is False
    assert result.fail_open is True
    assert result.offered_names == [
        "search_listings",
        "read_price",
        "check_bill",
        "read_meter",
        "send_message",
    ]
    assert result.hidden_names == []
    assert result.search_tool_included is False
    assert any(a["event"] == "narrowing_fail_open" for a in result.audit)


def test_narrowing_keeps_granted_tools(registry):
    skills, tools = _catalog(registry)
    send_message = next(t for t in tools if t.name == "send_message")
    tools = [t for t in tools if t.name != "send_message"]
    # Granted as a definition: the tool stays offered even though
    # it is not in this run's catalog.
    result = prepare_run_tools("cheapest price?", tools, skills=skills, granted=[send_message])
    assert "send_message" in result.offered_names
    assert "check_bill" not in result.offered_names


def test_offered_cap_keeps_best_matching_tools(registry):
    skill = Skill(
        id="big",
        description="Many tools about prices.",
        trigger_terms=("price",),
        tools=tuple(f"price_tool_{i:02d}" for i in range(12)),
    )
    tools = [
        _tool(f"price_tool_{i:02d}", f"Price helper {i} about cheapest price.", registry=registry)
        for i in range(12)
    ]
    result = prepare_run_tools("cheapest price?", tools, skills=[skill])
    assert len(result.offered_names) == DEFAULT_MAX_OFFERED_TOOLS + 1  # + search_tools
    assert result.search_tool_included is True
    assert len(result.hidden_names) == 12 - DEFAULT_MAX_OFFERED_TOOLS
    assert result.offered_names[-1] == SEARCH_TOOLS_NAME


def test_selector_failure_falls_back_to_keywords(registry):
    skills, tools = _catalog(registry)

    class BrokenSelector:
        def select(self, message, skills):
            raise RuntimeError("selector is down")

    result = prepare_run_tools(
        "my pln bill is high", tools, skills=skills, selector=BrokenSelector()
    )
    assert result.selected_skill_ids == ["bill-check"]
    assert result.offered_names[:2] == ["check_bill", "read_meter"]
    assert any(a["event"] == "narrowing_selector_failed" for a in result.audit)


def test_selector_naming_unknown_skills_fails_open(registry):
    skills, tools = _catalog(registry)

    class WrongSelector:
        def select(self, message, skills):
            return ["no-such-skill"]

    result = prepare_run_tools("cheapest price?", tools, skills=skills, selector=WrongSelector())
    assert result.fail_open is True
    assert result.offered_names == [t.name for t in tools]


def test_custom_selector_wins_over_keywords(registry):
    skills, tools = _catalog(registry)

    class FixedSelector:
        def select(self, message, skills):
            return ["bill-check"]

    result = prepare_run_tools("cheapest price?", tools, skills=skills, selector=FixedSelector())
    assert result.selected_skill_ids == ["bill-check"]
    assert result.offered_names[:2] == ["check_bill", "read_meter"]


def test_keyword_selector_is_deterministic(registry):
    skills, tools = _catalog(registry)
    first = prepare_run_tools("cheapest price pln bill?", tools, skills=skills)
    second = prepare_run_tools("cheapest price pln bill?", tools, skills=skills)
    assert first.to_dict() == second.to_dict()


def test_search_tools_pure_search_ranks_by_overlap(registry):
    _skills, tools = _catalog(registry)
    found = search_tools("electricity bill", tools)
    assert [item["name"] for item in found] == ["check_bill", "read_meter"]
    assert found[0]["description"] == "Check an electricity bill."
    assert found[0]["parameters"] == {"type": "object", "properties": {}}


def test_search_tools_empty_query_returns_all_capped(registry):
    _skills, tools = _catalog(registry)
    found = search_tools("", tools, limit=3)
    assert [item["name"] for item in found] == ["check_bill", "read_meter", "read_price"]
    assert search_tools("zzz-no-match", tools) == []


def test_search_tools_limit_must_be_positive(registry):
    _skills, tools = _catalog(registry)
    with pytest.raises(ValueError, match="limit must be at least 1"):
        search_tools("price", tools, limit=0)


def test_registered_search_tools_searches_hidden_tools(registry):
    skills, tools = _catalog(registry)
    register_search_tools(registry)
    result = prepare_run_tools("cheapest price?", tools, skills=skills)
    assert result.hidden_names == ["check_bill", "read_meter", "send_message"]
    with hidden_tools_context(result.hidden_tools):
        raw = registry.handle_function_call(SEARCH_TOOLS_NAME, {"query": "electricity bill"})
    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["scope"] == "hidden"
    assert [item["name"] for item in payload["results"]] == ["check_bill", "read_meter"]


def test_registered_search_tools_falls_back_to_registry(registry):
    _skills, _tools = _catalog(registry)
    register_search_tools(registry)
    assert current_hidden_tools() is None
    raw = registry.handle_function_call(SEARCH_TOOLS_NAME, {"query": "cheapest price"})
    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["scope"] == "registry"
    assert payload["results"][0]["name"] == "search_listings"


def test_register_search_tools_is_idempotent(registry):
    first = register_search_tools(registry)
    second = register_search_tools(registry)
    assert first is second
    assert registry.names().count(SEARCH_TOOLS_NAME) == 1


def test_search_tools_handler_rejects_empty_query(registry):
    register_search_tools(registry)
    raw = registry.handle_function_call(SEARCH_TOOLS_NAME, {"query": "  "})
    assert json.loads(raw)["status"] == "error"


def test_search_tools_definition_names_hidden_count():
    definition = search_tools_definition(7)
    assert "7 more tool(s)" in definition.description
    assert definition.required_params() == ["query"]
    assert definition.read_only is True


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


def _many_tools(registry, count):
    return [
        _tool(f"tool_{i:03d}", f"Helper tool number {i}.", registry=registry) for i in range(count)
    ]


def test_overflow_at_50_offers_search_tools(registry):
    tools = _many_tools(registry, 60)
    result = prepare_run_tools("tell me a joke", tools)
    assert result.fail_open is True
    assert len(result.offered_names) == DEFAULT_MAX_DIRECT_TOOLS + 1
    assert result.offered_names[-1] == SEARCH_TOOLS_NAME
    assert len(result.hidden_names) == 60 - DEFAULT_MAX_DIRECT_TOOLS
    assert result.offered_names[:DEFAULT_MAX_DIRECT_TOOLS] == [f"tool_{i:03d}" for i in range(50)]
    assert any(a["event"] == "tool_overflow_search" for a in result.audit)


def test_exactly_50_tools_do_not_overflow(registry):
    tools = _many_tools(registry, 50)
    result = prepare_run_tools("tell me a joke", tools)
    assert len(result.offered_names) == 50
    assert result.search_tool_included is False


def test_refusal_at_128_records_audit_event(registry):
    tools = _many_tools(registry, DEFAULT_MAX_TOOLS + 1)
    audit = []

    def sink(event, content):
        audit.append({"event": event, **content})

    with pytest.raises(ToolLimitError, match="129 tools exceed the 128 tool limit"):
        prepare_run_tools("tell me a joke", tools, audit_sink=sink)
    assert len(audit) == 1
    assert audit[0]["event"] == TOOL_LIMIT_REFUSED_EVENT
    assert audit[0]["tool_count"] == 129
    assert audit[0]["limit"] == 128


def test_exactly_128_tools_are_allowed(registry):
    tools = _many_tools(registry, 128)
    result = prepare_run_tools("tell me a joke", tools)
    assert len(result.offered_names) == 51  # 50 direct + search_tools


def test_granted_tools_count_toward_refusal_limit(registry):
    tools = _many_tools(registry, 128)
    extra = _tool("extra_tool", "One more tool.", registry=registry)
    with pytest.raises(ToolLimitError, match="129 tools exceed"):
        prepare_run_tools("tell me a joke", tools, granted=[extra])


def test_threshold_config_overrides_defaults(registry):
    tools = _many_tools(registry, 6)
    config = NarrowingConfig(max_direct_tools=3, max_tools=5, max_offered_tools=2)
    with pytest.raises(ToolLimitError, match="6 tools exceed the 5 tool limit"):
        prepare_run_tools("tell me a joke", tools, config=config)
    other_registry = ToolRegistry()
    tools = _many_tools(other_registry, 4)
    result = prepare_run_tools("tell me a joke", tools, config=config)
    assert result.offered_names == ["tool_000", "tool_001", "tool_002", SEARCH_TOOLS_NAME]
    assert result.hidden_names == ["tool_003"]


def test_narrowing_config_rejects_bad_thresholds():
    with pytest.raises(ValueError, match=r"max_tools .* must be larger"):
        NarrowingConfig(max_direct_tools=50, max_tools=50)
    with pytest.raises(ValueError, match=r"max_offered_tools .* must not exceed"):
        NarrowingConfig(max_direct_tools=5, max_offered_tools=6)
    with pytest.raises(ValueError, match="search_result_limit must be at least 1"):
        NarrowingConfig(search_result_limit=0)


def test_keyword_selector_scores_description_tokens():
    selector = KeywordSelector()
    skill = Skill(id="s1", description="Compare prices across shops.", tools=("t1",))
    assert selector.select("compare prices", [skill]) == ["s1"]
    assert selector.select("nothing relevant here", [skill]) == []
