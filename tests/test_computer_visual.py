"""Tests for the visual fallback (V2-32): OCR grounding, screenshot
backends, the gated coordinate-click + re-read loop, and the
end-to-end task in an app with no accessibility tree.

No database, no network, no real desktop for the fake-backed
tests. One test drives the real X11 screenshot backend live under
a throwaway Xvfb server (skipped when Xvfb is missing); the
tesseract and XTest tests only cover the fail-closed half, because
neither binary is installed on this VM.
"""

import ctypes
import io
import os
import shutil
import subprocess
import time

import pytest

from puspa.computer.backend import FakeBackend
from puspa.computer.fallback import (
    TOOL_CLICK_AT,
    FakeCoordinateClicker,
    VisualFallback,
    VisualFallbackError,
    XTestClicker,
    make_clicker,
    make_ocr_backend,
)
from puspa.computer.hands import DesktopHands
from puspa.computer.screenshot import (
    FakeScreenshotBackend,
    X11ScreenshotBackend,
    make_screenshot_backend,
)
from puspa.computer.tree import Snapshot
from puspa.computer.vision import (
    FakeOcrBackend,
    OcrWord,
    TesseractBackend,
    UnavailableVisionBackend,
    VisualElement,
    _parse_tsv,
    ground_text,
    tree_is_poor,
)
from puspa.localexec import PermissionDecision, PermissionDenied
from puspa.policy.gate import Gate

pytestmark = pytest.mark.unit

APPROVE_ONCE = PermissionDecision.APPROVE_ONCE
APPROVE_SESSION = PermissionDecision.APPROVE_SESSION
DENY = PermissionDecision.DENY


