"""MCP server over stdio (``puspa mcp``).

A small JSON-RPC 2.0 server speaking the Model Context Protocol
over newline-delimited JSON on stdin/stdout — the same shape an
outside agent (Claude Code, Codex, or any MCP client) expects:
``initialize``, ``ping``, ``tools/list``, ``tools/call``, and
notifications (which get no reply). Standard library only: no
MCP SDK is needed for these four methods, and keeping the
transport hand-rolled means the server's behaviour is fully
covered by its own tests.

Every ``tools/call`` goes through the Puspa policy gateway
first (:class:`puspa.policy.gateway.Gateway`): the gateway
decides from the tool name and its effect (``"read"`` or
``"act"``, see :mod:`puspa.mcp_server.tools`), and a refused
call is returned as a tool result with ``isError: true`` — the
handler never runs, so a denied price check never touches a
reader and a denied click never touches the desktop. A handler
that raises is reported the same way; a bad tool name, a bad
method, or a malformed message is a JSON-RPC error instead
(``-32601`` unknown method, ``-32602`` bad params, ``-32700``
bad JSON, ``-32002`` called before ``initialize``).

Only stdio is served here: no HTTP/SSE transport, no sampling,
no roots, no prompts or resources — the server exposes tools
and nothing else. Anything written to stdout that is not a
JSON-RPC reply would corrupt the stream, so the server logs
nothing to stdout; errors go to stderr through the ``log``
callable (quiet by default).
"""

from __future__ import annotations

import json
import sys

from puspa.policy.gateway import DEFAULT_POLICY_DATA, ActionContext, Gateway

from .handlers import HandlerContext, build_handlers
from .tools import TOOL_EFFECTS, all_tool_definitions

__all__ = [
    "LATEST_PROTOCOL_VERSION",
    "PROTOCOL_VERSIONS",
    "McpServer",
    "run_stdio_server",
]

#: Protocol versions the server understands (newest first).
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26")

#: The version offered when the client asks for one we do not know.
LATEST_PROTOCOL_VERSION = PROTOCOL_VERSIONS[0]

#: JSON-RPC error codes used here.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_NOT_INITIALIZED = -32002


