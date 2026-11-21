"""Command-line entry point for PuspaBot (public package scaffolding).

Split Step 1: this is the minimal shim behind the ``puspa`` and
``puspa-cli`` console entry points. It ships ``puspa web`` / ``puspa
serve`` (start the local gateway and open the PWA), ``puspa memory
export`` (a portable, encrypted memory archive, V2-09), ``puspa memory
import`` / ``rollback`` / ``forget`` (memory import, V2-10), ``puspa
conversation export`` / ``import`` (conversation export, V2-11), ``puspa
mcp`` (serve Puspa's tools to outside agents over MCP stdio), plus
help. The full one-shot CLI lands in a later step of the split;
until then any other subcommand prints help and says so.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import UTC, datetime

from puspa.computer.cli import add_computer_parser, run_computer
from puspa.mcp_server.cli import add_mcp_parser, run_mcp

FULL_CLI_NOTE = (
    "The full PuspaBot CLI lands in a later step of the public/private "
    "split; this build ships `puspa web` / `puspa serve`, `puspa memory "
    "export` / `import`, `puspa mcp`, plus help."
)

_KNOWN_COMMANDS = ("web", "serve", "memory", "conversation", "provider", "computer", "mcp")

#: Environment variable holding the export passphrase (never a
#: command-line argument: arguments are visible in the process list).
EXPORT_PASSPHRASE_ENV = "PUSPA_EXPORT_PASSPHRASE"

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
        help="Memory export and import (V2-09, V2-10).",
        description="Memory export and import (V2-09, V2-10).",
    )
    memory_sub = memory.add_subparsers(dest="memory_command", metavar="MEMORY_COMMAND")
    export = memory_sub.add_parser(
        "export",
        help="Export one user's memory as an encrypted archive.",
        description=(
            "Export one user's memory as a portable archive "
            "(MEMORY.md, ledger events, graph, people, values, "
            "skills, causal hypotheses, known unknowns, manifest). "
            "Full exports are encrypted by default; the passphrase "
            "comes from the PUSPA_EXPORT_PASSPHRASE environment "
            "variable or an interactive prompt, never an argument."
        ),
    )
    export.add_argument("--user", required=True, help="User id to export.")
    export.add_argument("--out", required=True, help="Where to write the archive file.")
    export.add_argument(
        "--layers",
        nargs="+",
        default=None,
        metavar="LAYER",
        help="Only these sections (memory events graph people values skills causal ignorance).",
    )
    export.add_argument(
        "--from",
        dest="date_from",
        default=None,
        metavar="ISO-DATE",
        help="Only events/episodes on or after this ISO-8601 date.",
    )
    export.add_argument(
        "--to",
        dest="date_to",
        default=None,
        metavar="ISO-DATE",
        help="Only events/episodes before this ISO-8601 date.",
    )
    export.add_argument("--person", default=None, help="Only this person's record.")
    export.add_argument("--project", default=None, help="Only one task (its task id).")
    export.add_argument(
        "--no-encrypt",
        action="store_true",
        help="Write a plain ZIP instead of an encrypted archive (debugging only).",
    )
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
    conversation = sub.add_parser(
        "conversation",
        help="Conversation export and import (V2-11).",
        description="Conversation export and import (V2-11).",
    )
    conversation_sub = conversation.add_subparsers(
        dest="conversation_command", metavar="CONVERSATION_COMMAND"
    )
    conversation_export = conversation_sub.add_parser(
        "export",
        help="Export one conversation (or all) to a portable zip archive.",
        description=(
            "Export one conversation (--conversation <id>) or every "
            "conversation in the profile (no --conversation) to a zip "
            "archive with a manifest, per-file hashes and counts."
        ),
    )
    conversation_export.add_argument(
        "--conversation", default=None, help="Conversation id to export (omit for all)."
    )
    conversation_export.add_argument("--out", required=True, help="Zip archive file to write.")
    conversation_export.add_argument(
        "--profile", default=None, help="Profile name (defaults to the default profile)."
    )
    conversation_export.add_argument(
        "--home", default=None, help="Puspa home directory (defaults to PUSPA_HOME or ~/.puspa)."
    )
    conversation_export.add_argument(
        "--live-home",
        action="store_true",
        help=(
            "Allow touching the live home (~/.puspa). Also needs "
            "PUSPA_ALLOW_LIVE_HOME=1 in the environment."
        ),
    )
    conversation_import = conversation_sub.add_parser(
        "import",
        help="Import conversations from a portable archive.",
        description=(
            "Import conversations from a zip archive. Without --approve "
            "this only shows the dry-run plan and writes nothing. "
            "Re-importing the same archive is a no-op; a conflicting "
            "conversation refuses the whole import."
        ),
    )
    conversation_import.add_argument("--file", required=True, help="Archive zip (or directory).")
    conversation_import.add_argument(
        "--profile", default=None, help="Profile name (defaults to the default profile)."
    )
    conversation_import.add_argument(
        "--home", default=None, help="Puspa home directory (defaults to PUSPA_HOME or ~/.puspa)."
    )
    conversation_import.add_argument(
        "--live-home",
        action="store_true",
        help=(
            "Allow touching the live home (~/.puspa). Also needs "
            "PUSPA_ALLOW_LIVE_HOME=1 in the environment."
        ),
    )
    conversation_import.add_argument(
        "--approve", action="store_true", help="Apply the import (without it, dry-run only)."
    )
    provider = sub.add_parser(
        "provider",
        help="Log in to a subscription provider (V2-28, V2-29).",
        description=(
            "Puspa-native subscription login: `puspa provider login <provider>` "
            "logs in without nanobot. PKCE providers (xai, codex, antigravity, "
            "gemini_cli) print an authorize URL and ask for the pasted code; "
            "device-flow providers (github_copilot, kimi, kiro, grok_cli, nous, "
            "muse, minimax_code, kilocode, qoder) print a user code and a "
            "verification URL to approve in a browser; polling providers (glm, "
            "codebuddy) print a sign-in URL to open and wait; pasted-token "
            "providers (claude setup token, cursor import, cline blob) ask for "
            "the token to paste. Tokens live only in the per-user vault, never "
            "printed or logged. Plan-credential logins are local-only: on a "
            "hosted deployment they are refused."
        ),
    )
    provider_sub = provider.add_subparsers(dest="provider_command", metavar="PROVIDER_COMMAND")
    provider_login = provider_sub.add_parser(
        "login",
        help="Log in to a subscription provider.",
        description="Log in to a subscription provider and store its tokens in the vault.",
    )
    provider_login.add_argument(
        "provider_name",
        metavar="PROVIDER",
        choices=_provider_names(),
        help="Provider to log in to.",
    )
    provider_login.add_argument("--user", required=True, help="User id the tokens belong to.")
    provider_logout = provider_sub.add_parser(
        "logout",
        help="Log out of a subscription provider (clears its vault tokens).",
        description="Delete one provider's tokens from the vault.",
    )
    provider_logout.add_argument(
        "provider_name",
        metavar="PROVIDER",
        choices=_provider_names(),
        help="Provider to log out of.",
    )
    provider_logout.add_argument("--user", required=True, help="User id the tokens belong to.")
    provider_status = provider_sub.add_parser(
        "status",
        help="Show login state for every subscription provider.",
        description="Show login state (logged in / expiring / expired / not logged in) per provider.",
    )
    provider_status.add_argument(
        "--user",
        default=None,
        help="User id to check (defaults to PUSPA_USER_ID or 'local-user').",
    )
    add_computer_parser(sub)
    add_mcp_parser(sub)
    return parser


def _run_web(host: str, port: int) -> int:
    """Start the local gateway (lazy import: ``--help`` stays fast)."""
    from puspa.pwa.app import run as run_gateway

    run_gateway(host=host, port=port)
    return 0


def _parse_scope_date(raw: str | None, name: str) -> datetime | None:
    """An ISO-8601 date as an aware datetime (naive means UTC)."""
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 date, got {raw!r}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _run_memory_export(args: argparse.Namespace) -> int:
    """Export one user's memory archive (lazy import: ``--help`` stays fast)."""
    from puspa.memory.memory_export import ExportScope, export_memory

    encrypted = not args.no_encrypt
    passphrase = os.environ.get(EXPORT_PASSPHRASE_ENV) or None
    if encrypted and passphrase is None:
        entered = getpass.getpass("Export passphrase: ")
        passphrase = entered or None
    try:
        scope = ExportScope(
            layers=tuple(args.layers) if args.layers else None,
            date_from=_parse_scope_date(args.date_from, "--from"),
            date_to=_parse_scope_date(args.date_to, "--to"),
            person=args.person,
            project=args.project,
        )
        result = export_memory(
            args.user,
            args.out,
            passphrase,
            scope,
            encrypted=encrypted,
        )
    except ValueError as exc:
        print(f"puspa: memory export failed: {exc}", file=sys.stderr)
        return 1
    counts = ", ".join(f"{name}={count}" for name, count in sorted(result.counts.items()))
    print(f"Wrote {result.path} ({counts})")
    print(f"Archive sha256: {result.archive_sha256}")
    print(f"Encrypted: {'yes' if result.encrypted else 'no'}")
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


