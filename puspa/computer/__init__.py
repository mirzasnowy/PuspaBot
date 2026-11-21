"""Desktop hands: snapshot, click, verify (V2-31, core), plus the
visual fallback (V2-32): when the accessibility tree is empty or
too poor, see the screen with a screenshot + OCR and click by
coordinates, or route a web target to headless Chromium.

This package is new in V2-31: no ``puspa/computer/local.py``
existed at this base, so the module was built from scratch here
instead of extending one. The local runner it stands next to is
:mod:`puspa.localexec` (subprocess commands, not the desktop).
"""

from .backend import AtspiBackend, BackendUnavailableError, FakeBackend, make_backend
from .fallback import (
    TOOL_CLICK_AT,
    CoordinateClicker,
    FakeCoordinateClicker,
    VisualClickResult,
    VisualFallback,
    XTestClicker,
    make_clicker,
    make_ocr_backend,
)
from .gating import ask_user, check_token_gate
from .hands import ClickResult, DesktopHands, DesktopHandsError, UnknownRefError, Verification
from .mcp import handle_tool, mcp_server_definition, tool_definitions
from .screenshot import (
    FakeScreenshotBackend,
    Screenshot,
    ScreenshotBackend,
    X11ScreenshotBackend,
    make_screenshot_backend,
)
from .tree import AccessibleNode, Snapshot
from .vision import (
    FakeOcrBackend,
    OcrBackend,
    OcrWord,
    TesseractBackend,
    UnavailableVisionBackend,
    VisionModelBackend,
    VisualElement,
    VisualFallbackError,
    ground_text,
    tree_is_poor,
)
from .web import (
    TOOL_WEB_CLICK,
    FakeWebBackend,
    PlaywrightWebBackend,
    WebBackend,
    WebBridge,
    WebClickResult,
    WebPage,
    is_web_target,
)

__all__ = [
    "TOOL_CLICK_AT",
    "TOOL_WEB_CLICK",
    "AccessibleNode",
    "AtspiBackend",
    "BackendUnavailableError",
    "ClickResult",
    "CoordinateClicker",
    "DesktopHands",
    "DesktopHandsError",
    "FakeBackend",
    "FakeCoordinateClicker",
    "FakeOcrBackend",
    "FakeScreenshotBackend",
    "FakeWebBackend",
    "OcrBackend",
    "OcrWord",
    "PlaywrightWebBackend",
    "Screenshot",
    "ScreenshotBackend",
    "Snapshot",
    "TesseractBackend",
    "UnavailableVisionBackend",
    "UnknownRefError",
    "Verification",
    "VisionModelBackend",
    "VisualClickResult",
    "VisualElement",
    "VisualFallback",
    "VisualFallbackError",
    "WebBackend",
    "WebBridge",
    "WebClickResult",
    "WebPage",
    "X11ScreenshotBackend",
    "XTestClicker",
    "ask_user",
    "check_token_gate",
    "ground_text",
    "handle_tool",
    "is_web_target",
    "make_backend",
    "make_clicker",
    "make_ocr_backend",
    "make_screenshot_backend",
    "mcp_server_definition",
    "tool_definitions",
    "tree_is_poor",
]
