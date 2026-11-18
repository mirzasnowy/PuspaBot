"""Dangerous-command approval gate (G23).

Before a shell command runs, it is classified:

- ``SAFE`` -- ordinary commands run without asking.
- ``RISKY`` -- destructive or privileged commands ask the user,
  unless the gate runs in ``yolo`` mode.
- ``DANGEROUS`` -- commands above the approval floor (filesystem
  wipes, disk writes, fork bombs, shutdown, piping a download
  straight into a shell). Yolo mode can **never** approve these;
  only an explicit ``approval_callback`` approval lets them run,
  and with no callback they are refused.

Dangerous patterns differ per OS, so classification takes an
``os`` argument (``"posix"`` or ``"windows"``); the Unix set and
the Windows set are kept separate and a command is only matched
against the set for its OS.

Call site (documented, not wired here -- see the follow-up note at
the end of this docstring):

- The coding harness runs generated code, not shell commands.
  Production runs go through :class:`puspa.harness.executor.SandboxExecutor`
  (Docker, no host shell), and the test-only
  :class:`puspa.harness.executor.LocalTestExecutor` runs host Python
  via ``subprocess``. Neither takes a free-form shell command today,
  so there is no clean seam to wire this gate into yet.
- The gate belongs **before** any future shell execution: a shell
  tool (a ``run_shell`` tool registered in ``puspa.tools``), the
  ``LocalTestExecutor.run`` host path, or any CLI path that shells
  out. The wiring is a two-line call::

      decision = gate.request(command, os=sys.platform ...)
      if not decision.allowed:
          return <error result naming decision.reason>

  Follow-up: wire the gate into the first shell-execution seam that
  lands (shell tool or harness host path), with a test that a
  ``DANGEROUS`` command is refused even in yolo mode.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

__all__ = [
    "ApprovalDecision",
    "ApprovalDenied",
    "ApprovalGate",
    "CommandAssessment",
    "DangerLevel",
    "classify_command",
]

#: Callback shape: ``callback(command, level, reasons) -> bool``.
ApprovalCallback = Callable[[str, "DangerLevel", list[str]], bool]


class DangerLevel(IntEnum):
    """How dangerous a command is. Higher needs stronger approval."""

    SAFE = 0
    RISKY = 1
    DANGEROUS = 2


class ApprovalDenied(Exception):
    """Raised by ``ensure_allowed`` when a command may not run."""


@dataclass(frozen=True)
class CommandAssessment:
    """Classification of one command."""

    command: str
    os: str
    level: DangerLevel
    #: Human-readable reasons (one per matched pattern).
    reasons: list[str] = field(default_factory=list)
    #: The matched pattern labels, for tests and logs.
    matched: list[str] = field(default_factory=list)

    @property
    def dangerous(self) -> bool:
        return self.level >= DangerLevel.DANGEROUS

    @property
    def needs_approval(self) -> bool:
        return self.level >= DangerLevel.RISKY


# -- pattern tables -----------------------------------------------------
# Each entry: (label, compiled regex, level). Patterns are matched
# against the command as written; they are deliberately narrow
# (anchored on the dangerous verb + its destructive argument) so
# ordinary commands do not trip them.

_POSIX_PATTERNS: list[tuple[str, str, DangerLevel]] = [
    # Filesystem wipes.
    (
        "rm_rf_root",
        r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\s+(/?\s*$|/\s|/\*|~/?\s*$)",
        DangerLevel.DANGEROUS,
    ),
    ("rm_rf_home", r"\brm\s+-[a-z]*r[a-z]*f\s+~", DangerLevel.DANGEROUS),
    ("rm_rf", r"\brm\s+-[a-z]*r[a-z]*f\b", DangerLevel.RISKY),
    ("rm", r"\brm\s+", DangerLevel.RISKY),
    # Disk / device writes.
    ("mkfs", r"\bmkfs(\.\w+)?\b", DangerLevel.DANGEROUS),
    ("dd_to_device", r"\bdd\b.*\bof=/dev/", DangerLevel.DANGEROUS),
    ("write_to_device", r">\s*/dev/(sd|hd|nvme|vd)[a-z0-9]*", DangerLevel.DANGEROUS),
    ("dd", r"\bdd\b", DangerLevel.RISKY),
    # Fork bomb.
    ("fork_bomb", r":\(\)\s*\{\s*: *\| *:", DangerLevel.DANGEROUS),
    # Power / system state.
    ("shutdown", r"\b(shutdown|reboot|halt|poweroff|init\s+0)\b", DangerLevel.DANGEROUS),
    # Download piped straight into a shell.
    ("pipe_to_shell", r"\b(curl|wget)\b[^|]*\|\s*(sh|bash|zsh)\b", DangerLevel.DANGEROUS),
    ("eval_pipe", r"\|\s*(sh|bash|zsh)\b", DangerLevel.RISKY),
    # Privilege / ownership across the whole filesystem.
    ("chmod_777_root", r"\bchmod\s+(-R\s+)?777\s+/", DangerLevel.DANGEROUS),
    ("chown_root", r"\bchown\s+-R\s+\S+\s+/", DangerLevel.DANGEROUS),
    ("chmod_777", r"\bchmod\s+(-R\s+)?777\b", DangerLevel.RISKY),
    ("sudo", r"(^|\s)sudo\b", DangerLevel.RISKY),
    ("su_root", r"(^|\s)su\s+(-|root)?\s*$", DangerLevel.RISKY),
    # Destructive git / package / process operations.
    ("git_push_force", r"\bgit\s+push\s+--force\b", DangerLevel.RISKY),
    ("git_clean_force", r"\bgit\s+clean\s+-[a-z]*f", DangerLevel.RISKY),
    ("kill_all", r"\b(killall|pkill\s+-9|kill\s+-9\s+-1)\b", DangerLevel.RISKY),
    ("eval", r"\beval\s+", DangerLevel.RISKY),
    ("docker_prune", r"\bdocker\s+system\s+prune\b", DangerLevel.RISKY),
    ("move_root", r"\bmv\s+\S+\s+/\s*$", DangerLevel.DANGEROUS),
]

_WINDOWS_PATTERNS: list[tuple[str, str, DangerLevel]] = [
    ("del_recursive_system", r"\bdel\s+(?:/[a-z]+\s+)+[a-z]:\\", DangerLevel.DANGEROUS),
    ("rd_recursive_system", r"\b(rd|rmdir)\s+(?:/[a-z]+\s+)+[a-z]:\\", DangerLevel.DANGEROUS),
    ("remove_item_system", r"Remove-Item\s+.*-Recurse.*-Force.*[a-z]:\\", DangerLevel.DANGEROUS),
    ("format_drive", r"\bformat\s+[a-z]:", DangerLevel.DANGEROUS),
    ("diskpart", r"\bdiskpart\b", DangerLevel.DANGEROUS),
    ("bcdedit", r"\bbcdedit\b", DangerLevel.DANGEROUS),
    ("reg_delete", r"\breg\s+delete\b", DangerLevel.DANGEROUS),
    ("shutdown_windows", r"\bshutdown\s+/[sr]", DangerLevel.DANGEROUS),
    ("del_recursive", r"\bdel\s+/[sq]+", DangerLevel.RISKY),
    ("rd_recursive", r"\b(rd|rmdir)\s+/[sq]+", DangerLevel.RISKY),
    ("remove_item_recursive", r"Remove-Item\s+.*-Recurse", DangerLevel.RISKY),
    ("powershell_encoded", r"powershell.*-EncodedCommand", DangerLevel.RISKY),
    ("invoke_expression", r"Invoke-Expression", DangerLevel.RISKY),
    ("run_as_admin", r"Start-Process\s+.*-Verb\s+RunAs", DangerLevel.RISKY),
    ("taskkill_force", r"\btaskkill\s+/f", DangerLevel.RISKY),
    ("del", r"\bdel\s+", DangerLevel.RISKY),
]

_PATTERN_TABLES: dict[str, list[tuple[str, str, DangerLevel]]] = {
    "posix": _POSIX_PATTERNS,
    "windows": _WINDOWS_PATTERNS,
    # Common aliases.
    "linux": _POSIX_PATTERNS,
    "darwin": _POSIX_PATTERNS,
    "win32": _WINDOWS_PATTERNS,
}


def _compile(table) -> list[tuple[str, Any, DangerLevel]]:
    return [(label, re.compile(pattern, re.IGNORECASE), level) for label, pattern, level in table]


_COMPILED: dict[str, list[tuple[str, Any, DangerLevel]]] = {
    name: _compile(table) for name, table in _PATTERN_TABLES.items()
}


def classify_command(command: str, *, os: str = "posix") -> CommandAssessment:
    """Classify one shell command for the given OS.

    ``os`` is ``"posix"`` (or ``"linux"``/``"darwin"``) or
    ``"windows"`` (or ``"win32"``); an unknown value falls back to
    the POSIX set. The result level is the highest level of any
    matched pattern; a command matching nothing is SAFE.
    """
    key = os.lower()
    patterns = _COMPILED.get(key, _COMPILED["posix"])
    level = DangerLevel.SAFE
    reasons: list[str] = []
    matched: list[str] = []
    for label, regex, pattern_level in patterns:
        if regex.search(command):
            matched.append(label)
            reasons.append(f"matched {label} ({pattern_level.name.lower()})")
            level = max(level, pattern_level)
    return CommandAssessment(command=command, os=key, level=level, reasons=reasons, matched=matched)


@dataclass
class ApprovalDecision:
    """The gate's answer for one command."""

    allowed: bool
    assessment: CommandAssessment
    #: Why the command was (not) allowed.
    reason: str
    #: True when the user was actually asked (callback ran).
    asked_user: bool = False


