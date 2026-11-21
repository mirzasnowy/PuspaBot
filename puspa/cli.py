"""Command-line entry point for PuspaBot (public package scaffolding).

Split Step 1: this is the minimal shim behind the ``puspa`` and
``puspa-cli`` console entry points. It ships ``puspa web`` / ``puspa
serve`` (start the local gateway and open the PWA) plus help. The
full one-shot CLI lands in a later step of the split; until then any
other subcommand prints help and says so.
"""

from __future__ import annotations

import argparse
import sys

FULL_CLI_NOTE = (
    "The full PuspaBot CLI lands in a later step of the public/private "
    "split; this build ships `puspa web` / `puspa serve` plus help."
)

_KNOWN_COMMANDS = ("web", "serve")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``puspa`` entry point."""
    parser = argparse.ArgumentParser(
        prog="puspa",
        description="PuspaBot — a personal AI agent you run yourself.",
        epilog=FULL_CLI_NOTE,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    web = sub.add_parser(
        "web",
        aliases=["serve"],
        help="Start the local gateway and serve the PWA.",
        description="Start the local gateway and serve the PWA.",
    )
    web.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    web.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000).")
    return parser


def _run_web(host: str, port: int) -> int:
    """Start the local gateway (lazy import: ``--help`` stays fast)."""
    from puspa.pwa.app import run as run_gateway

    run_gateway(host=host, port=port)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``puspa`` and ``puspa-cli`` console scripts."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and not args[0].startswith("-") and args[0] not in _KNOWN_COMMANDS:
        print(f"puspa: unknown command {args[0]!r}")
        print(FULL_CLI_NOTE)
        print()
        build_parser().print_help()
        return 0
    parser = build_parser()
    parsed = parser.parse_args(args)
    if parsed.command in _KNOWN_COMMANDS:
        return _run_web(host=parsed.host, port=parsed.port)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
