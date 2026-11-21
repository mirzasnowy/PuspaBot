"""Window management for Puspa desktop hands (V2-33).

List windows, open an app, focus a window, switch between
apps. Linux first, through the same AT-SPI tree V2-31 reads:
top-level applications and their frames are the windows. A
window id is its frame's path in the tree (``"0.1"``: first
application, second frame), so a future backend can keep the
same shape.

Two backends implement :class:`WindowBackend`:

- :class:`FakeWindowBackend`: windows derived from the shared
  fake tree spec (applications and their frames), so opening an
  app adds a real application + frame to the tree and focusing
  moves the ``focused`` state. This is what the tests run on.
- :class:`AtspiWindowBackend`: the real Linux backend. Listing
  reads the live tree; focusing grabs focus on the live frame
  through AT-SPI; opening launches the app (by executable name,
  falling back to ``gtk-launch`` for desktop entries) and the
  caller verifies by re-reading the tree. Every call fails
  closed with
  :class:`~puspa.computer.backend.BackendUnavailableError` when
  there is no desktop session to manage.

Windows and macOS are NOT built here (Linux first, per the
issue). :func:`make_window_backend` raises for them, so a
future backend plugs into the same seam.
"""

from __future__ import annotations

import ctypes
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
from .tree import Snapshot

__all__ = [
    "AtspiWindowBackend",
    "FakeWindowBackend",
    "WindowBackend",
    "WindowInfo",
    "WindowResult",
    "make_window_backend",
    "windows_from_snapshot",
]


@dataclass(frozen=True)
class WindowInfo:
    """One window: its id, app, title, and focus state."""

    id: str
    app: str
    title: str
    focused: bool = False

    def to_dict(self) -> dict:
        return {"id": self.id, "app": self.app, "title": self.title, "focused": self.focused}


@dataclass(frozen=True)
class WindowResult:
    """Outcome of one window action: whether it ran, and
    separately whether the re-read confirmed it."""

    action: str
    window_id: str
    performed: bool
    verified: bool
    reason: str
    before: str = ""
    after: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "window_id": self.window_id,
            "performed": self.performed,
            "verified": self.verified,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
        }


def windows_from_snapshot(shot: Snapshot) -> list[WindowInfo]:
    """Windows in a snapshot: every frame under every
    application. The id is the frame's path (``"0.1"``), the app
    is the application name, the title is the frame name."""
    windows: list[WindowInfo] = []
    for app in shot.nodes:
        for frame in app.children:
            if frame.role != "frame":
                continue
            windows.append(
                WindowInfo(
                    id=".".join(str(index) for index in frame.path),
                    app=app.name,
                    title=frame.name,
                    focused="focused" in frame.states,
                )
            )
    return windows


class WindowBackend(Protocol):
    """The port every window backend implements."""

    name: str

    def is_available(self) -> bool:
        """True when this backend can manage windows right now."""
        ...

    def list_windows(self) -> list[WindowInfo]:
        """All windows, in tree order. Raises
        BackendUnavailableError when there is no desktop."""
        ...

    def open_app(self, name: str) -> bool:
        """Launch ``name`` (executable, or desktop entry). True
        when the launch was started; the caller verifies by
        re-reading the tree."""
        ...

    def focus_window(self, window_id: str) -> bool:
        """Bring ``window_id`` to the front. False for an
        unknown id."""
        ...


# ------------------------------------------------------------------
# Fake backend (tests + demos)
# ------------------------------------------------------------------


def _spec_windows(spec: list[dict]) -> list[WindowInfo]:
    windows: list[WindowInfo] = []
    for app_index, app in enumerate(spec):
        for frame_index, frame in enumerate(app.get("children", [])):
            if frame.get("role") != "frame":
                continue
            windows.append(
                WindowInfo(
                    id=f"{app_index}.{frame_index}",
                    app=app.get("name", ""),
                    title=frame.get("name", ""),
                    focused="focused" in frame.get("states", ()),
                )
            )
    return windows


def _unfocus_spec(spec: list[dict]) -> None:
    for app in spec:
        for frame in app.get("children", []):
            states = [state for state in frame.get("states", ()) if state != "focused"]
            frame["states"] = states


class FakeWindowBackend:
    """In-memory windows over the shared fake tree spec.

    Opening an app appends an application with one focused
    frame to the spec (so the next snapshot shows it); focusing
    moves the ``focused`` state between frames.
    """

    name = "fake"

    def __init__(self, spec: list[dict] | None = None) -> None:
        self._spec: list[dict] = spec if spec is not None else []

    def is_available(self) -> bool:
        return True

    def list_windows(self) -> list[WindowInfo]:
        return _spec_windows(self._spec)

    def open_app(self, name: str) -> bool:
        if not name.strip():
            return False
        _unfocus_spec(self._spec)
        self._spec.append(
            {
                "role": "application",
                "name": name,
                "children": [
                    {"role": "frame", "name": name, "states": ["focused"]},
                ],
            }
        )
        return True

    def focus_window(self, window_id: str) -> bool:
        try:
            app_index, frame_index = (int(part) for part in window_id.split("."))
        except ValueError:
            return False
        frame = _find_spec(self._spec, (app_index, frame_index))
        if frame is None or frame.get("role") != "frame":
            return False
        _unfocus_spec(self._spec)
        states = list(frame.get("states", ()))
        if "focused" not in states:
            states.append("focused")
        frame["states"] = states
        return True


