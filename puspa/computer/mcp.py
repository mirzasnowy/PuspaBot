"""MCP tool definitions for desktop hands (V2-31, core; V2-32
visual fallback + web bridge; V2-33 full input + window
management; V2-34 settle + non-stale refs).

Nineteen tools, in the same shape as the rest of Puspa's MCP
definitions (``name`` / ``description`` / ``inputSchema``):
``computer_snapshot`` (read the screen), ``computer_click`` (the
gated act + re-read loop), ``computer_verify`` (re-read one ref
and check it), ``computer_wait_settle`` (wait for the tree to
stop changing), plus the V2-32 tools ``computer_visual_locate``
(find text on the screen by OCR), ``computer_visual_click``
(gated coordinate click + re-read verify), ``computer_web_open``
(open a URL in headless Chromium) and ``computer_web_click``
(gated web click + re-read verify), plus V2-33:
``computer_type``, ``computer_shortcut``, ``computer_scroll``,
``computer_drag``, ``computer_clipboard_read``,
``computer_clipboard_write``, ``computer_list_windows``,
``computer_open_app``, ``computer_focus_window``,
``computer_switch_window``, and ``computer_run_task`` (a
multi-step task run end to end). :func:`handle_tool` dispatches
one call to a :class:`~puspa.computer.hands.DesktopHands`
instance (and, for the V2-32 tools, to a
:class:`~puspa.computer.fallback.VisualFallback` /
:class:`~puspa.computer.web.WebBridge` passed as ``fallback`` /
``web``) and returns a plain JSON-ready dict.


V2-34: ``computer_click`` waits for the tree to settle and
re-resolves the ref right before acting; when the element is
gone or the ref now finds a different element, the click is
refused as stale (raised, never a mis-click) instead of acting.
``computer_visual_click`` re-grounds its OCR target the same
way. A stale refusal is not a permission denial.

These definitions are ready for the future ``puspa mcp`` server
(V2-37). That server does NOT exist yet: nothing here claims a
working MCP server, and :func:`mcp_server_definition` only builds
the validated definition dict the server will serve one day.

Clipboard content goes to the calling agent only, through the
``computer_clipboard_read`` result: it is never written to a
log, a file, or the CLI, and it never appears in any other
tool's result.
"""

from __future__ import annotations

import copy

from .fallback import VisualClickResult, VisualFallback
from .hands import ClickResult, DesktopHands, Verification
from .sync import SettleTimeoutError
from .tree import snapshot_to_dict
from .vision import VisualElement
from .web import WebBridge, WebClickResult

__all__ = [
    "TOOL_DEFINITIONS",
    "handle_tool",
    "mcp_server_definition",
    "tool_definitions",
]

