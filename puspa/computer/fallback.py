"""Visual fallback orchestrator (V2-32): see, locate, click, verify.

When the V2-31 accessibility tree is empty or too poor to act on
(:func:`puspa.computer.vision.tree_is_poor`), :class:`VisualFallback`
takes over: screenshot, OCR the text, ground the wanted label to a
box, click its centre by coordinates, then re-read the screen to
verify. The observe-act-verify rule is the same as V2-31: a click
that ran never proves the UI changed; only the re-read can set
``verified=True``.

Coordinate clicks are riskier than ref clicks (a stale box can
land on the wrong pixel), so they go through the same double gate
as V2-31, with their own token tool name ``computer.click_at``: a
token that only grants ``computer.click`` does NOT grant a
coordinate click (unknown tools deny by default), and the user is
still asked first, with the exact coordinates in the prompt.

The clicker seam is :class:`CoordinateClicker`. The fake clicker
is for tests and demos. There is NO working real clicker on this
VM yet: Linux coordinate input needs libXtst (XTest) or xdotool,
and neither is installed, so :class:`XTestClicker` fails closed
with a plain error naming what is missing. That is honest, not a
gap to paper over: the visual path can see and locate live (X11
screenshot + tesseract once installed), but it cannot press a
pixel until a real input path exists.
"""

from __future__ import annotations

import contextlib
import ctypes.util
from dataclasses import dataclass
from typing import Protocol

from puspa.localexec.executor import PermissionDecision, PermissionDenied

from .audit import InMemoryComputerAudit, LedgerComputerAudit, audit_entry
from .gating import ask_user, check_token_gate
from .hands import DesktopHands
from .screenshot import FakeScreenshotBackend, ScreenshotBackend, make_screenshot_backend
from .stop import ComputerStop, ComputerStopped
from .sync import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_SETTLE_TIMEOUT_S,
    DEFAULT_STABLE_ROUNDS,
    SettleReport,
    visual_fingerprint,
    wait_for_settle,
    words_signature,
)
from .tiers import Tier
from .vision import (
    FakeOcrBackend,
    OcrBackend,
    OcrWord,
    TesseractBackend,
    VisualElement,
    VisualFallbackError,
    ground_text,
    tree_is_poor,
)

__all__ = [
    "CoordinateClicker",
    "FakeCoordinateClicker",
    "StaleVisualTargetError",
    "VisualClickResult",
    "VisualFallback",
    "XTestClicker",
    "make_clicker",
    "make_ocr_backend",
]

#: Per-tool permission name for coordinate clicks under Puspa's
#: token model. Deliberately NOT "computer.click": a coordinate
#: click must be granted on its own.
TOOL_CLICK_AT = "computer.click_at"


@dataclass(frozen=True)
class VisualClickResult:
    """Outcome of one gated coordinate click: whether the action
    ran, and separately whether the re-read confirmed a change."""

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


class StaleVisualTargetError(VisualFallbackError):
    """The text was on the screen when it was located, but the
    screen changed before the click ran: the text is gone, or
    its box moved or changed. NOT a permission denial: nobody
    refused the click, the target simply is not at the approved
    box any more, so the click was refused and the old pixel was
    never pressed. Locate the text again on the fresh screen."""


class CoordinateClicker(Protocol):
    """The port every coordinate-click backend implements."""

    name: str

    def is_available(self) -> bool:
        """True when this clicker can press a pixel now."""
        ...

    def click_at(self, x: int, y: int) -> bool:
        """Press the pixel (x, y). Returns True when the press
        ran; False when the clicker cannot press. Raises
        VisualFallbackError when there is no input path at all."""
        ...


class FakeCoordinateClicker:
    """In-memory clicker: records every press, runs an optional
    ``on_click`` hook (how a fake screen changes after a click),
    and always reports the press ran. Tests and demos only."""

    name = "fake"

    def __init__(self, on_click=None) -> None:
        self._on_click = on_click
        self.clicks: list[tuple[int, int]] = []

    def is_available(self) -> bool:
        return True

    def click_at(self, x: int, y: int) -> bool:
        self.clicks.append((x, y))
        if self._on_click is not None:
            self._on_click(x, y)
        return True


