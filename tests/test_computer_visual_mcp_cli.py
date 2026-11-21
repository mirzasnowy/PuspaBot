"""Tests for the V2-32 MCP tools and CLI commands (visual fallback
+ web bridge). The CLI tests run on the fake backends through the
factories ``run_computer`` accepts; the fake prompt approves every
click, so no user input is needed.
"""

import argparse
import io
import json

import pytest

from puspa.computer.cli import add_computer_parser, run_computer
from puspa.computer.fallback import FakeCoordinateClicker, VisualFallback
from puspa.computer.mcp import handle_tool, mcp_server_definition, tool_definitions
from puspa.computer.screenshot import FakeScreenshotBackend
from puspa.computer.vision import FakeOcrBackend, OcrWord
from puspa.computer.web import FakeWebBackend, WebBridge
from puspa.localexec import PermissionDecision
from puspa.plugins.mcp import validate_mcp_definition

pytestmark = pytest.mark.unit

APPROVE_ONCE = PermissionDecision.APPROVE_ONCE


class FakePrompt:
    """A prompt that always approves once."""

    def ask(self, command, cwd):
        return APPROVE_ONCE


def _png_bytes() -> bytes:
    from PIL import Image

    picture = Image.new("RGB", (800, 600), "white")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def _fallback():
    ocr = FakeOcrBackend(
        [
            OcrWord(text="Save", x=100, y=200, width=60, height=24, confidence=0.9),
            OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.9),
        ]
    )

    def swap(x, y):
        if 100 <= x < 160 and 200 <= y < 224:
            ocr.set_words(
                [
                    OcrWord(text="Saved", x=100, y=200, width=60, height=24, confidence=0.9),
                    OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.9),
                ]
            )

    return VisualFallback(
        screenshot_backend=FakeScreenshotBackend(_png_bytes()),
        ocr_backend=ocr,
        clicker=FakeCoordinateClicker(on_click=swap),
        prompt=FakePrompt(),
    )


def _web():
    pages = {
        "https://example.com": {
            "title": "Example Domain",
            "elements": [
                {
                    "label": "Save",
                    "x": 100,
                    "y": 200,
                    "width": 60,
                    "height": 24,
                    "after_click": {"label": "Saved"},
                },
                {"label": "Cancel", "x": 300, "y": 200, "width": 70, "height": 24},
            ],
        }
    }
    return WebBridge(FakeWebBackend(pages), prompt=FakePrompt())


# ------------------------------------------------------------------
# MCP definitions
# ------------------------------------------------------------------


def test_tool_definitions_cover_all_eighteen_tools():
    tools = tool_definitions()
    names = [tool["name"] for tool in tools]
    assert names == [
        "computer_snapshot",
        "computer_click",
        "computer_verify",
        "computer_type",
        "computer_shortcut",
        "computer_scroll",
        "computer_drag",
        "computer_clipboard_read",
        "computer_clipboard_write",
        "computer_list_windows",
        "computer_open_app",
        "computer_focus_window",
        "computer_switch_window",
        "computer_run_task",
        "computer_visual_locate",
        "computer_visual_click",
        "computer_web_open",
        "computer_web_click",
    ]
    by_name = {tool["name"]: tool for tool in tools}
    assert by_name["computer_visual_locate"]["inputSchema"]["required"] == ["text"]
    assert by_name["computer_visual_click"]["inputSchema"]["required"] == ["text"]
    assert by_name["computer_web_open"]["inputSchema"]["required"] == ["url"]
    assert by_name["computer_web_click"]["inputSchema"]["required"] == ["text"]
    # The first three V2-31 definitions are untouched.
    assert by_name["computer_click"]["inputSchema"]["required"] == ["ref"]


def test_mcp_server_definition_validates_with_eighteen_tools():
    definition = mcp_server_definition()
    assert len(definition["tools"]) == 18
    validate_mcp_definition(definition)


def test_handle_visual_locate_and_click():
    from puspa.computer.backend import FakeBackend
    from puspa.computer.hands import DesktopHands

    hands = DesktopHands(FakeBackend([]))
    fallback = _fallback()
    found = handle_tool("computer_visual_locate", {"text": "Save"}, hands, fallback=fallback)
    assert found["found"] is True
    assert found["label"] == "Save"
    assert found["center"] == {"x": 130, "y": 212}
    missing = handle_tool("computer_visual_locate", {"text": "Delete"}, hands, fallback=fallback)
    assert missing == {"text": "Delete", "found": False}
    clicked = handle_tool(
        "computer_visual_click",
        {"text": "Save", "expect": {"text": "Saved"}},
        hands,
        fallback=fallback,
    )
    assert clicked["performed"] and clicked["verified"]


