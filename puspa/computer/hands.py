"""Desktop hands core (V2-31): snapshot, click, verify.

The loop follows the agent-desktop shape, rewritten as Puspa
code: read the accessibility tree as a snapshot, act on a ref,
then re-read the screen to verify. A successful action NEVER
proves the UI changed, so ``click`` always re-reads and only the
re-read can set ``verified=True``.

Permission: every click is gated twice. First, the token check:
when a :class:`puspa.policy.gate.Gate` and a token payload are
supplied, the ``computer.click`` tool must not be ``deny`` under
Puspa's per-tool permission model (unknown tools are ``deny`` by
default, so a token that never mentions desktop hands cannot
click). Second, the interactive prompt: the user is asked first
(the Claude Code model from :mod:`puspa.localexec`: approve once,
approve for the session, or deny). A denial raises
:class:`PermissionDenied` and nothing touches the desktop.
Snapshots and verifies are reads; they need no prompt, but what
they return is screen text, and screening that text is V2-36
work, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from puspa.localexec.executor import (
    PermissionDecision,
    PermissionDenied,
    PermissionPrompt,
    ask_permission_interactively,
)
from puspa.policy.gate import Gate, GateDenied

from .backend import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_NODES,
    AccessibilityBackend,
    BackendUnavailableError,
    make_backend,
)
from .tree import SKELETON_DEPTH, AccessibleNode, Snapshot, skeletonize

__all__ = [
    "ClickResult",
    "DesktopHands",
    "DesktopHandsError",
    "UnknownRefError",
    "Verification",
]

#: Per-tool permission name for desktop clicks under Puspa's
#: token model (see puspa/policy/tokens.py: unknown tools deny).
TOOL_CLICK = "computer.click"


class DesktopHandsError(Exception):
    """Desktop hands failed: unknown ref, unavailable backend, or
    a click the screen did not confirm."""


class UnknownRefError(DesktopHandsError):
    """The ref is not in the latest snapshot (stale ref, or a
    typo). Re-snapshot and pick a ref from the fresh tree."""


@dataclass(frozen=True)
class Verification:
    """What the re-read found for one ref."""

    ref: str
    found: bool
    verified: bool
    reason: str
    before: str = ""
    after: str = ""


@dataclass(frozen=True)
class ClickResult:
    """Outcome of one gated click: whether the action ran, and
    separately whether the re-read confirmed a change."""

    ref: str
    performed: bool
    verified: bool
    reason: str
    before: str = ""
    after: str = ""

    def to_dict(self) -> dict:
        return {
            "ref": self.ref,
            "performed": self.performed,
            "verified": self.verified,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
        }


def _expect_matches(node: AccessibleNode, expect: dict | None) -> tuple[bool, str]:
    """Check one re-read node against the caller's expectation."""
    if expect is None:
        return True, "element present after the action"
    if expect.get("absent"):
        return False, "element still present, expected it gone"
    if "name" in expect and node.name != expect["name"]:
        return False, f"name is {node.name!r}, expected {expect['name']!r}"
    state = expect.get("state")
    if state is not None and state not in node.states:
        return False, f"state {state!r} not in {sorted(node.states)}"
    if "role" in expect and node.role != expect["role"]:
        return False, f"role is {node.role!r}, expected {expect['role']!r}"
    return True, "re-read matches the expected outcome"


