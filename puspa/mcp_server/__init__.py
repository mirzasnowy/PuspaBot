"""Puspa's own MCP server (``puspa mcp``).

An outside agent (Claude Code, Codex, or any MCP client) can
list Puspa's tools and call them over stdio; every call is
policed by the Puspa policy gateway first. See
:mod:`puspa.mcp_server.server` for the transport,
:mod:`puspa.mcp_server.tools` for the tool list, and
:mod:`puspa.mcp_server.handlers` for what each tool does.
"""

from __future__ import annotations

from .handlers import HandlerContext, build_handlers
from .server import McpServer, run_stdio_server
from .tools import (
    CORE_TOOL_DEFINITIONS,
    TOOL_EFFECTS,
    all_tool_definitions,
    core_tool_definitions,
    mcp_server_definition,
)

__all__ = [
    "CORE_TOOL_DEFINITIONS",
    "TOOL_EFFECTS",
    "HandlerContext",
    "McpServer",
    "all_tool_definitions",
    "build_handlers",
    "core_tool_definitions",
    "mcp_server_definition",
    "run_stdio_server",
]
