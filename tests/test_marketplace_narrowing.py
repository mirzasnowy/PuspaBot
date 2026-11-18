"""Marketplace per-run narrowing + tool overflow (G3, OpenBot pattern).

In-memory: run with ``pytest tests/test_marketplace_narrowing.py
--noconftest -q``.
"""

import pytest

from puspa.marketplace import (
    SEARCH_TOOLS_NAME,
    TOOL_LIMIT_REFUSED_EVENT,
    narrow_tools,
    search_tools,
)
from puspa.marketplace.errors import ToolLimitError


def _skills():
    return [
        {"id": "price-compare", "description": "Compare prices.",
         "triggerTerms": ["price", "cheapest", "compare"]},
        {"id": "bill-check", "description": "Check bills.",
         "triggerTerms": ["bill", "pln", "electricity"]},
    ]


def _tools():
    return [
        {"name": "search_listings", "description": "Search listings.",
         "skill": "price-compare"},
        {"name": "read_price", "description": "Read a price.",
         "skill": "price-compare"},
        {"name": "check_bill", "description": "Check a bill.",
         "skill": "bill-check"},
        {"name": "read_meter", "description": "Read the meter.",
         "skill": "bill-check"},
        {"name": "send_message", "description": "Send a message.",
         "skill": None},
    ]


def test_narrowing_picks_matching_skill_tools_only():
    result = narrow_tools("what is the cheapest price?", _skills(), _tools())
    assert result.narrowed is True
    assert result.selected_skill_ids == ["price-compare"]
    names = [t["name"] for t in result.tools]
    assert names == ["search_listings", "read_price"]


def test_narrowing_keeps_granted_tools():
    granted = [{"name": "send_message", "description": "Send a message.",
                "skill": None}]
    tools = [t for t in _tools() if t["name"] != "send_message"]
    result = narrow_tools("cheapest price?", _skills(), tools,
                          granted_tools=granted)
    names = [t["name"] for t in result.tools]
    assert "send_message" in names
    assert "check_bill" not in names


def test_no_match_fails_open_to_whole_catalog():
    result = narrow_tools("tell me a joke", _skills(), _tools())
    assert result.narrowed is False
    assert [t["name"] for t in result.tools] == [
        "search_listings", "read_price", "check_bill", "read_meter",
        "send_message",
    ]
    assert any(a["event"] == "narrowing_fail_open" for a in result.audit)


def test_llm_selector_picks_skills():
    def fake_complete(prompt):
        return '{"skills": ["bill-check"]}'

    result = narrow_tools("my pln bill is high", _skills(), _tools(),
                          llm_complete=fake_complete)
    assert result.selected_skill_ids == ["bill-check"]
    assert [t["name"] for t in result.tools] == ["check_bill", "read_meter"]


def test_llm_failure_falls_back_to_deterministic_then_fail_open():
    def broken_complete(prompt):
        raise RuntimeError("no key")

    result = narrow_tools("tell me a joke", _skills(), _tools(),
                          llm_complete=broken_complete)
    assert result.narrowed is False
    assert len(result.tools) == 5


def test_llm_garbage_falls_back_to_deterministic():
    def garbage_complete(prompt):
        return "not json at all"

    result = narrow_tools("cheapest price?", _skills(), _tools(),
                          llm_complete=garbage_complete)
    assert result.selected_skill_ids == ["price-compare"]


def test_over_50_tools_overflow_behind_search_tools():
    tools = [
        {"name": f"tool-{i:03d}", "description": f"Tool {i}.",
         "skill": "price-compare"}
        for i in range(60)
    ]
    result = narrow_tools("cheapest price?", _skills(), tools)
    assert len(result.tools) == 51  # 50 direct + search_tools
    assert result.search_tool is not None
    assert result.search_tool["name"] == SEARCH_TOOLS_NAME
    assert len(result.overflow_tools) == 10
    assert any(a["event"] == "tool_overflow_search" for a in result.audit)


def test_search_tools_finds_overflow_tools():
    overflow = [
        {"name": "fetch_invoice", "description": "Fetch an invoice.",
         "skill": "bill-check"},
        {"name": "read_price", "description": "Read a price.",
         "skill": "price-compare"},
    ]
    found = search_tools("invoice", overflow)
    assert [t["name"] for t in found] == ["fetch_invoice"]
    assert search_tools("", overflow) == overflow


def test_over_128_tools_run_is_refused_with_audit():
    tools = [
        {"name": f"tool-{i:03d}", "description": f"Tool {i}.",
         "skill": "price-compare"}
        for i in range(129)
    ]
    audit_sink = []
    with pytest.raises(ToolLimitError, match="129 tools exceed the 128"):
        narrow_tools("cheapest price?", _skills(), tools,
                     audit_sink=audit_sink.append)
    assert audit_sink
    assert audit_sink[0]["event"] == TOOL_LIMIT_REFUSED_EVENT
    assert audit_sink[0]["limit"] == 128


def test_exactly_128_tools_is_allowed():
    tools = [
        {"name": f"tool-{i:03d}", "description": f"Tool {i}.",
         "skill": "price-compare"}
        for i in range(128)
    ]
    result = narrow_tools("cheapest price?", _skills(), tools)
    assert result.refused is False
    assert len(result.tools) == 51  # overflow still applies at 50
    assert len(result.overflow_tools) == 78
