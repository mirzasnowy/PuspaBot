"""Tests: aggregator broker interface + fake broker + sessions (G10).

In-memory only: no database, no network.
"""

import pytest

from puspa.connectors.aggregator import (
    Broker,
    BrokerError,
    FakeBroker,
    ProjectSession,
)
from puspa.connectors.allowlist import ToolAllowlist


def test_fake_broker_lists_services_and_tools():
    broker = FakeBroker()
    assert broker.list_services() == ["github", "notion", "slack"]
    assert [t["name"] for t in broker.list_tools("github")] == [
        "list_issues",
        "create_issue",
    ]
    assert broker.list_tools("unknown-service") == []
    assert broker.name == "fake"


def test_fake_broker_call_is_deterministic():
    broker = FakeBroker()
    result = broker.call_tool("list_issues", {"repo": "puspa-ai"})
    assert result == {
        "tool": "list_issues",
        "service": "github",
        "arguments": {"repo": "puspa-ai"},
        "result": "fake result of list_issues",
    }
    with pytest.raises(BrokerError, match="no tool"):
        broker.call_tool("delete_everything", {})


def test_project_session_advertises_only_allowed_tools():
    broker = FakeBroker()
    allowlist = ToolAllowlist(
        allowed_tools={"list_issues", "search_pages"},
        allowed_services={"github", "notion"},
    )
    session = ProjectSession("project-1", broker, allowlist=allowlist)
    advertised = session.advertised_tools()
    assert [t["name"] for t in advertised] == ["list_issues", "search_pages"]
    assert session.call_tool("list_issues", {"repo": "x"})["tool"] == "list_issues"
    with pytest.raises(BrokerError, match="not advertised"):
        session.call_tool("create_issue", {})
    with pytest.raises(BrokerError, match="not advertised"):
        session.call_tool("send_message", {})


def test_project_session_from_env_allowlist():
    broker = FakeBroker()
    session = ProjectSession.from_env(
        "project-1",
        broker,
        environ={
            "CONNECTOR_ALLOWED_TOOLS": "send_message",
            "CONNECTOR_SERVICE_SLUGS": "slack",
        },
    )
    assert [t["name"] for t in session.advertised_tools()] == ["send_message"]


def test_project_session_rejects_bad_inputs():
    with pytest.raises(BrokerError, match="project_id"):
        ProjectSession("", FakeBroker())
    with pytest.raises(BrokerError, match="Broker"):
        ProjectSession("project-1", object())


def test_broker_is_an_interface_not_a_live_connection():
    assert issubclass(FakeBroker, Broker)
    assert Broker.name == "base"
    # A bare Broker subclass without the three methods cannot run.
    class BrokenBroker(Broker):
        pass

    with pytest.raises(TypeError):
        BrokenBroker()
