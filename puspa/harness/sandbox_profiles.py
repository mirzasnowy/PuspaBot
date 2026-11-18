"""Sandbox profiles for the coding harness (openai/codex pattern).

Pattern studied, then rewritten as Puspa's own code (see
``docs/brain-depth-triage.md``, Tier 2 openai/codex, and the REUSE
fragment for this work):

- Codex separates two permission layers. The sandbox mode is the
  technical boundary: where the agent can write and whether it can
  reach the network. The approval policy decides when the agent
  must stop and ask before acting, for example before leaving the
  sandbox or using the network. The three sandbox modes are
  ``read-only`` (inspect files, no edits, no commands without
  approval), ``workspace-write`` (read anywhere, edit inside the
  workspace, run routine local commands, no network by default),
  and ``danger-full-access`` (no filesystem or network limits, only
  inside a boundary the operator built).
- The sandbox applies to every spawned command, not only to the
  agent's own file edits, and some paths stay protected even inside
  writable roots (``.git``, ``.codex``, ``.agents``).

This module is the Puspa version of that mechanism, mapped onto the
permission model that already exists in ``puspa.policy``:

- Profiles: ``read_only``, ``workspace_write``, ``full``. Puspa's
  ``full`` is NOT Codex ``danger-full-access``: it still runs only
  through the sandbox executor, still needs gate approval for
  deploy, and never widens the token (the effective level for a
  tool is always the narrower of the profile level and the token
  level, via :func:`puspa.policy.tokens.tool_permission`).
- Enforcement runs through the existing :meth:`Gate.authorize_tool`
  plus the checks in this module: :func:`check_tool` raises
  :class:`GateDenied` when the profile denies a tool, and
  :func:`check_write_path` raises :class:`ProfileDenied` when a
  write targets a path outside the workspace or a protected path.
- Approval before a write outside the profile boundary is modelled
  by :func:`requires_approval`: ``read_only`` always needs approval
  for a write or an execution, ``workspace_write`` needs approval
  for a write outside the workspace root, ``full`` needs approval
  only for deploy (which the gate already enforces).

No network calls are made from this module. The ``full`` profile
never enables network access: the sandbox runs with no network in
every profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from puspa.policy import GateDenied
from puspa.policy.tokens import TOOL_PERMISSION_RANK, tool_permission

__all__ = [
    "PROFILE_FULL",
    "PROFILE_READ_ONLY",
    "PROFILE_WORKSPACE_WRITE",
    "PROFILES",
    "ProfileDenied",
    "SandboxProfile",
    "check_tool",
    "check_write_path",
    "effective_tool_level",
    "get_profile",
    "profile_allows_tool",
    "requires_approval",
]

PROFILE_READ_ONLY = "read_only"
PROFILE_WORKSPACE_WRITE = "workspace_write"
PROFILE_FULL = "full"

# Tools the harness loop uses, mapped per profile. Levels use the
# same "allow" / "ask" / "deny" vocabulary as the token model.
# "read_code" is read-only in every profile; "write_code" and
# "run_tests" (execution) are denied under read_only; "deploy"
# always needs approval, so it is "ask" even under full.
_TOOL_LEVELS: dict[str, dict[str, str]] = {
    PROFILE_READ_ONLY: {
        "read_code": "allow",
        "write_code": "deny",
        "run_tests": "deny",
        "deploy": "deny",
    },
    PROFILE_WORKSPACE_WRITE: {
        "read_code": "allow",
        "write_code": "allow",
        "run_tests": "allow",
        "deploy": "ask",
    },
    PROFILE_FULL: {
        "read_code": "allow",
        "write_code": "allow",
        "run_tests": "allow",
        "deploy": "ask",
    },
}

# Paths that stay read-only even inside a writable workspace, the
# same idea as Codex protecting ``.git`` / ``.codex`` / ``.agents``
# under writable roots.
PROTECTED_NAMES = frozenset({".git", ".codex", ".agents", ".aws"})


class ProfileDenied(Exception):
    """A sandbox profile refuses an action.

    Raised when a write or execution is attempted under a profile
    that forbids it (for example any write under ``read_only``), or
    when a write targets a path outside the workspace root or a
    protected path. The action did not run.
    """


@dataclass(frozen=True)
class SandboxProfile:
    """One sandbox profile: name, description, and tool levels."""

    name: str
    description: str
    tool_levels: dict[str, str]

    def level_for(self, tool: str) -> str:
        """Profile level for ``tool``; unknown tools are "deny"."""
        return self.tool_levels.get(tool, "deny")


PROFILES: dict[str, SandboxProfile] = {
    name: SandboxProfile(
        name=name,
        description=description,
        tool_levels=dict(levels),
    )
    for name, (description, levels) in {
        PROFILE_READ_ONLY: (
            "Inspect files only; no edits and no command execution "
            "without approval.",
            _TOOL_LEVELS[PROFILE_READ_ONLY],
        ),
        PROFILE_WORKSPACE_WRITE: (
            "Read anywhere, edit inside the workspace, run routine "
            "local commands through the sandbox; no network.",
            _TOOL_LEVELS[PROFILE_WORKSPACE_WRITE],
        ),
        PROFILE_FULL: (
            "Everything the token allows, still only through the "
            "sandbox and still with gate approval for deploy; "
            "no network.",
            _TOOL_LEVELS[PROFILE_FULL],
        ),
    }.items()
}


def get_profile(name: str) -> SandboxProfile:
    """Return the profile for ``name`` or raise ProfileDenied."""
    try:
        return PROFILES[name]
    except KeyError:
        raise ProfileDenied(
            f"unknown sandbox profile {name!r}; known profiles: "
            f"{sorted(PROFILES)}"
        ) from None


def profile_allows_tool(profile_name: str, tool: str) -> bool:
    """True when the profile alone would allow ``tool``."""
    return get_profile(profile_name).level_for(tool) == "allow"


def effective_tool_level(profile_name: str, token_payload: dict | None, tool: str) -> str:
    """Effective level for ``tool``: the narrower of profile and token.

    A profile never widens a token (a token "deny" stays denied
    under ``full``), and a token never widens a profile (a token
    "allow" stays denied under ``read_only``). No payload means no
    permission information, so the answer is "deny" (fail-closed).
    """
    profile_level = get_profile(profile_name).level_for(tool)
    if token_payload is None:
        return "deny"
    token_level = tool_permission(token_payload, tool)
    if TOOL_PERMISSION_RANK[profile_level] <= TOOL_PERMISSION_RANK[token_level]:
        return profile_level
    return token_level


def check_tool(
    profile_name: str,
    gate,
    token_payload: dict | None,
    tool: str,
) -> str:
    """Enforce one tool use under a profile, through the gate.

    Returns "allow" or "ask". Raises :class:`GateDenied` with a
    clear message when the profile denies the tool, and propagates
    the gate's own :class:`GateDenied` when the token denies it.
    When there is no gate or no token payload, the profile alone
    decides: a profile "deny" still raises, a profile "allow"
    returns "allow".
    """
    profile_level = get_profile(profile_name).level_for(tool)
    if profile_level == "deny":
        raise GateDenied(
            f"sandbox profile {profile_name!r} denies tool {tool!r}: "
            "no writes or execution under this profile"
            if tool in ("write_code", "run_tests")
            else f"sandbox profile {profile_name!r} denies tool {tool!r}"
        )
    if gate is None or token_payload is None:
        return profile_level
    # The gate enforces the token side; the profile side was already
    # checked above, and "ask" from either side means approval is
    # needed before the tool runs.
    gate_level = gate.authorize_tool(token_payload, tool)
    if TOOL_PERMISSION_RANK[profile_level] <= TOOL_PERMISSION_RANK[gate_level]:
        return profile_level
    return gate_level


def requires_approval(profile_name: str, tool: str, *, path: str | None = None, workspace_root: str | Path | None = None) -> bool:
    """True when ``tool`` needs user approval before it runs.

    - ``read_only``: every write or execution needs approval.
    - ``workspace_write``: a write outside the workspace root needs
      approval; writes inside do not.
    - ``full``: only deploy needs approval (the gate enforces it).
    """
    profile = get_profile(profile_name)
    level = profile.level_for(tool)
    if level == "ask":
        return True
    if level == "deny":
        return True
    if profile_name == PROFILE_WORKSPACE_WRITE and tool == "write_code" and path is not None and workspace_root is not None:
        try:
            return not _is_under_workspace(path, workspace_root)
        except ProfileDenied:
            return True
    return False


def _is_under_workspace(path: str | Path, workspace_root: str | Path) -> bool:
    """True when ``path`` resolves inside ``workspace_root``."""
    root = Path(workspace_root).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError):
        raise ProfileDenied(f"cannot resolve write path {str(path)!r}") from None
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True


def check_write_path(profile_name: str, path: str | Path, *, workspace_root: str | Path) -> Path:
    """Validate a write path under a profile, or raise ProfileDenied.

    Rules, in order:

    1. ``read_only`` refuses every write with a clear error.
    2. A path escaping the workspace root is refused.
    3. A path inside a protected directory (``.git``, ``.codex``,
       ``.agents``, ``.aws``) is refused even under ``full``.

    Returns the resolved absolute path when the write is allowed.
    """
    get_profile(profile_name)
    if profile_name == PROFILE_READ_ONLY:
        raise ProfileDenied(
            f"sandbox profile 'read_only' denies writes: cannot write "
            f"{str(path)!r}; switch to 'workspace_write' or 'full' to edit"
        )
    if not _is_under_workspace(path, workspace_root):
        raise ProfileDenied(
            f"write path {str(path)!r} is outside the workspace root "
            f"{str(workspace_root)!r} under profile {profile_name!r}"
        )
    root = Path(workspace_root).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    resolved = target.resolve()
    for part in resolved.relative_to(root).parts:
        if part in PROTECTED_NAMES:
            raise ProfileDenied(
                f"write path {str(path)!r} is inside protected directory "
                f"{part!r}; protected paths stay read-only in every profile"
            )
    return resolved
