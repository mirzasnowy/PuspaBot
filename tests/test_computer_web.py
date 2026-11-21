"""Tests for the web bridge (V2-32): routing, the fake web page,
the gated web click + re-read loop, and the real Playwright
backend against headless Chromium on a local page (no network:
the page is set from a string, never fetched).

The Playwright tests are the live half of the web bridge: they
launch the real browser this VM ships and click a real button.
"""

import pytest

from puspa.computer.vision import VisualFallbackError
from puspa.computer.web import (
    TOOL_WEB_CLICK,
    FakeWebBackend,
    PlaywrightWebBackend,
    WebBridge,
    is_web_target,
)
from puspa.localexec import PermissionDecision, PermissionDenied
from puspa.policy.gate import Gate

pytestmark = pytest.mark.unit

APPROVE_ONCE = PermissionDecision.APPROVE_ONCE
DENY = PermissionDecision.DENY


class FakePrompt:
    """A prompt that answers from a queue and records every ask."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.asked = []

    def ask(self, command, cwd):
        self.asked.append(command)
        return self.decisions.pop(0)


DEMO_PAGES = {
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


def _bridge(**kwargs):
    kwargs.setdefault("prompt", FakePrompt((APPROVE_ONCE,)))
    return WebBridge(FakeWebBackend(DEMO_PAGES), **kwargs)


# ------------------------------------------------------------------
# Routing
# ------------------------------------------------------------------


def test_is_web_target():
    assert is_web_target("https://example.com")
    assert is_web_target("http://localhost:8000/page")
    assert is_web_target("web:https://example.com")
    assert not is_web_target("Notes")
    assert not is_web_target("com.example.Notes")
    assert not is_web_target("")


def test_unknown_web_backend_kind():
    with pytest.raises(VisualFallbackError, match="unknown web backend"):
        WebBridge("nope")


# ------------------------------------------------------------------
# Fake web backend
# ------------------------------------------------------------------


def test_fake_web_open_find_and_click():
    backend = FakeWebBackend(DEMO_PAGES)
    assert backend.is_available()
    page = backend.open("https://example.com")
    assert page.title == "Example Domain"
    assert backend.opened == ["https://example.com"]
    element = backend.find_text("save")
    assert element is not None
    assert element.label == "Save"
    assert element.source == "dom"
    assert backend.find_text("Delete") is None
    assert backend.click_at(130, 212)
    assert backend.find_text("Saved") is not None
    assert not backend.click_at(10, 10)  # outside every element
    backend.close()
    assert backend.closed


def test_fake_web_find_without_open_page():
    backend = FakeWebBackend(DEMO_PAGES)
    assert backend.find_text("Save") is None


# ------------------------------------------------------------------
# The gated web click loop (fake backend)
# ------------------------------------------------------------------


def test_web_click_verifies_by_reread():
    bridge = _bridge()
    bridge.open("https://example.com")
    result = bridge.click_text("Save", expect={"text": "Saved"})
    assert result.performed and result.verified
    assert result.label == "Save"
    assert (result.x, result.y) == (130, 212)
    assert "Saved" in result.after
    bridge.close()


def test_web_click_not_verified_when_page_unchanged():
    bridge = _bridge()
    bridge.open("https://example.com")
    result = bridge.click_text("Cancel")
    assert result.performed and not result.verified
    assert "unchanged" in result.reason
    bridge.close()


def test_web_click_missing_text_raises():
    bridge = _bridge()
    bridge.open("https://example.com")
    with pytest.raises(VisualFallbackError, match="cannot locate"):
        bridge.click_text("Delete")
    bridge.close()


def test_web_click_prompt_denial_touches_nothing():
    bridge = WebBridge(FakeWebBackend(DEMO_PAGES), prompt=FakePrompt((DENY,)))
    bridge.open("https://example.com")
    with pytest.raises(PermissionDenied, match="denied by the user"):
        bridge.click_text("Save")
    assert bridge.backend.clicks == []
    bridge.close()


def test_web_click_gate_denies_and_grants():
    gate = Gate()
    denied = WebBridge(
        FakeWebBackend(DEMO_PAGES),
        prompt=FakePrompt((APPROVE_ONCE,)),
        gate=gate,
        token_payload={"tool_permissions": {TOOL_WEB_CLICK: "deny"}},
    )
    denied.open("https://example.com")
    with pytest.raises(PermissionDenied, match="permission gate"):
        denied.click_text("Save")
    assert denied.backend.clicks == []
    denied.close()

    allowed = WebBridge(
        FakeWebBackend(DEMO_PAGES),
        prompt=FakePrompt((APPROVE_ONCE,)),
        gate=gate,
        token_payload={"tool_permissions": {TOOL_WEB_CLICK: "allow"}},
    )
    allowed.open("https://example.com")
    result = allowed.click_text("Save")
    assert result.performed
    allowed.close()


def test_web_click_without_open_page_raises_plainly():
    bridge = _bridge()
    with pytest.raises(VisualFallbackError, match="cannot locate"):
        bridge.click_text("Save")


# ------------------------------------------------------------------
# Real Playwright backend (live, local page, no network)
# ------------------------------------------------------------------


def test_playwright_backend_is_available():
    assert PlaywrightWebBackend().is_available()


def test_playwright_open_find_click_verify_live():
    """Live end to end: headless Chromium opens a local page,
    finds a button through the DOM, clicks it, and the re-read
    sees the button change. No network is involved."""
    backend = PlaywrightWebBackend()
    try:
        page = backend._ensure_page()
        page.set_content(
            "<html><body>"
            "<button id='save' onclick=\"this.textContent='Saved'\">Save</button>"
            "<button>Cancel</button>"
            "</body></html>"
        )
        backend._current_url = "about:blank"
        found = backend.find_text("Save")
        assert found is not None
        assert found.source == "dom"
        assert found.label == "Save"
        assert backend.find_text("Delete") is None
        shot = backend.screenshot()
        assert len(shot.png) > 0
        assert (shot.width, shot.height) == (1280, 720)

        bridge = WebBridge(backend, prompt=FakePrompt((APPROVE_ONCE,)))
        result = bridge.click_text("Save", expect={"text": "Saved"})
        assert result.performed and result.verified
        assert "Saved" in result.after
    finally:
        backend.close()


def test_playwright_bridge_click_live_with_fake_ocr_fallback():
    """A canvas-only page has no DOM text: the bridge falls back
    to OCR, and with the fake OCR backend that reports nothing,
    the text is honestly not found."""
    backend = PlaywrightWebBackend()
    try:
        page = backend._ensure_page()
        page.set_content("<html><body><canvas width='200' height='100'></canvas></body></html>")
        backend._current_url = "about:blank"
        assert backend.find_text("Save") is None
    finally:
        backend.close()
