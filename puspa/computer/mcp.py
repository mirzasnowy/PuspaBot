"""MCP tool definitions for desktop hands (V2-31, core; V2-32
visual fallback + web bridge).

Seven tools, in the same shape as the rest of Puspa's MCP
definitions (``name`` / ``description`` / ``inputSchema``):
``computer_snapshot`` (read the screen), ``computer_click`` (the
gated act + re-read loop), ``computer_verify`` (re-read one ref
and check it), plus the V2-32 tools ``computer_visual_locate``
(find text on the screen by OCR), ``computer_visual_click``
(gated coordinate click + re-read verify), ``computer_web_open``
(open a URL in headless Chromium) and ``computer_web_click``
(gated web click + re-read verify). :func:`handle_tool`
dispatches one call to a :class:`~puspa.computer.hands.DesktopHands`
instance (and, for the V2-32 tools, to a
:class:`~puspa.computer.fallback.VisualFallback` / :class:`~puspa.computer.web.WebBridge`
passed as ``fallback`` / ``web``) and returns a plain JSON-ready
dict.

These definitions are ready for the future ``puspa mcp`` server
(V2-37). That server does NOT exist yet: nothing here claims a
working MCP server, and :func:`mcp_server_definition` only builds
the validated definition dict the server will serve one day.
"""

from __future__ import annotations

import copy

from .fallback import VisualClickResult, VisualFallback
from .hands import ClickResult, DesktopHands, Verification
from .tree import snapshot_to_dict
from .vision import VisualElement
from .web import WebBridge, WebClickResult

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
    {
        "name": "computer_visual_locate",
        "description": (
            "Locate text on the screen by OCR (the visual fallback, for apps whose accessibility "
            "tree is empty or too poor to act on). Screenshots the screen, reads the text, and "
            "returns the matching element's box and centre pixel, or found=false when the text "
            "is not on the screen. Read-only: never clicks anything."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to find on the screen, e.g. 'Save'.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "computer_visual_click",
        "description": (
            "Click text on the screen by coordinates (the visual fallback). Locates the text by "
            "OCR, clicks its centre pixel, then re-reads the screen to verify. The click is "
            "permission-gated (the user is asked first, and a token without the "
            "computer.click_at permission is refused); coordinate clicks are riskier than ref "
            "clicks, so they need that permission on their own. Pass expect to say what should "
            "be true afterwards (text, or absent_text when the text should be gone). Returns "
            "performed (did the action run) separately from verified (did the re-read confirm "
            "a change)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to click on the screen, e.g. 'Save'.",
                },
                "expect": {
                    "type": "object",
                    "description": "What the re-read should find: text, or absent_text.",
                    "properties": {
                        "text": {"type": "string"},
                        "absent_text": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "computer_web_open",
        "description": (
            "Open a URL in headless Chromium (the web bridge) and return its URL and title. "
            "Use it before computer_web_click, or to read a page the desktop tree cannot see. "
            "Read-only: never clicks anything."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to open, e.g. 'https://example.com'.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "computer_web_click",
        "description": (
            "Click text on the open web page (the web bridge). Locates the text through the "
            "page layout, clicks its centre, then re-reads the page to verify. The click is "
            "permission-gated (the user is asked first, and a token without the "
            "computer.web_click permission is refused). Pass expect to say what should be true "
            "afterwards (text, or absent_text when the text should be gone). Returns performed "
            "separately from verified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to click on the open page, e.g. 'Save'.",
                },
                "expect": {
                    "type": "object",
                    "description": "What the re-read should find: text, or absent_text.",
                    "properties": {
                        "text": {"type": "string"},
                        "absent_text": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["text"],
        },
    },
]


def tool_definitions() -> list[dict]:
    """A fresh copy of the seven tool definitions."""
    return copy.deepcopy(TOOL_DEFINITIONS)


def mcp_server_definition() -> dict:
    """The MCP server definition these tools will be served under
    once ``puspa mcp`` exists (V2-37). Validated against
    :func:`puspa.plugins.mcp.validate_mcp_definition` in tests."""
    return {
        "id": "puspa-computer",
        "name": "Puspa Computer",
        "version": "0.1.0",
        "description": (
            "Drive the local desktop through its accessibility tree (snapshot, click, verify), "
            "fall back to screenshot + OCR when the tree is too poor, and drive web pages in "
            "headless Chromium."
        ),
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


def handle_tool(
    name: str,
    arguments: dict,
    hands: DesktopHands,
    *,
    fallback: VisualFallback | None = None,
    web: WebBridge | None = None,
) -> dict:
    """Dispatch one MCP tool call to ``hands`` (and, for the
    V2-32 tools, to ``fallback`` / ``web``).

    Returns a JSON-ready dict. Unknown tools and bad arguments
    raise ValueError (the future server turns that into an MCP
    error); permission denials and unknown refs raise from the
    hands layer unchanged. A visual or web tool called without
    its ``fallback`` / ``web`` object raises ValueError with a
    plain message instead of crashing.
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
    if name == "computer_visual_locate":
        if fallback is None:
            raise ValueError("computer_visual_locate needs a VisualFallback (no fallback wired up)")
        text = arguments.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("computer_visual_locate needs a string 'text'")
        element: VisualElement | None = fallback.locate_text(text)
        if element is None:
            return {"text": text, "found": False}
        return {"text": text, "found": True, **element.to_dict()}
    if name == "computer_visual_click":
        if fallback is None:
            raise ValueError("computer_visual_click needs a VisualFallback (no fallback wired up)")
        text = arguments.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("computer_visual_click needs a string 'text'")
        visual_result: VisualClickResult = fallback.click_text(text, expect=arguments.get("expect"))
        return visual_result.to_dict()
    if name == "computer_web_open":
        if web is None:
            raise ValueError("computer_web_open needs a WebBridge (no web bridge wired up)")
        url = arguments.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError("computer_web_open needs a string 'url'")
        return web.open(url).to_dict()
    if name == "computer_web_click":
        if web is None:
            raise ValueError("computer_web_click needs a WebBridge (no web bridge wired up)")
        text = arguments.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("computer_web_click needs a string 'text'")
        web_result: WebClickResult = web.click_text(text, expect=arguments.get("expect"))
        return web_result.to_dict()
    raise ValueError(f"unknown computer tool {name!r}")
