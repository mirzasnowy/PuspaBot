"""Web bridge (V2-32): route web targets to browser automation.

The V2-31 desktop core cannot automate web pages at all. This
module routes a web target to Puspa's browser automation instead
of the desktop: a headless Chromium page, driven through
Playwright. Playwright is already a Puspa dependency and the
pricing reader (:mod:`puspa.pricing.readers`) already launches
headless Chromium the same way; this bridge reuses that pattern
and the ``PUSPA_CHROMIUM_EXECUTABLE`` override, it does not
invent a second browser stack.

The seam is :class:`WebBackend`. :class:`FakeWebBackend` is an
in-memory page (elements with boxes, plus ``after_click``
effects) for tests and demos. :class:`PlaywrightWebBackend` is
the real one: it opens a URL in headless Chromium, finds text
through the page's own layout (the DOM gives exact bounding
boxes, so no OCR is needed on the web), clicks by coordinates,
and verifies by re-reading the page. When the DOM cannot find
the text, it falls back to screenshot + OCR through the OCR seam
(:mod:`puspa.computer.vision`), so a canvas-heavy page still has
a chance.

Routing is :func:`is_web_target`: an ``http://`` / ``https://``
URL, or a ``web:`` prefix. Anything else is a desktop target and
stays with :mod:`puspa.computer.hands` / :mod:`puspa.computer.fallback`.

Permission: a web click is a real action on a live page, so it
goes through the same double gate as every other computer click
(token check + user prompt, see :mod:`puspa.computer.gating`),
with its own tool name ``computer.web_click``. Opening a page and
finding text are reads and need no prompt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from puspa.localexec.executor import PermissionDecision

from .gating import ask_user, check_token_gate
from .screenshot import Screenshot
from .vision import (
    FakeOcrBackend,
    OcrBackend,
    VisualElement,
    VisualFallbackError,
    ground_text,
)

__all__ = [
    "FakeWebBackend",
    "PlaywrightWebBackend",
    "WebBackend",
    "WebBridge",
    "WebPage",
    "is_web_target",
]

#: Per-tool permission name for web clicks under Puspa's token
#: model. Deliberately NOT "computer.click": a web click must be
#: granted on its own, like a coordinate click.
TOOL_WEB_CLICK = "computer.web_click"


@dataclass(frozen=True)
class WebPage:
    """A page the bridge has open: its URL and title."""

    url: str
    title: str = ""

    def to_dict(self) -> dict:
        return {"url": self.url, "title": self.title}


def is_web_target(target: str) -> bool:
    """True when ``target`` names a web page: an http(s) URL, or
    an explicit ``web:`` prefix (``web:https://...``)."""
    text = target.strip()
    return text.startswith(("http://", "https://", "web:http://", "web:https://"))


def _strip_web_prefix(target: str) -> str:
    text = target.strip()
    if text.startswith("web:"):
        return text[len("web:") :]
    return text


class WebBackend(Protocol):
    """The port every web backend implements."""

    name: str

    def is_available(self) -> bool:
        """True when this backend can drive a browser now."""
        ...

    def open(self, url: str) -> WebPage:
        """Open ``url`` and return the page. Raises
        VisualFallbackError when the browser cannot open it."""
        ...

    def find_text(self, query: str) -> VisualElement | None:
        """Locate ``query`` on the open page, or None when it is
        not there. Read-only."""
        ...

    def click_at(self, x: int, y: int) -> bool:
        """Press the pixel (x, y) in the open page."""
        ...

    def screenshot(self) -> Screenshot:
        """Capture the open page as PNG bytes."""
        ...

    def close(self) -> None:
        """Close the browser. Safe to call twice."""
        ...


# ------------------------------------------------------------------
# Fake backend (tests + demos)
# ------------------------------------------------------------------


class FakeWebBackend:
    """In-memory web page.

    ``pages`` maps a URL to a page spec: ``{"title": ...,
    "elements": [{"label": ..., "x": ..., "y": ..., "width": ...,
    "height": ..., "after_click": {...}}]}``. An element spec may
    carry ``after_click``: ``{"label": ...}`` renames the element
    and ``{"removes": True}`` deletes it (a dialog that closes).
    Clicking outside every element returns False.
    """

    name = "fake"

    def __init__(self, pages: dict | None = None) -> None:
        # Deep-copied: clicks mutate the page (after_click), so
        # two backends built from one spec must not share state.
        import copy

        self._pages: dict = copy.deepcopy(pages) if pages is not None else {}
        self._current: str | None = None
        self.opened: list[str] = []
        self.clicks: list[tuple[int, int]] = []
        self.closed = False

    def is_available(self) -> bool:
        return True

    def open(self, url: str) -> WebPage:
        self._current = url
        self.opened.append(url)
        spec = self._pages.get(url, {})
        return WebPage(url=url, title=str(spec.get("title", "")))

    def _elements(self) -> list[dict]:
        if self._current is None:
            return []
        return list(self._pages.get(self._current, {}).get("elements", []))

    def find_text(self, query: str) -> VisualElement | None:
        want = query.strip().lower()
        if not want or self._current is None:
            return None
        for element in self._elements():
            label = str(element.get("label", ""))
            if want in label.lower():
                return VisualElement(
                    label=label,
                    x=int(element.get("x", 0)),
                    y=int(element.get("y", 0)),
                    width=int(element.get("width", 0)),
                    height=int(element.get("height", 0)),
                    source="dom",
                )
        return None

    def click_at(self, x: int, y: int) -> bool:
        self.clicks.append((x, y))
        if self._current is None:
            return False
        for element in self._elements():
            left, top = int(element.get("x", 0)), int(element.get("y", 0))
            right, bottom = left + int(element.get("width", 0)), top + int(element.get("height", 0))
            if left <= x < right and top <= y < bottom:
                after = element.get("after_click") or {}
                if "label" in after:
                    element["label"] = after["label"]
                if after.get("removes"):
                    self._pages[self._current]["elements"].remove(element)
                return True
        return False

    def screenshot(self) -> Screenshot:
        return Screenshot(png=b"", width=800, height=600, source=self.name)

    def close(self) -> None:
        self.closed = True


# ------------------------------------------------------------------
# Real backend: Playwright + headless Chromium
# ------------------------------------------------------------------


class PlaywrightWebBackend:
    """Web pages through Playwright + headless Chromium.

    The browser is launched on first use and kept open until
    :meth:`close`. Text is found through the DOM first
    (``page.locator("text=...")`` gives an exact bounding box);
    when the DOM cannot find it, the page is screenshotted and
    OCR-read through the injected OCR backend. The browser binary
    comes from Playwright's own install, or from
    ``PUSPA_CHROMIUM_EXECUTABLE`` (same override as the pricing
    reader). No Playwright or no browser binary fails closed.
    """

    name = "playwright"

    def __init__(self, *, ocr_backend: OcrBackend | None = None) -> None:
        self._ocr = ocr_backend if ocr_backend is not None else FakeOcrBackend()
        self._playwright = None
        self._browser = None
        self._page = None
        self._current_url: str | None = None

    def is_available(self) -> bool:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        executable = os.environ.get("PUSPA_CHROMIUM_EXECUTABLE")
        if executable:
            return os.path.exists(executable)
        return True

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise VisualFallbackError(
                "no web bridge available: Playwright is not installed on this machine"
            ) from exc
        try:
            self._playwright = sync_playwright().start()
            executable = os.environ.get("PUSPA_CHROMIUM_EXECUTABLE") or None
            self._browser = self._playwright.chromium.launch(executable_path=executable)
            self._page = self._browser.new_page()
        except Exception as exc:
            self.close()
            raise VisualFallbackError(f"headless Chromium failed to start ({exc})") from exc
        return self._page

    def open(self, url: str) -> WebPage:
        page = self._ensure_page()
        try:
            page.goto(url, timeout=30000)
        except Exception as exc:
            raise VisualFallbackError(f"cannot open {url!r} in the browser ({exc})") from exc
        self._current_url = url
        return WebPage(url=url, title=page.title())

    def _find_in_dom(self, query: str) -> VisualElement | None:
        if self._page is None:
            return None
        try:
            locator = self._page.locator(f"text={query}").first
            if locator.count() == 0:
                return None
            box = locator.bounding_box()
        except Exception:
            return None
        if not box:
            return None
        try:
            label = locator.inner_text(timeout=1000).strip()
        except Exception:
            label = query
        return VisualElement(
            label=label or query,
            x=int(box["x"]),
            y=int(box["y"]),
            width=int(box["width"]),
            height=int(box["height"]),
            source="dom",
        )

    def find_text(self, query: str) -> VisualElement | None:
        if self._page is None:
            raise VisualFallbackError("no page is open: call open(url) first")
        found = self._find_in_dom(query)
        if found is not None:
            return found
        shot = self.screenshot()
        if not shot.png:
            return None
        try:
            words = self._ocr.read(shot.png)
        except VisualFallbackError:
            return None
        return ground_text(words, query)

    def click_at(self, x: int, y: int) -> bool:
        if self._page is None:
            raise VisualFallbackError("no page is open: call open(url) first")
        try:
            self._page.mouse.click(x, y)
        except Exception as exc:
            raise VisualFallbackError(f"browser click at ({x}, {y}) failed ({exc})") from exc
        return True

    def screenshot(self) -> Screenshot:
        if self._page is None:
            raise VisualFallbackError("no page is open: call open(url) first")
        try:
            png = self._page.screenshot()
            viewport = self._page.viewport_size or {"width": 1280, "height": 720}
        except Exception as exc:
            raise VisualFallbackError(f"browser screenshot failed ({exc})") from exc
        return Screenshot(
            png=png, width=int(viewport["width"]), height=int(viewport["height"]), source=self.name
        )

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._page = None
        self._playwright = None
        self._current_url = None


# ------------------------------------------------------------------
# Bridge: routing + the gated act + verify loop
# ------------------------------------------------------------------


@dataclass(frozen=True)
class WebClickResult:
    """Outcome of one gated web click: whether the action ran,
    and separately whether the re-read confirmed a change."""

    label: str
    x: int
    y: int
    performed: bool
    verified: bool
    reason: str
    before: str = ""
    after: str = ""

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "center": {"x": self.x, "y": self.y},
            "performed": self.performed,
            "verified": self.verified,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
        }


