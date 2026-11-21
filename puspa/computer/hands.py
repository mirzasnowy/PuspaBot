"""Desktop hands core (V2-31): snapshot, click, verify.

The loop follows the agent-desktop shape, rewritten as Puspa
code: read the accessibility tree as a snapshot, act on a ref,
then re-read the screen to verify. A successful action NEVER
proves the UI changed, so ``click`` always re-reads and only the
re-read can set ``verified=True``.

V2-33 adds the rest of the input surface on the same loop:
typing, keyboard shortcuts, scroll, drag, clipboard read/write,
and window management (open app, focus window, switch between
apps, list windows). Every one of them is gated the same way
and verified by re-reading, never by the backend's word.

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
they return is screen text, so every snapshot this class hands
out first passes through the V2-36 safety layer
(:mod:`puspa.computer.safety`): injected instructions are marked
as data and logged, password/PIN/card content is masked, and
the guard cannot be bypassed by calling the raw backend.

V2-34 (synchronization + non-stale refs): ``click`` waits for
the tree to settle first (see :mod:`puspa.computer.sync`), then
resolves the ref, asks the user, and snapshots AGAIN right
before touching the desktop, checking the live element's
identity against the one that was approved. When the element is
gone, or the ref now finds a different element, the click is
refused with :class:`StaleRefError` and nothing is pressed. A
stale refusal is not a permission denial: the user did not deny
anything and the token gate did not refuse anything, the screen
changed, so it is raised (and reported) distinctly.

V2-35 replaces "ask before every action" with tiers (see
:mod:`puspa.computer.tiers`): reads are free, NORMAL actions
run free inside an allowlisted app (see
:mod:`puspa.computer.allowlist`) and ask elsewhere, and
DESTRUCTIVE actions always ask. The emergency stop (see
:mod:`puspa.computer.stop`) is checked before every action and
at every step of :meth:`DesktopHands.run_task`, and every
action is written to the audit log (see
:mod:`puspa.computer.audit`) with its tier and decision.

COW-13 layers one per-session permission mode over all of
that (see :mod:`puspa.policy.session_modes`): when a
:class:`~puspa.policy.session_modes.SessionModeState` is passed
in, read-only refuses every write before the prompt, ask-first
makes every write ask (even inside an allowlisted app), and
full-access lets ordinary writes run without a prompt while
destructive actions still ask. The mode never widens the
token check and never skips the stop check.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

from puspa.localexec.executor import (
    PermissionDecision,
    PermissionDenied,
    PermissionPrompt,
    ask_permission_interactively,
)
from puspa.policy.gate import Gate, GateDenied
from puspa.policy.session_modes import SessionModeState

from .allowlist import Allowlist
from .audit import InMemoryComputerAudit, LedgerComputerAudit, audit_entry
from .backend import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_NODES,
    AccessibilityBackend,
    BackendUnavailableError,
    FakeBackend,
    make_backend,
)
from .input import (
    ClipboardResult,
    InputBackend,
    InputResult,
    make_input_backend,
)
from .safety import ScreenTextSafety
from .stop import ComputerStop, ComputerStopped
from .sync import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_SETTLE_TIMEOUT_S,
    DEFAULT_STABLE_ROUNDS,
    SettleReport,
    fingerprint,
    tree_signature,
    wait_for_settle,
)
from .tiers import Tier, classify, is_submit_shortcut
from .tree import SKELETON_DEPTH, AccessibleNode, Snapshot, skeletonize
from .windows import (
    WindowBackend,
    WindowInfo,
    WindowResult,
    make_window_backend,
    windows_from_snapshot,
)

__all__ = [
    "ClickResult",
    "ComputerStopped",
    "DesktopHands",
    "DesktopHandsError",
    "SettleReport",
    "StaleRefError",
    "UnknownRefError",
    "Verification",
]

#: Per-tool permission name for desktop clicks under Puspa's
#: token model (see puspa/policy/tokens.py: unknown tools deny).
TOOL_CLICK = "computer.click"
#: Per-tool permission names for the V2-33 actions. Each action
#: has its own tool name, so a token can allow typing but not
#: window switching, and so on.
TOOL_TYPE = "computer.type"
TOOL_SHORTCUT = "computer.shortcut"
TOOL_SCROLL = "computer.scroll"
TOOL_DRAG = "computer.drag"
TOOL_CLIPBOARD_READ = "computer.clipboard_read"
TOOL_CLIPBOARD_WRITE = "computer.clipboard_write"
TOOL_OPEN_APP = "computer.open_app"
TOOL_FOCUS_WINDOW = "computer.focus_window"
TOOL_SWITCH_WINDOW = "computer.switch_window"
#: Per-tool permission name for closing a window (V2-35).
TOOL_CLOSE_WINDOW = "computer.close_window"


class DesktopHandsError(Exception):
    """Desktop hands failed: unknown ref, unavailable backend, or
    a click the screen did not confirm."""


class UnknownRefError(DesktopHandsError):
    """The ref is not in the latest snapshot (stale ref, or a
    typo). Re-snapshot and pick a ref from the fresh tree."""


class StaleRefError(DesktopHandsError):
    """The ref was valid when it was picked, but the screen
    changed before the click ran: the element is gone, or the
    ref now finds a different element. NOT a permission denial:
    nobody refused the click, the target simply is not the same
    element any more, so the click was refused and nothing was
    pressed. Re-snapshot and pick a ref from the fresh tree."""


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
    if "value" in expect and node.value != expect["value"]:
        return False, f"value is {node.value!r}, expected {expect['value']!r}"
    if "value_contains" in expect and expect["value_contains"] not in node.value:
        return False, f"value {node.value!r} does not contain {expect['value_contains']!r}"
    state = expect.get("state")
    if state is not None and state not in node.states:
        return False, f"state {state!r} not in {sorted(node.states)}"
    if "role" in expect and node.role != expect["role"]:
        return False, f"role is {node.role!r}, expected {expect['role']!r}"
    return True, "re-read matches the expected outcome"


class DesktopHands:
    """The agent's hands on one desktop.

    ``prompt`` asks the user before each action (default: the
    interactive stdin prompt). ``gate`` + ``token_payload`` add
    the token-level check on top. ``session_approved`` remembers
    an approve-for-session answer, like the local runner; one
    session approval covers every computer action, not just
    clicks. ``session_modes`` is one session's permission mode
    (COW-13): when given, read-only refuses every write, ask-first
    makes every write ask, and full-access lets ordinary writes
    run without a prompt while destructive actions still ask.
    ``input_backend`` / ``window_backend`` default to the
    backends matching ``backend`` (fake shares the fake tree;
    auto uses the real Linux ones).
    """

    def __init__(
        self,
        backend: AccessibilityBackend | None = None,
        *,
        prompt: PermissionPrompt | None = None,
        gate: Gate | None = None,
        token_payload: dict | None = None,
        session_modes: SessionModeState | None = None,
        input_backend: InputBackend | None = None,
        window_backend: WindowBackend | None = None,
        settle_timeout: float = DEFAULT_SETTLE_TIMEOUT_S,
        settle_stable_rounds: int = DEFAULT_STABLE_ROUNDS,
        settle_poll_interval: float = DEFAULT_POLL_INTERVAL_S,
        allowlist: Allowlist | None = None,
        stop: ComputerStop | None = None,
        audit: LedgerComputerAudit | InMemoryComputerAudit | None = None,
        safety: ScreenTextSafety | None = None,
    ) -> None:
        self.backend = backend if backend is not None else make_backend("auto")
        self.safety = safety if safety is not None else ScreenTextSafety()
        self._prompt = prompt
        self._gate = gate
        self._token_payload = token_payload
        self._session_approved = False
        #: This session's permission mode (COW-13). None means no
        #: mode layer: the V2-35 tier flow decides alone, exactly
        #: as before.
        self.session_modes = session_modes
        #: Apps the agent may click inside without asking (V2-35).
        #: Defaults to the user's allowlist file; pass an
        #: :class:`Allowlist` on a temp path in tests.
        self.allowlist = allowlist if allowlist is not None else Allowlist()
        #: The emergency stop flag (V2-35). Defaults to the
        #: user's stop file, re-read before every action.
        self.stop = stop if stop is not None else ComputerStop()
        #: The audit sink (V2-35). None means no audit: the CLI
        #: and the PWA server wire a ledger-backed one, tests
        #: pass an in-memory one.
        self.audit = audit
        if input_backend is not None:
            self.input_backend = input_backend
        elif isinstance(self.backend, FakeBackend):
            self.input_backend = make_input_backend("fake", self.backend)
        else:
            self.input_backend = make_input_backend("auto", self.backend)
        if window_backend is not None:
            self.window_backend = window_backend
        elif isinstance(self.backend, FakeBackend):
            self.window_backend = make_window_backend("fake", self.backend)
        else:
            self.window_backend = make_window_backend("auto", self.backend)
        self._settle_timeout = settle_timeout
        self._settle_stable_rounds = settle_stable_rounds
        self._settle_poll_interval = settle_poll_interval

    # -- reads ------------------------------------------------------
    def _read_snapshot(
        self,
        *,
        skeleton: bool = True,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> Snapshot:
        """Read the screen without auditing: the internal re-read
        every action does. Public callers use :meth:`snapshot`,
        which audits the read. The returned snapshot is sanitised
        by the safety layer: screen text the model sees is data,
        never instructions, and secrets are masked."""
        shot = self.backend.snapshot(max_nodes=max_nodes, max_depth=max_depth)
        if skeleton:
            shot = skeletonize(shot, SKELETON_DEPTH)
        return self.safety.sanitize_snapshot(shot)

    def wait_settle(
        self, *, timeout_s: float | None = None, stable_rounds: int | None = None
    ) -> tuple[Snapshot, SettleReport]:
        """Wait until the tree stops changing, then return the
        settled snapshot and the settle report. Raises
        :class:`puspa.computer.sync.SettleTimeoutError` when the
        screen never goes quiet inside the bounded wait."""
        return wait_for_settle(
            lambda: self._read_snapshot(skeleton=False),
            signature=tree_signature,
            timeout_s=self._settle_timeout if timeout_s is None else timeout_s,
            stable_rounds=self._settle_stable_rounds if stable_rounds is None else stable_rounds,
            poll_interval_s=self._settle_poll_interval,
        )

    def snapshot(
        self,
        *,
        skeleton: bool = True,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> Snapshot:
        """Read the screen. Skeleton by default (huge trees stay
        small); pass ``skeleton=False`` for the full tree. A read
        is tier READ: it never asks, but it is still written to
        the audit log when one is wired in."""
        shot = self._read_snapshot(skeleton=skeleton, max_nodes=max_nodes, max_depth=max_depth)
        self._audit(
            "snapshot", target="screen", tier=Tier.READ, decision="allow", outcome="performed"
        )
        return shot

    def drill(self, ref: str) -> AccessibleNode:
        """Expand one skeleton node: re-read the full tree and
        return that node with its children."""
        shot = self._read_snapshot(skeleton=False)
        node = shot.find(ref)
        if node is None:
            raise UnknownRefError(f"no element with ref {ref!r} in the latest snapshot")
        return node

    def verify(self, ref: str, *, expect: dict | None = None) -> Verification:
        """Re-read the screen and check one ref against
        ``expect`` (``{"name": ...}``, ``{"state": ...}``,
        ``{"absent": True}``). Never clicks anything. Tier READ:
        never asks, but still audited."""
        shot = self._read_snapshot(skeleton=False)
        node = shot.find(ref)
        if node is None:
            if expect and expect.get("absent"):
                self._audit(
                    "verify",
                    target=ref,
                    tier=Tier.READ,
                    decision="allow",
                    outcome="performed",
                    reason="element is gone, as expected",
                )
                return Verification(
                    ref=ref, found=False, verified=True, reason="element is gone, as expected"
                )
            self._audit(
                "verify",
                target=ref,
                tier=Tier.READ,
                decision="allow",
                outcome="not_performed",
                reason="element is gone from the screen",
            )
            return Verification(
                ref=ref, found=False, verified=False, reason="element is gone from the screen"
            )
        ok, reason = _expect_matches(node, expect)
        self._audit(
            "verify",
            target=ref,
            tier=Tier.READ,
            decision="allow",
            outcome="performed" if ok else "not_performed",
            reason=reason,
        )
        return Verification(ref=ref, found=True, verified=ok, reason=reason, after=node.summary())

    # -- permission (V2-35 tiers + stop) ---------------------------
    def _audit(
        self,
        action: str,
        *,
        target: str,
        tier: Tier,
        decision: str,
        outcome: str,
        app: str | None = None,
        length: int | None = None,
        reason: str = "",
    ) -> None:
        """Write one audit entry when a sink is wired in. Audit
        is best-effort: a sink that cannot write (no database)
        never fails the action itself."""
        if self.audit is None:
            return
        with contextlib.suppress(Exception):
            self.audit.record(
                audit_entry(
                    action,
                    target=target,
                    tier=tier.value,
                    decision=decision,
                    outcome=outcome,
                    app=app,
                    length=length,
                    reason=reason,
                )
            )

    @staticmethod
    def _app_of(shot: Snapshot, ref: str) -> str | None:
        """The app of one ref: the top-level application node
        whose subtree holds it. None when the ref is not on the
        screen (the caller raises UnknownRefError first)."""
        for top in shot.nodes:
            if top.find(ref) is not None:
                return top.name or None
        return None

    def _decide(
        self,
        action: str,
        tool: str,
        *,
        target: str,
        app: str | None = None,
        submits: bool = False,
        prompt_label: str,
        length: int | None = None,
    ) -> Tier:
        """The V2-35 gate for one action, in order: stop, token,
        session mode, tier, prompt.

        Returns the tier when the action may run. Raises
        :class:`ComputerStopped` when the user stopped the hands
        and :class:`PermissionDenied` when the token refuses, the
        session mode refuses, or the user denies; all three are
        audited before they raise. Reads never reach the prompt;
        NORMAL actions skip it inside an allowlisted app; DESTRUCTIVE
        actions always ask, allowlist or not. When a session mode
        is wired in (COW-13) it sits between the token check and
        the tier flow: read-only denies every write here, ask-first
        forces every write through the prompt (the allowlist
        shortcut does not apply), and full-access lets an ordinary
        write run without a prompt while a destructive one still
        asks through the prompt below.
        """
        tier = classify(action, submits=submits)
        if self.stop.is_active():
            self._audit(
                action,
                target=target,
                tier=tier,
                decision="stop",
                outcome="stopped",
                app=app,
                length=length,
                reason="computer hands are stopped by the user",
            )
            self.stop.check()
        self._check_token(tool)
        mode_decision = None
        if self.session_modes is not None:
            mode_decision = self.session_modes.decide(action, tier=tier)
            if mode_decision.verdict == "deny":
                self._audit(
                    action,
                    target=target,
                    tier=tier,
                    decision="deny",
                    outcome="denied",
                    app=app,
                    length=length,
                    reason=mode_decision.reason,
                )
                raise PermissionDenied(mode_decision.reason)
            if mode_decision.verdict == "allow":
                return tier
        if tier is Tier.READ:
            return tier
        if tier is Tier.NORMAL and self.allowlist.is_allowed(app) and mode_decision is None:
            return tier
        try:
            self._ask(prompt_label)
        except PermissionDenied as exc:
            self._audit(
                action,
                target=target,
                tier=tier,
                decision="deny",
                outcome="denied",
                app=app,
                length=length,
                reason=str(exc),
            )
            raise
        return tier

    def _check_token(self, tool: str) -> None:
        if self._gate is None or self._token_payload is None:
            return
        try:
            self._gate.authorize_tool(self._token_payload, tool)
        except GateDenied as exc:
            raise PermissionDenied(f"desktop action refused by the permission gate: {exc}") from exc

    def _ask(self, description: str) -> None:
        """Ask the user about one action. The description never
        carries typed or clipboard text itself, only its length,
        so secrets are not echoed into the terminal."""
        if self._session_approved:
            return
        prompt = self._prompt
        if prompt is None:
            decision = ask_permission_interactively(description, Path.cwd())
        elif callable(prompt):
            decision = prompt(description, Path.cwd())
        else:
            decision = prompt.ask(description, Path.cwd())
        if decision is PermissionDecision.DENY:
            raise PermissionDenied(f"denied by the user: {description}")
        if decision is PermissionDecision.APPROVE_SESSION:
            self._session_approved = True

    # -- the act + re-read loop ------------------------------------
    def click(self, ref: str, *, expect: dict | None = None) -> ClickResult:
        """Click one ref, then re-read to verify.

        Steps: wait for the tree to settle, resolve the ref,
        stop check, token check, tier decision (free inside an
        allowlisted app, otherwise the user is asked), snapshot
        AGAIN and re-resolve the ref against the approved
        element's identity (a changed screen refuses with
        :class:`StaleRefError` and nothing is pressed), backend
        click on the live element, fresh snapshot, verify.
        ``expect`` describes the change to look for (same shape
        as :meth:`verify`). With no ``expect``, verified means
        the re-read shows the element changed state, changed
        name, or is gone; an unchanged screen is reported as
        not verified, even when the backend said the click ran.
        """
        settled_shot, _report = self.wait_settle()
        node = settled_shot.find(ref)
        if node is None:
            raise UnknownRefError(f"no element with ref {ref!r} in the latest snapshot")
        expected = fingerprint(node)
        before = node.summary()
        app = self._app_of(settled_shot, ref)
        tier = self._decide(
            "click",
            TOOL_CLICK,
            target=ref,
            app=app,
            prompt_label=f"computer click {node.ref} {node.role} {node.name!r}",
        )
        live_shot = self._read_snapshot(skeleton=False)
        live = live_shot.find(ref)
        if live is None:
            raise StaleRefError(
                f"ref {ref!r} is gone: the element {before} left the screen after it was "
                "approved, so the click was refused and nothing was pressed. "
                "Re-snapshot and pick a ref from the fresh tree."
            )
        if fingerprint(live) != expected:
            raise StaleRefError(
                f"ref {ref!r} now points at a different element: it was {before}, "
                f"the screen now shows {live.summary()} there, so the click was refused "
                "and nothing was pressed. Re-snapshot and pick a ref from the fresh tree."
            )
        try:
            performed = self.backend.click(live)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            self._audit(
                "click",
                target=ref,
                tier=tier,
                decision="allow",
                outcome="failed",
                app=app,
                reason=str(exc),
            )
            raise DesktopHandsError(f"click on {ref} failed: {exc}") from exc
        if not performed:
            self._audit(
                "click",
                target=ref,
                tier=tier,
                decision="allow",
                outcome="not_performed",
                app=app,
                reason="element has no click action",
            )
            return ClickResult(
                ref=ref,
                performed=False,
                verified=False,
                reason="element has no click action",
                before=before,
            )
        after_shot = self._read_snapshot(skeleton=False)
        after_node = after_shot.find(ref)
        if after_node is None:
            if expect and expect.get("absent"):
                result = ClickResult(
                    ref=ref,
                    performed=True,
                    verified=True,
                    reason="element is gone, as expected",
                    before=before,
                )
            else:
                result = ClickResult(
                    ref=ref,
                    performed=True,
                    verified=False,
                    reason="element is gone from the screen and no disappearance was expected",
                    before=before,
                )
        else:
            after = after_node.summary()
            if expect is not None:
                ok, reason = _expect_matches(after_node, expect)
                result = ClickResult(
                    ref=ref, performed=True, verified=ok, reason=reason, before=before, after=after
                )
            elif after_node.name != node.name or tuple(after_node.states) != tuple(node.states):
                result = ClickResult(
                    ref=ref,
                    performed=True,
                    verified=True,
                    reason="element changed after the click",
                    before=before,
                    after=after,
                )
            else:
                result = ClickResult(
                    ref=ref,
                    performed=True,
                    verified=False,
                    reason="screen unchanged after the click: the action ran but nothing visibly changed",
                    before=before,
                    after=after,
                )
        self._audit(
            "click",
            target=ref,
            tier=tier,
            decision="allow",
            outcome="performed" if result.performed else "not_performed",
            app=app,
            reason=result.reason,
        )
        return result

    # -- V2-33: shared re-read helper ------------------------------
    def _reread_node(
        self, ref: str, expect: dict | None, before: AccessibleNode
    ) -> tuple[bool, str, str]:
        """Re-read the screen for one ref after an action.
        Returns (verified, reason, after-summary). With an
        expectation the re-read must match it; without one, the
        node must have changed (name, state, or value) or be
        gone when that was expected."""
        shot = self._read_snapshot(skeleton=False)
        after_node = shot.find(ref)
        if after_node is None:
            if expect and expect.get("absent"):
                return True, "element is gone, as expected", ""
            return False, "element is gone from the screen and no disappearance was expected", ""
        after = after_node.summary()
        if expect is not None:
            ok, reason = _expect_matches(after_node, expect)
            return ok, reason, after
        changed = (
            after_node.name != before.name
            or tuple(after_node.states) != tuple(before.states)
            or after_node.value != before.value
        )
        if changed:
            return True, "element changed after the action", after
        return False, "screen unchanged after the action: it ran but nothing visibly changed", after

    def _resolve(self, ref: str) -> tuple[Snapshot, AccessibleNode]:
        """Fresh snapshot + the node for ``ref`` (raises
        UnknownRefError before any prompt or gate check)."""
        shot = self._read_snapshot(skeleton=False)
        node = shot.find(ref)
        if node is None:
            raise UnknownRefError(f"no element with ref {ref!r} in the latest snapshot")
        return shot, node

    def _finish_input(
        self,
        action: str,
        *,
        target: str,
        tier: Tier,
        performed: bool,
        verified: bool,
        reason: str,
        before: str,
        after: str = "",
        app: str | None = None,
        length: int | None = None,
    ) -> InputResult:
        """Build an :class:`InputResult` and audit the action."""
        self._audit(
            action,
            target=target,
            tier=tier,
            decision="allow",
            outcome="performed" if performed else "not_performed",
            app=app,
            length=length,
            reason=reason,
        )
        return InputResult(
            action=action,
            performed=performed,
            verified=verified,
            reason=reason,
            before=before,
            after=after,
        )

    # -- V2-33: full input ------------------------------------------
    def type_text(
        self, ref: str, text: str, *, expect: dict | None = None, submits: bool = False
    ) -> InputResult:
        """Type ``text`` into the element at ``ref`` (it must be
        editable), then re-read to verify. The prompt shows the
        length of the text, never the text itself. ``submits``
        marks typing into a field that submits (a search box, a
        chat input): that is DESTRUCTIVE and always asks, while
        plain typing is NORMAL. The audit records only the
        length of the text, never the text."""
        shot, node = self._resolve(ref)
        before = node.summary()
        app = self._app_of(shot, ref)
        tier = self._decide(
            "type",
            TOOL_TYPE,
            target=ref,
            app=app,
            submits=submits,
            prompt_label=f"computer type {len(text)} chars into {node.ref} {node.role} {node.name!r}",
            length=len(text),
        )
        try:
            performed = self.input_backend.type_text(node, text)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            self._audit(
                "type",
                target=ref,
                tier=tier,
                decision="allow",
                outcome="failed",
                app=app,
                length=len(text),
                reason=str(exc),
            )
            raise DesktopHandsError(f"type into {ref} failed: {exc}") from exc
        if not performed:
            return self._finish_input(
                "type",
                target=ref,
                tier=tier,
                performed=False,
                verified=False,
                reason="element cannot take text (not editable)",
                before=before,
                app=app,
                length=len(text),
            )
        verified, reason, after = self._reread_node(ref, expect, node)
        return self._finish_input(
            "type",
            target=ref,
            tier=tier,
            performed=True,
            verified=verified,
            reason=reason,
            before=before,
            after=after,
            app=app,
            length=len(text),
        )

    def shortcut(
        self,
        keys: tuple[str, ...] | str,
        ref: str | None = None,
        *,
        expect: dict | None = None,
    ) -> InputResult:
        """Press a keyboard chord like ``"ctrl+a"`` (or
        ``("ctrl", "a")``). With ``ref`` the chord targets that
        element; without one it targets the focused element.
        Then re-read to verify. A chord holding Enter/Return
        submits, so it is DESTRUCTIVE and always asks."""
        if isinstance(keys, str):
            chord = tuple(part.strip() for part in keys.split("+") if part.strip())
        else:
            chord = tuple(keys)
        if not chord:
            raise DesktopHandsError("shortcut needs at least one key")
        if ref is not None:
            shot, node = self._resolve(ref)
            app = self._app_of(shot, ref)
            target = ref
        else:
            shot = self._read_snapshot(skeleton=False)
            focused: list[AccessibleNode] = []

            def walk(nodes: tuple[AccessibleNode, ...]) -> None:
                for item in nodes:
                    if "focused" in item.states:
                        focused.append(item)
                    walk(item.children)

            walk(shot.nodes)
            if not focused:
                raise UnknownRefError("no focused element on the screen to send the shortcut to")
            node = focused[-1]
            app = self._app_of(shot, node.ref)
            target = node.ref
        before = node.summary()
        tier = self._decide(
            "shortcut",
            TOOL_SHORTCUT,
            target=target,
            app=app,
            submits=is_submit_shortcut(chord),
            prompt_label=f"computer shortcut {'+'.join(chord)} on {node.ref} {node.role} {node.name!r}",
        )
        try:
            performed = self.input_backend.key(chord, node)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            self._audit(
                "shortcut",
                target=target,
                tier=tier,
                decision="allow",
                outcome="failed",
                app=app,
                reason=str(exc),
            )
            raise DesktopHandsError(f"shortcut {'+'.join(chord)} failed: {exc}") from exc
        if not performed:
            return self._finish_input(
                "shortcut",
                target=target,
                tier=tier,
                performed=False,
                verified=False,
                reason="shortcut had no effect on this element (unknown chord or no shortcut)",
                before=before,
                app=app,
            )
        verified, reason, after = self._reread_node(node.ref, expect, node)
        return self._finish_input(
            "shortcut",
            target=target,
            tier=tier,
            performed=True,
            verified=verified,
            reason=reason,
            before=before,
            after=after,
            app=app,
        )

    def scroll(
        self,
        ref: str,
        direction: str = "down",
        amount: int = 3,
        *,
        expect: dict | None = None,
    ) -> InputResult:
        """Scroll ``direction`` (up/down/left/right) by ``amount``
        clicks at the element at ``ref``, then re-read to verify.
        A scroll with no expected change reports unverified when
        the re-read shows nothing different: scrolling moves a
        viewport, and only the screen can say what it revealed."""
        if direction not in ("up", "down", "left", "right"):
            raise DesktopHandsError(f"unknown scroll direction {direction!r}")
        if amount <= 0:
            raise DesktopHandsError("scroll amount must be at least 1")
        shot, node = self._resolve(ref)
        before = node.summary()
        app = self._app_of(shot, ref)
        tier = self._decide(
            "scroll",
            TOOL_SCROLL,
            target=ref,
            app=app,
            prompt_label=f"computer scroll {direction} x{amount} on {node.ref} {node.role} {node.name!r}",
        )
        try:
            performed = self.input_backend.scroll(node, direction, amount)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            self._audit(
                "scroll",
                target=ref,
                tier=tier,
                decision="allow",
                outcome="failed",
                app=app,
                reason=str(exc),
            )
            raise DesktopHandsError(f"scroll on {ref} failed: {exc}") from exc
        if not performed:
            return self._finish_input(
                "scroll",
                target=ref,
                tier=tier,
                performed=False,
                verified=False,
                reason="element cannot scroll",
                before=before,
                app=app,
            )
        verified, reason, after = self._reread_node(ref, expect, node)
        return self._finish_input(
            "scroll",
            target=ref,
            tier=tier,
            performed=True,
            verified=verified,
            reason=reason,
            before=before,
            after=after,
            app=app,
        )

    def drag(self, ref_from: str, ref_to: str, *, expect: dict | None = None) -> InputResult:
        """Drag the element at ``ref_from`` onto the element at
        ``ref_to``, then re-read to verify (the drop target is
        what the re-read checks). DESTRUCTIVE: a drag moves or
        deletes things, so it always asks, even inside an
        allowlisted app."""
        from_shot, from_node = self._resolve(ref_from)
        _, to_node = self._resolve(ref_to)
        before = to_node.summary()
        app = self._app_of(from_shot, ref_from)
        target = f"{ref_from} onto {ref_to}"
        tier = self._decide(
            "drag",
            TOOL_DRAG,
            target=target,
            app=app,
            prompt_label=(
                f"computer drag {from_node.ref} {from_node.role} {from_node.name!r} "
                f"onto {to_node.ref} {to_node.role} {to_node.name!r}"
            ),
        )
        try:
            performed = self.input_backend.drag(from_node, to_node)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            self._audit(
                "drag",
                target=target,
                tier=tier,
                decision="allow",
                outcome="failed",
                app=app,
                reason=str(exc),
            )
            raise DesktopHandsError(f"drag {ref_from} onto {ref_to} failed: {exc}") from exc
        if not performed:
            return self._finish_input(
                "drag",
                target=target,
                tier=tier,
                performed=False,
                verified=False,
                reason="source element cannot be dragged",
                before=before,
                app=app,
            )
        verified, reason, after = self._reread_node(ref_to, expect, to_node)
        return self._finish_input(
            "drag",
            target=target,
            tier=tier,
            performed=True,
            verified=verified,
            reason=reason,
            before=before,
            after=after,
            app=app,
        )

    # -- V2-33: clipboard -------------------------------------------
    def clipboard_read(self) -> ClipboardResult:
        """Read the session clipboard. The content is returned
        in memory only: it never appears in a log, a file, or a
        result dict, only its length does. Tier NORMAL (it
        returns the user's own data), so it asks; the audit
        records only the length. Before it is returned it passes
        through the safety layer, so a password, PIN, or card
        number on the clipboard reaches the agent masked and an
        injected instruction arrives marked as data."""
        tier = self._decide(
            "clipboard_read",
            TOOL_CLIPBOARD_READ,
            target="clipboard",
            prompt_label="computer clipboard read",
        )
        try:
            content = self.input_backend.clipboard_read()
        except BackendUnavailableError:
            raise
        except Exception as exc:
            self._audit(
                "clipboard_read",
                target="clipboard",
                tier=tier,
                decision="allow",
                outcome="failed",
                reason=str(exc),
            )
            raise DesktopHandsError(f"clipboard read failed: {exc}") from exc
        content = self.safety.sanitize_clipboard(content)
        self._audit(
            "clipboard_read",
            target="clipboard",
            tier=tier,
            decision="allow",
            outcome="performed",
            length=len(content),
            reason="clipboard read",
        )
        return ClipboardResult(
            action="clipboard_read",
            performed=True,
            verified=True,
            reason="clipboard read",
            length=len(content),
            content=content,
        )

    def clipboard_write(self, text: str) -> ClipboardResult:
        """Replace the session clipboard, then read it back and
        compare in memory to verify. The prompt and the result
        report only the length, never the content. DESTRUCTIVE:
        it changes data outside the app, so it always asks, even
        inside an allowlisted app."""
        tier = self._decide(
            "clipboard_write",
            TOOL_CLIPBOARD_WRITE,
            target="clipboard",
            prompt_label=f"computer clipboard write ({len(text)} chars)",
            length=len(text),
        )
        try:
            performed = self.input_backend.clipboard_write(text)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            self._audit(
                "clipboard_write",
                target="clipboard",
                tier=tier,
                decision="allow",
                outcome="failed",
                length=len(text),
                reason=str(exc),
            )
            raise DesktopHandsError(f"clipboard write failed: {exc}") from exc
        if not performed:
            self._audit(
                "clipboard_write",
                target="clipboard",
                tier=tier,
                decision="allow",
                outcome="not_performed",
                length=len(text),
                reason="clipboard write did not run",
            )
            return ClipboardResult(
                action="clipboard_write",
                performed=False,
                verified=False,
                reason="clipboard write did not run",
                length=len(text),
            )
        try:
            verified = self.input_backend.clipboard_read() == text
        except BackendUnavailableError:
            verified = False
        self._audit(
            "clipboard_write",
            target="clipboard",
            tier=tier,
            decision="allow",
            outcome="performed",
            length=len(text),
            reason="clipboard reads back equal"
            if verified
            else "clipboard did not read back equal",
        )
        return ClipboardResult(
            action="clipboard_write",
            performed=True,
            verified=verified,
            reason="clipboard reads back equal"
            if verified
            else "clipboard did not read back equal",
            length=len(text),
        )

    # -- V2-33: windows ----------------------------------------------
    def list_windows(self) -> list[WindowInfo]:
        """List the open windows (a read, like snapshot: no
        prompt, no token check). Tier READ: never asks, but
        still audited."""
        windows = self.window_backend.list_windows()
        self._audit(
            "list_windows",
            target="windows",
            tier=Tier.READ,
            decision="allow",
            outcome="performed",
            reason=f"{len(windows)} windows open",
        )
        return windows

    def open_app(self, name: str, *, expect: dict | None = None) -> WindowResult:
        """Open an app by name, then re-read the tree to verify
        a window for it appeared. ``expect`` may carry ``app``
        (application name) or ``title`` (window title)."""
        if not name.strip():
            raise DesktopHandsError("open_app needs a non-empty app name")
        tier = self._decide(
            "open_app",
            TOOL_OPEN_APP,
            target=name,
            app=name,
            prompt_label=f"computer open app {name!r}",
        )
        try:
            performed = self.window_backend.open_app(name)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            self._audit(
                "open_app",
                target=name,
                tier=tier,
                decision="allow",
                outcome="failed",
                app=name,
                reason=str(exc),
            )
            raise DesktopHandsError(f"open app {name!r} failed: {exc}") from exc
        if not performed:
            self._audit(
                "open_app",
                target=name,
                tier=tier,
                decision="allow",
                outcome="not_performed",
                app=name,
                reason=f"could not launch {name!r}",
            )
            return WindowResult(
                action="open_app",
                window_id="",
                performed=False,
                verified=False,
                reason=f"could not launch {name!r}",
            )
        shot = self._read_snapshot(skeleton=False)
        windows = windows_from_snapshot(shot)
        wanted_app = (expect or {}).get("app", name)
        wanted_title = (expect or {}).get("title")
        for window in windows:
            if window.app != wanted_app:
                continue
            if wanted_title is not None and window.title != wanted_title:
                continue
            result = WindowResult(
                action="open_app",
                window_id=window.id,
                performed=True,
                verified=True,
                reason=f"window for {window.app!r} is on the screen",
                after=f"{window.id} {window.app} {window.title!r}",
            )
            self._audit(
                "open_app",
                target=name,
                tier=tier,
                decision="allow",
                outcome="performed",
                app=name,
                reason=result.reason,
            )
            return result
        self._audit(
            "open_app",
            target=name,
            tier=tier,
            decision="allow",
            outcome="performed",
            app=name,
            reason=f"no window for {name!r} appeared after the launch",
        )
        return WindowResult(
            action="open_app",
            window_id="",
            performed=True,
            verified=False,
            reason=f"no window for {name!r} appeared after the launch",
        )

    def focus_window(
        self, window_id: str, *, expect: dict | None = None, _audit_action: str = "focus_window"
    ) -> WindowResult:
        """Bring one window to the front, then re-read the window
        list to verify it is the focused one. ``_audit_action``
        lets :meth:`switch_window` record its own name instead
        of ``focus_window`` (the switch is the action the user
        asked for)."""
        del expect  # focus is verified by the focused flag itself
        before_windows = self.window_backend.list_windows()
        before = next((w for w in before_windows if w.id == window_id), None)
        if before is None:
            raise DesktopHandsError(f"no window with id {window_id!r}")
        tier = self._decide(
            _audit_action,
            TOOL_FOCUS_WINDOW,
            target=window_id,
            app=before.app,
            prompt_label=f"computer focus window {window_id} {before.app} {before.title!r}",
        )
        try:
            performed = self.window_backend.focus_window(window_id)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            self._audit(
                _audit_action,
                target=window_id,
                tier=tier,
                decision="allow",
                outcome="failed",
                app=before.app,
                reason=str(exc),
            )
            raise DesktopHandsError(f"focus window {window_id!r} failed: {exc}") from exc
        if not performed:
            self._audit(
                _audit_action,
                target=window_id,
                tier=tier,
                decision="allow",
                outcome="not_performed",
                app=before.app,
                reason=f"window {window_id!r} could not be focused",
            )
            return WindowResult(
                action=_audit_action,
                window_id=window_id,
                performed=False,
                verified=False,
                reason=f"window {window_id!r} could not be focused",
            )
        after_windows = self.window_backend.list_windows()
        after = next((w for w in after_windows if w.id == window_id), None)
        if after is not None and after.focused:
            result = WindowResult(
                action=_audit_action,
                window_id=window_id,
                performed=True,
                verified=True,
                reason=f"window {window_id} is focused",
                before=f"{before.id} focused={before.focused}",
                after=f"{after.id} focused={after.focused}",
            )
        else:
            result = WindowResult(
                action=_audit_action,
                window_id=window_id,
                performed=True,
                verified=False,
                reason=f"window {window_id} is not focused after the action",
            )
        self._audit(
            _audit_action,
            target=window_id,
            tier=tier,
            decision="allow",
            outcome="performed" if result.performed else "not_performed",
            app=before.app,
            reason=result.reason,
        )
        return result

    def switch_window(self, *, expect: dict | None = None) -> WindowResult:
        """Switch to the next window after the focused one
        (cycling), then re-read to verify the focus moved."""
        del expect  # focus is verified by the focused flag itself
        windows = self.window_backend.list_windows()
        if len(windows) < 2:
            self._audit(
                "switch_window",
                target="",
                tier=Tier.NORMAL,
                decision="allow",
                outcome="not_performed",
                reason="only one window is open, nothing to switch to",
            )
            return WindowResult(
                action="switch_window",
                window_id="",
                performed=False,
                verified=False,
                reason="only one window is open, nothing to switch to",
            )
        focused_index = next((i for i, w in enumerate(windows) if w.focused), 0)
        target = windows[(focused_index + 1) % len(windows)]
        return self.focus_window(target.id, _audit_action="switch_window")

    def close_window(self, window_id: str) -> WindowResult:
        """Close one window by id, then re-read the window list
        to verify it is gone. DESTRUCTIVE (V2-35): closing
        throws a window away, so it always asks, even inside an
        allowlisted app. Closing an app is closing its windows:
        there is no separate close-app action."""
        before_windows = self.window_backend.list_windows()
        before = next((w for w in before_windows if w.id == window_id), None)
        if before is None:
            raise DesktopHandsError(f"no window with id {window_id!r}")
        tier = self._decide(
            "close_window",
            TOOL_CLOSE_WINDOW,
            target=window_id,
            app=before.app,
            prompt_label=f"computer close window {window_id} {before.app} {before.title!r}",
        )
        try:
            performed = self.window_backend.close_window(window_id)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            self._audit(
                "close_window",
                target=window_id,
                tier=tier,
                decision="allow",
                outcome="failed",
                app=before.app,
                reason=str(exc),
            )
            raise DesktopHandsError(f"close window {window_id!r} failed: {exc}") from exc
        if not performed:
            self._audit(
                "close_window",
                target=window_id,
                tier=tier,
                decision="allow",
                outcome="not_performed",
                app=before.app,
                reason=f"window {window_id!r} could not be closed",
            )
            return WindowResult(
                action="close_window",
                window_id=window_id,
                performed=False,
                verified=False,
                reason=f"window {window_id!r} could not be closed",
            )
        after_windows = self.window_backend.list_windows()
        # Verify by app + title, not by id: window ids are
        # positional paths, so closing app 0 shifts app 1 to id
        # 0.0 and an id lookup would mistake the next window for
        # the closed one.
        gone = not any(w.app == before.app and w.title == before.title for w in after_windows)
        result = WindowResult(
            action="close_window",
            window_id=window_id,
            performed=True,
            verified=gone,
            reason=f"window {window_id} is closed"
            if gone
            else f"window {window_id} is still open after the close",
            before=f"{before.id} {before.app} {before.title!r}",
        )
        self._audit(
            "close_window",
            target=window_id,
            tier=tier,
            decision="allow",
            outcome="performed",
            app=before.app,
            reason=result.reason,
        )
        return result

    # -- V2-33: multi-step tasks --------------------------------------
    def run_task(self, steps: list[dict]) -> dict:
        """Run a list of steps end to end, stopping at the first
        step that did not perform or did not verify.

        Each step is a dict with ``"action"`` (``type``,
        ``shortcut``, ``scroll``, ``drag``, ``click``, ``verify``,
        ``clipboard_read``, ``clipboard_write``, ``open_app``,
        ``focus_window``, ``switch_window``, ``close_window``,
        ``list_windows``, ``snapshot``) plus that action's
        arguments and an optional ``"expect"``; a ``type`` step
        may carry ``"submits": true`` when the field submits.
        Returns ``{"steps": [...], "completed": bool,
        "stopped": bool}``; a step result carries ``performed``
        / ``verified`` / ``reason`` like the single-action
        results, and clipboard content never appears in it.

        The emergency stop is checked before every step, and
        every step checks it again before it acts: a task stops
        at the next step boundary at the latest, and the result
        says ``"stopped": true`` with the step that never ran.
        """
        results: list[dict] = []
        completed = True
        stopped = False
        for index, step in enumerate(steps):
            if self.stop.is_active():
                results.append(
                    {
                        "step": index,
                        "action": step.get("action", "") if isinstance(step, dict) else "",
                        "performed": False,
                        "verified": False,
                        "reason": "stopped by the user before this step ran",
                        "stopped": True,
                    }
                )
                completed = False
                stopped = True
                break
            if not isinstance(step, dict):
                results.append(
                    {
                        "step": index,
                        "action": "",
                        "performed": False,
                        "verified": False,
                        "reason": "step is not an object",
                    }
                )
                completed = False
                break
            action = step.get("action", "")
            expect = step.get("expect")
            try:
                if action == "type":
                    result = self.type_text(
                        step["ref"],
                        step["text"],
                        expect=expect,
                        submits=bool(step.get("submits", False)),
                    ).to_dict()
                elif action == "shortcut":
                    result = self.shortcut(step["keys"], step.get("ref"), expect=expect).to_dict()
                elif action == "scroll":
                    result = self.scroll(
                        step["ref"],
                        step.get("direction", "down"),
                        int(step.get("amount", 3)),
                        expect=expect,
                    ).to_dict()
                elif action == "drag":
                    result = self.drag(step["ref_from"], step["ref_to"], expect=expect).to_dict()
                elif action == "click":
                    result = self.click(step["ref"], expect=expect).to_dict()
                elif action == "verify":
                    check = self.verify(step["ref"], expect=expect)
                    result = {
                        "action": "verify",
                        "performed": check.found,
                        "verified": check.verified,
                        "reason": check.reason,
                        "before": check.before,
                        "after": check.after,
                    }
                elif action == "clipboard_read":
                    result = self.clipboard_read().to_dict()
                elif action == "clipboard_write":
                    result = self.clipboard_write(step["text"]).to_dict()
                elif action == "open_app":
                    result = self.open_app(step["name"], expect=expect).to_dict()
                elif action == "focus_window":
                    result = self.focus_window(step["window_id"], expect=expect).to_dict()
                elif action == "switch_window":
                    result = self.switch_window(expect=expect).to_dict()
                elif action == "close_window":
                    result = self.close_window(step["window_id"]).to_dict()
                elif action == "list_windows":
                    windows = self.list_windows()
                    result = {
                        "action": "list_windows",
                        "performed": True,
                        "verified": True,
                        "reason": f"{len(windows)} windows open",
                        "windows": [w.to_dict() for w in windows],
                    }
                elif action == "snapshot":
                    shot = self.snapshot(skeleton=False)
                    result = {
                        "action": "snapshot",
                        "performed": True,
                        "verified": True,
                        "reason": f"{shot.total_nodes} nodes",
                    }
                else:
                    result = {
                        "action": action,
                        "performed": False,
                        "verified": False,
                        "reason": f"unknown task action {action!r}",
                    }
            except ComputerStopped as exc:
                result = {
                    "action": action,
                    "performed": False,
                    "verified": False,
                    "reason": f"ComputerStopped: {exc}",
                    "stopped": True,
                }
                stopped = True
            except Exception as exc:
                result = {
                    "action": action,
                    "performed": False,
                    "verified": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            result = {"step": index, **result}
            results.append(result)
            if not result.get("performed", False) or not result.get("verified", False):
                completed = False
                break
        self._audit(
            "run_task",
            target=f"{len(steps)} steps",
            tier=Tier.NORMAL,
            decision="stop" if stopped else "allow",
            outcome="stopped" if stopped else ("performed" if completed else "not_performed"),
            reason=f"{len(results)} steps ran",
        )
        return {"steps": results, "completed": completed, "stopped": stopped}