def _run_conversation_export(args: argparse.Namespace) -> int:
    """Run ``puspa conversation export`` (lazy import: ``--help`` stays fast)."""
    from puspa.sessions import open_persistence_reader
    from puspa.sessions.export import export_conversations

    try:
        reader = open_persistence_reader(
            args.profile, home=args.home, allow_live_home=args.live_home
        )
    except Exception as exc:
        print(f"puspa conversation export: failed: {exc}")
        return 1
    try:
        result = export_conversations(
            reader,
            conversation_ids=[args.conversation] if args.conversation else None,
            out=args.out,
        )
    except Exception as exc:
        print(f"puspa conversation export: failed: {exc}")
        return 1
    finally:
        reader.close()
    print(json.dumps(result, indent=2, default=str))
    return 0


def _run_conversation_import(args: argparse.Namespace) -> int:
    """Run ``puspa conversation import`` (lazy import: ``--help`` stays fast)."""
    from puspa.sessions import open_persistence
    from puspa.sessions.export import import_conversations

    try:
        # The CLI runs on one machine: the file lock on state.db is
        # the operative cross-process guard here, so the Postgres
        # advisory layer is skipped (a CLI run must not assume a
        # database is reachable).
        gateway = open_persistence(
            args.profile, home=args.home, allow_live_home=args.live_home, advisory_lock=False
        )
    except Exception as exc:
        print(f"puspa conversation import: failed: {exc}")
        return 1
    try:
        result = import_conversations(args.file, writer=gateway, approve=args.approve)
    except Exception as exc:
        print(f"puspa conversation import: failed: {exc}")
        return 1
    finally:
        gateway.close()
    print(json.dumps(result, indent=2, default=str))
    if result["status"] in ("awaiting_approval", "dry_run"):
        print("Nothing was written. Re-run with --approve to apply this import.")
    if result["status"] == "refused":
        print("Nothing was written. Resolve the conflicts and re-run.")
        return 1
    return 0


