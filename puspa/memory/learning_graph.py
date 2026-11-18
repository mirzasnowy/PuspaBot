"""Learning graph (G24): skills and memory chunks as first-class nodes.

The graph connects what the agent can do (skill nodes) with
what it remembers (memory-chunk nodes), so a dashboard — or a
future planner — can ask "which skills does this memory touch"
and "which memories does this skill build on". Edges come from
two sources:

- Declarations: a skill declares the tools / domains it covers
  (its ``manifest.json`` ``tools`` / ``domains``, or its
  ``SKILL.md`` frontmatter), and a memory chunk that a skill
  explicitly claims is linked with a ``declared`` edge.
- Lexical overlap: a skill's declared tokens are matched
  against a chunk's concepts (``puspa.memory.extraction``);
  overlap at or above the threshold adds a ``lexical_overlap``
  edge weighted by the overlap.

The graph is in-memory by design and serialises to plain JSON
(``to_dict`` / ``from_dict`` / ``save_json`` / ``load_json``).
JSON was chosen over a Postgres table on purpose: the graph is
a derived view that is rebuilt from skills + memory on demand,
it must be inspectable without a database, and the JSON form is
what the dashboard consumes. A Postgres mirror with its own
schema functions is a follow-up, not a requirement for the
graph itself to be real.

Pattern rewritten from hermes-agent ``agent/learning_graph.py``
(MIT, adapted, not copied).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .extraction import extract_concepts

__all__ = [
    "DEFAULT_LEXICAL_THRESHOLD",
    "EDGE_DECLARED",
    "EDGE_LEXICAL",
    "EDGE_RELATED",
    "NODE_KIND_MEMORY",
    "NODE_KIND_SKILL",
    "GraphEdge",
    "GraphNode",
    "LearningGraph",
    "declarations_from_skill_dir",
]

#: Node kinds.
NODE_KIND_SKILL = "skill"
NODE_KIND_MEMORY = "memory"

#: Edge kinds.
EDGE_DECLARED = "declared"
EDGE_LEXICAL = "lexical_overlap"
EDGE_RELATED = "related"

#: Default lexical-overlap threshold for auto-linking.
DEFAULT_LEXICAL_THRESHOLD = 0.3


def _stem(token):
    """Light suffix stemmer for lexical matching.

    Strips one English suffix (``ing`` / ``ed`` / ``es`` / ``s``)
    and a trailing ``e``, so "prices" matches "price" and
    "compared" matches "compare". This is a cheap normaliser,
    not a real stemmer: it only exists so plural / tense
    variants of the same word link a skill to a chunk. Both
    sides of every comparison are stemmed the same way.
    """
    word = str(token).lower()
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


def _stems(tokens):
    return {_stem(token) for token in tokens if token}


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _frontmatter_list(text, key):
    """Parse a small YAML-ish list for ``key`` from frontmatter text.

    Supports both flow style (``tools: [a, b]``) and block style
    (``tools:`` followed by ``- a`` lines). This is a minimal
    reader for the ``tools`` / ``domains`` keys only — not a
    general YAML parser.
    """
    lines = text.splitlines()
    values = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(rf"^{re.escape(key)}\s*:\s*(.*)$", line.strip())
        if not match:
            i += 1
            continue
        rest = match.group(1).strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1]
            values.extend(item.strip().strip("\"'") for item in inner.split(",") if item.strip())
            return values
        if rest:
            values.append(rest.strip("\"'"))
            return values
        i += 1
        while i < len(lines):
            item = re.match(r"^\s*-\s+(.+?)\s*$", lines[i])
            if not item:
                break
            values.append(item.group(1).strip().strip("\"'"))
            i += 1
        return values
    return values


def declarations_from_skill_dir(skill_dir):
    """Declared tools + domains of a skill directory.

    Reads ``manifest.json`` (``tools`` / ``domains``) when
    present, otherwise the ``SKILL.md`` frontmatter keys of the
    same names. Returns a sorted list of unique lowercased
    tokens (multi-word entries are kept whole and also split,
    so "bill payment" matches both the phrase and the words).
    """
    skill_dir = Path(skill_dir)
    declared = set()
    manifest = skill_dir / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
        if isinstance(data, dict):
            for key in ("tools", "domains"):
                for item in data.get(key) or []:
                    declared.add(str(item))
    else:
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError:
                text = ""
            match = _FRONTMATTER_RE.match(text)
            if match:
                frontmatter = match.group(1)
                for key in ("tools", "domains"):
                    declared.update(_frontmatter_list(frontmatter, key))
    out = set()
    for item in declared:
        lowered = " ".join(str(item).lower().split())
        if not lowered:
            continue
        out.add(lowered)
        out.update(word for word in lowered.split() if len(word) >= 3)
    return sorted(out)


@dataclass
class GraphNode:
    """One graph node: a skill or a memory chunk."""

    id: str
    kind: str
    label: str
    content: str = ""
    declares: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "content": self.content,
            "declares": list(self.declares),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"],
            kind=data["kind"],
            label=data.get("label", data["id"]),
            content=data.get("content", ""),
            declares=list(data.get("declares") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class GraphEdge:
    """One directed edge between two nodes."""

    source: str
    target: str
    kind: str
    weight: float = 1.0

    def to_dict(self):
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            source=data["source"],
            target=data["target"],
            kind=data["kind"],
            weight=float(data.get("weight", 1.0)),
        )


def _skill_id(name):
    return f"skill:{name}"


def _memory_id(chunk_id):
    return f"memory:{chunk_id}"


class LearningGraph:
    """In-memory graph of skills and memory chunks.

    Nodes are keyed by id (``skill:<name>`` / ``memory:<id>``).
    Adding the same node twice keeps the first copy's content
    and merges declarations / metadata; adding the same edge
    twice keeps the higher weight. Nothing here touches a
    database or the network.
    """

    def __init__(self):
        self.nodes = {}
        self.edges = []

    # -- nodes ---------------------------------------------------------

    def add_skill(self, name, *, declares=None, path=None, metadata=None):
        """Add (or merge into) a skill node; return it."""
        node_id = _skill_id(name)
        declares = sorted({" ".join(str(d).lower().split()) for d in (declares or [])} - {""})
        existing = self.nodes.get(node_id)
        if existing is not None:
            merged = sorted(set(existing.declares) | set(declares))
            existing.declares = merged
            if metadata:
                existing.metadata.update(metadata)
            if path:
                existing.metadata["path"] = str(path)
            return existing
        node = GraphNode(
            id=node_id,
            kind=NODE_KIND_SKILL,
            label=name,
            declares=declares,
            metadata=dict(metadata or {}),
        )
        if path:
            node.metadata["path"] = str(path)
        self.nodes[node_id] = node
        return node

    def add_skill_from_dir(self, skill_dir):
        """Add a skill node from a directory (declarations read live)."""
        skill_dir = Path(skill_dir)
        return self.add_skill(
            skill_dir.name,
            declares=declarations_from_skill_dir(skill_dir),
            path=skill_dir,
        )

    def add_memory_chunk(self, chunk_id, content, *, metadata=None):
        """Add (or replace the content of) a memory-chunk node."""
        node_id = _memory_id(chunk_id)
        existing = self.nodes.get(node_id)
        if existing is not None:
            existing.content = content
            if metadata:
                existing.metadata.update(metadata)
            return existing
        node = GraphNode(
            id=node_id,
            kind=NODE_KIND_MEMORY,
            label=str(chunk_id),
            content=content,
            metadata=dict(metadata or {}),
        )
        self.nodes[node_id] = node
        return node

    # -- edges ---------------------------------------------------------

    def add_link(self, source_id, target_id, kind, weight=1.0):
        """Add an edge; same (source, target, kind) keeps max weight."""
        if source_id not in self.nodes or target_id not in self.nodes:
            raise KeyError(f"unknown node in edge: {source_id!r} -> {target_id!r}")
        if kind not in (EDGE_DECLARED, EDGE_LEXICAL, EDGE_RELATED):
            raise ValueError(f"unknown edge kind: {kind!r}")
        weight = float(weight)
        for edge in self.edges:
            if edge.source == source_id and edge.target == target_id and edge.kind == kind:
                edge.weight = max(edge.weight, weight)
                return edge
        edge = GraphEdge(source_id, target_id, kind, weight)
        self.edges.append(edge)
        return edge

    def link_declared(self, skill_name, chunk_id):
        """Explicit declaration link: this skill claims this chunk."""
        return self.add_link(_skill_id(skill_name), _memory_id(chunk_id), EDGE_DECLARED, 1.0)

    def link_lexical_overlap(self, *, threshold=DEFAULT_LEXICAL_THRESHOLD):
        """Auto-link skills to chunks by lexical overlap.

        For each skill / memory pair, overlap is the share of the
        skill's declared tokens that appear among the chunk's
        concepts (both sides stemmed, so "prices" matches
        "price"). Pairs at or above ``threshold`` get a
        ``lexical_overlap`` edge weighted by the overlap. Returns
        the list of edges created (or strengthened) by this call.
        """
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        created = []
        skills = [n for n in self.nodes.values() if n.kind == NODE_KIND_SKILL]
        chunks = [n for n in self.nodes.values() if n.kind == NODE_KIND_MEMORY]
        for skill in skills:
            declared = _stems(skill.declares)
            if not declared:
                continue
            for chunk in chunks:
                concepts = _stems(extract_concepts(chunk.content))
                overlap = len(declared & concepts) / len(declared)
                if overlap >= threshold:
                    created.append(self.add_link(skill.id, chunk.id, EDGE_LEXICAL, overlap))
        return created

    # -- queries -------------------------------------------------------

    def neighbors(self, node_id, *, kind=None):
        """(node, edge) pairs adjacent to ``node_id``, best weight first."""
        if node_id not in self.nodes:
            raise KeyError(f"unknown node: {node_id!r}")
        out = []
        for edge in self.edges:
            if kind is not None and edge.kind != kind:
                continue
            if edge.source == node_id:
                out.append((self.nodes[edge.target], edge))
            elif edge.target == node_id:
                out.append((self.nodes[edge.source], edge))
        out.sort(key=lambda pair: (-pair[1].weight, pair[0].id))
        return out

    def related_skills(self, chunk_id, *, min_weight=0.0):
        """Skill nodes linked to a memory chunk, best weight first."""
        node_id = _memory_id(chunk_id)
        return [
            node
            for node, edge in self.neighbors(node_id)
            if node.kind == NODE_KIND_SKILL and edge.weight >= min_weight
        ]

    def related_chunks(self, skill_name, *, min_weight=0.0):
        """Memory-chunk nodes linked to a skill, best weight first."""
        node_id = _skill_id(skill_name)
        return [
            node
            for node, edge in self.neighbors(node_id)
            if node.kind == NODE_KIND_MEMORY and edge.weight >= min_weight
        ]

    def skills(self):
        """All skill nodes, sorted by name."""
        return sorted(
            (n for n in self.nodes.values() if n.kind == NODE_KIND_SKILL),
            key=lambda n: n.label,
        )

    def chunks(self):
        """All memory-chunk nodes, sorted by id."""
        return sorted(
            (n for n in self.nodes.values() if n.kind == NODE_KIND_MEMORY),
            key=lambda n: n.id,
        )

    # -- serialisation -------------------------------------------------

    def to_dict(self):
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    @classmethod
    def from_dict(cls, data):
        graph = cls()
        for raw in data.get("nodes") or []:
            node = GraphNode.from_dict(raw)
            graph.nodes[node.id] = node
        for raw in data.get("edges") or []:
            graph.edges.append(GraphEdge.from_dict(raw))
        return graph

    def save_json(self, path):
        """Write the graph as JSON; return the path."""
        path = Path(path)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    @classmethod
    def load_json(cls, path):
        """Read a graph back from ``save_json`` output."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)
