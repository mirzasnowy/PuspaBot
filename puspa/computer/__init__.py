"""Desktop hands: snapshot, click, verify (V2-31, core), plus
full input and window management (V2-33), plus the visual fallback
(V2-32): when the accessibility tree is empty or too poor, see the
screen with a screenshot + OCR and click by coordinates, or route a
web target to headless Chromium. V2-34 adds synchronization: wait
for the tree (or the OCR words) to settle before acting, and
re-resolve every ref (or re-ground every OCR target) right before
acting, refusing stale targets instead of mis-clicking them.

This package is new in V2-31: no ``puspa/computer/local.py``
existed at this base, so the module was built from scratch here
instead of extending one. The local runner it stands next to is
:mod:`puspa.localexec` (subprocess commands, not the desktop).

V2-35 adds tiers (:mod:`puspa.computer.tiers`), the per-app
allowlist (:mod:`puspa.computer.allowlist`), the emergency stop
(:mod:`puspa.computer.stop`), and the ledger audit sink
(:mod:`puspa.computer.audit`).
"""

from .allowlist import Allowlist, default_allowlist_path
from .audit import InMemoryComputerAudit, LedgerComputerAudit, audit_entry
from .backend import AtspiBackend, BackendUnavailableError, FakeBackend, make_backend
from .fallback import (
    TOOL_CLICK_AT,
    CoordinateClicker,
    FakeCoordinateClicker,
    StaleVisualTargetError,
    VisualClickResult,
    VisualFallback,
    XTestClicker,
    make_clicker,
    make_ocr_backend,
)
from .gating import ask_user, check_token_gate
from .hands import (
    ClickResult,
    DesktopHands,
    DesktopHandsError,
    StaleRefError,
    UnknownRefError,
    Verification,
)
from .input import (
    ClipboardResult,
    FakeInputBackend,
    InputResult,
    XTestInputBackend,
    make_input_backend,
)
from .mcp import handle_tool, mcp_server_definition, tool_definitions
from .screenshot import (
    FakeScreenshotBackend,
    Screenshot,
    ScreenshotBackend,
    X11ScreenshotBackend,
    make_screenshot_backend,
)
from .stop import ComputerStop, ComputerStopped, default_stop_path
from .sync import (
    ElementIdentity,
    SettleReport,
    SettleTimeoutError,
    fingerprint,
    tree_signature,
    visual_fingerprint,
    wait_for_settle,
    words_signature,
)
from .tiers import Tier, classify, is_submit_shortcut
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
    "Allowlist",
    "AtspiBackend",
    "AtspiWindowBackend",
    "BackendUnavailableError",
    "ClickResult",
    "ClipboardResult",
    "ComputerStop",
    "ComputerStopped",
    "CoordinateClicker",
    "DesktopHands",
    "DesktopHandsError",
    "ElementIdentity",
    "FakeBackend",
    "FakeCoordinateClicker",
    "FakeInputBackend",
    "FakeOcrBackend",
    "FakeScreenshotBackend",
    "FakeWebBackend",
    "FakeWindowBackend",
    "InMemoryComputerAudit",
    "InputResult",
    "LedgerComputerAudit",
    "OcrBackend",
    "OcrWord",
    "PlaywrightWebBackend",
    "Screenshot",
    "ScreenshotBackend",
    "SettleReport",
    "SettleTimeoutError",
    "Snapshot",
    "StaleRefError",
    "StaleVisualTargetError",
    "TesseractBackend",
    "Tier",
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
    "WindowInfo",
    "WindowResult",
    "X11ScreenshotBackend",
    "XTestClicker",
    "XTestInputBackend",
    "ask_user",
    "audit_entry",
    "check_token_gate",
    "classify",
    "default_allowlist_path",
    "default_stop_path",
    "fingerprint",
    "ground_text",
    "handle_tool",
    "is_submit_shortcut",
    "is_web_target",
    "make_backend",
    "make_clicker",
    "make_input_backend",
    "make_ocr_backend",
    "make_screenshot_backend",
    "make_window_backend",
    "mcp_server_definition",
    "tool_definitions",
    "tree_is_poor",
    "tree_signature",
    "visual_fingerprint",
    "wait_for_settle",
    "words_signature",
]