_EXPECT_SCHEMA = {
    "type": "object",
    "description": "What the re-read should find: name, state, role, absent=true, value, or value_contains.",
    "properties": {
        "name": {"type": "string"},
        "state": {"type": "string"},
        "role": {"type": "string"},
        "absent": {"type": "boolean"},
        "value": {"type": "string"},
        "value_contains": {"type": "string"},
    },
    "additionalProperties": False,
}

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
            "Click one element by ref from the latest snapshot. The tree is waited on until it "
            "settles first, then the click is permission-gated (the user is asked first, and a "
            "token without the computer.click permission is refused), then the ref is re-resolved "
            "right before acting: when the element is gone, or the ref now finds a different "
            "element, the click is refused as stale and nothing is pressed (a stale refusal is "
            "not a permission denial). After the click the screen is re-read to verify: a "
            "successful click never proves the UI changed. Pass expect to say what should be true "
            "afterwards (name, state, or absent=true when the element should be gone). Returns "
            "performed (did the action run) separately from verified (did the re-read confirm "
            "a change)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Element ref from computer_snapshot, e.g. 'n3'.",
                },
                "expect": _EXPECT_SCHEMA,
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
                "expect": _EXPECT_SCHEMA,
            },
            "required": ["ref"],
        },
    },
    {
        "name": "computer_type",
        "description": (
            "Type text into one editable element by ref from the latest snapshot. Permission-gated "
            "(the user is asked first, and a token without the computer.type permission is refused), "
            "then the screen is re-read to verify: pass expect with value or value_contains to say "
            "what the element should hold afterwards. Returns performed separately from verified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Element ref from computer_snapshot, e.g. 'n5'.",
                },
                "text": {"type": "string", "description": "Text to type into the element."},
                "expect": _EXPECT_SCHEMA,
            },
            "required": ["ref", "text"],
        },
    },
    {
        "name": "computer_shortcut",
        "description": (
            "Press a keyboard chord like 'ctrl+a' (keys joined with '+'). With ref the chord targets "
            "that element; without one it targets the focused element. Permission-gated, then the "
            "screen is re-read to verify. Returns performed separately from verified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "Chord to press, e.g. 'ctrl+a'."},
                "ref": {
                    "type": "string",
                    "description": "Element ref to target; without one, the focused element.",
                },
                "expect": _EXPECT_SCHEMA,
            },
            "required": ["keys"],
        },
    },
    {
        "name": "computer_scroll",
        "description": (
            "Scroll at one element by ref from the latest snapshot. Permission-gated, then the screen "
            "is re-read to verify: without an expect the result is only verified when the re-read "
            "shows a visible change, so pass expect (name, state, value, or value_contains) when the "
            "scroll should reveal something specific."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Element ref from computer_snapshot, e.g. 'n5'.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                    "default": "down",
                    "description": "Scroll direction (default down).",
                },
                "amount": {
                    "type": "integer",
                    "default": 3,
                    "description": "Scroll clicks (default 3).",
                },
                "expect": _EXPECT_SCHEMA,
            },
            "required": ["ref"],
        },
    },
    {
        "name": "computer_drag",
        "description": (
            "Drag the element at ref_from onto the element at ref_to. Permission-gated, then the "
            "drop target is re-read to verify. Returns performed separately from verified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref_from": {"type": "string", "description": "Element ref to drag, e.g. 'n7'."},
                "ref_to": {"type": "string", "description": "Element ref to drop onto, e.g. 'n8'."},
                "expect": _EXPECT_SCHEMA,
            },
            "required": ["ref_from", "ref_to"],
        },
    },
    {
        "name": "computer_clipboard_read",
        "description": (
            "Read the session clipboard. Permission-gated (the user is asked first). The content is "
            "returned to the calling agent only; it is never written to a log, a file, or the CLI."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "computer_clipboard_write",
        "description": (
            "Replace the session clipboard with the given text, then read it back to verify. "
            "Permission-gated (the user is asked first). The result reports only the length, never "
            "the content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to put on the clipboard."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "computer_list_windows",
        "description": (
            "List the open windows (id, app, title, focused). Read-only: never asks the user. "
            "Use the ids with computer_focus_window and computer_switch_window."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "computer_open_app",
        "description": (
            "Open an app by executable name (or desktop entry), then re-read the tree to verify a "
            "window for it appeared. Permission-gated. Returns performed separately from verified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "App to open, e.g. 'gedit'."},
                "expect": {
                    "type": "object",
                    "description": "What the new window should be: app (application name) or title.",
                    "properties": {
                        "app": {"type": "string"},
                        "title": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "computer_focus_window",
        "description": (
            "Bring one window to the front by id from computer_list_windows, then re-read the window "
            "list to verify it is the focused one. Permission-gated."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "window_id": {"type": "string", "description": "Window id, e.g. '0.1'."},
            },
            "required": ["window_id"],
        },
    },
    {
        "name": "computer_switch_window",
        "description": (
            "Switch to the next window after the focused one (cycling), then re-read to verify the "
            "focus moved. Permission-gated. Refused when only one window is open."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "computer_run_task",
        "description": (
            "Run a multi-step task end to end: a list of steps (type, shortcut, scroll, drag, click, "
            "verify, clipboard_read, clipboard_write, open_app, focus_window, switch_window, "
            "list_windows, snapshot), each with that action's arguments and an optional expect. "
            "Stops at the first step that did not perform or did not verify. Clipboard content never "
            "appears in the results."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Steps to run in order; each step is an object with an 'action' field.",
                    "items": {"type": "object"},
                },
            },
            "required": ["steps"],
        },
    },
    {
        "name": "computer_wait_settle",
        "description": (
            "Wait for the desktop accessibility tree to stop changing, then return the settle "
            "report (settled, snapshots read, elapsed_ms, total_nodes). The wait ends when the "
            "same tree comes back twice in a row, or when the timeout runs out: a screen that "
            "never goes quiet returns settled=false instead of acting on a moving target. "
            "Read-only: never clicks anything. Use it before computer_click when the app is "
            "still loading or animating."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout": {
                    "type": "number",
                    "default": 5,
                    "description": "Longest to wait for the tree to settle, in seconds.",
                },
                "stable_rounds": {
                    "type": "integer",
                    "default": 2,
                    "description": "Identical reads in a row that count as settled.",
                },
            },
            "required": [],
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
            "Click text on the screen by coordinates (the visual fallback). The words on the "
            "screen are waited on until they settle, then the text is located by OCR, the click "
            "is permission-gated (the user is asked first, and a token without the "
            "computer.click_at permission is refused), and the text is re-grounded right before "
            "pressing: when the text is gone, or its box moved or changed, the click is refused "
            "as stale and the old pixel is never pressed (a stale refusal is not a permission "
            "denial). After the click the screen is re-read to verify. Pass expect to say what "
            "should be true afterwards (text, or absent_text when the text should be gone). "
            "Returns performed (did the action run) separately from verified (did the re-read "
            "confirm a change)."
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
    """A fresh copy of the nineteen tool definitions."""
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
            "type, shortcuts, scroll, drag, clipboard, and window management, wait for the "
            "tree to settle before acting, fall back to screenshot + OCR when the "
            "tree is too poor, and drive web pages in headless Chromium."
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

    if name == "computer_type":
        ref = arguments.get("ref")
        text = arguments.get("text")
        if not isinstance(ref, str) or not ref:
            raise ValueError("computer_type needs a string 'ref'")
        if not isinstance(text, str):
            raise ValueError("computer_type needs a string 'text'")
        return hands.type_text(ref, text, expect=arguments.get("expect")).to_dict()
    if name == "computer_shortcut":
        keys = arguments.get("keys")
        if not isinstance(keys, str) or not keys:
            raise ValueError("computer_shortcut needs a string 'keys'")
        ref = arguments.get("ref")
        if ref is not None and not isinstance(ref, str):
            raise ValueError("computer_shortcut 'ref' must be a string")
        return hands.shortcut(keys, ref, expect=arguments.get("expect")).to_dict()
    if name == "computer_scroll":
        ref = arguments.get("ref")
        if not isinstance(ref, str) or not ref:
            raise ValueError("computer_scroll needs a string 'ref'")
        direction = arguments.get("direction", "down")
        if direction not in ("up", "down", "left", "right"):
            raise ValueError(
                f"computer_scroll direction must be up/down/left/right, got {direction!r}"
            )
        return hands.scroll(
            ref, direction, int(arguments.get("amount", 3)), expect=arguments.get("expect")
        ).to_dict()
    if name == "computer_drag":
        ref_from = arguments.get("ref_from")
        ref_to = arguments.get("ref_to")
        if not isinstance(ref_from, str) or not ref_from:
            raise ValueError("computer_drag needs a string 'ref_from'")
        if not isinstance(ref_to, str) or not ref_to:
            raise ValueError("computer_drag needs a string 'ref_to'")
        return hands.drag(ref_from, ref_to, expect=arguments.get("expect")).to_dict()
    if name == "computer_clipboard_read":
        clip = hands.clipboard_read()
        return {"length": clip.length, "content": clip.content}
    if name == "computer_clipboard_write":
        text = arguments.get("text")
        if not isinstance(text, str):
            raise ValueError("computer_clipboard_write needs a string 'text'")
        return hands.clipboard_write(text).to_dict()
    if name == "computer_list_windows":
        return {"windows": [window.to_dict() for window in hands.list_windows()]}
    if name == "computer_open_app":
        app_name = arguments.get("name")
        if not isinstance(app_name, str) or not app_name:
            raise ValueError("computer_open_app needs a string 'name'")
        return hands.open_app(app_name, expect=arguments.get("expect")).to_dict()
    if name == "computer_focus_window":
        window_id = arguments.get("window_id")
        if not isinstance(window_id, str) or not window_id:
            raise ValueError("computer_focus_window needs a string 'window_id'")
        return hands.focus_window(window_id).to_dict()
    if name == "computer_switch_window":
        return hands.switch_window().to_dict()
    if name == "computer_run_task":
        steps = arguments.get("steps")
        if not isinstance(steps, list):
            raise ValueError("computer_run_task needs a list 'steps'")
        return hands.run_task(steps)

    if name == "computer_wait_settle":
        try:
            timeout = float(arguments.get("timeout", 5))
            stable_rounds = int(arguments.get("stable_rounds", 2))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "computer_wait_settle needs a numeric 'timeout' and 'stable_rounds'"
            ) from exc
        try:
            shot, report = hands.wait_settle(timeout_s=timeout, stable_rounds=stable_rounds)
        except SettleTimeoutError as exc:
            data = exc.report.to_dict()
            data["total_nodes"] = -1
            return data
        data = report.to_dict()
        data["total_nodes"] = shot.total_nodes
        return data
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