class XTestClicker:
    """Linux coordinate clicks through XTest (libXtst).

    Not usable on this VM: libXtst is not installed, and neither
    is xdotool. The class exists so the seam is visible and the
    failure names the missing piece, instead of a click silently
    doing nothing. When libXtst is installed, this backend fills
    in with XTestFakeButtonEvent through ctypes, the same way the
    X11 screenshot backend talks to libX11.
    """

    name = "xtest"

    def is_available(self) -> bool:
        return ctypes.util.find_library("Xtst") is not None

    def click_at(self, x: int, y: int) -> bool:
        del x, y
        raise VisualFallbackError(
            "no coordinate input available: libXtst (XTest) is not installed on this machine, "
            "and neither is xdotool, so the agent cannot press a pixel. Install libxtst6 "
            "(apt install libxtst6) and retry, or run with the fake clicker for a demo."
        )


def make_ocr_backend(kind: str = "auto") -> OcrBackend:
    """Build an OCR backend by name. ``auto`` uses tesseract when
    its binary exists, otherwise it fails closed (it never
    silently falls back to fake). ``fake`` is the in-memory
    backend."""
    if kind == "fake":
        return FakeOcrBackend()
    if kind == "auto":
        backend = TesseractBackend()
        if backend.is_available():
            return backend
        raise VisualFallbackError(
            "no OCR backend available: the 'tesseract' binary is not installed on this machine. "
            "Install tesseract-ocr and retry, or pass --backend fake for a demo."
        )
    raise VisualFallbackError(f"unknown OCR backend {kind!r} (expected 'auto' or 'fake')")


def make_clicker(kind: str = "auto") -> CoordinateClicker:
    """Build a coordinate clicker by name. ``auto`` uses XTest
    when libXtst exists, otherwise it fails closed. ``fake`` is
    the in-memory clicker."""
    if kind == "fake":
        return FakeCoordinateClicker()
    if kind == "auto":
        clicker = XTestClicker()
        if clicker.is_available():
            return clicker
        raise VisualFallbackError(
            "no coordinate clicker available: libXtst is not installed on this machine. "
            "Install libxtst6 and retry, or pass --backend fake for a demo."
        )
    raise VisualFallbackError(f"unknown clicker {kind!r} (expected 'auto' or 'fake')")


def _words_text(words: list[OcrWord]) -> str:
    return " ".join(word.text for word in words)


