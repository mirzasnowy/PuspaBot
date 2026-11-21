"""``puspa computer`` command group (V2-31, core).

``snapshot`` prints the accessibility tree as JSON (skeleton by
default), ``click`` clicks one ref through the permission gate
and re-reads to verify, ``verify`` re-reads one ref and checks
it. ``--backend auto`` (the default) uses the real Linux AT-SPI
backend and fails closed with a plain error when there is no
desktop; it never silently falls back to the fake tree.
``--backend fake`` reads a small in-memory demo tree instead.
"""

from __future__ import annotations

import argparse
import json

from .backend import BackendUnavailableError, make_backend
from .hands import DesktopHands, DesktopHandsError
from .tree import snapshot_to_dict

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
    return computer


def _default_hands(backend_kind: str) -> DesktopHands:
    return DesktopHands(make_backend(backend_kind))


def run_computer(args: argparse.Namespace, *, hands_factory=None, print_fn=print) -> int:
    """Run one ``puspa computer`` command. Returns an exit code."""
    if args.computer_command == "snapshot":
        return _run_snapshot(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "click":
        return _run_click(args, hands_factory=hands_factory, print_fn=print_fn)
    if args.computer_command == "verify":
        return _run_verify(args, hands_factory=hands_factory, print_fn=print_fn)
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
