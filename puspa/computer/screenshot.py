"""Screenshot backends for the visual fallback (V2-32).

The seam is :class:`ScreenshotBackend`: a backend captures the
screen as PNG bytes. Two backends exist today:

- :class:`FakeScreenshotBackend`: returns PNG bytes it was built
  with. This is what the tests and the CLI demo run on; it never
  touches a real display.
- :class:`X11ScreenshotBackend`: the real Linux backend. It reads
  the root window through ``libX11`` ``XGetImage`` via ``ctypes``
  (no scrot, no gnome-screenshot, no ImageMagick: none of those
  is installed on the build VM). It is verified live under Xvfb
  on this VM: a red rectangle drawn in a test window reads back
  as red pixels at the right place. With no ``DISPLAY`` it fails
  closed with :class:`VisualFallbackError`; it never pretends to
  have seen a screen.

A Wayland session has no root-window screenshot through X11; that
is a later backend (portal API), not this one.
"""

from __future__ import annotations

import contextlib
import ctypes
import io
import os
from dataclasses import dataclass
from typing import Any, Protocol

from .vision import VisualFallbackError

__all__ = [
    "FakeScreenshotBackend",
    "Screenshot",
    "ScreenshotBackend",
    "X11ScreenshotBackend",
    "make_screenshot_backend",
]


@dataclass(frozen=True)
class Screenshot:
    """One capture: PNG bytes plus the size they cover."""

    png: bytes
    width: int
    height: int
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "bytes": len(self.png),
            "source": self.source,
        }


class ScreenshotBackend(Protocol):
    """The port every screenshot backend implements."""

    name: str

    def is_available(self) -> bool:
        """True when this backend can capture a screen now."""
        ...

    def capture(self) -> Screenshot:
        """Capture the screen. Raises VisualFallbackError when
        there is no screen to capture."""
        ...


class FakeScreenshotBackend:
    """In-memory screenshot: returns the PNG bytes it was built
    with (plus the size they claim to cover). Tests hand it a
    small PNG made with Pillow; the fake OCR backend ignores the
    bytes anyway, so the picture itself is only a stand-in."""

    name = "fake"

    def __init__(self, png: bytes = b"", *, width: int = 800, height: int = 600) -> None:
        self._png = png
        self._width = width
        self._height = height
        self.captures = 0

    def is_available(self) -> bool:
        return True

    def capture(self) -> Screenshot:
        self.captures += 1
        return Screenshot(png=self._png, width=self._width, height=self._height, source=self.name)


class _XImage(ctypes.Structure):
    """The head of libX11's XImage: only the fields this backend
    reads (width through blue_mask). ``data`` is a void pointer on
    purpose: as ``c_char_p`` ctypes would hand back bytes and a
    later read of the buffer would crash."""

    _fields_ = [
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int),
        ("bitmap_bit_order", ctypes.c_int),
        ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong),
        ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
    ]


def _load_x11() -> Any:
    """Load libX11 and bind the five symbols the backend needs.
    Raises VisualFallbackError when the library or a symbol is
    missing."""
    try:
        lib = ctypes.CDLL("libX11.so.6")
    except OSError as exc:
        raise VisualFallbackError(f"X11 libraries not found ({exc})") from exc
    try:
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XCloseDisplay.restype = ctypes.c_int
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XDefaultScreen.restype = ctypes.c_int
        lib.XDefaultScreen.argtypes = [ctypes.c_void_p]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XDisplayWidth.restype = ctypes.c_int
        lib.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.XDisplayHeight.restype = ctypes.c_int
        lib.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.XGetImage.restype = ctypes.c_void_p
        lib.XGetImage.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_ulong,
            ctypes.c_int,
        ]
        lib.XDestroyImage.restype = ctypes.c_int
        lib.XDestroyImage.argtypes = [ctypes.c_void_p]
    except AttributeError as exc:
        raise VisualFallbackError(f"libX11 is missing a symbol this backend needs ({exc})") from exc
    return lib