class WebBridge:
    """Drive web pages through a :class:`WebBackend`.

    ``open`` / ``find_text`` are reads. ``click_text`` is the
    gated act + re-read loop, same shape as the desktop hands:
    locate the text, token check + user prompt (tool
    ``computer.web_click``), click its centre, re-read the page
    and verify. ``expect`` is ``{"text": ...}`` (this text must
    be on the page afterwards), ``{"absent_text": ...}`` (it must
    be gone), or None (any change in the page text counts).
    """

    def __init__(
        self,
        backend: WebBackend | str | None = None,
        *,
        prompt=None,
        gate=None,
        token_payload: dict | None = None,
    ) -> None:
        if isinstance(backend, str):
            if backend == "fake":
                backend = FakeWebBackend()
            elif backend == "playwright":
                backend = PlaywrightWebBackend()
            else:
                raise VisualFallbackError(
                    f"unknown web backend {backend!r} (expected 'fake' or 'playwright')"
                )
        self.backend: WebBackend = backend if backend is not None else PlaywrightWebBackend()
        self._prompt = prompt
        self._gate = gate
        self._token_payload = token_payload
        self._session_approved = False

    def open(self, target: str) -> WebPage:
        """Open a web target (``web:`` prefix stripped)."""
        return self.backend.open(_strip_web_prefix(target))

    def find_text(self, query: str) -> VisualElement | None:
        """Locate ``query`` on the open page. Read-only."""
        return self.backend.find_text(query)

    def _ask(self, element: VisualElement) -> None:
        label = (
            f"computer web_click {element.label!r} at ({element.center[0]}, {element.center[1]})"
        )
        check_token_gate(self._gate, self._token_payload, TOOL_WEB_CLICK)
        decision = ask_user(label, self._prompt, session_approved=self._session_approved)
        if decision is PermissionDecision.APPROVE_SESSION:
            self._session_approved = True

    def click_text(self, query: str, *, expect: dict | None = None) -> WebClickResult:
        """Locate ``query`` on the open page, click its centre
        (gated), then re-read to verify."""
        element = self.backend.find_text(query)
        if element is None:
            raise VisualFallbackError(f"cannot locate {query!r} on the open page")
        before = self._page_text()
        self._ask(element)
        try:
            performed = self.backend.click_at(*element.center)
        except VisualFallbackError:
            raise
        except Exception as exc:
            raise VisualFallbackError(f"web click on {query!r} failed: {exc}") from exc
        if not performed:
            return WebClickResult(
                label=element.label,
                x=element.center[0],
                y=element.center[1],
                performed=False,
                verified=False,
                reason="browser click did not run",
                before=before,
            )
        after = self._page_text()
        if expect is not None:
            if "text" in expect:
                found = self.backend.find_text(str(expect["text"])) is not None
                return WebClickResult(
                    label=element.label,
                    x=element.center[0],
                    y=element.center[1],
                    performed=True,
                    verified=found,
                    reason=f"text {expect['text']!r} on the page after the click"
                    if found
                    else f"text {expect['text']!r} not on the page after the click",
                    before=before,
                    after=after,
                )
            if "absent_text" in expect:
                gone = self.backend.find_text(str(expect["absent_text"])) is None
                return WebClickResult(
                    label=element.label,
                    x=element.center[0],
                    y=element.center[1],
                    performed=True,
                    verified=gone,
                    reason=f"text {expect['absent_text']!r} gone after the click"
                    if gone
                    else f"text {expect['absent_text']!r} still on the page after the click",
                    before=before,
                    after=after,
                )
        changed = before != after
        return WebClickResult(
            label=element.label,
            x=element.center[0],
            y=element.center[1],
            performed=True,
            verified=changed,
            reason="page text changed after the click"
            if changed
            else "page unchanged after the click: the action ran but nothing visibly changed",
            before=before,
            after=after,
        )

    def _page_text(self) -> str:
        """The text the bridge can see right now: the labels of
        the elements the backend reports. For the Playwright
        backend this reads the page body text; for the fake it
        reads the fake page's labels."""
        page = getattr(self.backend, "_page", None)
        if page is not None:
            try:
                return page.locator("body").inner_text(timeout=1000)
            except Exception:
                return ""
        if isinstance(self.backend, FakeWebBackend):
            return " ".join(str(element.get("label", "")) for element in self.backend._elements())
        return ""

    def close(self) -> None:
        """Close the browser behind the backend."""
        self.backend.close()
