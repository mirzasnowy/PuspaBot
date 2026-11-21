"""MCP tool definitions for desktop hands (V2-31, core).

Three tools, in the same shape as the rest of Puspa's MCP
definitions (``name`` / ``description`` / ``inputSchema``):
``computer_snapshot`` (read the screen), ``computer_click`` (the
gated act + re-read loop), ``computer_verify`` (re-read one ref
and check it). :func:`handle_tool` dispatches one call to a
:class:`~puspa.computer.hands.DesktopHands` instance and returns
a plain JSON-ready dict.

These definitions are ready for the future ``puspa mcp`` server
(V2-37). That server does NOT exist yet: nothing here claims a
working MCP server, and :func:`mcp_server_definition` only builds
the validated definition dict the server will serve one day.
"""

from __future__ import annotations

import copy

from .hands import ClickResult, DesktopHands, Verification
from .tree import snapshot_to_dict

__all__ = [
    "TOOL_DEFINITIONS",
    "handle_tool",
    "mcp_server_definition",
    "tool_definitions",
]

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "computer_snapshot",
        "description": (
            "Read the desktop accessibility tree as a JSON snapshot. Every element gets a stable ref "
            "inside this snapshot; act on refs with computer_click. Skeleton by default: deep subtrees "
            "are collapsed (child_count only) so huge app trees stay small; drill into one node with "
            "skeleton=false or computer_verify. Read-only: never moves the mouse or steals focus."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "skeleton": {
                    "type": "boolean",
                    "default": True,
                    "description": "Collapse subtrees deeper than 2 levels (default true).",
                },
                "max_nodes": {
                    "type": "integer",
                    "default": 5000,
                    "description": "Hard cap on nodes in one snapshot.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "computer_click",
        "description": (
            "Click one element by ref from the latest snapshot. The click is permission-gated (the user "
            "is asked first, and a token without the computer.click permission is refused), then the "
            "screen is re-read to verify: a successful click never proves the UI changed. Pass expect "
            "to say what should be true afterwards (name, state, or absent=true when the element should "
            "be gone). Returns performed (did the action run) separately from verified (did the re-read "
            "confirm a change)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Element ref from computer_snapshot, e.g. 'n3'.",
                },
                "expect": {
                    "type": "object",
                    "description": "What the re-read should find: name, state, or absent=true.",
                    "properties": {
                        "name": {"type": "string"},
                        "state": {"type": "string"},
                        "role": {"type": "string"},
                        "absent": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["ref"],
        },
    },
    {
        "name": "computer_verify",
        "description": (
            "Re-read the screen and check one ref against an expectation (name, state, role, or "
            "absent=true). Read-only: never clicks anything. Use it to confirm a click landed, or to "
            "check the screen before acting."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Element ref from computer_snapshot, e.g. 'n3'.",
                },
                "expect": {
                    "type": "object",
                    "description": "What the re-read should find: name, state, role, or absent=true.",
                    "properties": {
                        "name": {"type": "string"},
                        "state": {"type": "string"},
                        "role": {"type": "string"},
                        "absent": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["ref"],
        },
    },
]


def tool_definitions() -> list[dict]:
    """A fresh copy of the three tool definitions."""
    return copy.deepcopy(TOOL_DEFINITIONS)


def mcp_server_definition() -> dict:
    """The MCP server definition these tools will be served under
    once ``puspa mcp`` exists (V2-37). Validated against
    :func:`puspa.plugins.mcp.validate_mcp_definition` in tests."""
    return {
        "id": "puspa-computer",
        "name": "Puspa Computer",
        "version": "0.1.0",
        "description": "Drive the local desktop through its accessibility tree: snapshot, click, verify.",
        "server": {"command": "puspa", "args": ["mcp"], "transport": "stdio", "env": {}},
        "tools": tool_definitions(),
    }


def _verification_to_dict(result: Verification) -> dict:
    return {
        "ref": result.ref,
        "found": result.found,
        "verified": result.verified,
        "reason": result.reason,
        "before": result.before,
        "after": result.after,
    }


def handle_tool(name: str, arguments: dict, hands: DesktopHands) -> dict:
    """Dispatch one MCP tool call to ``hands``.

    Returns a JSON-ready dict. Unknown tools and bad arguments
    raise ValueError (the future server turns that into an MCP
    error); permission denials and unknown refs raise from the
    hands layer unchanged.
    """
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    if name == "computer_snapshot":
        shot = hands.snapshot(
            skeleton=bool(arguments.get("skeleton", True)),
            max_nodes=int(arguments.get("max_nodes", 5000)),
        )
        return snapshot_to_dict(shot)
    if name == "computer_click":
        ref = arguments.get("ref")
        if not isinstance(ref, str) or not ref:
            raise ValueError("computer_click needs a string 'ref'")
        result: ClickResult = hands.click(ref, expect=arguments.get("expect"))
        return result.to_dict()
    if name == "computer_verify":
        ref = arguments.get("ref")
        if not isinstance(ref, str) or not ref:
            raise ValueError("computer_verify needs a string 'ref'")
        return _verification_to_dict(hands.verify(ref, expect=arguments.get("expect")))
    raise ValueError(f"unknown computer tool {name!r}")