def _run_conversation(args: argparse.Namespace) -> int:
    if args.conversation_command == "export":
        return _run_conversation_export(args)
    if args.conversation_command == "import":
        return _run_conversation_import(args)
    build_parser().parse_args(["conversation", "--help"])
    return 0


def _provider_service():
    """Build the subscription-login service (test seam: monkeypatch this)."""
    from puspa.router.provider_login import ProviderLoginService

    return ProviderLoginService()


def _provider_names() -> tuple[str, ...]:
    """All known subscription providers, for the CLI choices."""
    from puspa.router.provider_login import known_provider_configs

    return tuple(sorted(known_provider_configs()))


#: Loopback redirect used by the terminal PKCE flow: the user opens
#: the authorize URL, approves, and pastes the returned code back
#: here (the same paste-back shape as `claude setup-token`).
_PROVIDER_LOOPBACK_REDIRECT = "http://127.0.0.1:8765/callback"


def _run_provider_login(args, *, service=None, input_fn=None, print_fn=None) -> int:
    """`puspa provider login <provider>`: interactive login, vault storage."""
    from puspa.router.provider_login import ProviderLoginError

    input_fn = input_fn or input
    print_fn = print_fn or print
    service = service if service is not None else _provider_service()
    try:
        config = service.config(args.provider_name)
    except ProviderLoginError as exc:
        print_fn(str(exc))
        return 1
    try:
        if config.flow == "device":
            started = service.start_device(args.provider_name)
            print_fn(f"Open {started['verification_uri']} and enter code: {started['user_code']}")
            print_fn("Waiting for approval in your browser...")
            service.wait_for_device_login(args.user, args.provider_name, started["poll_key"])
        elif config.flow == "polling":
            started = service.start_polling(args.provider_name)
            print_fn(f"Open this URL and sign in:\n{started['verification_uri']}")
            print_fn("Waiting for the sign-in to finish...")
            service.wait_for_polling_login(args.user, args.provider_name, started["poll_key"])
        elif config.flow in ("setup_token", "import_token"):
            started = service.start_pasted_token(args.provider_name)
            if started["help"]:
                print_fn(started["help"])
            token = input_fn("Paste the token: ").strip()
            if not token:
                print_fn("No token pasted; login cancelled.")
                return 1
            service.finish_pasted_token(args.user, args.provider_name, token)
        elif config.flow == "encoded_token":
            if config.setup_token_help:
                print_fn(config.setup_token_help)
            code = input_fn("Paste the token code: ").strip()
            if not code:
                print_fn("No code pasted; login cancelled.")
                return 1
            service.finish_encoded_token(args.user, args.provider_name, code)
        else:
            started = service.start_pkce(args.provider_name, _PROVIDER_LOOPBACK_REDIRECT)
            print_fn(f"Open this URL and approve the login:\n{started['url']}")
            code = input_fn("Paste the authorization code: ").strip()
            if not code:
                print_fn("No code pasted; login cancelled.")
                return 1
            service.finish_pkce(args.user, started["state"], code)
    except ProviderLoginError as exc:
        print_fn(str(exc))
        return 1
    print_fn(f"Logged in to {args.provider_name}. Token stored in your vault.")
    return 0


