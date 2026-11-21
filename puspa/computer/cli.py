"""``puspa computer`` command group (V2-31, core; V2-32 visual fallback + web bridge).

``snapshot`` prints the accessibility tree as JSON (skeleton by
default), ``click`` clicks one ref through the permission gate
and re-reads to verify, ``verify`` re-reads one ref and checks
it. ``--backend auto`` (the default) uses the real Linux AT-SPI
backend and fails closed with a plain error when there is no
desktop; it never silently falls back to the fake tree.
``--backend fake`` reads a small in-memory demo tree instead.

V2-32 adds two groups under the same ``--backend`` flag:

- ``visual``: when the tree is empty or too poor, locate text on
  the screen by OCR and click it by coordinates (gated, verified
  by re-reading). ``auto`` uses the real X11 screenshot, the
  tesseract OCR binary, and the XTest clicker, and fails closed
  when any of them is missing here; ``fake`` runs a canned demo
  screen (a "Save" button that turns into "Saved").
- ``web``: route a web target to headless Chromium through
  Playwright (``auto``), or to a canned demo page (``fake``).
  ``web click`` opens the URL, finds the text in the page, clicks
  it (gated), and verifies by re-reading the page.
"""

from __future__ import annotations

import argparse
import json

from .backend import BackendUnavailableError, make_backend
from .fallback import VisualFallback, VisualFallbackError
from .hands import DesktopHands, DesktopHandsError
from .screenshot import FakeScreenshotBackend
from .tree import snapshot_to_dict
from .vision import FakeOcrBackend, OcrWord
from .web import FakeWebBackend, WebBridge

__all__ = ["add_computer_parser", "run_computer"]


def _expect_from_args(args: argparse.Namespace) -> dict | None:
    expect: dict = {}
    if getattr(args, "expect_name", None):
        expect["name"] = args.expect_name
    if getattr(args, "expect_state", None):
        expect["state"] = args.expect_state
    if getattr(args, "expect_absent", False):
        expect["absent"] = True
    return expect or None


def _add_backend_arg(parser: argparse.ArgumentParser) -> None:
    """Add --backend to a subcommand too, so it works after the
    subcommand as well as before it. The SUPPRESS default keeps a
    value given before the subcommand from being overwritten."""
    parser.add_argument(
        "--backend",
        choices=["auto", "fake"],
        default=argparse.SUPPRESS,
        help="Backend: auto uses the real Linux AT-SPI desktop (default), fake reads a demo tree.",
    )


def _add_expect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--expect-name", default=None, help="Name the element should have after the action."
    )
    parser.add_argument(
        "--expect-state", default=None, help="State the element should have after the action."
    )
    parser.add_argument(
        "--expect-absent",
        action="store_true",
        help="The element should be gone after the action.",
    )


