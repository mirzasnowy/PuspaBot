"""Optional MCP definitions + readiness check (G27, hermes-agent).

An MCP definition describes one optional Model Context Protocol
server a plugin may use: the command that starts it, the tools
it exposes, and the pip dependencies it needs. Definitions are
data (JSON files under ``mcp_definitions/``), validated by
``validate_mcp_definition``.

Readiness follows the hermes-agent PM/dashboard split: the
package manager prepares dependencies *before* a plugin is
used, and the dashboard only *checks* readiness without
installing anything. ``check_readiness`` implements the check
side: for each declared dependency it verifies the importable
top-level module is present (``importlib.util.find_spec``) and,
for a server command, that the executable resolves
(``shutil.which``). It never installs, downloads, or runs
anything, and returns a report instead of raising.
"""

from __future__ import annotations

import importlib.util
import re
import shutil

from .errors import PluginStructureError

__all__ = [
    "check_mcp_readiness",
    "check_readiness",
    "load_mcp_definition",
    "validate_mcp_definition",
]

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_REQUIREMENT_RE = re.compile(r"^[A-Za-z0-9_.\-]+(\[[A-Za-z0-9_,\-]+\])?\s*([<>=!~].*)?$")
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

REQUIRED_MCP_FIELDS = (
    "id",
    "name",
    "version",
    "description",
    "server",
    "tools",
)

#: Requirement name -> importable top-level module, for the
#: packages whose import name differs from their pip name.
_IMPORT_NAME_OVERRIDES = {
    "pyyaml": "yaml",
    "pillow": "PIL",
    "beautifulsoup4": "bs4",
    "python-dateutil": "dateutil",
    "pyjwt": "jwt",
    "opencv-python": "cv2",
    "scikit-learn": "sklearn",
}


def _requirement_base(requirement: str) -> str:
    """The package name part of a pip requirement string."""
    name = re.split(r"[<>=!~\s\[]", requirement.strip(), maxsplit=1)[0]
    return name.strip().lower().replace("_", "-")


def _import_name(requirement: str) -> str:
    """The top-level module a pip requirement should provide."""
    base = _requirement_base(requirement)
    return _IMPORT_NAME_OVERRIDES.get(base, base.replace("-", "_"))


def validate_mcp_definition(definition) -> list[str]:
    """Validate one MCP definition dict; return problem strings."""
    problems: list[str] = []
    if not isinstance(definition, dict):
        return ["MCP definition must be a JSON object"]
    for field_name in REQUIRED_MCP_FIELDS:
        if field_name not in definition or definition[field_name] in (None, ""):
            problems.append(f"missing required field: {field_name}")
    if problems:
        return problems
    mcp_id = definition["id"]
    if not isinstance(mcp_id, str) or not _ID_RE.match(mcp_id):
        problems.append(f"id {mcp_id!r} must be a lowercase slug")
    for field_name in ("name", "description"):
        if not isinstance(definition[field_name], str) or not definition[
            field_name
        ].strip():
            problems.append(f"{field_name} must be a non-empty string")
    version = definition["version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        problems.append(f"version {version!r} must be semver MAJOR.MINOR.PATCH")
    server = definition["server"]
    if not isinstance(server, dict):
        problems.append("server must be an object")
    else:
        command = server.get("command")
        if not isinstance(command, str) or not command.strip():
            problems.append("server.command must be a non-empty string")
        args = server.get("args", [])
        if not isinstance(args, list) or not all(
            isinstance(item, str) for item in args
        ):
            problems.append("server.args must be a list of strings")
        env = server.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in env.items()
        ):
            problems.append("server.env must be an object of string values")
        transport = server.get("transport", "stdio")
        if transport not in ("stdio", "http", "sse"):
            problems.append(
                f"server.transport {transport!r} must be stdio, http, or sse"
            )
    tools = definition["tools"]
    if not isinstance(tools, list) or not tools:
        problems.append("tools must be a non-empty list")
    else:
        seen: set[str] = set()
        for tool in tools:
            if not isinstance(tool, dict):
                problems.append("each tool must be an object")
                continue
            tool_name = tool.get("name")
            if not isinstance(tool_name, str) or not _TOOL_NAME_RE.match(tool_name):
                problems.append(f"tool name {tool_name!r} must be snake_case")
                continue
            if tool_name in seen:
                problems.append(f"duplicate tool name: {tool_name!r}")
            seen.add(tool_name)
            if not isinstance(tool.get("description"), str) or not tool[
                "description"
            ].strip():
                problems.append(f"tool {tool_name!r} needs a description")
    dependencies = definition.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) and _REQUIREMENT_RE.match(item.strip())
        for item in dependencies
    ):
        problems.append("dependencies must be a list of pip requirement strings")
    return problems


def load_mcp_definition(path) -> dict:
    """Load and validate one MCP definition JSON file."""
    import json
    from pathlib import Path

    path = Path(path)
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PluginStructureError(
            f"cannot read MCP definition {path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise PluginStructureError(
            f"MCP definition {path} is not valid JSON: {exc}"
        ) from exc
    problems = validate_mcp_definition(definition)
    if problems:
        raise PluginStructureError(
            f"MCP definition {path} is invalid: " + "; ".join(problems)
        )
    return definition


def check_readiness(dependencies, *, server_command=None) -> dict:
    """Check declared dependencies are present, without installing.

    ``dependencies`` is a list of pip requirement strings;
    ``server_command`` (optional) is the executable that must
    resolve on PATH. Returns::

        {"ready": bool, "missing": [...], "checks": [...]}

    where ``checks`` is one ``{"item", "kind", "ready", "detail"}``
    entry per dependency/command. A missing dependency never
    raises: it is reported. Nothing is installed, downloaded,
    or executed.
    """
    checks: list[dict] = []
    missing: list[str] = []
    for requirement in dependencies or []:
        module_name = _import_name(requirement)
        found = importlib.util.find_spec(module_name) is not None
        checks.append(
            {
                "item": requirement,
                "kind": "dependency",
                "ready": found,
                "detail": (
                    f"module {module_name!r} importable"
                    if found
                    else f"module {module_name!r} not installed"
                ),
            }
        )
        if not found:
            missing.append(requirement)
    if server_command:
        command = server_command.split()[0]
        resolved = shutil.which(command)
        checks.append(
            {
                "item": server_command,
                "kind": "server_command",
                "ready": resolved is not None,
                "detail": (
                    f"resolves to {resolved}"
                    if resolved
                    else f"{command!r} not found on PATH"
                ),
            }
        )
        if resolved is None:
            missing.append(server_command)
    return {"ready": not missing, "missing": missing, "checks": checks}


def check_mcp_readiness(definition: dict) -> dict:
    """Readiness report for one MCP definition (no installation)."""
    server = definition.get("server") or {}
    return check_readiness(
        definition.get("dependencies", []),
        server_command=server.get("command"),
    )