def _run_provider_logout(args, *, service=None, print_fn=None) -> int:
    """`puspa provider logout <provider>`: clear the vault tokens."""
    from puspa.router.provider_login import ProviderLoginError

    print_fn = print_fn or print
    service = service if service is not None else _provider_service()
    try:
        service.logout(args.user, args.provider_name)
    except ProviderLoginError as exc:
        print_fn(str(exc))
        return 1
    print_fn(f"Logged out of {args.provider_name}. Vault tokens cleared.")
    return 0


def _run_provider_status(args, *, service=None, print_fn=None) -> int:
    """`puspa provider status`: login state per provider."""
    print_fn = print_fn or print
    service = service if service is not None else _provider_service()
    user_id = args.user or os.environ.get("PUSPA_USER_ID") or "local-user"
    for entry in service.list_status(user_id):
        ready = "" if entry["login_ready"] else " (login not wired yet)"
        expires = entry["expires_at"] or "never"
        print_fn(
            f"{entry['provider']}: {entry['state']} "
            f"(flow: {entry['flow']}, expires: {expires}){ready}"
        )
    return 0


def _run_provider(args: argparse.Namespace) -> int:
    if args.provider_command == "login":
        return _run_provider_login(args)
    if args.provider_command == "logout":
        return _run_provider_logout(args)
    if args.provider_command == "status":
        return _run_provider_status(args)
    build_parser().parse_args(["provider", "--help"])
    return 0


def _run_memory(args: argparse.Namespace) -> int:
    if args.memory_command == "export":
        return _run_memory_export(args)
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
    if parsed.command == "provider":
        return _run_provider(parsed)
    if parsed.command == "memory":
        return _run_memory(parsed)
    if parsed.command == "conversation":
        return _run_conversation(parsed)
    if parsed.command == "computer":
        if parsed.computer_command is None:
            parser.print_help()
            return 0
        return run_computer(parsed)
    if parsed.command == "mcp":
        return run_mcp(parsed)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