class FakePrompt:
    """A prompt that answers from a queue and records every ask."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.asked = []

    def ask(self, command, cwd):
        self.asked.append(command)
        return self.decisions.pop(0)


def _png_bytes() -> bytes:
    from PIL import Image

    picture = Image.new("RGB", (800, 600), "white")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def _words():
    return [
        OcrWord(text="Save", x=100, y=200, width=60, height=24, confidence=0.9),
        OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.8),
        OcrWord(text="Untitled", x=100, y=100, width=80, height=20, confidence=0.7),
    ]


def _fallback(words=None, *, decisions=(APPROVE_ONCE,), on_click=None, **kwargs):
    ocr = FakeOcrBackend(words if words is not None else _words())
    clicker = FakeCoordinateClicker(on_click=on_click)
    if "prompt" not in kwargs:
        kwargs["prompt"] = FakePrompt(decisions)
    fallback = VisualFallback(
        screenshot_backend=FakeScreenshotBackend(_png_bytes()),
        ocr_backend=ocr,
        clicker=clicker,
        **kwargs,
    )
    return fallback, ocr, clicker


# ------------------------------------------------------------------
# OCR grounding
# ------------------------------------------------------------------


def test_ground_text_single_word_exact_and_case_insensitive():
    element = ground_text(_words(), "save")
    assert element is not None
    assert element.label == "Save"
    assert element.center == (130, 212)
    assert element.source == "ocr"


def test_ground_text_phrase_spans_neighbouring_words():
    words = [
        OcrWord(text="Save", x=10, y=20, width=40, height=10),
        OcrWord(text="file", x=55, y=20, width=30, height=10),
    ]
    element = ground_text(words, "save file")
    assert element is not None
    assert element.label == "Save file"
    assert (element.x, element.y, element.width, element.height) == (10, 20, 75, 10)


def test_ground_text_substring_and_missing():
    assert ground_text(_words(), "anc") is not None  # inside "Cancel"
    assert ground_text(_words(), "Delete") is None
    assert ground_text([], "Save") is None
    assert ground_text(_words(), "") is None


def test_fake_ocr_returns_its_words_and_records_images():
    ocr = FakeOcrBackend(_words())
    assert ocr.is_available()
    found = ocr.read(b"fake-png")
    assert [word.text for word in found] == ["Save", "Cancel", "Untitled"]
    assert ocr.words_seen == [b"fake-png"]
    ocr.set_words([])
    assert ocr.read(b"again") == []


def test_tesseract_backend_fails_closed_without_binary():
    backend = TesseractBackend()
    assert backend.is_available() == (shutil.which("tesseract") is not None)
    if shutil.which("tesseract") is None:
        assert not backend.is_available()
        with pytest.raises(VisualFallbackError, match="tesseract"):
            backend.read(_png_bytes())


def test_parse_tsv_reads_word_rows_only():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t100\t200\t60\t24\t90.0\tSave\n"
        "4\t1\t1\t1\t1\t0\t100\t200\t60\t24\t90.0\t\n"
        "5\t1\t1\t1\t1\t2\t300\t200\t70\t24\t80.0\tCancel\n"
    )
    words = _parse_tsv(tsv)
    assert [word.text for word in words] == ["Save", "Cancel"]
    assert words[0].center == (130, 212)
    assert words[0].confidence == pytest.approx(0.9)


def test_vision_model_seam_is_unavailable_and_plain():
    backend = UnavailableVisionBackend()
    assert not backend.is_available()
    with pytest.raises(VisualFallbackError, match="not wired up"):
        backend.ground(b"png", "the red button")


# ------------------------------------------------------------------
# Poor-tree decision
# ------------------------------------------------------------------


def test_tree_is_poor_for_empty_and_actionless_trees():
    assert tree_is_poor(Snapshot(nodes=()))
    poor = FakeBackend([]).snapshot()
    assert tree_is_poor(poor)
    # One app root with a named frame but no actions: still poor.
    spec = [
        {"role": "application", "name": "Canvas", "children": [{"role": "frame", "name": "Game"}]}
    ]
    assert tree_is_poor(FakeBackend(spec).snapshot())


def test_tree_is_not_poor_when_a_click_exists():
    spec = [
        {
            "role": "application",
            "name": "Notes",
            "children": [
                {"role": "push button", "name": "Save", "actions": ["click"]},
            ],
        }
    ]
    assert not tree_is_poor(FakeBackend(spec).snapshot())


# ------------------------------------------------------------------
# Screenshot backends
# ------------------------------------------------------------------


def test_fake_screenshot_returns_its_bytes():
    backend = FakeScreenshotBackend(_png_bytes(), width=800, height=600)
    assert backend.is_available()
    shot = backend.capture()
    assert shot.width == 800 and shot.height == 600
    assert len(shot.png) > 0
    assert backend.captures == 1


def test_x11_backend_fails_closed_without_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    backend = X11ScreenshotBackend()
    assert not backend.is_available()
    with pytest.raises(VisualFallbackError, match="DISPLAY is unset"):
        backend.capture()
    with pytest.raises(VisualFallbackError, match="DISPLAY is unset"):
        make_screenshot_backend("auto")


def test_make_screenshot_backend_unknown_kind():
    with pytest.raises(VisualFallbackError, match="unknown screenshot backend"):
        make_screenshot_backend("nope")


def test_make_ocr_backend_and_clicker_fail_closed_when_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(VisualFallbackError, match="tesseract"):
        make_ocr_backend("auto")
    with pytest.raises(VisualFallbackError, match="unknown OCR backend"):
        make_ocr_backend("nope")
    with pytest.raises(VisualFallbackError, match="unknown clicker"):
        make_clicker("nope")


def test_xtest_clicker_fails_closed_without_libxtst():
    clicker = XTestClicker()
    if shutil.which("xdotool") is None:
        import ctypes.util

        if ctypes.util.find_library("Xtst") is None:
            assert not clicker.is_available()
            with pytest.raises(VisualFallbackError, match="libXtst"):
                clicker.click_at(10, 10)


@pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed")
def test_x11_screenshot_live_under_xvfb(tmp_path):
    """Real X11 capture: start Xvfb, draw a red rectangle in a
    test window with raw Xlib, capture the root window, and check
    the pixels. This is the live half of the X11 backend."""
    display = ":99"
    env = dict(os.environ, DISPLAY=display)
    server = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "800x600x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        lib = ctypes.CDLL("libX11.so.6")
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XCreateSimpleWindow.restype = ctypes.c_ulong
        lib.XCreateSimpleWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        lib.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        lib.XCreateGC.restype = ctypes.c_void_p
        lib.XCreateGC.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        lib.XSetForeground.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        lib.XFillRectangle.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        deadline = time.time() + 10
        opened = None
        while time.time() < deadline:
            opened = lib.XOpenDisplay(display.encode())
            if opened:
                break
            time.sleep(0.2)
        assert opened, "Xvfb did not come up"
        root = lib.XDefaultRootWindow(opened)
        win = lib.XCreateSimpleWindow(opened, root, 100, 80, 200, 120, 0, 0, 0xFFFFFF)
        lib.XMapWindow(opened, win)
        gc = lib.XCreateGC(opened, win, 0, None)
        lib.XSetForeground(opened, gc, 0xFF0000)
        lib.XFillRectangle(opened, win, gc, 0, 0, 200, 120)
        lib.XFlush(opened)
        time.sleep(0.5)
        # The window belongs to this connection: closing the
        # display first would make the server destroy it, so the
        # capture runs while the connection is still open.
        backend = X11ScreenshotBackend()
        old_display = os.environ.get("DISPLAY")
        os.environ["DISPLAY"] = display
        try:
            assert backend.is_available()
            shot = backend.capture()
        finally:
            if old_display is None:
                os.environ.pop("DISPLAY", None)
            else:
                os.environ["DISPLAY"] = old_display
        lib.XCloseDisplay(opened)
        assert (shot.width, shot.height) == (800, 600)
        assert len(shot.png) > 0
        from PIL import Image

        picture = Image.open(io.BytesIO(shot.png))
        assert picture.size == (800, 600)
        assert picture.getpixel((200, 140)) == (255, 0, 0)
    finally:
        server.terminate()
        server.wait(timeout=10)


# ------------------------------------------------------------------
# The gated coordinate-click loop
# ------------------------------------------------------------------


def test_locate_text_reads_the_screen():
    fallback, _, _ = _fallback()
    element = fallback.locate_text("Cancel")
    assert element is not None
    assert element.label == "Cancel"
    assert fallback.locate_text("Delete") is None


def test_click_text_verifies_by_reread_when_words_change():
    ocr_after = [
        OcrWord(text="Saved", x=100, y=200, width=60, height=24, confidence=0.9),
        OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.8),
    ]

    def swap(x, y):
        if 100 <= x < 160 and 200 <= y < 224:
            ocr.set_words(ocr_after)

    fallback, ocr, clicker = _fallback(on_click=swap)
    result = fallback.click_text("Save")
    assert result.performed and result.verified
    assert result.label == "Save"
    assert (result.x, result.y) == (130, 212)
    assert clicker.clicks == [(130, 212)]
    assert "Saved" in result.after


def test_click_text_not_verified_when_screen_unchanged():
    fallback, _, clicker = _fallback()
    result = fallback.click_text("Save")
    assert result.performed and not result.verified
    assert "unchanged" in result.reason
    assert clicker.clicks == [(130, 212)]


def test_click_text_expect_text_and_absent_text():
    fallback, ocr, _ = _fallback(decisions=(APPROVE_ONCE, APPROVE_ONCE, APPROVE_ONCE))
    ocr.set_words([OcrWord(text="Saved", x=100, y=200, width=60, height=24, confidence=0.9)])
    # The screen already says Saved, so expect=text verifies.
    result = fallback.click_text("Saved", expect={"text": "Saved"})
    assert result.performed and result.verified
    result = fallback.click_text("Saved", expect={"text": "Missing"})
    assert result.performed and not result.verified
    # "Cancel" is not on the screen at all, so absent_text verifies.
    result = fallback.click_text("Saved", expect={"absent_text": "Cancel"})
    assert result.performed and result.verified


def test_click_text_missing_text_raises():
    fallback, _, _ = _fallback()
    with pytest.raises(VisualFallbackError, match="cannot locate"):
        fallback.click_text("Delete")


def test_click_text_prompt_denial_touches_nothing():
    fallback, _, clicker = _fallback(decisions=(DENY,))
    with pytest.raises(PermissionDenied, match="denied by the user"):
        fallback.click_text("Save")
    assert clicker.clicks == []


def test_click_text_gate_denies_coordinate_clicks():
    gate = Gate()
    payload = {"tool_permissions": {TOOL_CLICK_AT: "deny"}}
    fallback, _, clicker = _fallback(gate=gate, token_payload=payload)
    with pytest.raises(PermissionDenied, match="permission gate"):
        fallback.click_text("Save")
    assert clicker.clicks == []


def test_click_text_gate_allows_when_granted():
    gate = Gate()
    payload = {"tool_permissions": {TOOL_CLICK_AT: "allow"}}
    fallback, _, clicker = _fallback(gate=gate, token_payload=payload)
    result = fallback.click_text("Save")
    assert result.performed
    assert clicker.clicks == [(130, 212)]


def test_computer_click_grant_does_not_cover_coordinate_clicks():
    """A token that only grants computer.click must NOT grant a
    coordinate click: the tool names are deliberately different."""
    gate = Gate()
    payload = {"tool_permissions": {"computer.click": "allow"}}
    fallback, _, clicker = _fallback(gate=gate, token_payload=payload)
    with pytest.raises(PermissionDenied, match="permission gate"):
        fallback.click_text("Save")
    assert clicker.clicks == []


def test_click_text_session_approval_asks_once():
    prompt = FakePrompt((APPROVE_SESSION,))
    fallback, _, clicker = _fallback(prompt=prompt)
    fallback.click_text("Save")
    fallback.click_text("Cancel")
    assert len(prompt.asked) == 1
    assert len(clicker.clicks) == 2


def test_visual_element_to_dict_shape():
    element = VisualElement(label="Save", x=10, y=20, width=40, height=10, source="ocr")
    data = element.to_dict()
    assert data["label"] == "Save"
    assert data["center"] == {"x": 30, "y": 25}
    assert data["source"] == "ocr"


# ------------------------------------------------------------------
# End to end: an app with no accessibility tree
# ------------------------------------------------------------------


def _empty_tree_hands():
    return DesktopHands(FakeBackend([]))


def test_complete_task_in_app_with_no_tree():
    ocr_words = [
        OcrWord(text="Play", x=50, y=50, width=40, height=20, confidence=0.9),
    ]
    ocr = FakeOcrBackend(ocr_words)
    after = [OcrWord(text="Pause", x=50, y=50, width=40, height=20, confidence=0.9)]

    def swap(x, y):
        ocr.set_words(after)

    fallback = VisualFallback(
        _empty_tree_hands(),
        screenshot_backend=FakeScreenshotBackend(_png_bytes()),
        ocr_backend=ocr,
        clicker=FakeCoordinateClicker(on_click=swap),
        prompt=FakePrompt((APPROVE_ONCE,)),
    )
    assert fallback.tree_needs_visual()
    result = fallback.complete_task("Play", expect={"text": "Pause"})
    assert result.performed and result.verified
    assert result.label == "Play"
    assert result.after == "Pause"


def test_complete_task_refuses_when_tree_is_usable():
    spec = [
        {
            "role": "application",
            "name": "Notes",
            "children": [{"role": "push button", "name": "Save", "actions": ["click"]}],
        }
    ]
    fallback = VisualFallback(
        DesktopHands(FakeBackend(spec)),
        screenshot_backend=FakeScreenshotBackend(_png_bytes()),
        ocr_backend=FakeOcrBackend(_words()),
        clicker=FakeCoordinateClicker(),
        prompt=FakePrompt((APPROVE_ONCE,)),
    )
    assert not fallback.tree_needs_visual()
    with pytest.raises(VisualFallbackError, match="tree is usable"):
        fallback.complete_task("Save")


def test_complete_task_without_hands_goes_visual():
    ocr = FakeOcrBackend([OcrWord(text="Go", x=0, y=0, width=10, height=10)])
    fallback = VisualFallback(
        None,
        screenshot_backend=FakeScreenshotBackend(_png_bytes()),
        ocr_backend=ocr,
        clicker=FakeCoordinateClicker(),
        prompt=FakePrompt((APPROVE_ONCE,)),
    )
    result = fallback.complete_task("Go")
    assert result.performed and not result.verified  # fake screen never changes