class McpServer:
    """One MCP server: handlers behind a policy gateway.

    ``handlers`` maps tool name to a callable taking an arguments
    dict and returning a JSON-ready dict. ``gateway`` defaults to
    an allow-everything gateway (the explicit default policy,
    written out, never implicit); pass a gateway built from a
    policy file to refuse calls. ``context`` is kept for the
    ``serverInfo`` user field and for introspection in tests.
    """

    def __init__(
        self,
        handlers: dict | None = None,
        *,
        gateway: Gateway | None = None,
        context: HandlerContext | None = None,
        server_name: str = "puspa",
        server_version: str = "0.1.0",
    ) -> None:
        self._handlers = dict(handlers) if handlers is not None else {}
        self.gateway = gateway if gateway is not None else Gateway(policy=DEFAULT_POLICY_DATA)
        self.context = context
        self.server_name = server_name
        self.server_version = server_version
        self._initialized = False

    # -- introspection -------------------------------------------------
    @property
    def initialized(self) -> bool:
        return self._initialized

    def tool_names(self) -> list[str]:
        """Tool names served, in definition order."""
        return [tool["name"] for tool in all_tool_definitions()]

    def definitions(self) -> list[dict]:
        """Fresh copies of every tool definition served."""
        return all_tool_definitions()

    # -- JSON-RPC plumbing ----------------------------------------------
    @staticmethod
    def _result(request_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _tool_error(text: str) -> dict:
        """A tool result reporting failure (MCP's ``isError`` shape)."""
        return {"content": [{"type": "text", "text": text}], "isError": True}

    @staticmethod
    def _tool_result(payload: dict) -> dict:
        return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}

    def handle_message(self, message) -> dict | None:
        """Handle one decoded JSON-RPC message.

        Returns the reply dict, or ``None`` for notifications and
        for client-to-server responses (which need no reply).
        """
        if not isinstance(message, dict):
            return self._error(None, _INVALID_REQUEST, "request must be a JSON object")
        method = message.get("method")
        request_id = message.get("id", None)
        has_id = "id" in message
        if not isinstance(method, str):
            # A message with no method is a response to a request
            # we never sent (or garbage): nothing to reply to.
            return None
        if method.startswith("notifications/") or not has_id:
            return None
        handler = {
            "initialize": self._on_initialize,
            "ping": self._on_ping,
            "tools/list": self._on_tools_list,
            "tools/call": self._on_tools_call,
        }.get(method)
        if handler is None:
            return self._error(request_id, _METHOD_NOT_FOUND, f"unknown method {method!r}")
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._error(request_id, _INVALID_PARAMS, "params must be an object")
        return handler(request_id, params)

    def _needs_initialized(self, request_id) -> dict | None:
        """The not-initialized error, or None when initialized."""
        if self._initialized:
            return None
        return self._error(
            request_id, _NOT_INITIALIZED, "server not initialized: send 'initialize' first"
        )

    def _on_initialize(self, request_id, params: dict) -> dict:
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        self._initialized = True
        return self._result(
            request_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self.server_name, "version": self.server_version},
            },
        )

    def _on_ping(self, request_id, params: dict) -> dict:
        return self._result(request_id, {})

    def _on_tools_list(self, request_id, params: dict) -> dict:
        blocked = self._needs_initialized(request_id)
        if blocked is not None:
            return blocked
        return self._result(request_id, {"tools": self.definitions()})

    def _on_tools_call(self, request_id, params: dict) -> dict:
        blocked = self._needs_initialized(request_id)
        if blocked is not None:
            return blocked
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return self._error(request_id, _INVALID_PARAMS, "tools/call needs a string 'name'")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return self._error(
                request_id, _INVALID_PARAMS, "tools/call 'arguments' must be an object"
            )
        handler = self._handlers.get(name)
        if handler is None:
            return self._result(request_id, self._tool_error(f"unknown tool {name!r}"))
        effect = TOOL_EFFECTS.get(name, "act")
        decision = self.gateway.decide(
            ActionContext(
                tool_name=name,
                mcp_server="puspa",
                mcp_tool=name,
                mcp_effect=effect,
            ),
            arguments=arguments,
        )
        if not decision.allowed:
            return self._result(
                request_id,
                self._tool_error(
                    json.dumps(
                        {
                            "error": "denied",
                            "tool": name,
                            "reason": decision.reason,
                            "rule": decision.rule,
                        }
                    )
                ),
            )
        try:
            payload = handler(arguments)
        except Exception as exc:  # reported, never crashes the loop
            return self._result(request_id, self._tool_error(f"tool {name!r} failed: {exc}"))
        if not isinstance(payload, dict):
            return self._result(
                request_id, self._tool_error(f"tool {name!r} returned a non-object result")
            )
        return self._result(request_id, self._tool_result(payload))

    # -- stdio loop ------------------------------------------------------
    def serve(self, in_stream, out_stream, *, log=None) -> None:
        """Serve until EOF: one JSON message per line in, one per line out.

        A line that is not JSON gets a ``-32700`` reply and the
        loop goes on; a blank line is skipped. Nothing but replies
        is ever written to ``out_stream``.
        """
        for raw in in_stream:
            if isinstance(raw, bytes):
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    self._write(out_stream, self._error(None, _PARSE_ERROR, "line is not UTF-8"))
                    continue
            else:
                line = raw
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                self._write(out_stream, self._error(None, _PARSE_ERROR, f"invalid JSON: {exc}"))
                continue
            try:
                reply = self.handle_message(message)
            except Exception as exc:  # one bad message never kills the server
                if log is not None:
                    log(f"puspa mcp: handler failed: {exc}")
                reply = self._error(
                    message.get("id") if isinstance(message, dict) else None,
                    _INVALID_REQUEST,
                    f"internal error: {exc}",
                )
            if reply is not None:
                self._write(out_stream, reply)

    @staticmethod
    def _write(out_stream, reply: dict) -> None:
        text = json.dumps(reply) + "\n"
        try:
            out_stream.write(text.encode("utf-8"))
        except TypeError:
            out_stream.write(text)
        flush = getattr(out_stream, "flush", None)
        if flush is not None:
            flush()


def run_stdio_server(
    *,
    context: HandlerContext | None = None,
    policy_data: dict | None = None,
    policy_file=None,
    dry_run: bool = False,
    in_stream=None,
    out_stream=None,
    log=None,
) -> McpServer:
    """Build the server and serve stdio until EOF.

    ``policy_file`` (a JSON file with ``deny`` / ``allow`` rule
    lists) wins over ``policy_data``; with neither, the explicit
    allow-everything default applies. A malformed policy raises
    :class:`puspa.policy.gateway.PolicyError` before serving
    starts, so a bad policy fails closed at startup instead of
    mid-session. Returns the server (mainly for tests).
    """
    from puspa.policy.gateway import load_policy_from_file

    if policy_file is not None:
        gateway = Gateway(policy=load_policy_from_file(policy_file), dry_run=dry_run)
    elif policy_data is not None:
        gateway = Gateway(policy=policy_data, dry_run=dry_run)
    else:
        gateway = Gateway(policy=DEFAULT_POLICY_DATA, dry_run=dry_run)
    ctx = context if context is not None else HandlerContext()
    server = McpServer(build_handlers(ctx), gateway=gateway, context=ctx)
    server.serve(
        sys.stdin.buffer if in_stream is None else in_stream,
        sys.stdout.buffer if out_stream is None else out_stream,
        log=log,
    )
    return server