class VisualFallback:
    """The agent's eyes when the accessibility tree is blind.

    ``hands`` is the V2-31 :class:`DesktopHands` the tree is read
    through (pass None when only the visual path is wanted).
    ``screenshot_backend``, ``ocr_backend`` and ``clicker`` are
    the seams; ``"auto"`` picks the real ones and fails closed
    when they are missing, ``"fake"`` builds the in-memory ones.
    ``prompt`` / ``gate`` / ``token_payload`` are the same double
    gate V2-31 uses (see :mod:`puspa.computer.gating`). ``stop``
    is the V2-35 emergency stop (checked before the prompt, so a
    stopped hands never asks); ``audit`` is the V2-35 audit sink
    (every locate and click is written to the ledger when one is
    wired in). A coordinate click is tier NORMAL with no known
    app, so it always asks: the allowlist cannot cover a click
    whose app the fallback cannot see.
    """

    def __init__(
        self,
        hands: DesktopHands | None = None,
        *,
        screenshot_backend: ScreenshotBackend | str | None = None,
        ocr_backend: OcrBackend | str | None = None,
        clicker: CoordinateClicker | str | None = None,
        prompt=None,
        gate=None,
        token_payload: dict | None = None,
        settle_timeout: float = DEFAULT_SETTLE_TIMEOUT_S,
        settle_stable_rounds: int = DEFAULT_STABLE_ROUNDS,
        settle_poll_interval: float = DEFAULT_POLL_INTERVAL_S,
        stop: ComputerStop | None = None,
        audit: LedgerComputerAudit | InMemoryComputerAudit | None = None,
    ) -> None:
        if isinstance(screenshot_backend, str):
            screenshot_backend = make_screenshot_backend(screenshot_backend)
        if isinstance(ocr_backend, str):
            ocr_backend = make_ocr_backend(ocr_backend)
        if isinstance(clicker, str):
            clicker = make_clicker(clicker)
        self.hands = hands
        self.screenshot_backend = screenshot_backend or FakeScreenshotBackend()
        self.ocr_backend = ocr_backend or FakeOcrBackend()
        self.clicker = clicker or FakeCoordinateClicker()
        self._prompt = prompt
        self._gate = gate
        self._token_payload = token_payload
        self._session_approved = False
        self._settle_timeout = settle_timeout
        self._settle_stable_rounds = settle_stable_rounds
        self._settle_poll_interval = settle_poll_interval
        self.stop = stop if stop is not None else ComputerStop()
        self.audit = audit

    # -- reads ------------------------------------------------------
    def read_screen(self) -> list[OcrWord]:
        """Screenshot + OCR: the words on the screen right now."""
        shot = self.screenshot_backend.capture()
        return self.ocr_backend.read(shot.png)

    def wait_settle(
        self, *, timeout_s: float | None = None, stable_rounds: int | None = None
    ) -> tuple[list[OcrWord], SettleReport]:
        """Wait until the words on the screen stop changing, then
        return the settled words and the settle report. Raises
        :class:`puspa.computer.sync.SettleTimeoutError` when the
        screen never goes quiet inside the bounded wait."""
        return wait_for_settle(
            self.read_screen,
            signature=words_signature,
            timeout_s=self._settle_timeout if timeout_s is None else timeout_s,
            stable_rounds=self._settle_stable_rounds if stable_rounds is None else stable_rounds,
            poll_interval_s=self._settle_poll_interval,
        )

    def locate_text(self, query: str) -> VisualElement | None:
        """Locate ``query`` on the screen by OCR text, or None
        when it is not there. Read-only: never clicks. Tier
        READ: never asks, but still audited."""
        element = ground_text(self.read_screen(), query)
        self._audit(
            "visual_locate",
            target=query,
            tier=Tier.READ,
            decision="allow",
            outcome="performed" if element is not None else "not_performed",
            reason="text found on the screen" if element is not None else "text not on the screen",
        )
        return element

    def tree_needs_visual(self) -> bool:
        """True when the accessibility tree is too poor to act on
        (or there is no hands/tree at all)."""
        if self.hands is None:
            return True
        try:
            return tree_is_poor(self.hands.snapshot(skeleton=False))
        except Exception:
            return True

    # -- permission -------------------------------------------------
    def _audit(
        self,
        action: str,
        *,
        target: str,
        tier: Tier,
        decision: str,
        outcome: str,
        reason: str = "",
    ) -> None:
        """Write one audit entry when a sink is wired in.
        Best-effort: a sink that cannot write never fails the
        action itself."""
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
                )
            )

    def _ask(self, element: VisualElement) -> None:
        label = f"computer click_at {element.label!r} at ({element.center[0]}, {element.center[1]})"
        if self.stop.is_active():
            self._audit(
                "visual_click",
                target=element.label,
                tier=Tier.NORMAL,
                decision="stop",
                outcome="stopped",
                reason="computer hands are stopped by the user",
            )
            self.stop.check()
        check_token_gate(self._gate, self._token_payload, TOOL_CLICK_AT)
        try:
            decision = ask_user(label, self._prompt, session_approved=self._session_approved)
        except PermissionDenied as exc:
            self._audit(
                "visual_click",
                target=element.label,
                tier=Tier.NORMAL,
                decision="deny",
                outcome="denied",
                reason=str(exc),
            )
            raise
        if decision is PermissionDecision.APPROVE_SESSION:
            self._session_approved = True

    # -- the act + re-read loop ------------------------------------
    def click_text(self, query: str, *, expect: dict | None = None) -> VisualClickResult:
        """Locate ``query`` by OCR, click its centre by
        coordinates (gated), then re-read to verify.

        Steps: wait for the words on the screen to settle,
        ground the query, ask the user, read the screen AGAIN
        and re-ground (a moved or vanished target refuses with
        :class:`StaleVisualTargetError` and the old pixel is
        never pressed), click the re-grounded centre, re-read to
        verify.

        ``expect`` describes the change to look for: ``{"text":
        ...}`` (this text must be on the screen afterwards),
        ``{"absent_text": ...}`` (this text must be gone), or
        None (any change in the words on the screen counts).
        With no change the result is performed=true,
        verified=false, exactly like a V2-31 ref click that left
        the screen unchanged. The click is audited with its
        outcome; the label clicked is screen text, never typed
        text or clipboard content.
        """
        try:
            result = self._click_text_inner(query, expect=expect)
        except (ComputerStopped, PermissionDenied):
            raise  # already audited in _ask
        except VisualFallbackError as exc:
            self._audit(
                "visual_click",
                target=query,
                tier=Tier.NORMAL,
                decision="allow",
                outcome="failed",
                reason=str(exc),
            )
            raise
        self._audit(
            "visual_click",
            target=query,
            tier=Tier.NORMAL,
            decision="allow",
            outcome="performed" if result.performed else "not_performed",
            reason=result.reason,
        )
        return result

    def _click_text_inner(self, query: str, *, expect: dict | None = None) -> VisualClickResult:
        """The locate + gated click + re-read loop behind
        :meth:`click_text` (which adds the audit entry)."""
        before_words, _report = self.wait_settle()
        element = ground_text(before_words, query)
        if element is None:
            raise VisualFallbackError(f"cannot locate {query!r} on the screen by OCR text")
        expected = visual_fingerprint(element)
        before = _words_text(before_words)
        self._ask(element)
        fresh_words = self.read_screen()
        fresh = ground_text(fresh_words, query)
        if fresh is None:
            raise StaleVisualTargetError(
                f"text {query!r} is gone: it was at {element.center} when approved, "
                "but the screen changed before the click ran, so the click was refused "
                "and the old pixel was never pressed. Locate the text again."
            )
        if visual_fingerprint(fresh) != expected:
            raise StaleVisualTargetError(
                f"text {query!r} moved or changed: it was {element.label!r} at "
                f"{element.center}, the screen now shows it at {fresh.center}, so the "
                "click was refused and the old pixel was never pressed. Locate the text "
                "again on the fresh screen instead of aiming at the stale box."
            )
        try:
            performed = self.clicker.click_at(*fresh.center)
        except VisualFallbackError:
            raise
        except Exception as exc:
            raise VisualFallbackError(f"coordinate click on {query!r} failed: {exc}") from exc
        if not performed:
            return VisualClickResult(
                label=element.label,
                x=element.center[0],
                y=element.center[1],
                performed=False,
                verified=False,
                reason="coordinate clicker did not press",
                before=before,
            )
        after_words = self.read_screen()
        after = _words_text(after_words)
        if expect is not None:
            if "text" in expect:
                found = ground_text(after_words, str(expect["text"])) is not None
                return VisualClickResult(
                    label=element.label,
                    x=element.center[0],
                    y=element.center[1],
                    performed=True,
                    verified=found,
                    reason=f"text {expect['text']!r} on screen after the click"
                    if found
                    else f"text {expect['text']!r} not on screen after the click",
                    before=before,
                    after=after,
                )
            if "absent_text" in expect:
                gone = ground_text(after_words, str(expect["absent_text"])) is None
                return VisualClickResult(
                    label=element.label,
                    x=element.center[0],
                    y=element.center[1],
                    performed=True,
                    verified=gone,
                    reason=f"text {expect['absent_text']!r} gone after the click"
                    if gone
                    else f"text {expect['absent_text']!r} still on screen after the click",
                    before=before,
                    after=after,
                )
        changed = {word.text for word in after_words} != {word.text for word in before_words}
        if changed:
            return VisualClickResult(
                label=element.label,
                x=element.center[0],
                y=element.center[1],
                performed=True,
                verified=True,
                reason="screen text changed after the click",
                before=before,
                after=after,
            )
        return VisualClickResult(
            label=element.label,
            x=element.center[0],
            y=element.center[1],
            performed=True,
            verified=False,
            reason="screen unchanged after the click: the action ran but nothing visibly changed",
            before=before,
            after=after,
        )

    def complete_task(self, query: str, *, expect: dict | None = None) -> VisualClickResult:
        """Do one task in an app with no usable accessibility tree.

        Reads the tree first: when it is usable, this refuses
        (the tree path owns that app; use ``DesktopHands.click``
        by ref instead) instead of clicking blind. When the tree
        is poor or missing, it runs the visual path: locate,
        gated coordinate click, verify by re-reading. That is the
        acceptance test of V2-32, end to end.
        """
        if self.hands is not None and not self.tree_needs_visual():
            raise VisualFallbackError(
                "the accessibility tree is usable for this app, so the visual fallback refuses: "
                "click by ref through DesktopHands instead of by coordinates."
            )
        return self.click_text(query, expect=expect)
