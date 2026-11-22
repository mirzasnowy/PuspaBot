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

import contextlib
import os
from dataclasses import dataclass
from typing import Protocol

from puspa.localexec.executor import PermissionDecision, PermissionDenied

from .audit import InMemoryComputerAudit, LedgerComputerAudit, audit_entry
from .gating import ask_user, check_token_gate
from .screenshot import Screenshot
from .stop import ComputerStop, ComputerStopped
from .tiers import Tier
from .vision import (
    FakeOcrBackend,
    OcrBackend,
    VisualElement,
    VisualFallbackError,
    ground_text,
)

__all__ = [
    "TOOL_WEB_CLICK",
    "TOOL_WEB_FILL",
    "FakeWebBackend",
    "PlaywrightWebBackend",
    "WebBackend",
    "WebBridge",
    "WebFillResult",
    "WebPage",
    "is_web_target",
]

#: Per-tool permission name for web clicks under Puspa's token
#: model. Deliberately NOT "computer.click": a web click must be
#: granted on its own, like a coordinate click.
TOOL_WEB_CLICK = "computer.web_click"

#: Per-tool permission name for filling a form field on a web
#: page. A fill types into a live page, so it is gated like a
#: click, under its own name.
TOOL_WEB_FILL = "computer.web_fill"


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

    def read_text(self) -> str:
        """The visible text of the open page, or "" when no page
        is open. Read-only."""
        ...

    def fill(self, selector: str, value: str) -> str:
        """Type ``value`` into the form field ``selector`` names
        and return the field's value afterwards. Raises
        VisualFallbackError when no page is open or the field is
        not found."""
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
    "height": ..., "after_click": {...}}],
    "fields": [{"selector": ..., "label": ..., "value": ...}]}``.
    An element spec may carry ``after_click``: ``{"label": ...}``
    renames the element and ``{"removes": True}`` deletes it (a
    dialog that closes). A field spec names one form field by its
    CSS ``selector`` (matched exactly, or by its ``label`` when
    the caller passes the label instead). Clicking outside every
    element returns False.
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

    def _fields(self) -> list[dict]:
        if self._current is None:
            return []
        return list(self._pages.get(self._current, {}).get("fields", []))

    def read_text(self) -> str:
        """The fake page's visible text: its element labels."""
        return " ".join(str(element.get("label", "")) for element in self._elements())

    def fill(self, selector: str, value: str) -> str:
        """Set the named field's value and return it. The field
        is matched by its ``selector`` first, then by its
        ``label`` (so a caller may pass either)."""
        if self._current is None:
            raise VisualFallbackError("no page is open: call open(url) first")
        want = selector.strip()
        for field in self._fields():
            if want in (str(field.get("selector", "")), str(field.get("label", ""))):
                field["value"] = value
                return value
        raise VisualFallbackError(f"no form field {selector!r} on the open page")

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

    def read_text(self) -> str:
        """The open page's body text, through the DOM."""
        if self._page is None:
            raise VisualFallbackError("no page is open: call open(url) first")
        try:
            return self._page.locator("body").inner_text(timeout=1000)
        except Exception as exc:
            raise VisualFallbackError(f"browser read text failed ({exc})") from exc

    def fill(self, selector: str, value: str) -> str:
        """Type into the field ``selector`` names (a CSS selector
        for Playwright) and return the value read back."""
        if self._page is None:
            raise VisualFallbackError("no page is open: call open(url) first")
        try:
            locator = self._page.locator(selector).first
            if locator.count() == 0:
                raise VisualFallbackError(f"no form field {selector!r} on the open page")
            locator.fill(value, timeout=5000)
        except VisualFallbackError:
            raise
        except Exception as exc:
            raise VisualFallbackError(f"browser fill of {selector!r} failed ({exc})") from exc
        try:
            return str(locator.input_value(timeout=1000))
        except Exception:
            return value

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


