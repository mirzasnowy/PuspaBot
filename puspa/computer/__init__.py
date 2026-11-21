"""Desktop hands: snapshot, click, verify (V2-31, core).

This package is new in V2-31: no ``puspa/computer/local.py``
existed at this base, so the module was built from scratch here
instead of extending one. The local runner it stands next to is
:mod:`puspa.localexec` (subprocess commands, not the desktop).
"""

from .backend import AtspiBackend, BackendUnavailableError, FakeBackend, make_backend
from .hands import ClickResult, DesktopHands, DesktopHandsError, UnknownRefError, Verification
from .mcp import handle_tool, mcp_server_definition, tool_definitions
from .tree import AccessibleNode, Snapshot

__all__ = [
    "AccessibleNode",
    "AtspiBackend",
    "BackendUnavailableError",
    "ClickResult",
    "DesktopHands",
    "DesktopHandsError",
    "FakeBackend",
    "Snapshot",
    "UnknownRefError",
    "Verification",
    "handle_tool",
    "make_backend",
    "mcp_server_definition",
    "tool_definitions",
]
