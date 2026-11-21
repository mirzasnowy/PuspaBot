"""Full input for Puspa desktop hands (V2-33).

Typing, keyboard shortcuts, scroll, drag, and clipboard, all
through the same V2-31 discipline: every action is permission
gated (see :mod:`puspa.computer.hands`), then the screen is
re-read to verify. A successful action never proves the UI
changed.

Two backends implement :class:`InputBackend`:

- :class:`FakeInputBackend`: an in-memory model sharing the
  fake tree spec with :class:`~puspa.computer.backend.FakeBackend`,
  so typed text, shortcut effects, scroll positions, drag
  effects, and clipboard contents are visible in the next
  snapshot. This is what the tests run on.
- :class:`XTestInputBackend`: the real Linux backend. Typing
  goes through the AT-SPI editable-text interface of the target
  element (the same library V2-31 already binds, no new
  dependency); keys, scroll, and drag go through XTest
  (``libXtst`` + ``libX11``), the standard X11 input-injection
  path; clipboard goes through the session clipboard tools
  (``xclip`` / ``xsel`` on X11, ``wl-copy`` / ``wl-paste`` on
  Wayland). Every one of those paths fails closed with
  :class:`~puspa.computer.backend.BackendUnavailableError` when
  its piece is missing: no display, no libXtst, no clipboard
  tool, or an element with no editable-text interface. Nothing
  here ever pretends an action worked.

Clipboard privacy: clipboard content is returned to the calling
agent only, never written to a log, a file, or stdout, and never
included in a result dict. Results report only the length and
whether a write read back equal. The permission prompt for a
clipboard write or a type shows the length of the text, never
the text itself, so a password typed through the agent is not
echoed into the terminal.

Windows and macOS are NOT built here (Linux first, per the
issue). :func:`make_input_backend` raises for them, so a future
backend plugs into the same seam.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from .backend import (
    BackendUnavailableError,
    FakeBackend,
    _find_spec,
    _load_atspi_libs,
    find_live_accessible,
)
from .tree import AccessibleNode

__all__ = [
    "ClipboardResult",
    "FakeInputBackend",
    "InputBackend",
    "InputResult",
    "XTestInputBackend",
    "make_input_backend",
]

#: Scroll directions the backends understand.
SCROLL_DIRECTIONS = ("up", "down", "left", "right")

#: X11 button numbers for scrolling (XTestFakeButtonEvent).
_SCROLL_BUTTONS = {"up": 4, "down": 5, "left": 6, "right": 7}

#: Plain key names the XTest backend maps to X keysyms.
_KEY_NAMES = {
    "space": "space",
    "enter": "Return",
    "return": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "backspace": "BackSpace",
    "delete": "Delete",
    "tab": "Tab",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "Prior",
    "pagedown": "Next",
    "insert": "Insert",
    "ctrl": "Control_L",
    "control": "Control_L",
    "alt": "Alt_L",
    "shift": "Shift_L",
    "super": "Super_L",
    "meta": "Meta_L",
    "cmd": "Super_L",
}


@dataclass(frozen=True)
class InputResult:
    """Outcome of one input action: whether it ran, and
    separately whether the re-read confirmed a change."""

    action: str
    performed: bool
    verified: bool
    reason: str
    before: str = ""
    after: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "performed": self.performed,
            "verified": self.verified,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class ClipboardResult:
    """Outcome of a clipboard read or write.

    ``content`` is present only on a read, only in memory, and
    is deliberately left OUT of :meth:`to_dict`: result dicts
    are what gets printed and logged, and clipboard content
    must never be logged or persisted.
    """

    action: str
    performed: bool
    verified: bool
    reason: str
    length: int = 0
    content: str | None = None

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "performed": self.performed,
            "verified": self.verified,
            "reason": self.reason,
            "length": self.length,
        }


class InputBackend(Protocol):
    """The port every input backend implements."""

    name: str

    def is_available(self) -> bool:
        """True when this backend can inject input right now."""
        ...

    def type_text(self, node: AccessibleNode, text: str) -> bool:
        """Type ``text`` into ``node``. False when the element
        cannot take text. Raises BackendUnavailableError when
        there is no desktop to type into."""
        ...

    def key(self, keys: tuple[str, ...], node: AccessibleNode | None = None) -> bool:
        """Press a chord like ("ctrl", "a"): modifiers down,
        key down and up, modifiers up. The real backend sends
        keys to whatever has focus (``node`` is ignored); the
        fake backend applies the chord to ``node`` when given.
        False for an unknown key name. Raises
        BackendUnavailableError with no display."""
        ...

    def scroll(self, node: AccessibleNode, direction: str, amount: int) -> bool:
        """Scroll ``direction`` by ``amount`` clicks at ``node``.
        False when the element cannot scroll."""
        ...

    def drag(self, from_node: AccessibleNode, to_node: AccessibleNode) -> bool:
        """Drag from ``from_node`` to ``to_node``. False when the
        source cannot be dragged."""
        ...

    def clipboard_read(self) -> str:
        """Read the session clipboard. Raises
        BackendUnavailableError when no clipboard tool exists."""
        ...

    def clipboard_write(self, text: str) -> bool:
        """Replace the session clipboard. Raises
        BackendUnavailableError when no clipboard tool exists."""
        ...


# ------------------------------------------------------------------
# Shared fake helpers
# ------------------------------------------------------------------


def _apply_after(spec: dict, after: dict, root: list[dict]) -> None:
    """Apply an ``after_*`` effect dict to one spec node, the
    same keys the fake click backend understands: ``name``,
    ``value``, ``states_add`` / ``states_remove``, and
    ``removes_path`` (delete another node, e.g. a dragged card
    that leaves its list)."""
    if "name" in after:
        spec["name"] = after["name"]
    if "value" in after:
        spec["value"] = after["value"]
    states = set(spec.get("states", ()))
    states.update(after.get("states_add", ()))
    states.difference_update(after.get("states_remove", ()))
    spec["states"] = sorted(states)
    removes = after.get("removes_path")
    if removes is not None:
        parent = _find_spec(root, tuple(removes[:-1])) if len(removes) > 1 else None
        if parent is None:
            if removes:
                del root[removes[0]]
        else:
            del parent["children"][removes[-1]]


def _normalize_combo(keys: tuple[str, ...] | str) -> str:
    """One canonical spelling for a chord: lowercased parts,
    joined with ``+`` ("ctrl+a")."""
    parts = keys.split("+") if isinstance(keys, str) else list(keys)
    return "+".join(part.strip().lower() for part in parts if part.strip())


# ------------------------------------------------------------------
# Fake backend (tests + demos)
# ------------------------------------------------------------------


class FakeInputBackend:
    """In-memory input over the shared fake tree spec.

    A node spec may carry:

    - ``"editable": True`` + ``"value"``: typing appends to the
      value. Typing into a non-editable node returns False.
    - ``"shortcuts": {"ctrl+a": {...}}``: a chord applies the
      effect dict to the node. An unlisted chord returns False.
    - ``"scrollable": True`` + ``"scroll"``: scrolling moves
      the position (``"scroll_x"`` for left/right). A
      non-scrollable node returns False. ``"after_scroll"``
      applies an effect dict to the node when scrolled.
    - ``"after_drag"``: dragging this node onto another applies
      the effect dict to the drop target. Without it, a drag
      returns False.
    - ``"after_type"``: an effect dict applied to the node
      after typing (e.g. a search box that renames itself).

    The clipboard is one in-memory string.
    """

    name = "fake"

    def __init__(self, spec: list[dict] | None = None) -> None:
        self._spec: list[dict] = spec if spec is not None else []
        self._clipboard = ""

    def is_available(self) -> bool:
        return True

    def type_text(self, node: AccessibleNode, text: str) -> bool:
        spec = _find_spec(self._spec, node.path)
        if spec is None or not spec.get("editable"):
            return False
        spec["value"] = str(spec.get("value", "")) + text
        after = spec.get("after_type") or {}
        if after:
            _apply_after(spec, after, self._spec)
        return True

    def key(self, keys: tuple[str, ...], node: AccessibleNode | None = None) -> bool:
        """Press a chord on ``node``: the fake tree applies the
        chord's effect from the node's ``"shortcuts"`` spec
        (``{"ctrl+a": {...}}``). Without a target node the fake
        tree has nothing to apply the chord to, so it returns
        False; the real backend instead sends keys to whatever
        has focus."""
        if node is None:
            return False
        spec = _find_spec(self._spec, node.path)
        if spec is None:
            return False
        shortcuts = spec.get("shortcuts") or {}
        after = shortcuts.get(_normalize_combo(keys))
        if after is None:
            return False
        _apply_after(spec, after, self._spec)
        return True

    def scroll(self, node: AccessibleNode, direction: str, amount: int) -> bool:
        spec = _find_spec(self._spec, node.path)
        if spec is None or not spec.get("scrollable"):
            return False
        if direction in ("up", "down"):
            position = int(spec.get("scroll", 0)) + (amount if direction == "down" else -amount)
            spec["scroll"] = max(0, position)
        else:
            position = int(spec.get("scroll_x", 0)) + (amount if direction == "right" else -amount)
            spec["scroll_x"] = max(0, position)
        after = spec.get("after_scroll") or {}
        if after:
            _apply_after(spec, after, self._spec)
        return True

    def drag(self, from_node: AccessibleNode, to_node: AccessibleNode) -> bool:
        source = _find_spec(self._spec, from_node.path)
        target = _find_spec(self._spec, to_node.path)
        if source is None or target is None:
            return False
        after = source.get("after_drag")
        if after is None:
            return False
        _apply_after(target, after, self._spec)
        return True

    def clipboard_read(self) -> str:
        return self._clipboard

    def clipboard_write(self, text: str) -> bool:
        self._clipboard = text
        return True


# ------------------------------------------------------------------
# Real Linux backend: AT-SPI editable text + XTest + clipboard tools
# ------------------------------------------------------------------


def _display_address() -> str:
    return os.environ.get("DISPLAY", "") or os.environ.get("WAYLAND_DISPLAY", "")


def _bus_address() -> str:
    return (
        os.environ.get("AT_SPI_BUS_ADDRESS", "")
        or os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        or _display_address()
    )


class _XTestLibs:
    """Bound libXtst + libX11 handles (loaded lazily)."""

    def __init__(self, xtst: Any, x11: Any) -> None:
        self.xtst = xtst
        self.x11 = x11


def _load_xtest_libs() -> _XTestLibs:
    """Load libXtst + libX11, binding only the symbols the input
    backend needs. Raises BackendUnavailableError when a library
    or a symbol is missing (this VM has libX11 but no libXtst)."""
    xtst_name = ctypes.util.find_library("Xtst") or "libXtst.so.6"
    x11_name = ctypes.util.find_library("X11") or "libX11.so.6"
    try:
        xtst = ctypes.CDLL(xtst_name)
        x11 = ctypes.CDLL(x11_name)
    except OSError as exc:
        raise BackendUnavailableError(f"XTest libraries not found ({exc})") from exc
    void_p = ctypes.c_void_p
    int_p = ctypes.c_int
    ulong = ctypes.c_ulong
    try:
        x11.XOpenDisplay.restype = void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XCloseDisplay.restype = int_p
        x11.XCloseDisplay.argtypes = [void_p]
        x11.XFlush.restype = int_p
        x11.XFlush.argtypes = [void_p]
        x11.XDefaultScreen.restype = int_p
        x11.XDefaultScreen.argtypes = [void_p]
        x11.XStringToKeysym.restype = ulong
        x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XKeysymToKeycode.argtypes = [void_p, ulong]
        xtst.XTestFakeKeyEvent.restype = int_p
        xtst.XTestFakeKeyEvent.argtypes = [void_p, ctypes.c_uint, ctypes.c_bool, ulong]
        xtst.XTestFakeButtonEvent.restype = int_p
        xtst.XTestFakeButtonEvent.argtypes = [void_p, ctypes.c_uint, ctypes.c_bool, ulong]
        xtst.XTestFakeMotionEvent.restype = int_p
        xtst.XTestFakeMotionEvent.argtypes = [void_p, int_p, int_p, int_p, ulong]
    except AttributeError as exc:
        raise BackendUnavailableError(
            f"XTest is missing a symbol this backend needs ({exc})"
        ) from exc
    return _XTestLibs(xtst=xtst, x11=x11)


def _bind_editable_text(libs: Any) -> None:
    """Bind the AT-SPI editable-text symbols on an already
    loaded libatspi handle (idempotent: setting the same
    restype twice is harmless)."""
    void_p = ctypes.c_void_p
    int_p = ctypes.c_int
    atspi = libs.atspi
    atspi.atspi_accessible_get_editable_text.restype = void_p
    atspi.atspi_accessible_get_editable_text.argtypes = [void_p]
    atspi.atspi_editable_text_insert_text.restype = ctypes.c_bool
    atspi.atspi_editable_text_insert_text.argtypes = [
        void_p,
        ctypes.c_char_p,
        int_p,
        ctypes.POINTER(int_p),
    ]
    atspi.atspi_accessible_get_text.restype = void_p
    atspi.atspi_accessible_get_text.argtypes = [void_p]
    atspi.atspi_text_get_character_count.restype = int_p
    atspi.atspi_text_get_character_count.argtypes = [void_p]
    atspi.atspi_accessible_get_component.restype = void_p
    atspi.atspi_accessible_get_component.argtypes = [void_p]
    atspi.atspi_component_grab_focus.restype = ctypes.c_bool
    atspi.atspi_component_grab_focus.argtypes = [void_p, void_p]


class XTestInputBackend:
    """Real Linux input: AT-SPI editable text for typing, XTest
    for keys / scroll / drag, session clipboard tools for the
    clipboard. Headless by default: it never moves the mouse
    except to the target element's own position for a scroll or
    drag, and every call fails closed when its piece is missing.
    """

    name = "xtest"

    def __init__(self) -> None:
        self._xtest: _XTestLibs | None = None
        self._atspi: Any | None = None

    # -- availability ---------------------------------------------
    def is_available(self) -> bool:
        if not _display_address():
            return False
        try:
            self._xtest_libs()
        except BackendUnavailableError:
            return False
        return True

    def _xtest_libs(self) -> _XTestLibs:
        if not _display_address():
            raise BackendUnavailableError(
                "no display: DISPLAY and WAYLAND_DISPLAY are both unset, so there is "
                "no desktop to send input to. Run inside a desktop session and retry."
            )
        if self._xtest is None:
            self._xtest = _load_xtest_libs()
        return self._xtest

    def _atspi_libs(self) -> Any:
        if not _bus_address():
            raise BackendUnavailableError(
                "no accessibility bus: AT_SPI_BUS_ADDRESS, DBUS_SESSION_BUS_ADDRESS, "
                "DISPLAY and WAYLAND_DISPLAY are all unset, so there is no desktop "
                "to type into."
            )
        if self._atspi is None:
            self._atspi = _load_atspi_libs()
            _bind_editable_text(self._atspi)
        return self._atspi

    def _open_display(self) -> int:
        libs = self._xtest_libs()
        display = libs.x11.XOpenDisplay(None)
        if not display:
            raise BackendUnavailableError("XOpenDisplay failed: no X server answers.")
        return display

    # -- typing (AT-SPI editable text) ------------------------------
    def type_text(self, node: AccessibleNode, text: str) -> bool:
        libs = self._atspi_libs()
        live = find_live_accessible(libs, node.path)
        if live is None:
            return False
        try:
            component = libs.atspi.atspi_accessible_get_component(live)
            if component:
                try:
                    libs.atspi.atspi_component_grab_focus(component, None)
                finally:
                    libs.gobject.g_object_unref(component)
            editable = libs.atspi.atspi_accessible_get_editable_text(live)
            if not editable:
                return False
            try:
                text_iface = libs.atspi.atspi_accessible_get_text(live)
                position = 0
                if text_iface:
                    try:
                        position = libs.atspi.atspi_text_get_character_count(text_iface)
                    finally:
                        libs.gobject.g_object_unref(text_iface)
                pos = ctypes.c_int(max(position, 0))
                return bool(
                    libs.atspi.atspi_editable_text_insert_text(
                        editable, text.encode("utf-8"), len(text.encode("utf-8")), ctypes.byref(pos)
                    )
                )
            finally:
                libs.gobject.g_object_unref(editable)
        finally:
            libs.gobject.g_object_unref(live)

    # -- keys / scroll / drag (XTest) -------------------------------
    def _keysym(self, libs: _XTestLibs, display: int, name: str) -> int:
        key_name = _KEY_NAMES.get(name.lower())
        if key_name is None:
            if len(name) == 1:
                key_name = name
            elif name.upper().startswith("F") and name[1:].isdigit():
                key_name = name.upper()
            else:
                return 0
        keysym = libs.x11.XStringToKeysym(key_name.encode("utf-8"))
        if not keysym:
            return 0
        return int(keysym)

    def _fake_key(self, libs: _XTestLibs, display: int, keysym: int, press: bool) -> None:
        keycode = libs.x11.XKeysymToKeycode(display, keysym)
        if not keycode:
            raise ValueError(f"no keycode for keysym {keysym}")
        libs.xtst.XTestFakeKeyEvent(display, keycode, press, 0)

    def key(self, keys: tuple[str, ...], node: AccessibleNode | None = None) -> bool:
        del node  # XTest sends keys to whatever has focus
        if not keys:
            return False
        libs = self._xtest_libs()
        display = self._open_display()
        try:
            keysyms = [self._keysym(libs, display, name) for name in keys]
            if any(keysym == 0 for keysym in keysyms):
                return False
            for keysym in keysyms[:-1]:
                self._fake_key(libs, display, keysym, True)
            self._fake_key(libs, display, keysyms[-1], True)
            self._fake_key(libs, display, keysyms[-1], False)
            for keysym in reversed(keysyms[:-1]):
                self._fake_key(libs, display, keysym, False)
            libs.x11.XFlush(display)
            return True
        finally:
            libs.x11.XCloseDisplay(display)

    def _center(self, node: AccessibleNode) -> tuple[int, int]:
        x = node.x + (node.width // 2 if node.width else 0)
        y = node.y + (node.height // 2 if node.height else 0)
        return (x, y)

    def scroll(self, node: AccessibleNode, direction: str, amount: int) -> bool:
        if direction not in _SCROLL_BUTTONS or amount <= 0:
            return False
        libs = self._xtest_libs()
        display = self._open_display()
        try:
            x, y = self._center(node)
            screen = libs.x11.XDefaultScreen(display)
            libs.xtst.XTestFakeMotionEvent(display, screen, x, y, 0)
            button = _SCROLL_BUTTONS[direction]
            for _ in range(amount):
                libs.xtst.XTestFakeButtonEvent(display, button, True, 0)
                libs.xtst.XTestFakeButtonEvent(display, button, False, 0)
            libs.x11.XFlush(display)
            return True
        finally:
            libs.x11.XCloseDisplay(display)

    def drag(self, from_node: AccessibleNode, to_node: AccessibleNode) -> bool:
        libs = self._xtest_libs()
        display = self._open_display()
        try:
            screen = libs.x11.XDefaultScreen(display)
            from_x, from_y = self._center(from_node)
            to_x, to_y = self._center(to_node)
            libs.xtst.XTestFakeMotionEvent(display, screen, from_x, from_y, 0)
            libs.xtst.XTestFakeButtonEvent(display, 1, True, 0)
            libs.xtst.XTestFakeMotionEvent(display, screen, to_x, to_y, 0)
            libs.xtst.XTestFakeButtonEvent(display, 1, False, 0)
            libs.x11.XFlush(display)
            return True
        finally:
            libs.x11.XCloseDisplay(display)

    # -- clipboard (session tools, never logged) --------------------
    def _clipboard_commands(self) -> tuple[list[str], list[str]]:
        """(read command, write command) for this session, or
        raise when no clipboard tool is installed."""
        if os.environ.get("WAYLAND_DISPLAY"):
            if shutil.which("wl-paste") and shutil.which("wl-copy"):
                return (["wl-paste"], ["wl-copy"])
            raise BackendUnavailableError(
                "no Wayland clipboard tool: install wl-clipboard (wl-copy / wl-paste)."
            )
        for tool in ("xclip", "xsel"):
            if shutil.which(tool):
                if tool == "xclip":
                    return (
                        ["xclip", "-selection", "clipboard", "-o"],
                        ["xclip", "-selection", "clipboard", "-i"],
                    )
                return (
                    ["xsel", "--clipboard", "--output"],
                    ["xsel", "--clipboard", "--input"],
                )
        raise BackendUnavailableError(
            "no X11 clipboard tool: install xclip or xsel (or run a Wayland session "
            "with wl-clipboard)."
        )

    def clipboard_read(self) -> str:
        read_cmd, _ = self._clipboard_commands()
        try:
            result = subprocess.run(read_cmd, check=False, capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendUnavailableError(f"clipboard read failed ({exc})") from exc
        if result.returncode != 0:
            raise BackendUnavailableError("clipboard read failed: the tool exited non-zero.")
        return result.stdout.decode("utf-8", "replace")

    def clipboard_write(self, text: str) -> bool:
        _, write_cmd = self._clipboard_commands()
        try:
            result = subprocess.run(
                write_cmd, check=False, input=text.encode("utf-8"), capture_output=True, timeout=5
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendUnavailableError(f"clipboard write failed ({exc})") from exc
        return result.returncode == 0


# ------------------------------------------------------------------
# Seam
# ------------------------------------------------------------------


def make_input_backend(kind: str = "auto", backend: Any | None = None) -> InputBackend:
    """Build an input backend by name.

    ``fake`` shares the fake tree spec of a :class:`FakeBackend`
    (pass it as ``backend``); ``auto`` picks the real Linux
    backend on Linux. Anything else, and any OS without a
    backend yet, raises BackendUnavailableError.
    """
    if kind == "fake":
        if isinstance(backend, FakeBackend):
            return FakeInputBackend(backend.spec)
        raise BackendUnavailableError(
            "the fake input backend needs a FakeBackend to share its tree with"
        )
    if kind == "auto":
        if sys.platform.startswith("linux"):
            return XTestInputBackend()
        raise BackendUnavailableError(
            f"desktop input is Linux-only in V2-33 (this machine reports {sys.platform!r}); "
            "Windows and macOS backends are separate follow-up work."
        )
    raise BackendUnavailableError(f"unknown input backend {kind!r} (expected 'auto' or 'fake')")