@dataclass(frozen=True)
class WebFillResult:
    """Outcome of one gated web fill: whether the action ran,
    and separately whether the read-back confirmed the value."""

    selector: str
    value: str
    performed: bool
    verified: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "selector": self.selector,
            "value": self.value,
            "performed": self.performed,
            "verified": self.verified,
            "reason": self.reason,
        }


class WebBridge:
    """Drive web pages through a :class:`WebBackend`.

    ``open`` / ``find_text`` / ``read_text`` are reads.
    ``click_text`` is the gated act + re-read loop, same shape as
    the desktop hands: locate the text, token check + user prompt
    (tool ``computer.web_click``), click its centre, re-read the
    page and verify. ``fill`` is the same shape for form fields:
    token check + user prompt (tool ``computer.web_fill``), type
    the value, read the field back to verify. ``expect`` is
    ``{"text": ...}`` (this text must be on the page afterwards),
    ``{"absent_text": ...}`` (it must be gone), or None (any
    change in the page text counts). ``stop`` is the V2-35
    emergency stop (checked before the prompt); ``audit`` is the
    V2-35 audit sink. A web click or fill is tier NORMAL with no
    known app, so it always asks.
    """

    def __init__(
        self,
        backend: WebBackend | str | None = None,
        *,
        prompt=None,
        gate=None,
        token_payload: dict | None = None,
        stop: ComputerStop | None = None,
        audit: LedgerComputerAudit | InMemoryComputerAudit | None = None,
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
        self.stop = stop if stop is not None else ComputerStop()
        self.audit = audit

    def open(self, target: str) -> WebPage:
        """Open a web target (``web:`` prefix stripped). Tier
        READ: never asks, but still audited."""
        page = self.backend.open(_strip_web_prefix(target))
        self._audit(
            "web_open",
            target=_strip_web_prefix(target),
            tier=Tier.READ,
            decision="allow",
            outcome="performed",
            reason=f"opened {page.url}",
        )
        return page

    def read_text(self) -> str:
        """The visible text of the open page. Read-only. Tier
        READ: never asks, but still audited."""
        text = self.backend.read_text()
        self._audit(
            "web_read",
            target="",
            tier=Tier.READ,
            decision="allow",
            outcome="performed",
            reason=f"read {len(text)} characters of page text",
        )
        return text

    def fill(self, selector: str, value: str) -> WebFillResult:
        """Type ``value`` into the form field ``selector`` names
        (gated: token check + user prompt, tool
        ``computer.web_fill``), then read the field back to
        verify. The typed value is never written to the audit
        log, only its length (see :mod:`puspa.computer.audit`).
        """
        try:
            result = self._fill_inner(selector, value)
        except (ComputerStopped, PermissionDenied):
            raise  # already audited in _ask_fill
        except VisualFallbackError as exc:
            self._audit(
                "web_fill",
                target=selector,
                tier=Tier.NORMAL,
                decision="allow",
                outcome="failed",
                reason=str(exc),
            )
            raise
        self._audit(
            "web_fill",
            target=selector,
            tier=Tier.NORMAL,
            decision="allow",
            outcome="performed" if result.performed else "not_performed",
            reason=result.reason,
            length=len(value),
        )
        return result

    def _ask_fill(self, selector: str) -> None:
        label = f"computer web_fill {selector!r}"
        if self.stop.is_active():
            self._audit(
                "web_fill",
                target=selector,
                tier=Tier.NORMAL,
                decision="stop",
                outcome="stopped",
                reason="computer hands are stopped by the user",
            )
            self.stop.check()
        check_token_gate(self._gate, self._token_payload, TOOL_WEB_FILL)
        try:
            decision = ask_user(label, self._prompt, session_approved=self._session_approved)
        except PermissionDenied as exc:
            self._audit(
                "web_fill",
                target=selector,
                tier=Tier.NORMAL,
                decision="deny",
                outcome="denied",
                reason=str(exc),
            )
            raise
        if decision is PermissionDecision.APPROVE_SESSION:
            self._session_approved = True

    def _fill_inner(self, selector: str, value: str) -> WebFillResult:
        """The gated fill + read-back loop behind :meth:`fill`."""
        self._ask_fill(selector)
        try:
            read_back = self.backend.fill(selector, value)
        except VisualFallbackError:
            raise
        except Exception as exc:
            raise VisualFallbackError(f"web fill of {selector!r} failed: {exc}") from exc
        verified = read_back == value
        return WebFillResult(
            selector=selector,
            value=value,
            performed=True,
            verified=verified,
            reason="field holds the typed value after the fill"
            if verified
            else "field value differs after the fill: the action ran but the value did not stick",
        )

    def find_text(self, query: str) -> VisualElement | None:
        """Locate ``query`` on the open page. Read-only. Tier
        READ: never asks, but still audited."""
        element = self.backend.find_text(query)
        self._audit(
            "web_find",
            target=query,
            tier=Tier.READ,
            decision="allow",
            outcome="performed" if element is not None else "not_performed",
            reason="text found on the page" if element is not None else "text not on the page",
        )
        return element

    def _audit(
        self,
        action: str,
        *,
        target: str,
        tier: Tier,
        decision: str,
        outcome: str,
        reason: str = "",
        length: int | None = None,
    ) -> None:
        """Write one audit entry when a sink is wired in.
        Best-effort: a sink that cannot write never fails the
        action itself. ``length`` records how many characters a
        value held, never the value itself (typed text stays out
        of the log, see :mod:`puspa.computer.audit`)."""
        if self.audit is None:
            return
        with contextlib.suppress(Exception):
            self.audit.record(
                audit_entry(
                    action,
                    target=target,
                    tier=tier.value,
                    decision=decision,
                    outcome=outcome,
                    reason=reason,
                    length=length,
                )
            )

    def _ask(self, element: VisualElement) -> None:
        label = (
            f"computer web_click {element.label!r} at ({element.center[0]}, {element.center[1]})"
        )
        if self.stop.is_active():
            self._audit(
                "web_click",
                target=element.label,
                tier=Tier.NORMAL,
                decision="stop",
                outcome="stopped",
                reason="computer hands are stopped by the user",
            )
            self.stop.check()
        check_token_gate(self._gate, self._token_payload, TOOL_WEB_CLICK)
        try:
            decision = ask_user(label, self._prompt, session_approved=self._session_approved)
        except PermissionDenied as exc:
            self._audit(
                "web_click",
                target=element.label,
                tier=Tier.NORMAL,
                decision="deny",
                outcome="denied",
                reason=str(exc),
            )
            raise
        if decision is PermissionDecision.APPROVE_SESSION:
            self._session_approved = True

    def click_text(self, query: str, *, expect: dict | None = None) -> WebClickResult:
        """Locate ``query`` on the open page, click its centre
        (gated), then re-read to verify. The click is audited
        with its outcome."""
        try:
            result = self._click_text_inner(query, expect=expect)
        except (ComputerStopped, PermissionDenied):
            raise  # already audited in _ask
        except VisualFallbackError as exc:
            self._audit(
                "web_click",
                target=query,
                tier=Tier.NORMAL,
                decision="allow",
                outcome="failed",
                reason=str(exc),
            )
            raise
        self._audit(
            "web_click",
            target=query,
            tier=Tier.NORMAL,
            decision="allow",
            outcome="performed" if result.performed else "not_performed",
            reason=result.reason,
        )
        return result

    def _click_text_inner(self, query: str, *, expect: dict | None = None) -> WebClickResult:
        """The locate + gated click + re-read loop behind
        :meth:`click_text` (which adds the audit entry)."""
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
        """The text the bridge can see right now, through the
        backend's own read. A backend that cannot read returns ""
        instead of failing the verify step."""
        try:
            return self.backend.read_text()
        except Exception:
            return ""

    def close(self) -> None:
        """Close the browser behind the backend."""
        self.backend.close()
