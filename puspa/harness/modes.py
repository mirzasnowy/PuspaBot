"""Sub-agent working modes for the coding harness (KiloCode pattern).

Pattern studied, then rewritten as Puspa's own code (see
``docs/brain-depth-triage.md``, Tier 2 KiloCode):

- KiloCode runs sub-agents in working modes: Architect plans and
  never edits, Code implements, Debug reproduces a failure before
  fixing it, Ask answers without changing anything. The agent's
  behaviour changes per task phase: what it may do, what it must
  do first, and what counts as a finished phase.

This module is the Puspa version of that mechanism, mapped onto the
harness pieces that already exist:

- :class:`ModeSpec` fixes one mode's behaviour: which sandbox
  profile it runs under, whether the generator must answer in
  unified diffs, and whether reproduce-first is required. The
  profile is the enforcement, not a label: Architect and Ask run
  under ``read_only``, so :func:`enforce_read_only_tools` proves
  through the Gate and the sandbox profile that ``write_code``
  and ``run_tests`` are refused before any planning or answering
  happens. Code and Debug run under ``workspace_write`` and must
  answer in diffs.
- :func:`get_mode` validates a mode name; unknown names are
  refused at construction time, before anything runs.
- The loop (:mod:`puspa.harness.loop`) takes a ``mode`` parameter
  and applies the spec. With no mode the loop keeps its old
  behaviour exactly: the mode parameter only adds behaviour, it
  never changes the default run.

No network calls are made from this module. The generator itself
is still caller-supplied, as in the rest of the harness: this
module decides what a mode may do, never what the model says.
"""

from __future__ import annotations

from dataclasses import dataclass

from puspa.policy import GateDenied

from .sandbox_profiles import (
    PROFILE_READ_ONLY,
    PROFILE_WORKSPACE_WRITE,
    check_tool,
    get_profile,
)

__all__ = [
    "MODE_ARCHITECT",
    "MODE_ASK",
    "MODE_CODE",
    "MODE_DEBUG",
    "MODES",
    "ModeError",
    "ModeSpec",
    "enforce_read_only_tools",
    "get_mode",
    "list_modes",
]

MODE_ARCHITECT = "architect"
MODE_CODE = "code"
MODE_DEBUG = "debug"
MODE_ASK = "ask"


class ModeError(Exception):
    """An unknown or inconsistent working mode was requested."""


@dataclass(frozen=True)
class ModeSpec:
    """One working mode: what the agent may do in this phase.

    ``profile`` is the sandbox profile the mode runs under and is
    the real enforcement: a mode that forbids writes runs under
    ``read_only``, so the Gate refuses ``write_code`` and
    ``run_tests`` before anything happens. ``diff_mode`` and
    ``reproduce_first`` are the harness flags the mode turns on.
    ``requires_diff`` means every generator answer must be a
    unified diff; a full-program answer fails that attempt instead
    of being applied. ``produces`` names what a finished run in
    this mode returns: a plan, code, or an answer.
    """

    name: str
    description: str
    profile: str
    diff_mode: bool
    reproduce_first: bool
    requires_diff: bool
    allows_write: bool
    allows_execute: bool
    produces: str  # "plan" | "code" | "answer"


MODES: dict[str, ModeSpec] = {
    MODE_ARCHITECT: ModeSpec(
        name=MODE_ARCHITECT,
        description=(
            "Plan only: build a repo map and a plan, never write a "
            "file and never execute code."
        ),
        profile=PROFILE_READ_ONLY,
        diff_mode=False,
        reproduce_first=False,
        requires_diff=False,
        allows_write=False,
        allows_execute=False,
        produces="plan",
    ),
    MODE_CODE: ModeSpec(
        name=MODE_CODE,
        description=(
            "Implement through surgical unified diffs only; every "
            "generator answer must be a diff."
        ),
        profile=PROFILE_WORKSPACE_WRITE,
        diff_mode=True,
        reproduce_first=False,
        requires_diff=True,
        allows_write=True,
        allows_execute=True,
        produces="code",
    ),
    MODE_DEBUG: ModeSpec(
        name=MODE_DEBUG,
        description=(
            "Reproduce first, then fix through surgical diffs: no "
            "fix is accepted without a failing reproduction first."
        ),
        profile=PROFILE_WORKSPACE_WRITE,
        diff_mode=True,
        reproduce_first=True,
        requires_diff=True,
        allows_write=True,
        allows_execute=True,
        produces="code",
    ),
    MODE_ASK: ModeSpec(
        name=MODE_ASK,
        description=(
            "Answer only: one answer, no file changes, no code "
            "execution."
        ),
        profile=PROFILE_READ_ONLY,
        diff_mode=False,
        reproduce_first=False,
        requires_diff=False,
        allows_write=False,
        allows_execute=False,
        produces="answer",
    ),
}


def list_modes() -> list[str]:
    """Mode names, in definition order."""
    return list(MODES)


def get_mode(name: str) -> ModeSpec:
    """Return the spec for ``name`` or raise :class:`ModeError`.

    The profile named by the spec is validated too, so a mode
    whose profile does not exist is refused here, not mid-run.
    """
    try:
        spec = MODES[name]
    except KeyError:
        raise ModeError(
            f"unknown working mode {name!r}; known modes: "
            f"{sorted(MODES)}"
        ) from None
    get_profile(spec.profile)
    return spec


def enforce_read_only_tools(profile_name: str, gate, token_payload: dict | None) -> None:
    """Prove a read-only mode really cannot write or execute.

    Calls :func:`puspa.harness.sandbox_profiles.check_tool` for
    ``write_code`` and ``run_tests`` and requires it to raise
    :class:`GateDenied` for both. A profile that lets either
    through raises :class:`ModeError` instead: a read-only mode
    must be refused, not run with a hole in it. The check runs
    through the Gate when one is configured, so a token that
    allows everything still cannot widen the profile.
    """
    for tool in ("write_code", "run_tests"):
        try:
            check_tool(profile_name, gate, token_payload, tool)
        except GateDenied:
            continue
        raise ModeError(
            f"read-only profile {profile_name!r} does not deny tool "
            f"{tool!r}; refusing to run a read-only mode under it"
        )