def _ximage_to_png(lib, image_ptr: int, width: int, height: int) -> bytes:
    """Turn one XGetImage result into PNG bytes.

    The root window on a normal desktop is 24-bit depth stored as
    32 bits per pixel, little-endian BGRX, which Pillow decodes
    with the ``BGRX`` raw mode (verified live under Xvfb: a red
    rectangle reads back red). Anything else fails closed with a
    plain error instead of a scrambled picture.
    """
    image = ctypes.cast(image_ptr, ctypes.POINTER(_XImage)).contents
    if image.bits_per_pixel != 32 or image.depth != 24:
        raise VisualFallbackError(
            f"unsupported X image format: depth {image.depth}, {image.bits_per_pixel} bits "
            "per pixel (this backend only reads 24-bit depth stored as 32 bits per pixel)"
        )
    raw = ctypes.string_at(image.data, image.bytes_per_line * image.height)
    try:
        from PIL import Image
    except ImportError as exc:
        raise VisualFallbackError("cannot encode the screenshot: Pillow is not installed") from exc
    try:
        picture = Image.frombytes("RGB", (width, height), raw, "raw", "BGRX")
    except Exception as exc:
        raise VisualFallbackError(f"cannot decode the X image ({exc})") from exc
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


class X11ScreenshotBackend:
    """Linux screenshot through X11 ``XGetImage`` on the root
    window (ctypes against libX11, no helper binary).

    Needs a running X server (``DISPLAY`` set, e.g. a desktop
    session or Xvfb). With no display it fails closed. It reads
    the whole screen; a region capture is later work, not this
    item's.
    """

    name = "x11"

    def __init__(self) -> None:
        self._lib: Any = None

    def is_available(self) -> bool:
        if not os.environ.get("DISPLAY"):
            return False
        try:
            lib = self._lib or _load_x11()
        except VisualFallbackError:
            return False
        display = lib.XOpenDisplay(os.environ["DISPLAY"].encode())
        if not display:
            return False
        lib.XCloseDisplay(display)
        return True

    def capture(self) -> Screenshot:
        display_name = os.environ.get("DISPLAY", "")
        if not display_name:
            raise VisualFallbackError(
                "no screenshot available: DISPLAY is unset, so there is no X screen to capture. "
                "Run inside a desktop session (or under Xvfb) and retry."
            )
        if self._lib is None:
            self._lib = _load_x11()
        lib = self._lib
        display = lib.XOpenDisplay(display_name.encode())
        if not display:
            raise VisualFallbackError(
                f"no screenshot available: cannot open the X display {display_name!r}"
            )
        try:
            screen = lib.XDefaultScreen(display)
            root = lib.XDefaultRootWindow(display)
            width = lib.XDisplayWidth(display, screen)
            height = lib.XDisplayHeight(display, screen)
            if width <= 0 or height <= 0:
                raise VisualFallbackError(
                    f"no screenshot available: the X display reports size {width}x{height}"
                )
            image_ptr = lib.XGetImage(display, root, 0, 0, width, height, 0xFFFFFFFF, 2)
            if not image_ptr:
                raise VisualFallbackError("XGetImage returned no image for the root window")
            try:
                png = _ximage_to_png(lib, image_ptr, width, height)
            finally:
                # Free the XImage through libX11's own destroy
                # entry point (exported by libX11.so.6; do not
                # chase the funcs table by hand).
                with contextlib.suppress(Exception):
                    lib.XDestroyImage(image_ptr)
            return Screenshot(png=png, width=width, height=height, source=self.name)
        finally:
            lib.XCloseDisplay(display)


def make_screenshot_backend(kind: str = "auto") -> ScreenshotBackend:
    """Build a screenshot backend by name.

    ``auto`` picks the backend for this machine (X11 on Linux
    when a display exists, otherwise it fails closed).
    ``fake`` is the in-memory backend. Anything else raises
    VisualFallbackError.
    """
    if kind == "fake":
        return FakeScreenshotBackend()
    if kind == "auto":
        if os.environ.get("DISPLAY"):
            return X11ScreenshotBackend()
        raise VisualFallbackError(
            "no screenshot backend available: DISPLAY is unset, so there is no screen to "
            "capture on this headless machine. Pass --backend fake for a demo."
        )
    raise VisualFallbackError(f"unknown screenshot backend {kind!r} (expected 'auto' or 'fake')")
