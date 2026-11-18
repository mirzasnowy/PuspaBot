"""Aggregator layer (G10, layer B): one broker for hundreds of apps.

An aggregator broker forwards tool calls to hundreds of third-party
apps through a single integration. Puspa has no real broker yet: no
broker has been chosen and no credential exists. This module is the
interface plus a deterministic fake broker for tests, with the
advertising path wired through the allowlist so the engine only ever
sees allowed tools.

A real broker is a follow-up: pick a broker, store its credential
sealed in the vault, implement :class:`Broker` against its API, and
register it here. Nothing in this module pretends a broker is
connected.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .allowlist import ToolAllowlist

__all__ = [
    "Broker",
    "BrokerError",
    "FakeBroker",
    "ProjectSession",
    "FAKE_BROKER_SERVICES",
]

#: Services and tools the fake broker advertises (deterministic).
FAKE_BROKER_SERVICES = {
    "github": [
        {"name": "list_issues", "description": "List open issues.", "service": "github"},
        {"name": "create_issue", "description": "Create an issue.", "service": "github"},
    ],
    "notion": [
        {"name": "search_pages", "description": "Search pages.", "service": "notion"},
        {"name": "create_page", "description": "Create a page.", "service": "notion"},
    ],
    "slack": [
        {"name": "send_message", "description": "Send a message.", "service": "slack"},
    ],
}


class BrokerError(Exception):
    """The broker (or a session through it) failed or refused."""


class Broker(ABC):
    """Interface a real aggregator broker must implement.

    ``name`` identifies the broker (for example ``"composio"``).
    ``list_services`` returns the service slugs the broker offers.
    ``list_tools`` returns tool descriptors for one service, each
    ``{"name", "description", "service"}``. ``call_tool`` performs
    one tool call and returns its result dict.
    """

    name = "base"

    @abstractmethod
    def list_services(self) -> list[str]:
        """Service slugs this broker offers."""

    @abstractmethod
    def list_tools(self, service_slug: str) -> list[dict]:
        """Tool descriptors for one service (empty list if unknown)."""

    @abstractmethod
    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Perform one tool call; return the result dict."""


class FakeBroker(Broker):
    """Deterministic fake broker for tests and demos.

    Serves the fixed service/tool table above and answers every
    call with a deterministic ``{"tool", "arguments", "result"}``
    dict. It never touches the network.
    """

    name = "fake"

    def __init__(self, services=None):
        self._services = dict(services or FAKE_BROKER_SERVICES)

    def list_services(self) -> list[str]:
        return sorted(self._services)

    def list_tools(self, service_slug: str) -> list[dict]:
        return [dict(tool) for tool in self._services.get(service_slug, [])]

    def all_tools(self) -> list[dict]:
        """Every tool of every service, in service order."""
        tools: list[dict] = []
        for slug in self.list_services():
            tools.extend(self.list_tools(slug))
        return tools

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        for tool in self.all_tools():
            if tool["name"] == tool_name:
                return {
                    "tool": tool_name,
                    "service": tool["service"],
                    "arguments": dict(arguments or {}),
                    "result": f"fake result of {tool_name}",
                }
        raise BrokerError(f"broker has no tool {tool_name!r}")


class ProjectSession:
    """One project's session through a broker.

    The session owns the broker and the allowlist. ``advertised_tools``
    is the only tool list the engine sees: broker tools filtered by
    the allowlist. ``call_tool`` refuses any tool that is not
    advertised, even if the broker itself offers it.
    """

    def __init__(self, project_id: str, broker: Broker, *, allowlist=None):
        if not isinstance(project_id, str) or not project_id:
            raise BrokerError("project_id must be a non-empty string")
        if not isinstance(broker, Broker):
            raise BrokerError("broker must be a Broker instance")
        self.project_id = project_id
        self.broker = broker
        self.allowlist = allowlist or ToolAllowlist()

    @classmethod
    def from_env(cls, project_id: str, broker: Broker, *, environ=None):
        """Build a session whose allowlist comes from the environment."""
        return cls(project_id, broker, allowlist=ToolAllowlist.from_env(environ))

    def advertised_tools(self) -> list[dict]:
        """Broker tools filtered by the allowlist (engine-facing)."""
        tools: list[dict] = []
        for slug in self.broker.list_services():
            tools.extend(self.broker.list_tools(slug))
        advertised = self.allowlist.advertise(tools)
        self.allowlist.assert_no_leak(tools, advertised)
        return advertised

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call one advertised tool. Refuses non-advertised tools."""
        advertised_names = {
            tool["name"] for tool in self.advertised_tools()
        }
        if tool_name not in advertised_names:
            raise BrokerError(
                f"tool {tool_name!r} is not advertised to the engine "
                "for this project session"
            )
        return self.broker.call_tool(tool_name, arguments)
