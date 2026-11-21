"""Command-line entry point for PuspaBot (public package scaffolding).

Split Step 1: this is the minimal shim behind the ``puspa`` and
``puspa-cli`` console entry points. It ships ``puspa web`` / ``puspa
serve`` (start the local gateway and open the PWA), ``puspa memory
import`` / ``rollback`` / ``forget`` (memory import, V2-10), plus
help. The full one-shot CLI lands in a later step of the split;
until then any other subcommand prints help and says so.
"""

from __future__ import annotations

import argparse
import json
import sys

FULL_CLI_NOTE = (
    "The full PuspaBot CLI lands in a later step of the public/private "
    "split; this build ships `puspa web` / `puspa serve` plus help."
)

_KNOWN_COMMANDS = ("web", "serve", "memory")

_IMPORT_SOURCES = ("kosmos", "memory_md", "generic_json", "generic_markdown")


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

    memory = sub.add_parser(
        "memory",
        help="Memory import, rollback and forget (V2-10).",
        description="Memory import, rollback and forget (V2-10).",
    )
    memory_sub = memory.add_subparsers(dest="memory_command", metavar="MEMORY_COMMAND")
    memory_import = memory_sub.add_parser(
        "import",
        help="Import memory from a Kosmos archive, a MEMORY.md file, or another assistant's export.",
        description=(
            "Import memory from a Kosmos archive, a MEMORY.md file, or another "
            "assistant's export. Without --approve this only shows the dry-run "
            "plan and writes nothing."
        ),
    )
    memory_import.add_argument("path", help="Archive directory or zip, MEMORY.md, or export file.")
    memory_import.add_argument("--user", required=True, help="User id the memory belongs to.")
    memory_import.add_argument("--agent", default="puspa", help="Agent id for the ledger event.")
    memory_import.add_argument(
        "--source",
        choices=_IMPORT_SOURCES,
        default=None,
        help="Import source (auto-detected from the path when omitted).",
    )
    memory_import.add_argument(
        "--approve", action="store_true", help="Apply the import (without it, dry-run only)."
    )
    memory_import.add_argument(
        "--replay-events",
        action="store_true",
        help="Replay validated archive ledger events into the live ledger.",
    )
    memory_rollback = memory_sub.add_parser(
        "rollback",
        help="Roll back one import by batch id (expires exactly its facts).",
        description="Roll back one import by batch id (expires exactly its facts).",
    )
    memory_rollback.add_argument("batch_id", help="Import batch id to roll back.")
    memory_rollback.add_argument("--user", required=True, help="User id the memory belongs to.")
    memory_rollback.add_argument("--agent", default="puspa", help="Agent id for the ledger event.")
    memory_forget = memory_sub.add_parser(
        "forget",
        help="Forget one fact and block its re-import.",
        description="Forget one fact and block its re-import.",
    )
    memory_forget.add_argument("key", help="Fact key to forget.")
    memory_forget.add_argument("--user", required=True, help="User id the memory belongs to.")
    memory_forget.add_argument("--agent", default="puspa", help="Agent id for the ledger event.")
    memory_forget.add_argument(
        "--content", default=None, help="Fact text (defaults to the stored text)."
    )
    return parser


def _run_web(host: str, port: int) -> int:
    """Start the local gateway (lazy import: ``--help`` stays fast)."""
    from puspa.pwa.app import run as run_gateway

    run_gateway(host=host, port=port)
    return 0


def _run_memory_import(args: argparse.Namespace) -> int:
    """Run ``puspa memory import`` (lazy import: ``--help`` stays fast)."""
    from puspa.memory.importer import import_memory

    try:
        result = import_memory(
            args.user,
            args.path,
            source=args.source,
            approve=args.approve,
            agent_id=args.agent,
            replay_events=args.replay_events,
        )
    except Exception as exc:
        print(f"puspa memory import: failed: {exc}")
        return 1
    print(json.dumps(result, indent=2, default=str))
    if result["status"] in ("awaiting_approval", "dry_run"):
        print("Nothing was written. Re-run with --approve to apply this import.")
    return 0


def _run_memory_rollback(args: argparse.Namespace) -> int:
    """Run ``puspa memory rollback`` (lazy import: ``--help`` stays fast)."""
    from puspa.memory.importer import rollback_import

    try:
        result = rollback_import(args.user, args.batch_id, agent_id=args.agent)
    except Exception as exc:
        print(f"puspa memory rollback: failed: {exc}")
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def _run_memory_forget(args: argparse.Namespace) -> int:
    """Run ``puspa memory forget`` (lazy import: ``--help`` stays fast)."""
    from puspa.memory.importer import forget_memory_item

    try:
        result = forget_memory_item(args.user, args.key, content=args.content, agent_id=args.agent)
    except Exception as exc:
        print(f"puspa memory forget: failed: {exc}")
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def _run_memory(args: argparse.Namespace) -> int:
    if args.memory_command == "import":
        return _run_memory_import(args)
    if args.memory_command == "rollback":
        return _run_memory_rollback(args)
    if args.memory_command == "forget":
        return _run_memory_forget(args)
    build_parser().parse_args(["memory", "--help"])
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
    if parsed.command in ("web", "serve"):
        return _run_web(host=parsed.host, port=parsed.port)
    if parsed.command == "memory":
        return _run_memory(parsed)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