def test_handle_visual_tools_need_a_fallback():
    from puspa.computer.backend import FakeBackend
    from puspa.computer.hands import DesktopHands

    hands = DesktopHands(FakeBackend([]))
    with pytest.raises(ValueError, match="needs a VisualFallback"):
        handle_tool("computer_visual_locate", {"text": "Save"}, hands)
    with pytest.raises(ValueError, match="needs a VisualFallback"):
        handle_tool("computer_visual_click", {"text": "Save"}, hands)
    with pytest.raises(ValueError, match="needs a string 'text'"):
        handle_tool("computer_visual_click", {}, hands, fallback=_fallback())


def test_handle_web_open_and_click():
    from puspa.computer.backend import FakeBackend
    from puspa.computer.hands import DesktopHands

    hands = DesktopHands(FakeBackend([]))
    web = _web()
    opened = handle_tool("computer_web_open", {"url": "https://example.com"}, hands, web=web)
    assert opened == {"url": "https://example.com", "title": "Example Domain"}
    clicked = handle_tool(
        "computer_web_click",
        {"text": "Save", "expect": {"text": "Saved"}},
        hands,
        web=web,
    )
    assert clicked["performed"] and clicked["verified"]
    web.close()


def test_handle_web_tools_need_a_bridge():
    from puspa.computer.backend import FakeBackend
    from puspa.computer.hands import DesktopHands

    hands = DesktopHands(FakeBackend([]))
    with pytest.raises(ValueError, match="needs a WebBridge"):
        handle_tool("computer_web_open", {"url": "https://example.com"}, hands)
    with pytest.raises(ValueError, match="needs a WebBridge"):
        handle_tool("computer_web_click", {"text": "Save"}, hands)
    with pytest.raises(ValueError, match="needs a string 'url'"):
        handle_tool("computer_web_open", {}, hands, web=_web())


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------


def _parse(argv):
    parser = argparse.ArgumentParser(prog="puspa")
    subparsers = parser.add_subparsers(dest="command")
    add_computer_parser(subparsers)
    args = parser.parse_args(argv)
    args.backend = getattr(args, "backend", "auto")
    return args


def _run(argv, **factories):
    out: list[str] = []
    code = run_computer(_parse(argv), print_fn=out.append, **factories)
    return code, out


def test_cli_visual_locate_fake():
    code, out = _run(
        ["computer", "visual", "locate", "Save"],
        fallback_factory=lambda kind: _fallback(),
    )
    assert code == 0
    data = json.loads(out[0])
    assert data["label"] == "Save"
    assert data["center"] == {"x": 130, "y": 212}


def test_cli_visual_locate_missing_returns_1():
    code, out = _run(
        ["computer", "visual", "locate", "Delete"],
        fallback_factory=lambda kind: _fallback(),
    )
    assert code == 1
    assert "not found" in out[0]


def test_cli_visual_click_fake_verifies():
    code, out = _run(
        ["computer", "visual", "click", "Save", "--expect-text", "Saved"],
        fallback_factory=lambda kind: _fallback(),
    )
    assert code == 0
    data = json.loads(out[0])
    assert data["performed"] and data["verified"]


def test_cli_visual_click_unverified_returns_1():
    code, out = _run(
        ["computer", "visual", "click", "Cancel"],
        fallback_factory=lambda kind: _fallback(),
    )
    assert code == 1
    data = json.loads(out[0])
    assert data["performed"] and not data["verified"]


def test_cli_web_find_and_click_fake():
    code, out = _run(
        ["computer", "web", "find", "https://example.com", "Save"],
        web_factory=lambda kind: _web(),
    )
    assert code == 0
    data = json.loads(out[0])
    assert data["label"] == "Save"
    assert data["source"] == "dom"

    code, out = _run(
        ["computer", "web", "click", "https://example.com", "Save", "--expect-text", "Saved"],
        web_factory=lambda kind: _web(),
    )
    assert code == 0
    data = json.loads(out[0])
    assert data["performed"] and data["verified"]


def test_cli_web_open_fake():
    code, out = _run(
        ["computer", "web", "open", "https://example.com"],
        web_factory=lambda kind: _web(),
    )
    assert code == 0
    data = json.loads(out[0])
    assert data["title"] == "Example Domain"
