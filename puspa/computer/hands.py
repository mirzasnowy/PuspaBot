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
they return is screen text, and screening that text is V2-36
work, not this module's.

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
    FakeBackend,
    make_backend,
)
from .input import (
    ClipboardResult,
    InputBackend,
    InputResult,
    make_input_backend,
)
from .sync import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_SETTLE_TIMEOUT_S,
    DEFAULT_STABLE_ROUNDS,
    SettleReport,
    fingerprint,
    tree_signature,
    wait_for_settle,
)
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
    clicks. ``input_backend`` / ``window_backend`` default to
    the backends matching ``backend`` (fake shares the fake
    tree; auto uses the real Linux ones).
    """

    def __init__(
        self,
        backend: AccessibilityBackend | None = None,
        *,
        prompt: PermissionPrompt | None = None,
        gate: Gate | None = None,
        token_payload: dict | None = None,
        input_backend: InputBackend | None = None,
        window_backend: WindowBackend | None = None,
        settle_timeout: float = DEFAULT_SETTLE_TIMEOUT_S,
        settle_stable_rounds: int = DEFAULT_STABLE_ROUNDS,
        settle_poll_interval: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self.backend = backend if backend is not None else make_backend("auto")
        self._prompt = prompt
        self._gate = gate
        self._token_payload = token_payload
        self._session_approved = False
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

    def wait_settle(
        self, *, timeout_s: float | None = None, stable_rounds: int | None = None
    ) -> tuple[Snapshot, SettleReport]:
        """Wait until the tree stops changing, then return the
        settled snapshot and the settle report. Raises
        :class:`puspa.computer.sync.SettleTimeoutError` when the
        screen never goes quiet inside the bounded wait."""
        return wait_for_settle(
            lambda: self.snapshot(skeleton=False),
            signature=tree_signature,
            timeout_s=self._settle_timeout if timeout_s is None else timeout_s,
            stable_rounds=self._settle_stable_rounds if stable_rounds is None else stable_rounds,
            poll_interval_s=self._settle_poll_interval,
        )

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
        token check, user prompt, snapshot AGAIN and re-resolve
        the ref against the approved element's identity (a
        changed screen refuses with :class:`StaleRefError` and
        nothing is pressed), backend click on the live element,
        fresh snapshot, verify.
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
        self._check_token(TOOL_CLICK)
        self._ask(f"computer click {node.ref} {node.role} {node.name!r}")
        live_shot = self.snapshot(skeleton=False)
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

    # -- V2-33: shared re-read helper ------------------------------
    def _reread_node(
        self, ref: str, expect: dict | None, before: AccessibleNode
    ) -> tuple[bool, str, str]:
        """Re-read the screen for one ref after an action.
        Returns (verified, reason, after-summary). With an
        expectation the re-read must match it; without one, the
        node must have changed (name, state, or value) or be
        gone when that was expected."""
        shot = self.snapshot(skeleton=False)
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
        shot = self.snapshot(skeleton=False)
        node = shot.find(ref)
        if node is None:
            raise UnknownRefError(f"no element with ref {ref!r} in the latest snapshot")
        return shot, node

    # -- V2-33: full input ------------------------------------------
    def type_text(self, ref: str, text: str, *, expect: dict | None = None) -> InputResult:
        """Type ``text`` into the element at ``ref`` (it must be
        editable), then re-read to verify. The prompt shows the
        length of the text, never the text itself."""
        _, node = self._resolve(ref)
        before = node.summary()
        self._check_token(TOOL_TYPE)
        self._ask(f"computer type {len(text)} chars into {node.ref} {node.role} {node.name!r}")
        try:
            performed = self.input_backend.type_text(node, text)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise DesktopHandsError(f"type into {ref} failed: {exc}") from exc
        if not performed:
            return InputResult(
                action="type",
                performed=False,
                verified=False,
                reason="element cannot take text (not editable)",
                before=before,
            )
        verified, reason, after = self._reread_node(ref, expect, node)
        return InputResult(
            action="type",
            performed=True,
            verified=verified,
            reason=reason,
            before=before,
            after=after,
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
        Then re-read to verify."""
        if isinstance(keys, str):
            chord = tuple(part.strip() for part in keys.split("+") if part.strip())
        else:
            chord = tuple(keys)
        if not chord:
            raise DesktopHandsError("shortcut needs at least one key")
        if ref is not None:
            _, node = self._resolve(ref)
        else:
            shot = self.snapshot(skeleton=False)
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
        before = node.summary()
        self._check_token(TOOL_SHORTCUT)
        self._ask(f"computer shortcut {'+'.join(chord)} on {node.ref} {node.role} {node.name!r}")
        try:
            performed = self.input_backend.key(chord, node)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise DesktopHandsError(f"shortcut {'+'.join(chord)} failed: {exc}") from exc
        if not performed:
            return InputResult(
                action="shortcut",
                performed=False,
                verified=False,
                reason="shortcut had no effect on this element (unknown chord or no shortcut)",
                before=before,
            )
        verified, reason, after = self._reread_node(node.ref, expect, node)
        return InputResult(
            action="shortcut",
            performed=True,
            verified=verified,
            reason=reason,
            before=before,
            after=after,
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
        _, node = self._resolve(ref)
        before = node.summary()
        self._check_token(TOOL_SCROLL)
        self._ask(f"computer scroll {direction} x{amount} on {node.ref} {node.role} {node.name!r}")
        try:
            performed = self.input_backend.scroll(node, direction, amount)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise DesktopHandsError(f"scroll on {ref} failed: {exc}") from exc
        if not performed:
            return InputResult(
                action="scroll",
                performed=False,
                verified=False,
                reason="element cannot scroll",
                before=before,
            )
        verified, reason, after = self._reread_node(ref, expect, node)
        return InputResult(
            action="scroll",
            performed=True,
            verified=verified,
            reason=reason,
            before=before,
            after=after,
        )

    def drag(self, ref_from: str, ref_to: str, *, expect: dict | None = None) -> InputResult:
        """Drag the element at ``ref_from`` onto the element at
        ``ref_to``, then re-read to verify (the drop target is
        what the re-read checks)."""
        _, from_node = self._resolve(ref_from)
        _, to_node = self._resolve(ref_to)
        before = to_node.summary()
        self._check_token(TOOL_DRAG)
        self._ask(
            f"computer drag {from_node.ref} {from_node.role} {from_node.name!r} "
            f"onto {to_node.ref} {to_node.role} {to_node.name!r}"
        )
        try:
            performed = self.input_backend.drag(from_node, to_node)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise DesktopHandsError(f"drag {ref_from} onto {ref_to} failed: {exc}") from exc
        if not performed:
            return InputResult(
                action="drag",
                performed=False,
                verified=False,
                reason="source element cannot be dragged",
                before=before,
            )
        verified, reason, after = self._reread_node(ref_to, expect, to_node)
        return InputResult(
            action="drag",
            performed=True,
            verified=verified,
            reason=reason,
            before=before,
            after=after,
        )

    # -- V2-33: clipboard -------------------------------------------
    def clipboard_read(self) -> ClipboardResult:
        """Read the session clipboard. The content is returned
        in memory only: it never appears in a log, a file, or a
        result dict, only its length does."""
        self._check_token(TOOL_CLIPBOARD_READ)
        self._ask("computer clipboard read")
        try:
            content = self.input_backend.clipboard_read()
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise DesktopHandsError(f"clipboard read failed: {exc}") from exc
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
        report only the length, never the content."""
        self._check_token(TOOL_CLIPBOARD_WRITE)
        self._ask(f"computer clipboard write ({len(text)} chars)")
        try:
            performed = self.input_backend.clipboard_write(text)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise DesktopHandsError(f"clipboard write failed: {exc}") from exc
        if not performed:
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
        prompt, no token check)."""
        return self.window_backend.list_windows()

    def open_app(self, name: str, *, expect: dict | None = None) -> WindowResult:
        """Open an app by name, then re-read the tree to verify
        a window for it appeared. ``expect`` may carry ``app``
        (application name) or ``title`` (window title)."""
        if not name.strip():
            raise DesktopHandsError("open_app needs a non-empty app name")
        self._check_token(TOOL_OPEN_APP)
        self._ask(f"computer open app {name!r}")
        try:
            performed = self.window_backend.open_app(name)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise DesktopHandsError(f"open app {name!r} failed: {exc}") from exc
        if not performed:
            return WindowResult(
                action="open_app",
                window_id="",
                performed=False,
                verified=False,
                reason=f"could not launch {name!r}",
            )
        shot = self.snapshot(skeleton=False)
        windows = windows_from_snapshot(shot)
        wanted_app = (expect or {}).get("app", name)
        wanted_title = (expect or {}).get("title")
        for window in windows:
            if window.app != wanted_app:
                continue
            if wanted_title is not None and window.title != wanted_title:
                continue
            return WindowResult(
                action="open_app",
                window_id=window.id,
                performed=True,
                verified=True,
                reason=f"window for {window.app!r} is on the screen",
                after=f"{window.id} {window.app} {window.title!r}",
            )
        return WindowResult(
            action="open_app",
            window_id="",
            performed=True,
            verified=False,
            reason=f"no window for {name!r} appeared after the launch",
        )

    def focus_window(self, window_id: str, *, expect: dict | None = None) -> WindowResult:
        """Bring one window to the front, then re-read the window
        list to verify it is the focused one."""
        del expect  # focus is verified by the focused flag itself
        before_windows = self.list_windows()
        before = next((w for w in before_windows if w.id == window_id), None)
        if before is None:
            raise DesktopHandsError(f"no window with id {window_id!r}")
        self._check_token(TOOL_FOCUS_WINDOW)
        self._ask(f"computer focus window {window_id} {before.app} {before.title!r}")
        try:
            performed = self.window_backend.focus_window(window_id)
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise DesktopHandsError(f"focus window {window_id!r} failed: {exc}") from exc
        if not performed:
            return WindowResult(
                action="focus_window",
                window_id=window_id,
                performed=False,
                verified=False,
                reason=f"window {window_id!r} could not be focused",
            )
        after_windows = self.list_windows()
        after = next((w for w in after_windows if w.id == window_id), None)
        if after is not None and after.focused:
            return WindowResult(
                action="focus_window",
                window_id=window_id,
                performed=True,
                verified=True,
                reason=f"window {window_id} is focused",
                before=f"{before.id} focused={before.focused}",
                after=f"{after.id} focused={after.focused}",
            )
        return WindowResult(
            action="focus_window",
            window_id=window_id,
            performed=True,
            verified=False,
            reason=f"window {window_id} is not focused after the action",
        )

    def switch_window(self, *, expect: dict | None = None) -> WindowResult:
        """Switch to the next window after the focused one
        (cycling), then re-read to verify the focus moved."""
        del expect  # focus is verified by the focused flag itself
        windows = self.list_windows()
        if len(windows) < 2:
            return WindowResult(
                action="switch_window",
                window_id="",
                performed=False,
                verified=False,
                reason="only one window is open, nothing to switch to",
            )
        focused_index = next((i for i, w in enumerate(windows) if w.focused), 0)
        target = windows[(focused_index + 1) % len(windows)]
        return self.focus_window(target.id)

    # -- V2-33: multi-step tasks --------------------------------------
    def run_task(self, steps: list[dict]) -> dict:
        """Run a list of steps end to end, stopping at the first
        step that did not perform or did not verify.

        Each step is a dict with ``"action"`` (``type``,
        ``shortcut``, ``scroll``, ``drag``, ``click``, ``verify``,
        ``clipboard_read``, ``clipboard_write``, ``open_app``,
        ``focus_window``, ``switch_window``, ``list_windows``,
        ``snapshot``) plus that action's arguments and an
        optional ``"expect"``. Returns ``{"steps": [...],
        "completed": bool}``; a step result carries
        ``performed`` / ``verified`` / ``reason`` like the
        single-action results, and clipboard content never
        appears in it.
        """
        results: list[dict] = []
        completed = True
        for index, step in enumerate(steps):
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
                    result = self.type_text(step["ref"], step["text"], expect=expect).to_dict()
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
        return {"steps": results, "completed": completed}
