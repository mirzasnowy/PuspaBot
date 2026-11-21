"""Accessibility tree model for Puspa desktop hands (V2-31, core).

A snapshot is a plain JSON tree of :class:`AccessibleNode` values:
every element carries a stable-within-the-snapshot ``ref`` (``n1``,
``n2``, ... in walk order) plus a ``path`` of child indices from the
desktop root, so a backend can find the same live element again
after the snapshot was taken. Refs are only stable inside one
snapshot; keeping them valid across snapshots is V2-34 work.

A skeleton snapshot keeps the top levels and collapses the rest:
each collapsed node still reports its ``child_count`` but carries
no children, so the agent can drill into one subtree at a time
instead of swallowing a whole Electron tree at once. Use
:meth:`DesktopHands.drill` for that; see :mod:`puspa.computer.hands`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

__all__ = [
    "AccessibleNode",
    "Snapshot",
    "assign_refs",
    "find_ref",
    "node_to_dict",
    "skeletonize",
    "snapshot_to_dict",
]

#: Depth of a skeleton snapshot: the root plus this many levels of
#: children stay expanded; anything deeper is collapsed.
SKELETON_DEPTH = 2


@dataclass(frozen=True)
class AccessibleNode:
    """One element of the accessibility tree.

    ``states`` uses plain words (``enabled``, ``focused``,
    ``showing``, ``visible``, ``selected``, ``checked``,
    ``expanded``). ``actions`` lists the action names the element
    offers (``click`` is the one the core knows how to run).
    ``collapsed`` means the node was left out of a skeleton
    snapshot even though ``child_count`` says it has children.
    """

    ref: str
    path: tuple[int, ...]
    role: str
    name: str = ""
    description: str = ""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    states: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    children: tuple[AccessibleNode, ...] = ()
    child_count: int = 0
    collapsed: bool = False

    def summary(self) -> str:
        """One short line for logs and click results."""
        label = self.name or self.role
        states = ",".join(self.states) if self.states else "-"
        return f"{self.ref} {self.role} {label!r} [{states}]"

    def find(self, ref: str) -> AccessibleNode | None:
        """Return the node with this ref in this subtree, or None."""
        if self.ref == ref:
            return self
        for child in self.children:
            found = child.find(ref)
            if found is not None:
                return found
        return None


@dataclass(frozen=True)
class Snapshot:
    """One read of the screen: the top-level windows and apps."""

    nodes: tuple[AccessibleNode, ...] = ()
    backend: str = ""
    taken_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def total_nodes(self) -> int:
        """Nodes present in this snapshot (collapsed subtrees count
        as one node each, plus their reported child_count)."""

        def count(node: AccessibleNode) -> int:
            total = 1
            for child in node.children:
                total += count(child)
            if node.collapsed:
                total += node.child_count
            return total

        return sum(count(node) for node in self.nodes)

    def find(self, ref: str) -> AccessibleNode | None:
        """Return the node with this ref anywhere in the snapshot."""
        for node in self.nodes:
            found = node.find(ref)
            if found is not None:
                return found
        return None


def _walk(node: AccessibleNode) -> list[AccessibleNode]:
    out = [node]
    for child in node.children:
        out.extend(_walk(child))
    return out


def assign_refs(nodes: list[AccessibleNode]) -> list[AccessibleNode]:
    """Rebuild ``nodes`` with fresh refs (``n1``, ``n2``, ...) in
    walk order, keeping every other field. Paths are kept as given
    by the backend; refs are display handles only."""
    counter = [0]

    def rebuild(node: AccessibleNode) -> AccessibleNode:
        counter[0] += 1
        return AccessibleNode(
            ref=f"n{counter[0]}",
            path=node.path,
            role=node.role,
            name=node.name,
            description=node.description,
            x=node.x,
            y=node.y,
            width=node.width,
            height=node.height,
            states=node.states,
            actions=node.actions,
            children=tuple(rebuild(child) for child in node.children),
            child_count=node.child_count,
            collapsed=node.collapsed,
        )

    return [rebuild(node) for node in nodes]


def find_ref(
    nodes: list[AccessibleNode] | tuple[AccessibleNode, ...], ref: str
) -> AccessibleNode | None:
    """Return the node with this ref, or None."""
    for node in nodes:
        found = node.find(ref)
        if found is not None:
            return found
    return None


def node_to_dict(node: AccessibleNode) -> dict:
    """JSON shape of one node (the shape the CLI prints)."""
    data: dict = {
        "ref": node.ref,
        "role": node.role,
        "name": node.name,
        "description": node.description,
        "bounds": {"x": node.x, "y": node.y, "width": node.width, "height": node.height},
        "states": list(node.states),
        "actions": list(node.actions),
        "child_count": node.child_count,
    }
    if node.collapsed:
        data["collapsed"] = True
    else:
        data["children"] = [node_to_dict(child) for child in node.children]
    return data


def snapshot_to_dict(snapshot: Snapshot) -> dict:
    """JSON shape of a whole snapshot."""
    return {
        "backend": snapshot.backend,
        "taken_at": snapshot.taken_at.isoformat(),
        "total_nodes": snapshot.total_nodes,
        "nodes": [node_to_dict(node) for node in snapshot.nodes],
    }


def _collapse(node: AccessibleNode, depth: int, max_depth: int) -> AccessibleNode:
    if depth >= max_depth or not node.children:
        return AccessibleNode(
            ref=node.ref,
            path=node.path,
            role=node.role,
            name=node.name,
            description=node.description,
            x=node.x,
            y=node.y,
            width=node.width,
            height=node.height,
            states=node.states,
            actions=node.actions,
            children=(),
            child_count=node.child_count or len(node.children),
            collapsed=bool(node.children),
        )
    return AccessibleNode(
        ref=node.ref,
        path=node.path,
        role=node.role,
        name=node.name,
        description=node.description,
        x=node.x,
        y=node.y,
        width=node.width,
        height=node.height,
        states=node.states,
        actions=node.actions,
        children=tuple(_collapse(child, depth + 1, max_depth) for child in node.children),
        child_count=node.child_count or len(node.children),
        collapsed=False,
    )


def skeletonize(snapshot: Snapshot, max_depth: int = SKELETON_DEPTH) -> Snapshot:
    """Return a copy of ``snapshot`` with subtrees deeper than
    ``max_depth`` collapsed. The root level is depth 0."""
    return Snapshot(
        nodes=tuple(_collapse(node, 0, max_depth) for node in snapshot.nodes),
        backend=snapshot.backend,
        taken_at=snapshot.taken_at,
    )
