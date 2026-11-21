"""``puspa mcp`` command: serve Puspa's tools over MCP stdio.

Reads JSON-RPC requests from stdin, writes replies to stdout,
until EOF — the shape an outside MCP client (Claude Code, Codex,
or any client that can spawn a stdio server) needs. Nothing but
replies goes to stdout; errors and the startup line go to
stderr, so piping stdout straight into a client is safe.

Policy: ``--policy`` takes a JSON file with ``deny`` / ``allow``
rule lists (the gateway's rule language); a malformed policy
refuses to start (exit 1) instead of serving unprotected. With
no policy the explicit allow-everything default applies.
``--dry-run`` records decisions without enforcing them (a denied
call is still forwarded, and the decision says so).

Desktop hands: ``--backend auto`` (the default) uses the real
Linux AT-SPI backend and fails closed with a plain error when
there is no desktop; it never silently falls back to the fake
tree. ``--backend fake`` runs the in-memory demo tree instead.
"""

from __future__ import annotations

import argparse
import os
import sys

from puspa.policy.gateway import PolicyError

__all__ = ["add_mcp_parser", "run_mcp"]


def add_mcp_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Add the ``mcp`` subparser (``puspa mcp``)."""
    parser = subparsers.add_parser(
        "mcp",
        help="Serve Puspa's tools to outside agents over MCP stdio.",
        description=(
            "Serve Puspa's tools (memory search, ledger, price check, bills, "
            "scheduler, desktop hands) to an outside MCP client over stdio: "
            "JSON-RPC requests on stdin, replies on stdout, until EOF. Every "
            "call is policed by the policy gateway."
        ),
    )
    parser.add_argument(
        "--user",
        default=None,
        help="User id the tools act for (defaults to PUSPA_USER_ID or 'local-user').",
    )
    parser.add_argument(
        "--policy",
        default=None,
        help="JSON policy file with deny/allow rule lists (a malformed policy refuses to start).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Record gateway decisions without enforcing them.",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "fake"],
        default="auto",
        help="Desktop backend: the real Linux desktop (default) or the in-memory demo tree.",
    )
    return parser


def _hands_for(args, *, hands_factory=None):
    """Hands + visual fallback + web bridge for this backend.

    ``hands_factory`` is the test seam: pass a callable returning
    a hands object and no real backend is built.
    """
    if hands_factory is not None:
        return hands_factory(), None, None
    from puspa.computer.cli import _default_fallback, _default_hands, _default_web

    backend_kind = getattr(args, "backend", "auto")
    return (
        _default_hands(backend_kind),
        _default_fallback(backend_kind),
        _default_web(backend_kind),
    )


def run_mcp(
    args: argparse.Namespace, *, in_stream=None, out_stream=None, hands_factory=None
) -> int:
    """Run ``puspa mcp``: build the gateway + handlers, serve stdio."""
    from puspa.computer.backend import BackendUnavailableError

    from .handlers import HandlerContext
    from .server import run_stdio_server

    user_id = getattr(args, "user", None) or os.environ.get("PUSPA_USER_ID") or "local-user"
    try:
        hands, fallback, web = _hands_for(args, hands_factory=hands_factory)
    except BackendUnavailableError as exc:
        print(f"puspa mcp: {exc}", file=sys.stderr)
        return 1
    context = HandlerContext(user_id=user_id, hands=hands, fallback=fallback, web=web)
    try:
        run_stdio_server(
            context=context,
            policy_file=getattr(args, "policy", None),
            dry_run=bool(getattr(args, "dry_run", False)),
            in_stream=in_stream,
            out_stream=out_stream,
            log=lambda message: print(message, file=sys.stderr),
        )
    except PolicyError as exc:
        print(f"puspa mcp: bad policy: {exc}", file=sys.stderr)
        return 1
    return 0