class ApprovalGate:
    """Gate shell commands behind user approval, with a yolo mode.

    ``floor`` is the danger level yolo mode can never approve
    (default ``DANGEROUS``). ``approval_callback`` is called as
    ``callback(command, level, reasons) -> bool``; a callback that
    raises is treated as a refusal.
    """

    def __init__(
        self,
        *,
        approval_callback: ApprovalCallback | None = None,
        yolo: bool = False,
        os: str = "posix",
        floor: DangerLevel = DangerLevel.DANGEROUS,
    ) -> None:
        self.approval_callback = approval_callback
        self.yolo = yolo
        self.os = os
        self.floor = floor

    def request(
        self,
        command: str,
        *,
        approval_callback: ApprovalCallback | None = None,
        yolo: bool | None = None,
        os: str | None = None,
    ) -> ApprovalDecision:
        """Decide whether ``command`` may run.

        Order: safe commands pass; risky commands pass in yolo mode
        and otherwise ask the callback; dangerous commands (at or
        above the floor) always ask the callback and yolo never
        applies; with no callback they are refused.
        """
        callback = approval_callback if approval_callback is not None else self.approval_callback
        yolo_mode = self.yolo if yolo is None else yolo
        assessment = classify_command(command, os=os or self.os)
        if assessment.level == DangerLevel.SAFE:
            return ApprovalDecision(True, assessment, "safe command, no approval needed")
        if assessment.level >= self.floor:
            if callback is None:
                return ApprovalDecision(
                    False,
                    assessment,
                    f"{assessment.level.name.lower()} command at or above "
                    "the approval floor with no approval callback",
                )
            try:
                approved = bool(callback(command, assessment.level, assessment.reasons))
            except Exception:
                approved = False
            return ApprovalDecision(
                approved,
                assessment,
                "user approved" if approved else "user refused",
                asked_user=True,
            )
        # RISKY, below the floor.
        if yolo_mode:
            return ApprovalDecision(True, assessment, "yolo mode approved")
        if callback is None:
            return ApprovalDecision(
                False,
                assessment,
                "risky command with no approval callback and yolo off",
            )
        try:
            approved = bool(callback(command, assessment.level, assessment.reasons))
        except Exception:
            approved = False
        return ApprovalDecision(
            approved,
            assessment,
            "user approved" if approved else "user refused",
            asked_user=True,
        )

    def ensure_allowed(
        self,
        command: str,
        *,
        approval_callback: ApprovalCallback | None = None,
        yolo: bool | None = None,
        os: str | None = None,
    ) -> CommandAssessment:
        """Like :meth:`request`, but raises :class:`ApprovalDenied`."""
        decision = self.request(command, approval_callback=approval_callback, yolo=yolo, os=os)
        if not decision.allowed:
            raise ApprovalDenied(f"command refused ({decision.reason}): {command!r}")
        return decision.assessment