def add_computer_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the ``computer`` group to the top-level parser."""
    computer = subparsers.add_parser(
        "computer",
        help="Desktop hands: snapshot, click, verify (V2-31).",
        description=(
            "Drive the local desktop through its accessibility tree: read a snapshot, click a ref, "
            "verify by re-reading. Linux first (AT-SPI); every click asks the user first."
        ),
    )
    computer.add_argument(
        "--backend",
        choices=["auto", "fake"],
        default="auto",
        help="Backend: auto uses the real Linux AT-SPI desktop (default), fake reads a demo tree.",
    )
    computer_sub = computer.add_subparsers(dest="computer_command", metavar="COMPUTER_COMMAND")
    snapshot = computer_sub.add_parser(
        "snapshot",
        help="Print the accessibility tree as JSON.",
        description="Print the accessibility tree as JSON (skeleton by default).",
    )
    _add_backend_arg(snapshot)
    snapshot.add_argument(
        "--full",
        action="store_true",
        help="Print the full tree instead of the skeleton (can be huge for Electron apps).",
    )
    click = computer_sub.add_parser(
        "click",
        help="Click one element by ref, then verify by re-reading.",
        description="Click one element by ref. Asks the user first, then re-reads the screen to verify.",
    )
    click.add_argument("ref", help="Element ref from `puspa computer snapshot`, e.g. n3.")
    _add_backend_arg(click)
    _add_expect_args(click)
    verify = computer_sub.add_parser(
        "verify",
        help="Re-read the screen and check one ref.",
        description="Re-read the screen and check one ref against an expectation. Read-only.",
    )
    verify.add_argument("ref", help="Element ref from `puspa computer snapshot`, e.g. n3.")
    _add_backend_arg(verify)
    _add_expect_args(verify)
    visual = computer_sub.add_parser(
        "visual",
        help="Visual fallback: locate text by OCR, click by coordinates (V2-32).",
        description=(
            "When the accessibility tree is empty or too poor, see the screen instead: "
            "screenshot + OCR, locate text, click its centre by coordinates (gated), "
            "verify by re-reading."
        ),
    )
    _add_backend_arg(visual)
    visual_sub = visual.add_subparsers(dest="visual_command", metavar="VISUAL_COMMAND")
    locate = visual_sub.add_parser(
        "locate",
        help="Locate text on the screen by OCR.",
        description="Screenshot + OCR, then print the box of the matching text as JSON.",
    )
    locate.add_argument("text", help="Text to find on the screen, e.g. Save.")
    click_v = visual_sub.add_parser(
        "click",
        help="Click text by coordinates, then verify by re-reading.",
        description=(
            "Locate text by OCR, click its centre by coordinates (asks the user first), "
            "then re-read the screen to verify."
        ),
    )
    click_v.add_argument("text", help="Text to click on the screen, e.g. Save.")
    click_v.add_argument(
        "--expect-text", default=None, help="Text that must be on screen after the click."
    )
    click_v.add_argument(
        "--expect-absent-text",
        default=None,
        help="Text that must be gone from the screen after the click.",
    )
    web = computer_sub.add_parser(
        "web",
        help="Web bridge: drive a web page in headless Chromium (V2-32).",
        description=(
            "Route a web target to browser automation: open the page in headless Chromium, "
            "find text through the page layout, click it (gated), verify by re-reading."
        ),
    )
    _add_backend_arg(web)
    web_sub = web.add_subparsers(dest="web_command", metavar="WEB_COMMAND")
    open_p = web_sub.add_parser(
        "open",
        help="Open a URL and print its title.",
        description="Open a URL in headless Chromium and print its URL and title as JSON.",
    )
    open_p.add_argument("url", help="URL to open, e.g. https://example.com.")
    find = web_sub.add_parser(
        "find",
        help="Find text on a web page.",
        description="Open a URL, then print the box of the matching text as JSON.",
    )
    find.add_argument("url", help="URL to open, e.g. https://example.com.")
    find.add_argument("text", help="Text to find on the page, e.g. Save.")
    click_w = web_sub.add_parser(
        "click",
        help="Click text on a web page, then verify by re-reading.",
        description=(
            "Open a URL, find the text, click its centre (asks the user first), "
            "then re-read the page to verify."
        ),
    )
    click_w.add_argument("url", help="URL to open, e.g. https://example.com.")
    click_w.add_argument("text", help="Text to click on the page, e.g. Save.")
    click_w.add_argument(
        "--expect-text", default=None, help="Text that must be on the page after the click."
    )
    click_w.add_argument(
        "--expect-absent-text",
        default=None,
        help="Text that must be gone from the page after the click.",
    )
    return computer


def _default_hands(backend_kind: str) -> DesktopHands:
    return DesktopHands(make_backend(backend_kind))


def _demo_png() -> bytes:
    """A tiny blank PNG for the fake visual demo (the fake OCR
    backend ignores the bytes; they only stand in for a real
    screenshot)."""
    import io

    from PIL import Image

    picture = Image.new("RGB", (800, 600), "white")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def _demo_fake_fallback() -> VisualFallback:
    """The canned demo screen: a "Save" button that turns into
    "Saved" when its centre is pressed, plus a "Cancel" button
    that does nothing. The fake clicker rewrites the fake OCR
    words, so a demo click verifies by re-reading."""
    ocr = FakeOcrBackend(
        [
            OcrWord(text="Save", x=100, y=200, width=60, height=24, confidence=0.95),
            OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.95),
        ]
    )

    def on_click(x: int, y: int) -> None:
        if 100 <= x < 160 and 200 <= y < 224:
            ocr.set_words(
                [
                    OcrWord(text="Saved", x=100, y=200, width=60, height=24, confidence=0.95),
                    OcrWord(text="Cancel", x=300, y=200, width=70, height=24, confidence=0.95),
                ]
            )

    from .fallback import FakeCoordinateClicker

    return VisualFallback(
        screenshot_backend=FakeScreenshotBackend(_demo_png()),
        ocr_backend=ocr,
        clicker=FakeCoordinateClicker(on_click=on_click),
    )


def _default_fallback(backend_kind: str) -> VisualFallback:
    if backend_kind == "fake":
        return _demo_fake_fallback()
    return VisualFallback(screenshot_backend="auto", ocr_backend="auto", clicker="auto")


def _demo_fake_web() -> WebBridge:
    """The canned demo page: https://example.com with a "Save"
    button that turns into "Saved" when clicked."""
    pages = {
        "https://example.com": {
            "title": "Example Domain",
            "elements": [
                {
                    "label": "Save",
                    "x": 100,
                    "y": 200,
                    "width": 60,
                    "height": 24,
                    "after_click": {"label": "Saved"},
                },
                {"label": "Cancel", "x": 300, "y": 200, "width": 70, "height": 24},
            ],
        }
    }
    return WebBridge(FakeWebBackend(pages))


def _default_web(backend_kind: str) -> WebBridge:
    if backend_kind == "fake":
        return _demo_fake_web()
    return WebBridge("playwright")


def run_computer(
    args: argparse.Namespace,
    *,
    hands_factory=None,
    fallback_factory=None,
    web_factory=None,
    print_fn=print,
) -> int:
    """Run one ``puspa computer`` command. Returns an exit code."""
    if args.computer_command == "snapshot":
        return _run_snapshot(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "click":
        return _run_click(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "verify":
        return _run_verify(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "visual":
        return _run_visual(args, fallback_factory=fallback_factory, print_fn=print_fn)
    if args.computer_command == "web":
        return _run_web(args, web_factory=web_factory, print_fn=print_fn)
    return 0


def _hands(args: argparse.Namespace, hands_factory) -> DesktopHands:
    backend_kind = getattr(args, "backend", "auto")
    if hands_factory is not None:
        return hands_factory(backend_kind)
    return _default_hands(backend_kind)


def _run_snapshot(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    try:
        hands = _hands(args, hands_factory)
        shot = hands.snapshot(skeleton=not args.full)
    except (BackendUnavailableError, DesktopHandsError) as exc:
        print_fn(f"puspa computer snapshot: failed: {exc}")
        return 1
    print_fn(json.dumps(snapshot_to_dict(shot), indent=2))
    return 0


def _run_click(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    try:
        hands = _hands(args, hands_factory)
        result = hands.click(args.ref, expect=_expect_from_args(args))
    except (BackendUnavailableError, DesktopHandsError) as exc:
        print_fn(f"puspa computer click: failed: {exc}")
        return 1
    except Exception as exc:  # permission denial from the prompt or gate
        print_fn(f"puspa computer click: refused: {exc}")
        return 1
    print_fn(json.dumps(result.to_dict(), indent=2))
    if not result.performed or not result.verified:
        return 1
    return 0


def _run_verify(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    try:
        hands = _hands(args, hands_factory)
        result = hands.verify(args.ref, expect=_expect_from_args(args))
    except (BackendUnavailableError, DesktopHandsError) as exc:
        print_fn(f"puspa computer verify: failed: {exc}")
        return 1
    print_fn(
        json.dumps(
            {
                "ref": result.ref,
                "found": result.found,
                "verified": result.verified,
                "reason": result.reason,
                "before": result.before,
                "after": result.after,
            },
            indent=2,
        )
    )
    if not result.verified:
        return 1
    return 0


def _visual_expect(args: argparse.Namespace) -> dict | None:
    expect: dict = {}
    if getattr(args, "expect_text", None):
        expect["text"] = args.expect_text
    if getattr(args, "expect_absent_text", None):
        expect["absent_text"] = args.expect_absent_text
    return expect or None


def _fallback(args: argparse.Namespace, fallback_factory) -> VisualFallback:
    backend_kind = getattr(args, "backend", "auto")
    if fallback_factory is not None:
        return fallback_factory(backend_kind)
    return _default_fallback(backend_kind)


def _run_visual(args: argparse.Namespace, *, fallback_factory, print_fn) -> int:
    try:
        fallback = _fallback(args, fallback_factory)
    except VisualFallbackError as exc:
        print_fn(f"puspa computer visual: failed: {exc}")
        return 1
    if args.visual_command == "locate":
        try:
            element = fallback.locate_text(args.text)
        except VisualFallbackError as exc:
            print_fn(f"puspa computer visual locate: failed: {exc}")
            return 1
        if element is None:
            print_fn(f"puspa computer visual locate: {args.text!r} not found on the screen")
            return 1
        print_fn(json.dumps(element.to_dict(), indent=2))
        return 0
    if args.visual_command == "click":
        try:
            result = fallback.click_text(args.text, expect=_visual_expect(args))
        except VisualFallbackError as exc:
            print_fn(f"puspa computer visual click: failed: {exc}")
            return 1
        except Exception as exc:  # permission denial from the prompt or gate
            print_fn(f"puspa computer visual click: refused: {exc}")
            return 1
        print_fn(json.dumps(result.to_dict(), indent=2))
        if not result.performed or not result.verified:
            return 1
        return 0
    return 0


def _web(args: argparse.Namespace, web_factory) -> WebBridge:
    backend_kind = getattr(args, "backend", "auto")
    if web_factory is not None:
        return web_factory(backend_kind)
    return _default_web(backend_kind)


def _run_web(args: argparse.Namespace, *, web_factory, print_fn) -> int:
    try:
        bridge = _web(args, web_factory)
    except VisualFallbackError as exc:
        print_fn(f"puspa computer web: failed: {exc}")
        return 1
    try:
        if args.web_command == "open":
            page = bridge.open(args.url)
            print_fn(json.dumps(page.to_dict(), indent=2))
            return 0
        if args.web_command == "find":
            bridge.open(args.url)
            element = bridge.find_text(args.text)
            if element is None:
                print_fn(f"puspa computer web find: {args.text!r} not found on the page")
                return 1
            print_fn(json.dumps(element.to_dict(), indent=2))
            return 0
        if args.web_command == "click":
            bridge.open(args.url)
            result = bridge.click_text(args.text, expect=_visual_expect(args))
            print_fn(json.dumps(result.to_dict(), indent=2))
            if not result.performed or not result.verified:
                return 1
            return 0
    except VisualFallbackError as exc:
        print_fn(f"puspa computer web {args.web_command}: failed: {exc}")
        return 1
    except Exception as exc:  # permission denial from the prompt or gate
        print_fn(f"puspa computer web {args.web_command}: refused: {exc}")
        return 1
    finally:
        bridge.close()
    return 0