class DesktopHands:
    """The agent's hands on one desktop.

    ``prompt`` asks the user before each click (default: the
    interactive stdin prompt). ``gate`` + ``token_payload`` add
    the token-level check on top. ``session_approved`` remembers
    an approve-for-session answer, like the local runner.
    """

    def __init__(
        self,
        backend: AccessibilityBackend | None = None,
        *,
        prompt: PermissionPrompt | None = None,
        gate: Gate | None = None,
        token_payload: dict | None = None,
    ) -> None:
        self.backend = backend if backend is not None else make_backend("auto")
        self._prompt = prompt
        self._gate = gate
        self._token_payload = token_payload
        self._session_approved = False

    # -- reads ------------------------------------------------------
    def snapshot(
        self,
        *,
        skeleton: bool = True,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> Snapshot:
        """Read the screen. Skeleton by default (huge trees stay
        small); pass ``skeleton=False`` for the full tree."""
        shot = self.backend.snapshot(max_nodes=max_nodes, max_depth=max_depth)
        if skeleton:
            return skeletonize(shot, SKELETON_DEPTH)
        return shot

    def drill(self, ref: str) -> AccessibleNode:
        """Expand one skeleton node: re-read the full tree and
        return that node with its children."""
        shot = self.snapshot(skeleton=False)
        node = shot.find(ref)
        if node is None:
            raise UnknownRefError(f"no element with ref {ref!r} in the latest snapshot")
        return node

    def verify(self, ref: str, *, expect: dict | None = None) -> Verification:
        """Re-read the screen and check one ref against
        ``expect`` (``{"name": ...}``, ``{"state": ...}``,
        ``{"absent": True}``). Never clicks anything."""
        shot = self.snapshot(skeleton=False)
        node = shot.find(ref)
        if node is None:
            if expect and expect.get("absent"):
                return Verification(
                    ref=ref, found=False, verified=True, reason="element is gone, as expected"
                )
            return Verification(
                ref=ref, found=False, verified=False, reason="element is gone from the screen"
            )
        ok, reason = _expect_matches(node, expect)
        return Verification(ref=ref, found=True, verified=ok, reason=reason, after=node.summary())

    # -- permission -------------------------------------------------
    def _check_token(self) -> None:
        if self._gate is None or self._token_payload is None:
            return
        self._gate.authorize_tool(self._token_payload, TOOL_CLICK)

    def _ask(self, node: AccessibleNode) -> None:
        if self._session_approved:
            return
        prompt = self._prompt
        if prompt is None:
            decision = ask_permission_interactively(
                f"computer click {node.ref} {node.role} {node.name!r}", Path.cwd()
            )
        elif callable(prompt):
            decision = prompt(f"computer click {node.ref} {node.role} {node.name!r}", Path.cwd())
        else:
            decision = prompt.ask(
                f"computer click {node.ref} {node.role} {node.name!r}", Path.cwd()
            )
        if decision is PermissionDecision.DENY:
            raise PermissionDenied(
                f"denied by the user: click {node.ref} {node.role} {node.name!r}"
            )
        if decision is PermissionDecision.APPROVE_SESSION:
            self._session_approved = True

    # -- the act + re-read loop ------------------------------------
    def click(self, ref: str, *, expect: dict | None = None) -> ClickResult:
        """Click one ref, then re-read to verify.

        Steps: fresh snapshot, resolve the ref, token check,
        user prompt, backend click, fresh snapshot, verify.
        ``expect`` describes the change to look for (same shape
        as :meth:`verify`). With no ``expect``, verified means
        the re-read shows the element changed state, changed
        name, or is gone; an unchanged screen is reported as
        not verified, even when the backend said the click ran.
        """
        before_shot = self.snapshot(skeleton=False)
        node = before_shot.find(ref)
        if node is None:
            raise UnknownRefError(f"no element with ref {ref!r} in the latest snapshot")
        before = node.summary()
        try:
            self._check_token()
        except GateDenied as exc:
            raise PermissionDenied(f"desktop click refused by the permission gate: {exc}") from exc
        self._ask(node)
        try:
            performed = self.backend.click(node)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise DesktopHandsError(f"click on {ref} failed: {exc}") from exc
        if not performed:
            return ClickResult(
                ref=ref,
                performed=False,
                verified=False,
                reason="element has no click action",
                before=before,
            )
        after_shot = self.snapshot(skeleton=False)
        after_node = after_shot.find(ref)
        if after_node is None:
            if expect and expect.get("absent"):
                return ClickResult(
                    ref=ref,
                    performed=True,
                    verified=True,
                    reason="element is gone, as expected",
                    before=before,
                )
            return ClickResult(
                ref=ref,
                performed=True,
                verified=False,
                reason="element is gone from the screen and no disappearance was expected",
                before=before,
            )
        after = after_node.summary()
        if expect is not None:
            ok, reason = _expect_matches(after_node, expect)
            return ClickResult(
                ref=ref, performed=True, verified=ok, reason=reason, before=before, after=after
            )
        changed = after_node.name != node.name or tuple(after_node.states) != tuple(node.states)
        if changed:
            return ClickResult(
                ref=ref,
                performed=True,
                verified=True,
                reason="element changed after the click",
                before=before,
                after=after,
            )
        return ClickResult(
            ref=ref,
            performed=True,
            verified=False,
            reason="screen unchanged after the click: the action ran but nothing visibly changed",
            before=before,
            after=after,
        )
