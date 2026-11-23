"""Per-app MCP server process for token apps (the shipped command).

Choice recorded (V2-39): SHIP the ``puspa-token-app`` console script
the token-app MCP definition promises, instead of repointing the
definition at a command that already exists. Repointing at ``puspa``
would make ``check_readiness`` pass while the arguments behind it
serve nothing: the CLI has no token-app subcommand, and the
``puspa mcp`` server is a parallel worker's in-flight work, so the
definition would claim a server that is not there. Shipping keeps
the definition's promise: the command resolves on a fresh install
because the package installs it, and ``check_readiness`` passes for
the emitted definition.

What this process is: the server the definition names. The engine
spawns ``puspa-token-app <app_id> <tools-json>`` and speaks MCP
over stdio (newline-delimited JSON-RPC). The process unseals the
app's token from the vault at startup and refuses to serve when it
cannot (unknown app, missing vault entry, no master key): a server
that cannot unseal its token must fail loudly instead of answering
as if it could serve. The token itself is never printed or logged.
The vault keys entries by UUID user id, so the user cannot be
guessed: it must come from ``--user`` or ``PUSPA_USER_ID`` (the
same env var the CLI reads); there is no working default.

Honest boundary: the process lists exactly the tools the definition
declares, but a tool CALL cannot run yet. A token app declares tool
names and descriptions only; the token-app layer defines no mapping
from a tool to the HTTP call that tool would make against the app's
API, so there is nothing real to execute. Until that mapping exists,
every call answers with a clear MCP error saying so, instead of
pretending to run. That mapping is follow-up work, not part of this
gap fix.

Launch contract: the MCP stdio transport scrubs the environment
(the official SDK only inherits HOME, LOGNAME, PATH, SHELL, TERM
and USER), so a server spawned by the engine cannot see
PUSPA_USER_ID, PUSPA_TEST_DSN or the vault master key. The
definition therefore carries what the server needs on argv
(``--user``, ``--dsn``, ``--key-file``); the master key itself is
never on argv, only the path of the 0600 key file that holds it
(the vault's own key-file mechanism). Running the server directly
from a shell still works: every flag falls back to the matching
env var when it is not given.

The server speaks MCP with plain JSON over stdio and deliberately
does NOT use the official SDK's server code: the chassis wrapper
(``puspa.chassis.tools``) documents that Puspa does not depend on
the SDK's server side, only on its client.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import TextIO

from puspa.db import Error as _db_Error

from puspa.accounts.vault import VaultError
from puspa.connectors.token_apps import VaultSealStore

__all__ = ["main", "serve_stdio"]

#: MCP protocol versions this server answers with. A client's
#: requested version is echoed back when it is one of these;
#: anything else gets the oldest one, so a client that only knows
#: an old version still gets a version it understands.
_SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

#: The oldest supported protocol version (the fallback answer).
_FALLBACK_PROTOCOL_VERSION = "2024-11-05"

#: Server name/version reported in the MCP initialize handshake.
_SERVER_NAME = "puspa-token-app"
_SERVER_VERSION = "0.1.0"


def _tool_call_unavailable_text(name: str) -> str:
    """Why a tool call cannot run yet (plain language, for the caller)."""
    return (
        f"tool {name!r} cannot run yet: a token app declares tool names "
        "and descriptions, but no mapping from a tool to the HTTP call "
        "it would make against the app's API exists, so there is nothing "
        "real to execute. The sealed token for this app was loaded, so "
        "the server is ready to serve the tool once the mapping exists."
    )


def _error(request_id, code: int, message: str) -> dict:
    """One JSON-RPC error response."""
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _handle_request(app_id: str, declared: dict, message: dict, request_id) -> dict:
    """Answer one JSON-RPC request (``request_id`` is never None here)."""
    method = message["method"]
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, -32602, "params must be an object")
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = (
            requested if requested in _SUPPORTED_PROTOCOL_VERSIONS else _FALLBACK_PROTOCOL_VERSION
        )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": name,
                        "description": description,
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                    for name, description in declared.items()
                ]
            },
        }
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in declared:
            return _error(request_id, -32602, f"unknown tool {name!r} for token app {app_id!r}")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": _tool_call_unavailable_text(name)}],
                "isError": True,
            },
        }
    return _error(request_id, -32601, f"method {method!r} is not supported by this server")


def _reply_to_line(app_id: str, declared: dict, line: str) -> dict | None:
    """The reply for one input line, or None when no reply is owed.

    Blank lines and notifications (no ``id``, e.g.
    ``notifications/initialized``) get no reply. Anything malformed
    gets a JSON-RPC error instead of crashing the server.
    """
    if not line:
        return None
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return _error(None, -32700, "request is not valid JSON")
    if not isinstance(message, dict):
        return _error(None, -32600, "request must be a JSON object")
    method = message.get("method")
    request_id = message.get("id")
    if not isinstance(method, str) or not method:
        if request_id is None:
            return None
        return _error(request_id, -32600, "request needs a method string")
    if request_id is None:
        return None
    return _handle_request(app_id, declared, message, request_id)


def serve_stdio(app_id: str, tools: list[dict], *, stdin: TextIO, stdout: TextIO) -> int:
    """Serve one token app as an MCP server over stdio.

    Reads newline-delimited JSON-RPC requests from ``stdin`` and
    writes responses to ``stdout`` (nothing else may ever be written
    to stdout: it is the MCP channel). Handles ``initialize``,
    ``ping``, ``tools/list`` and ``tools/call``; anything else gets
    a JSON-RPC "method not found" error, and a line that is not JSON
    gets a parse error. A tool call answers with a clear MCP error
    (see the module docstring: no endpoint mapping exists yet).
    Returns 0 when the client closes the connection.
    """
    declared = {tool["name"]: tool["description"] for tool in tools}
    while True:
        line = stdin.readline()
        if not line:
            return 0
        reply = _reply_to_line(app_id, declared, line.strip())
        if reply is None:
            continue
        try:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
        except BrokenPipeError:
            return 0


def _parse_tools(raw: str, *, parser: argparse.ArgumentParser) -> list[dict]:
    """Parse the tools JSON argument, or fail with a usage error."""
    try:
        tools = json.loads(raw)
    except json.JSONDecodeError as exc:
        parser.error(f"tools argument must be a JSON array of tools: {exc}")
    if not isinstance(tools, list) or not tools:
        parser.error("tools argument must be a non-empty JSON array of tools")
    for tool in tools:
        if (
            not isinstance(tool, dict)
            or not isinstance(tool.get("name"), str)
            or not tool["name"].strip()
            or not isinstance(tool.get("description"), str)
            or not tool["description"].strip()
        ):
            parser.error('each tool must be {"name", "description"} with non-empty strings')
    return tools


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point: ``puspa-token-app <app_id> <tools-json>``.

    Loads the sealed token for the app from the vault (fail fast
    when the app is not attached) and then serves the app's declared
    tools as an MCP server over stdio. The token is never printed.
    Returns 0 when the client closes the connection, 1 when the
    token cannot be loaded, and 2 (via argparse) for bad arguments.
    """
    parser = argparse.ArgumentParser(
        prog="puspa-token-app",
        description="Serve one token app as an MCP server over stdio.",
    )
    parser.add_argument("app_id", help="the token app id, e.g. 'notes'")
    parser.add_argument(
        "tools_json",
        help='JSON array of the app\'s tools: [{"name", "description"}, ...]',
    )
    parser.add_argument(
        "--user",
        default=None,
        help="user id (UUID) owning the sealed token (or PUSPA_USER_ID); required",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="Postgres DSN for the vault (default: PUSPA_DSN, PUSPA_TEST_DSN, then local default)",
    )
    parser.add_argument(
        "--key-file",
        default=None,
        help="path to a 0600 file holding the vault master key (or PUSPA_VAULT_MASTER_KEY_FILE)",
    )
    args = parser.parse_args(argv)
    if not args.app_id.strip():
        parser.error("app_id must be a non-empty string")
    tools = _parse_tools(args.tools_json, parser=parser)
    # The vault keys entries by UUID user id, so the user cannot be
    # guessed: --user or PUSPA_USER_ID must name the app's owner.
    user_id = args.user or os.environ.get("PUSPA_USER_ID")
    if not user_id:
        parser.error("serving a token app needs a user id: pass --user or set PUSPA_USER_ID")
    if args.key_file:
        # The key itself is never on argv, only the path of the file
        # that holds it; the vault reads the file from this env var.
        os.environ["PUSPA_VAULT_MASTER_KEY_FILE"] = args.key_file
    token_ref = f"token_app:{args.app_id}"
    try:
        token = VaultSealStore(user_id, dsn=args.dsn).get(token_ref)
    except KeyError:
        print(
            f"puspa-token-app: no sealed token for app {args.app_id!r} "
            f"(user {user_id!r}); attach the app first",
            file=sys.stderr,
        )
        return 1
    except VaultError as exc:
        print(f"puspa-token-app: the vault cannot be used: {exc}", file=sys.stderr)
        return 1
    except _db_Error as exc:
        print(f"puspa-token-app: the vault database cannot be used: {exc}", file=sys.stderr)
        return 1
    if not isinstance(token, str) or not token:
        print(
            f"puspa-token-app: sealed token for app {args.app_id!r} is empty; refusing to serve",
            file=sys.stderr,
        )
        return 1
    return serve_stdio(args.app_id, tools, stdin=sys.stdin, stdout=sys.stdout)
