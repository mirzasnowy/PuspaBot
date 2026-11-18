"""Builtin general plugin: echo (G27 proof plugin).

The smallest possible general plugin: one read-only tool, one
observation hook, one CLI command. It proves the ``ctx`` API
end to end without touching any core module beyond the
published API.
"""

from __future__ import annotations

PLUGIN_ID = "echo"
PLUGIN_VERSION = "1.0.0"


def register(ctx):
    """Register the echo tool, hook, and command."""

    def echo_tool(text):
        return f"echo: {text}"

    ctx.register_tool(
        "plugin_echo",
        "Echo the given text back, prefixed with 'echo: '.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to echo."}
            },
            "required": ["text"],
        },
        echo_tool,
        read_only=True,
    )

    def after_run(event, payload):
        ctx.shared.setdefault("events", []).append(
            {"event": event, "payload": payload}
        )

    ctx.register_hook("after_run", after_run)

    def echo_command(argv):
        ctx.shared.setdefault("command_calls", []).append(list(argv))
        return 0

    ctx.register_command(
        "echo", "Echo the command arguments (exit code 0).", echo_command
    )
