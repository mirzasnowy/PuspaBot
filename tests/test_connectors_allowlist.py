"""Tests: advertised-tool allowlist must never leak (G10, flagship).

In-memory only: no database, no network.
"""

import pytest

from puspa.connectors.allowlist import (
    MAX_ENV_BYTES,
    AllowlistError,
    ToolAllowlist,
    parse_env_list,
)
from puspa.connectors.wedges import curated_wedge_directory

TOOLS = [
    {"name": "check_price", "description": "Price.", "service": "tokopedia"},
    {"name": "check_bill", "description": "Bill.", "service": "pln"},
    {"name": "pay_bill", "description": "Pay.", "service": "pln"},
    {"name": "search_pages", "description": "Search.", "service": "notion"},
]


def test_allowlist_filters_by_tool_and_service():
    allowlist = ToolAllowlist(
        allowed_tools={"check_price", "check_bill"},
        allowed_services={"tokopedia", "pln"},
    )
    assert allowlist.advertised_names(TOOLS) == ["check_price", "check_bill"]
    # Same tool name under a disallowed service is not advertised.
    assert allowlist.is_advertised("check_price", service="shopee") is False
    # A tool with no service never passes a service allowlist.
    assert allowlist.is_advertised("check_price", service=None) is False


def test_allowlist_axes_are_independent():
    by_tool = ToolAllowlist(allowed_tools={"pay_bill"})
    assert by_tool.advertised_names(TOOLS) == ["pay_bill"]
    by_service = ToolAllowlist(allowed_services={"notion"})
    assert by_service.advertised_names(TOOLS) == ["search_pages"]
    unrestricted = ToolAllowlist()
    assert unrestricted.advertised_names(TOOLS) == [t["name"] for t in TOOLS]
    empty = ToolAllowlist(allowed_tools=set(), allowed_services=set())
    assert empty.advertise(TOOLS) == []


def test_allowlist_never_leaks_outside_tools():
    allowlist = ToolAllowlist(
        allowed_tools={"check_price"}, allowed_services={"tokopedia"}
    )
    advertised = allowlist.advertise(TOOLS)
    allowlist.assert_no_leak(TOOLS, advertised)
    # A hand-built list that smuggles a disallowed tool is caught.
    smuggled = advertised + [
        {"name": "pay_bill", "description": "Pay.", "service": "pln"}
    ]
    with pytest.raises(AllowlistError, match="leaked"):
        allowlist.assert_no_leak(TOOLS, smuggled)
    # The full wedge directory filtered by a narrow allowlist leaks nothing.
    wedge_tools = curated_wedge_directory().all_tools()
    narrow = ToolAllowlist(allowed_tools={"check_price"}, allowed_services={"blibli"})
    advertised_wedge = narrow.advertise(wedge_tools)
    assert advertised_wedge == [
        {"name": "check_price", "description": "Check the price of a new product by search query.", "service": "blibli"}
    ]
    narrow.assert_no_leak(wedge_tools, advertised_wedge)


def test_env_parsing_and_byte_cap():
    allowlist = ToolAllowlist.from_env(
        {
            "CONNECTOR_ALLOWED_TOOLS": " check_price , pay_bill ",
            "CONNECTOR_SERVICE_SLUGS": "tokopedia,pln",
        }
    )
    assert allowlist.allowed_tools == {"check_price", "pay_bill"}
    assert allowlist.allowed_services == {"tokopedia", "pln"}
    assert ToolAllowlist.from_env({}).allowed_tools is None
    assert ToolAllowlist.from_env({"CONNECTOR_ALLOWED_TOOLS": "  "}).allowed_tools is None
    with pytest.raises(AllowlistError, match="empty entry"):
        parse_env_list("check_price,,pay_bill", "CONNECTOR_ALLOWED_TOOLS")
    with pytest.raises(AllowlistError, match="over the"):
        parse_env_list("x" * (MAX_ENV_BYTES + 1), "CONNECTOR_ALLOWED_TOOLS")
    # Exactly at the cap is accepted.
    assert parse_env_list("x" * MAX_ENV_BYTES, "CONNECTOR_ALLOWED_TOOLS") == {
        "x" * MAX_ENV_BYTES
    }
    # A multi-byte value is measured in bytes, not characters.
    with pytest.raises(AllowlistError, match="over the"):
        parse_env_list("é" * (MAX_ENV_BYTES // 2 + 1), "CONNECTOR_ALLOWED_TOOLS")
