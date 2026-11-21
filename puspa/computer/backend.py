"""Accessibility backends for Puspa desktop hands (V2-31, core).

The seam is :class:`AccessibilityBackend`: a backend reads a
snapshot of the OS accessibility tree and clicks one node in it.
Two backends exist today:

- :class:`FakeBackend`: an in-memory tree built from a nested dict
  spec. This is what the tests run on, and what the CLI uses with
  ``--backend fake`` for demos. It never touches a real desktop.
- :class:`AtspiBackend`: the real Linux backend. It talks to
  AT-SPI over ``libatspi`` through ``ctypes`` (no pyatspi, no
  dbus-python: neither is installed on the build VM). It is
  headless by default: it only reads the tree and runs the
  element's own ``click`` action, never moves the mouse and never
  steals focus. When no accessibility bus exists it fails closed
  with :class:`BackendUnavailableError`; it never pretends an
  action worked.

Windows and macOS are NOT built here (Linux first, per the
issue). :func:`make_backend` raises
:class:`BackendUnavailableError` for them with a plain message, so
a future backend plugs into the same seam.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from .tree import AccessibleNode, Snapshot, assign_refs

__all__ = [
    "AccessibilityBackend",
    "AtspiBackend",
    "BackendUnavailableError",
    "FakeBackend",
    "demo_tree",
    "find_live_accessible",
    "make_backend",
]

#: Hard caps so one Electron app cannot flood the agent with a
#: million-node tree. A snapshot that hits a cap stops expanding
#: and marks the cut-off node collapsed.
DEFAULT_MAX_NODES = 5000
DEFAULT_MAX_DEPTH = 32

#: AT-SPI state numbers used here (lib atspi, AtspiStateType).
#: Only the handful the core reports; the numbers are stable
#: across libatspi releases.
_STATE_NUMBERS = {
    "checked": 4,
    "enabled": 8,
    "expanded": 10,
    "focused": 12,
    "selected": 23,
    "showing": 25,
    "visible": 30,
}

#: Action names a click may use, in preference order.
_CLICK_ACTION_NAMES = ("click", "press", "activate")


class BackendUnavailableError(Exception):
    """No usable accessibility backend: no bus, no library, or an
    OS whose backend is not built yet. Fail-closed by design."""


class AccessibilityBackend(Protocol):
    """The port every OS backend implements."""

    name: str

    def is_available(self) -> bool:
        """True when this backend can talk to a real desktop now."""
        ...

    def snapshot(
        self, *, max_nodes: int = DEFAULT_MAX_NODES, max_depth: int = DEFAULT_MAX_DEPTH
    ) -> Snapshot:
        """Read the screen. Raises BackendUnavailableError when
        there is no desktop to read."""
        ...

    def click(self, node: AccessibleNode) -> bool:
        """Run the node's click action. Returns True when the
        action ran; False when the element has no click action.
        Raises BackendUnavailableError when there is no desktop."""
        ...


def find_live_accessible(libs: _AtspiLibs, path: tuple[int, ...]) -> int | None:
    """Walk the live tree along ``path``; returns the raw pointer
    (owned, caller must unref) or None. Shared by the click path
    and the V2-33 input/window backends so the walk lives in one
    place."""
    if not path:
        return None
    current = libs.atspi.atspi_get_desktop(path[0])
    if not current:
        return None
    for index in path[1:]:
        child = libs.atspi.atspi_accessible_get_child_at_index(current, index)
        libs.gobject.g_object_unref(current)
        if not child:
            return None
        current = child
    return current


# ------------------------------------------------------------------
# Fake backend (tests + demos)
# ------------------------------------------------------------------


def demo_tree() -> list[dict]:
    """A tiny two-window demo tree for ``--backend fake``."""
    return [
        {
            "role": "application",
            "name": "Demo Notes",
            "children": [
                {
                    "role": "frame",
                    "name": "Untitled note",
                    "children": [
                        {"role": "push button", "name": "Save", "actions": ["click"]},
                        {
                            "role": "check box",
                            "name": "Pin note",
                            "actions": ["click"],
                            "after_click": {"states_add": ["checked"]},
                        },
                        {
                            "role": "text",
                            "name": "Note body",
                            "editable": True,
                            "value": "",
                            "states": ["focused", "enabled"],
                            "shortcuts": {"ctrl+a": {"states_add": ["selected"]}},
                            "scrollable": True,
                            "scroll": 0,
                            "after_scroll": {"states_add": ["scrolled"]},
                        },
                    ],
                }
            ],
        },
        {
            "role": "application",
            "name": "Demo Player",
            "children": [
                {
                    "role": "frame",
                    "name": "Player",
                    "children": [
                        {
                            "role": "push button",
                            "name": "Play",
                            "actions": ["click"],
                            "after_click": {"name": "Pause"},
                        },
                    ],
                }
            ],
        },
    ]


def _spec_to_node(spec: dict, path: tuple[int, ...]) -> AccessibleNode:
    children = tuple(
        _spec_to_node(child, (*path, index)) for index, child in enumerate(spec.get("children", []))
    )
    return AccessibleNode(
        ref="",
        path=path,
        role=spec.get("role", ""),
        name=spec.get("name", ""),
        description=spec.get("description", ""),
        x=int(spec.get("x", 0)),
        y=int(spec.get("y", 0)),
        width=int(spec.get("width", 0)),
        height=int(spec.get("height", 0)),
        states=tuple(spec.get("states", ())),
        actions=tuple(spec.get("actions", ())),
        children=children,
        child_count=len(children),
        value=str(spec.get("value", "")),
    )


def _find_spec(specs: list[dict], path: tuple[int, ...]) -> dict | None:
    current: Any = specs
    spec: dict | None = None
    for index in path:
        if not isinstance(current, list) or index >= len(current):
            return None
        spec = current[index]
        current = spec.get("children", [])
    return spec


class FakeBackend:
    """In-memory accessibility tree.

    ``spec`` is a list of nested dicts (see :func:`demo_tree`).
    A node spec may carry ``after_click``: ``{"name": ...}`` renames
    the node, ``{"states_add": [...]}`` / ``{"states_remove":
    [...]}`` change its states, and ``{"removes_path": [..]}``
    deletes another node (a dialog that closes). Clicking a node
    without a click action returns False.
    """

    name = "fake"

    def __init__(self, spec: list[dict] | None = None) -> None:
        self._spec: list[dict] = spec if spec is not None else demo_tree()

    @property
    def spec(self) -> list[dict]:
        """The live spec list, shared with the V2-33 fake input
        and window backends so one tree is the single source of
        truth for reads and writes."""
        return self._spec

    def is_available(self) -> bool:
        return True

    def snapshot(
        self, *, max_nodes: int = DEFAULT_MAX_NODES, max_depth: int = DEFAULT_MAX_DEPTH
    ) -> Snapshot:
        del max_nodes, max_depth  # the fake tree is tiny by construction
        nodes = assign_refs(
            [_spec_to_node(spec, (index,)) for index, spec in enumerate(self._spec)]
        )
        return Snapshot(nodes=tuple(nodes), backend=self.name)

    def click(self, node: AccessibleNode) -> bool:
        spec = _find_spec(self._spec, node.path)
        if spec is None:
            return False
        if "click" not in spec.get("actions", ()):
            return False
        after = spec.get("after_click") or {}
        if "name" in after:
            spec["name"] = after["name"]
        states = set(spec.get("states", ()))
        states.update(after.get("states_add", ()))
        states.difference_update(after.get("states_remove", ()))
        spec["states"] = sorted(states)
        removes = after.get("removes_path")
        if removes is not None:
            parent = _find_spec(self._spec, tuple(removes[:-1])) if len(removes) > 1 else None
            if parent is None:
                if removes:
                    del self._spec[removes[0]]
            else:
                del parent["children"][removes[-1]]
        return True


# ------------------------------------------------------------------
# Real Linux backend: AT-SPI through ctypes
# ------------------------------------------------------------------


@dataclass
class _AtspiLibs:
    atspi: Any
    gobject: Any
    glib: Any


def _load_atspi_libs() -> _AtspiLibs:
    """Load libatspi + friends, binding only the symbols the core
    needs. Raises BackendUnavailableError when a library or a
    symbol is missing."""
    names = {
        "atspi": ctypes.util.find_library("atspi") or "libatspi.so.0",
        "gobject": ctypes.util.find_library("gobject-2.0") or "libgobject-2.0.so.0",
        "glib": ctypes.util.find_library("glib-2.0") or "libglib-2.0.so.0",
    }
    try:
        libs = {key: ctypes.CDLL(value) for key, value in names.items()}
    except OSError as exc:
        raise BackendUnavailableError(f"AT-SPI libraries not found ({exc})") from exc
    atspi = libs["atspi"]
    void_p = ctypes.c_void_p
    int_p = ctypes.c_int
    try:
        atspi.atspi_get_desktop_count.restype = int_p
        atspi.atspi_get_desktop_count.argtypes = []
        atspi.atspi_get_desktop.restype = void_p
        atspi.atspi_get_desktop.argtypes = [int_p]
        atspi.atspi_accessible_get_child_count.restype = int_p
        atspi.atspi_accessible_get_child_count.argtypes = [void_p]
        atspi.atspi_accessible_get_child_at_index.restype = void_p
        atspi.atspi_accessible_get_child_at_index.argtypes = [void_p, int_p]
        atspi.atspi_accessible_get_name.restype = void_p
        atspi.atspi_accessible_get_name.argtypes = [void_p]
        atspi.atspi_accessible_get_description.restype = void_p
        atspi.atspi_accessible_get_description.argtypes = [void_p]
        atspi.atspi_accessible_get_role_name.restype = void_p
        atspi.atspi_accessible_get_role_name.argtypes = [void_p]
        atspi.atspi_accessible_get_state_set.restype = void_p
        atspi.atspi_accessible_get_state_set.argtypes = [void_p]
        atspi.atspi_state_set_contains.restype = ctypes.c_bool
        atspi.atspi_state_set_contains.argtypes = [void_p, int_p]
        atspi.atspi_accessible_get_component.restype = void_p
        atspi.atspi_accessible_get_component.argtypes = [void_p]
        atspi.atspi_component_get_position.restype = None
        atspi.atspi_component_get_position.argtypes = [
            void_p,
            ctypes.POINTER(int_p),
            ctypes.POINTER(int_p),
            int_p,
        ]
        atspi.atspi_component_get_size.restype = None
        atspi.atspi_component_get_size.argtypes = [
            void_p,
            ctypes.POINTER(int_p),
            ctypes.POINTER(int_p),
        ]
        atspi.atspi_accessible_get_action.restype = void_p
        atspi.atspi_accessible_get_action.argtypes = [void_p]
        atspi.atspi_action_get_n_actions.restype = int_p
        atspi.atspi_action_get_n_actions.argtypes = [void_p]
        atspi.atspi_action_get_action_name.restype = void_p
        atspi.atspi_action_get_action_name.argtypes = [void_p, int_p]
        atspi.atspi_action_do_action.restype = ctypes.c_bool
        atspi.atspi_action_do_action.argtypes = [void_p, int_p]
        # Text interface (V2-33): read an element's current text
        # so typed text is visible in the re-read, and find the
        # editable-text interface for typing.
        atspi.atspi_accessible_get_text.restype = void_p
        atspi.atspi_accessible_get_text.argtypes = [void_p]
        atspi.atspi_text_get_text.restype = void_p
        atspi.atspi_text_get_text.argtypes = [void_p, int_p, int_p]
        atspi.atspi_text_get_character_count.restype = int_p
        atspi.atspi_text_get_character_count.argtypes = [void_p]
        libs["gobject"].g_object_unref.restype = None
        libs["gobject"].g_object_unref.argtypes = [void_p]
        libs["glib"].g_free.restype = None
        libs["glib"].g_free.argtypes = [void_p]
    except AttributeError as exc:
        raise BackendUnavailableError(
            f"libatspi is missing a symbol this backend needs ({exc})"
        ) from exc
    return _AtspiLibs(atspi=atspi, gobject=libs["gobject"], glib=libs["glib"])


class AtspiBackend:
    """Linux AT-SPI backend (read the tree, run a click action).

    Headless by default: it never moves the pointer and never
    calls grab_focus; a click runs the element's own action
    through AT-SPI, which is how agent-desktop drives apps
    without touching the mouse.
    """

    name = "atspi"

    def __init__(self) -> None:
        self._libs: _AtspiLibs | None = None

    # -- availability (never touches the bus when there is none) --
    @staticmethod
    def _bus_address() -> str:
        return (
            os.environ.get("AT_SPI_BUS_ADDRESS", "")
            or os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
            or os.environ.get("DISPLAY", "")
            or os.environ.get("WAYLAND_DISPLAY", "")
        )

    def is_available(self) -> bool:
        """False on a headless machine with no accessibility bus
        and no display: there is nothing to read, so the backend
        refuses instead of guessing."""
        if not self._bus_address():
            return False
        try:
            libs = self._libs or _load_atspi_libs()
        except BackendUnavailableError:
            return False
        try:
            return bool(libs.atspi.atspi_get_desktop_count() >= 0)
        except Exception:
            return False

    def _libs_or_raise(self) -> _AtspiLibs:
        if not self._bus_address():
            raise BackendUnavailableError(
                "no accessibility bus: AT_SPI_BUS_ADDRESS, DBUS_SESSION_BUS_ADDRESS, "
                "DISPLAY and WAYLAND_DISPLAY are all unset, so there is no desktop "
                "to read. Run inside a desktop session (or start one) and retry."
            )
        if self._libs is None:
            self._libs = _load_atspi_libs()
        return self._libs

    # -- reading ----------------------------------------------------
    def _read_string(self, libs: _AtspiLibs, raw: int | None) -> str:
        if not raw:
            return ""
        try:
            text = ctypes.string_at(raw).decode("utf-8", "replace")
        finally:
            libs.glib.g_free(raw)
        return text

    def _states(self, libs: _AtspiLibs, accessible: int) -> tuple[str, ...]:
        state_set = libs.atspi.atspi_accessible_get_state_set(accessible)
        if not state_set:
            return ()
        try:
            return tuple(
                name
                for name, number in _STATE_NUMBERS.items()
                if libs.atspi.atspi_state_set_contains(state_set, number)
            )
        finally:
            libs.gobject.g_object_unref(state_set)

    def _bounds(self, libs: _AtspiLibs, accessible: int) -> tuple[int, int, int, int]:
        component = libs.atspi.atspi_accessible_get_component(accessible)
        if not component:
            return (0, 0, 0, 0)
        try:
            x = ctypes.c_int(0)
            y = ctypes.c_int(0)
            width = ctypes.c_int(0)
            height = ctypes.c_int(0)
            libs.atspi.atspi_component_get_position(component, ctypes.byref(x), ctypes.byref(y), 0)
            libs.atspi.atspi_component_get_size(
                component, ctypes.byref(width), ctypes.byref(height)
            )
            return (x.value, y.value, width.value, height.value)
        except Exception:
            return (0, 0, 0, 0)
        finally:
            libs.gobject.g_object_unref(component)

    def _actions(self, libs: _AtspiLibs, accessible: int) -> tuple[str, ...]:
        action = libs.atspi.atspi_accessible_get_action(accessible)
        if not action:
            return ()
        try:
            count = libs.atspi.atspi_action_get_n_actions(action)
            names = []
            for index in range(max(count, 0)):
                names.append(
                    self._read_string(libs, libs.atspi.atspi_action_get_action_name(action, index))
                )
            return tuple(name for name in names if name)
        finally:
            libs.gobject.g_object_unref(action)

    def _text_value(self, libs: _AtspiLibs, accessible: int) -> str:
        """Current text of an element through the AT-SPI text
        interface (V2-33); "" when the element has no text."""
        text = libs.atspi.atspi_accessible_get_text(accessible)
        if not text:
            return ""
        try:
            count = libs.atspi.atspi_text_get_character_count(text)
            if count <= 0:
                return ""
            return self._read_string(libs, libs.atspi.atspi_text_get_text(text, 0, -1))
        except Exception:
            return ""
        finally:
            libs.gobject.g_object_unref(text)

    def _walk(
        self,
        libs: _AtspiLibs,
        accessible: int,
        path: tuple[int, ...],
        depth: int,
        max_nodes: int,
        max_depth: int,
        seen: list[int],
    ) -> AccessibleNode:
        role = self._read_string(libs, libs.atspi.atspi_accessible_get_role_name(accessible))
        name = self._read_string(libs, libs.atspi.atspi_accessible_get_name(accessible))
        description = self._read_string(
            libs, libs.atspi.atspi_accessible_get_description(accessible)
        )
        states = self._states(libs, accessible)
        x, y, width, height = self._bounds(libs, accessible)
        actions = self._actions(libs, accessible)
        value = self._text_value(libs, accessible)
        child_count = max(libs.atspi.atspi_accessible_get_child_count(accessible), 0)
        children: list[AccessibleNode] = []
        collapsed = False
        if depth < max_depth and len(seen) < max_nodes and child_count:
            if len(seen) + child_count > max_nodes:
                collapsed = True
            else:
                for index in range(child_count):
                    child = libs.atspi.atspi_accessible_get_child_at_index(accessible, index)
                    if not child:
                        continue
                    try:
                        children.append(
                            self._walk(
                                libs, child, (*path, index), depth + 1, max_nodes, max_depth, seen
                            )
                        )
                    finally:
                        libs.gobject.g_object_unref(child)
                    seen.append(1)
        elif child_count:
            collapsed = True
        return AccessibleNode(
            ref="",
            path=path,
            role=role or "unknown",
            name=name,
            description=description,
            x=x,
            y=y,
            width=width,
            height=height,
            states=states,
            actions=actions,
            children=tuple(children),
            child_count=child_count,
            collapsed=collapsed,
            value=value,
        )

    def snapshot(
        self, *, max_nodes: int = DEFAULT_MAX_NODES, max_depth: int = DEFAULT_MAX_DEPTH
    ) -> Snapshot:
        libs = self._libs_or_raise()
        try:
            desktop_count = libs.atspi.atspi_get_desktop_count()
        except Exception as exc:
            raise BackendUnavailableError(f"AT-SPI desktop read failed ({exc})") from exc
        if desktop_count <= 0:
            raise BackendUnavailableError(
                "AT-SPI reports no desktops: no application is reachable."
            )
        nodes: list[AccessibleNode] = []
        seen: list[int] = []
        for index in range(desktop_count):
            desktop = libs.atspi.atspi_get_desktop(index)
            if not desktop:
                continue
            try:
                nodes.append(self._walk(libs, desktop, (index,), 0, max_nodes, max_depth, seen))
            finally:
                libs.gobject.g_object_unref(desktop)
        return Snapshot(nodes=tuple(assign_refs(nodes)), backend=self.name)

    # -- clicking ---------------------------------------------------
    def _find_live(self, libs: _AtspiLibs, path: tuple[int, ...]) -> int | None:
        """Walk the live tree along ``path``; returns the raw
        pointer (owned, caller must unref) or None."""
        return find_live_accessible(libs, path)

    def click(self, node: AccessibleNode) -> bool:
        libs = self._libs_or_raise()
        live = self._find_live(libs, node.path)
        if live is None:
            return False
        try:
            action = libs.atspi.atspi_accessible_get_action(live)
            if not action:
                return False
            try:
                count = libs.atspi.atspi_action_get_n_actions(action)
                names = [
                    self._read_string(libs, libs.atspi.atspi_action_get_action_name(action, index))
                    for index in range(max(count, 0))
                ]
                for wanted in _CLICK_ACTION_NAMES:
                    if wanted in names:
                        return bool(libs.atspi.atspi_action_do_action(action, names.index(wanted)))
                return False
            finally:
                libs.gobject.g_object_unref(action)
        finally:
            libs.gobject.g_object_unref(live)


# ------------------------------------------------------------------
# Seam
# ------------------------------------------------------------------


def make_backend(kind: str = "auto") -> AccessibilityBackend:
    """Build a backend by name.

    ``auto`` picks the backend for this OS (Linux only today).
    ``fake`` is the in-memory tree. Anything else, and any OS
    without a backend yet, raises BackendUnavailableError.
    """
    if kind == "fake":
        return FakeBackend()
    if kind == "auto":
        if sys.platform.startswith("linux"):
            return AtspiBackend()
        raise BackendUnavailableError(
            f"desktop hands are Linux-only in V2-31 (this machine reports {sys.platform!r}); "
            "Windows and macOS backends are separate follow-up work."
        )
    raise BackendUnavailableError(f"unknown desktop backend {kind!r} (expected 'auto' or 'fake')")
