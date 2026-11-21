"""``puspa computer`` command group (V2-31 core, V2-32 visual fallback + web bridge,
V2-33 input + windows).


``snapshot`` prints the accessibility tree as JSON (skeleton by
default), ``click`` clicks one ref through the permission gate
and re-reads to verify, ``verify`` re-reads one ref and checks
it. V2-33 adds ``type``, ``shortcut``, ``scroll``, ``drag``,
``clipboard`` (read/write), ``windows``, ``open-app``,
``focus-window``, ``switch-window``, and ``task`` (a JSON list
of steps run end to end, stopping at the first unverified one).

``--backend auto`` (the default) uses the real Linux AT-SPI
backend and fails closed with a plain error when there is no
desktop; it never silently falls back to the fake tree.
``--backend fake`` reads a small in-memory demo tree instead.

Clipboard content is never printed: ``clipboard read`` reports
only the length.


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
    if getattr(args, "expect_value", None):
        expect["value"] = args.expect_value
    if getattr(args, "expect_value_contains", None):
        expect["value_contains"] = args.expect_value_contains
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
    parser.add_argument(
        "--expect-value", default=None, help="Text the element should hold after the action."
    )
    parser.add_argument(
        "--expect-value-contains",
        default=None,
        help="Text the element should contain after the action.",
    )


def add_computer_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the ``computer`` group to the top-level parser."""
    computer = subparsers.add_parser(
        "computer",
        help="Desktop hands: snapshot, click, verify, type, shortcut, scroll, drag, clipboard, windows (V2-31 + V2-33).",
        description=(
            "Drive the local desktop through its accessibility tree: read a snapshot, click a ref, "
            "type, press shortcuts, scroll, drag, use the clipboard, manage windows. Linux first "
            "(AT-SPI); every action asks the user first."
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

    type_cmd = computer_sub.add_parser(
        "type",
        help="Type text into one editable element by ref, then verify by re-reading.",
        description="Type text into an editable element. Asks the user first, then re-reads to verify.",
    )
    type_cmd.add_argument("ref", help="Element ref from `puspa computer snapshot`, e.g. n5.")
    type_cmd.add_argument("text", help="Text to type into the element.")
    _add_backend_arg(type_cmd)
    _add_expect_args(type_cmd)
    shortcut = computer_sub.add_parser(
        "shortcut",
        help="Press a keyboard chord like ctrl+a, then verify by re-reading.",
        description="Press a keyboard chord (keys joined with +, e.g. ctrl+a). Asks the user first.",
    )
    shortcut.add_argument("keys", help="Chord to press, e.g. ctrl+a.")
    shortcut.add_argument(
        "--ref", default=None, help="Element ref to target; without one, the focused element."
    )
    _add_backend_arg(shortcut)
    _add_expect_args(shortcut)
    scroll = computer_sub.add_parser(
        "scroll",
        help="Scroll at one element by ref, then verify by re-reading.",
        description="Scroll at an element. Asks the user first, then re-reads to verify.",
    )
    scroll.add_argument("ref", help="Element ref from `puspa computer snapshot`, e.g. n5.")
    scroll.add_argument(
        "--direction",
        choices=["up", "down", "left", "right"],
        default="down",
        help="Scroll direction (default down).",
    )
    scroll.add_argument("--amount", type=int, default=3, help="Scroll clicks (default 3).")
    _add_backend_arg(scroll)
    _add_expect_args(scroll)
    drag = computer_sub.add_parser(
        "drag",
        help="Drag one element onto another by ref, then verify by re-reading.",
        description="Drag one element onto another. Asks the user first, then re-reads the drop target.",
    )
    drag.add_argument("ref_from", help="Element ref to drag, e.g. n7.")
    drag.add_argument("ref_to", help="Element ref to drop onto, e.g. n8.")
    _add_backend_arg(drag)
    _add_expect_args(drag)
    clipboard = computer_sub.add_parser(
        "clipboard",
        help="Read or write the session clipboard (content is never printed).",
        description="Clipboard read/write. Read reports only the length; the content is never printed.",
    )
    _add_backend_arg(clipboard)
    clipboard_sub = clipboard.add_subparsers(dest="clipboard_command", metavar="CLIPBOARD_COMMAND")
    clipboard_sub.add_parser("read", help="Read the clipboard (reports only its length).")
    clipboard_write = clipboard_sub.add_parser("write", help="Replace the clipboard contents.")
    clipboard_write.add_argument("text", help="Text to put on the clipboard.")
    windows = computer_sub.add_parser(
        "windows",
        help="List the open windows.",
        description="List the open windows (id, app, title, focused). Read-only.",
    )
    _add_backend_arg(windows)
    open_app = computer_sub.add_parser(
        "open-app",
        help="Open an app by name, then verify a window for it appeared.",
        description="Open an app by executable name (or desktop entry). Asks the user first.",
    )
    open_app.add_argument("name", help="App to open, e.g. gedit.")
    _add_backend_arg(open_app)
    _add_expect_args(open_app)
    focus_window = computer_sub.add_parser(
        "focus-window",
        help="Bring one window to the front by id, then verify it is focused.",
        description="Focus a window by id from `puspa computer windows`. Asks the user first.",
    )
    focus_window.add_argument("window_id", help="Window id, e.g. 0.1.")
    _add_backend_arg(focus_window)
    switch_window = computer_sub.add_parser(
        "switch-window",
        help="Switch to the next window, then verify the focus moved.",
        description="Switch to the next window after the focused one (cycling). Asks the user first.",
    )
    _add_backend_arg(switch_window)
    task = computer_sub.add_parser(
        "task",
        help="Run a JSON list of steps end to end, stopping at the first unverified one.",
        description="Run a multi-step task from a JSON file (a list of step objects, see docs/computer-hands.md).",
    )
    task.add_argument("steps_file", help="Path to a JSON file holding a list of steps.")
    _add_backend_arg(task)

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

    if args.computer_command == "type":
        return _run_type(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "shortcut":
        return _run_shortcut(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "scroll":
        return _run_scroll(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "drag":
        return _run_drag(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "clipboard":
        return _run_clipboard(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "windows":
        return _run_windows(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "open-app":
        return _run_open_app(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "focus-window":
        return _run_focus_window(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "switch-window":
        return _run_switch_window(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "task":
        return _run_task(args, hands_factory=hands_factory, print_fn=print_fn)

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


def _run_action(args: argparse.Namespace, *, hands_factory, print_fn, label: str, call) -> int:
    """Run one gated input/window action, print its JSON result,
    exit non-zero when it did not perform or did not verify."""
    try:
        hands = _hands(args, hands_factory)
        result = call(hands)
    except (BackendUnavailableError, DesktopHandsError) as exc:
        print_fn(f"puspa computer {label}: failed: {exc}")
        return 1
    except Exception as exc:  # permission denial from the prompt or gate
        print_fn(f"puspa computer {label}: refused: {exc}")
        return 1
    print_fn(json.dumps(result.to_dict(), indent=2))
    if not result.performed or not result.verified:
        return 1
    return 0


def _run_type(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    return _run_action(
        args,
        hands_factory=hands_factory,
        print_fn=print_fn,
        label="type",
        call=lambda hands: hands.type_text(args.ref, args.text, expect=_expect_from_args(args)),
    )


def _run_shortcut(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    return _run_action(
        args,
        hands_factory=hands_factory,
        print_fn=print_fn,
        label="shortcut",
        call=lambda hands: hands.shortcut(args.keys, args.ref, expect=_expect_from_args(args)),
    )


def _run_scroll(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    return _run_action(
        args,
        hands_factory=hands_factory,
        print_fn=print_fn,
        label="scroll",
        call=lambda hands: hands.scroll(
            args.ref, args.direction, args.amount, expect=_expect_from_args(args)
        ),
    )


def _run_drag(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    return _run_action(
        args,
        hands_factory=hands_factory,
        print_fn=print_fn,
        label="drag",
        call=lambda hands: hands.drag(args.ref_from, args.ref_to, expect=_expect_from_args(args)),
    )


def _run_clipboard(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    if getattr(args, "clipboard_command", None) is None:
        print_fn("puspa computer clipboard: needs a subcommand (read or write)")
        return 1
    try:
        hands = _hands(args, hands_factory)
        if args.clipboard_command == "read":
            result = hands.clipboard_read()
            # Content is never printed: only the length leaves this process.
            print_fn(
                json.dumps(
                    {
                        "action": result.action,
                        "performed": result.performed,
                        "verified": result.verified,
                        "reason": result.reason,
                        "length": result.length,
                        "note": "content is not printed; read it through the Python or MCP API",
                    },
                    indent=2,
                )
            )
            return 0 if result.performed else 1
        result = hands.clipboard_write(args.text)
    except (BackendUnavailableError, DesktopHandsError) as exc:
        print_fn(f"puspa computer clipboard: failed: {exc}")
        return 1
    except Exception as exc:  # permission denial from the prompt or gate
        print_fn(f"puspa computer clipboard: refused: {exc}")
        return 1
    print_fn(json.dumps(result.to_dict(), indent=2))
    if not result.performed or not result.verified:
        return 1
    return 0


def _run_windows(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    try:
        hands = _hands(args, hands_factory)
        windows = hands.list_windows()
    except (BackendUnavailableError, DesktopHandsError) as exc:
        print_fn(f"puspa computer windows: failed: {exc}")
        return 1
    print_fn(json.dumps([window.to_dict() for window in windows], indent=2))
    return 0


def _run_open_app(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    return _run_action(
        args,
        hands_factory=hands_factory,
        print_fn=print_fn,
        label="open-app",
        call=lambda hands: hands.open_app(args.name, expect=_expect_from_args(args)),
    )


def _run_focus_window(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    return _run_action(
        args,
        hands_factory=hands_factory,
        print_fn=print_fn,
        label="focus-window",
        call=lambda hands: hands.focus_window(args.window_id),
    )


def _run_switch_window(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    return _run_action(
        args,
        hands_factory=hands_factory,
        print_fn=print_fn,
        label="switch-window",
        call=lambda hands: hands.switch_window(),
    )


def _run_task(args: argparse.Namespace, *, hands_factory, print_fn) -> int:
    try:
        with open(args.steps_file, encoding="utf-8") as handle:
            steps = json.load(handle)
    except OSError as exc:
        print_fn(f"puspa computer task: cannot read {args.steps_file}: {exc}")
        return 1
    except json.JSONDecodeError as exc:
        print_fn(f"puspa computer task: {args.steps_file} is not valid JSON: {exc}")
        return 1
    if not isinstance(steps, list):
        print_fn("puspa computer task: the steps file must hold a JSON list of steps")
        return 1
    try:
        hands = _hands(args, hands_factory)
        outcome = hands.run_task(steps)
    except (BackendUnavailableError, DesktopHandsError) as exc:
        print_fn(f"puspa computer task: failed: {exc}")
        return 1
    except Exception as exc:  # permission denial from the prompt or gate
        print_fn(f"puspa computer task: refused: {exc}")
        return 1
    print_fn(json.dumps(outcome, indent=2))
    return 0 if outcome["completed"] else 1


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
