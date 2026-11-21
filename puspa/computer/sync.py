"""Synchronization + non-stale refs (V2-34).

Two rules, both above the backend seam so every OS backend
inherits them when it plugs in (Linux first here; Windows and
macOS are later backends, not later rules):

1. Wait for the UI to settle before acting. :func:`wait_for_settle`
   reads the screen repeatedly until the same picture comes back
   ``stable_rounds`` times in a row, with a bounded wait. It never
   waits a fixed sleep and then hopes: the wait ends when the
   screen stops changing, or when the timeout runs out and the
   caller refuses. A spinner that never stops is a refusal, not a
   reason to act on a moving target.
2. Re-resolve every ref before acting. A ref is only a handle
   inside one snapshot (see :mod:`puspa.computer.tree`): the
   element it pointed at can move, change, or be deleted while
   the agent is thinking or the user is being asked. So an act
   call snapshots again right before it touches the desktop and
   checks the live element's identity with :func:`fingerprint`.
   Identity is role + name + path + description + actions: a list
   position alone is never enough, so a ref that pointed at a
   deleted row can never silently land on the row that slid into
   its place. When the element is gone, or what the ref finds is
   a different element, the call refuses with a stale error
   (raised by the caller: :class:`puspa.computer.hands.StaleRefError`
   for tree refs, :class:`puspa.computer.fallback.StaleVisualTargetError`
   for OCR targets). A stale refusal is NOT a permission denial:
   nothing was denied by the user or the token gate, the screen
   simply changed, so it is reported distinctly and the caller
   re-reads and tries again.

The same rule covers the V2-32 visual fallback: an OCR-grounded
target is a box, not a ref, so its identity is its label plus its
box (:func:`visual_fingerprint`). Before a coordinate click the
screen is read again and the text is re-grounded; when the box
moved or the text is gone, the click is refused instead of
pressing the old pixel. Re-finding a moved target means a fresh
call (fresh grounding, fresh user approval), never this call
quietly aiming somewhere new.

The pattern name in the issue (the macOS private command-host
pattern) is only a name: the mechanism here is written as Puspa
code from the requirements above, no upstream code was copied.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .tree import AccessibleNode, Snapshot
from .vision import OcrWord, VisualElement

__all__ = [
    "ElementIdentity",
    "SettleReport",
    "SettleTimeoutError",
    "fingerprint",
    "tree_signature",
    "visual_fingerprint",
    "wait_for_settle",
    "words_signature",
]

#: How many identical reads in a row count as settled.
DEFAULT_STABLE_ROUNDS = 2
#: Longest a settle wait runs before the caller refuses.
DEFAULT_SETTLE_TIMEOUT_S = 5.0
#: Pause between settle reads (polling, not the wait itself: the
#: wait ends on stability, this only spaces the reads out).
DEFAULT_POLL_INTERVAL_S = 0.05


@dataclass(frozen=True)
class SettleReport:
    """What one settle wait saw: whether the screen went quiet,
    and how many reads it took."""

    settled: bool
    snapshots: int
    elapsed_ms: float

    def to_dict(self) -> dict:
        return {
            "settled": self.settled,
            "snapshots": self.snapshots,
            "elapsed_ms": self.elapsed_ms,
        }


class SettleTimeoutError(Exception):
    """The screen never went quiet inside the bounded wait, so
    the caller refused to act. ``report`` says how many reads
    ran before the timeout."""

    def __init__(self, message: str, *, report: SettleReport) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class ElementIdentity:
    """What makes one tree element the same element across two
    snapshots: its role, its name, where it sits in the tree,
    its description, and what it can do.

    Deliberately NOT bounds or states. A window can move without
    the element becoming a different element, and a click is
    supposed to change states, so neither belongs in an identity
    check. For a coordinate target the box IS the identity, see
    :func:`visual_fingerprint`.
    """

    role: str
    name: str
    path: tuple[int, ...]
    description: str
    actions: tuple[str, ...]


def fingerprint(node: AccessibleNode) -> ElementIdentity:
    """The identity of one tree element (see :class:`ElementIdentity`)."""
    return ElementIdentity(
        role=node.role,
        name=node.name,
        path=node.path,
        description=node.description,
        actions=node.actions,
    )


def visual_fingerprint(element: VisualElement) -> tuple[str, int, int, int, int]:
    """The identity of one OCR-grounded target: its label plus
    its box. A target that moved is a different target for the
    purpose of clicking, even when the text still matches."""
    return (element.label, element.x, element.y, element.width, element.height)


def _node_signature(node: AccessibleNode) -> tuple:
    """One node's part of a settle signature: everything that
    counts as the screen changing (identity, bounds, states,
    and whether its subtree is collapsed)."""
    return (
        node.path,
        node.role,
        node.name,
        node.description,
        node.x,
        node.y,
        node.width,
        node.height,
        node.states,
        node.actions,
        node.child_count,
        node.collapsed,
        tuple(_node_signature(child) for child in node.children),
    )


def tree_signature(snapshot: Snapshot) -> tuple:
    """A settle signature for one tree snapshot: the whole tree
    minus the refs (display handles) and the timestamp, so two
    snapshots of an unchanged screen compare equal."""
    return tuple(_node_signature(node) for node in snapshot.nodes)


def words_signature(words: list[OcrWord]) -> tuple:
    """A settle signature for one OCR read: each word's text and
    box. Confidence is left out: OCR scoring can wobble without
    the screen changing."""
    return tuple((word.text, word.x, word.y, word.width, word.height) for word in words)


def wait_for_settle(
    read: Callable[[], Any],
    *,
    signature: Callable[[Any], Any] = lambda value: value,
    timeout_s: float = DEFAULT_SETTLE_TIMEOUT_S,
    stable_rounds: int = DEFAULT_STABLE_ROUNDS,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
) -> tuple[Any, SettleReport]:
    """Read until the picture stops changing.

    Calls ``read()`` repeatedly and compares each result's
    ``signature`` with the previous one. Returns ``(value,
    report)`` once ``stable_rounds`` reads in a row agree (the
    last read is the returned value). Raises
    :class:`SettleTimeoutError` when the timeout runs out first;
    the exception carries the report of how many reads ran.
    ``stable_rounds`` of 1 returns the first read at once.
    """
    if stable_rounds < 1:
        raise ValueError("stable_rounds must be at least 1")
    started = time.monotonic()
    previous = object()
    run = 0
    reads = 0
    while True:
        value = read()
        reads += 1
        current = signature(value)
        if current == previous:
            run += 1
        else:
            run = 1
            previous = current
        if run >= stable_rounds:
            return value, SettleReport(
                settled=True,
                snapshots=reads,
                elapsed_ms=round((time.monotonic() - started) * 1000, 1),
            )
        if time.monotonic() - started >= timeout_s:
            raise SettleTimeoutError(
                f"screen did not settle within {timeout_s}s "
                f"({reads} reads, never {stable_rounds} identical in a row)",
                report=SettleReport(
                    settled=False,
                    snapshots=reads,
                    elapsed_ms=round((time.monotonic() - started) * 1000, 1),
                ),
            )
        time.sleep(poll_interval_s)