# ------------------------------------------------------------------
# Real Linux backend: AT-SPI tree + launcher
# ------------------------------------------------------------------


def _bus_address() -> str:
    return (
        os.environ.get("AT_SPI_BUS_ADDRESS", "")
        or os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        or os.environ.get("DISPLAY", "")
        or os.environ.get("WAYLAND_DISPLAY", "")
    )


class AtspiWindowBackend:
    """Real Linux windows: list and focus through AT-SPI, open
    through the session launcher. Focus never moves the mouse:
    it grabs focus on the frame itself."""

    name = "atspi"

    def __init__(self, backend: Any | None = None) -> None:
        # ``backend`` is the AtspiBackend used for snapshots; a
        # fresh one is built when none is given.
        from .backend import AtspiBackend

        self._backend = backend if backend is not None else AtspiBackend()
        self._libs: Any | None = None

    def is_available(self) -> bool:
        if not _bus_address():
            return False
        return self._backend.is_available()

    def _libs_or_raise(self) -> Any:
        if not _bus_address():
            raise BackendUnavailableError(
                "no accessibility bus: AT_SPI_BUS_ADDRESS, DBUS_SESSION_BUS_ADDRESS, "
                "DISPLAY and WAYLAND_DISPLAY are all unset, so there is no desktop "
                "whose windows could be listed."
            )
        if self._libs is None:
            self._libs = _load_atspi_libs()
            void_p = ctypes.c_void_p
            self._libs.atspi.atspi_accessible_get_component.restype = void_p
            self._libs.atspi.atspi_accessible_get_component.argtypes = [void_p]
            self._libs.atspi.atspi_component_grab_focus.restype = ctypes.c_bool
            self._libs.atspi.atspi_component_grab_focus.argtypes = [void_p, void_p]
        return self._libs

    def list_windows(self) -> list[WindowInfo]:
        shot = self._backend.snapshot()
        return windows_from_snapshot(shot)

    def open_app(self, name: str) -> bool:
        if not name.strip():
            return False
        if not _bus_address():
            raise BackendUnavailableError(
                "no desktop session: DISPLAY and WAYLAND_DISPLAY are both unset, "
                "so there is nowhere to open an app."
            )
        command: list[str] | None = None
        if shutil.which(name):
            command = [name]
        elif shutil.which("gtk-launch"):
            command = ["gtk-launch", name]
        if command is None:
            raise BackendUnavailableError(
                f"cannot open {name!r}: no executable by that name and no "
                "gtk-launch to open a desktop entry with."
            )
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise BackendUnavailableError(f"launching {name!r} failed ({exc})") from exc
        return process.poll() is None

    def focus_window(self, window_id: str) -> bool:
        libs = self._libs_or_raise()
        try:
            path = tuple(int(part) for part in window_id.split("."))
        except ValueError:
            return False
        live = find_live_accessible(libs, path)
        if live is None:
            return False
        try:
            component = libs.atspi.atspi_accessible_get_component(live)
            if not component:
                return False
            try:
                return bool(libs.atspi.atspi_component_grab_focus(component, None))
            finally:
                libs.gobject.g_object_unref(component)
        finally:
            libs.gobject.g_object_unref(live)


# ------------------------------------------------------------------
# Seam
# ------------------------------------------------------------------


def make_window_backend(kind: str = "auto", backend: Any | None = None) -> WindowBackend:
    """Build a window backend by name.

    ``fake`` shares the fake tree spec of a :class:`FakeBackend`
    (pass it as ``backend``); ``auto`` picks the real Linux
    backend on Linux. Anything else, and any OS without a
    backend yet, raises BackendUnavailableError.
    """
    if kind == "fake":
        if isinstance(backend, FakeBackend):
            return FakeWindowBackend(backend.spec)
        raise BackendUnavailableError(
            "the fake window backend needs a FakeBackend to share its tree with"
        )
    if kind == "auto":
        if sys.platform.startswith("linux"):
            return AtspiWindowBackend(backend)
        raise BackendUnavailableError(
            f"window management is Linux-only in V2-33 (this machine reports {sys.platform!r}); "
            "Windows and macOS backends are separate follow-up work."
        )
    raise BackendUnavailableError(f"unknown window backend {kind!r} (expected 'auto' or 'fake')")
