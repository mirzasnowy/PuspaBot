"""Schema-less knowledge graph (HippoRAG / LightRAG pattern, rewritten).

A knowledge graph built at index time with no fixed schema: entities
and relations come from the documents themselves, exactly as HippoRAG
builds an open KG with an LLM and LightRAG builds an entity/relation
graph at index time. Nothing here is copied from either repo; the
mechanism (extract triples while indexing, merge by normalized name,
link passages to the entities they mention) is reimplemented in
Puspa's own code.

Extraction is done through a replaceable ``EntityExtractor``:

- ``DeterministicExtractor`` (default): rule-based, no model, no
  network, no cost. It finds model codes, Capitalized phrases and
  single Capitalized words, then reads subject-verb-object triples
  out of each sentence from a small verb list. It is honest about
  its limits: it only sees the patterns it knows, so recall is
  lower than LLM extraction. Tests use this extractor.
- ``JsonTripleExtractor``: takes any callable returning a JSON
  string of entities and triples. That is the seam where a real
  LLM extractor plugs in (a completion function from the provider
  layer, when a key exists). No LLM call is made by this module
  and no key is required; without a completion function the
  deterministic extractor is used.

Incremental update: ``add_document`` only processes the new
document. An entity that already exists keeps its node and gains
the new source id; only new nodes and edges are added. There is
no full rebuild. ``remove_document`` drops the document's
passages and any node or edge left with no source.
"""

from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field

from puspa.memory.episodic import extract_entities

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_CODE_PHRASE_RE = re.compile(
    r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+"
    r"(?:[A-Z][0-9][A-Za-z0-9]*|[0-9]{2,})\b"
)
_CAPITAL_WORD_RE = re.compile(r"\b[A-Z][a-z]{3,}\b")
_KIND_RE = re.compile(
    r"\b((?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+"
    r"(?:[A-Z][0-9][A-Za-z0-9]*|[0-9]{2,})"
    r"|[A-Z][A-Za-z0-9]+(?:\s+[A-Z][a-z]+)*)"
    r")\s+is\s+(?:a|an|the)\s+([a-z]+)\b"
)

#: Words a single Capitalized word may not be (sentence starters,
#: question words). The deterministic extractor is a stand-in, not
#: a named-entity recognizer.
_CAPITAL_STOPWORDS = frozenset(
    [
        "The",
        "This",
        "That",
        "These",
        "Those",
        "What",
        "Which",
        "Who",
        "When",
        "Where",
        "Why",
        "How",
        "Never",
        "Always",
        "Buyer",
        "Seller",
        "Also",
        "Then",
        "There",
        "Here",
    ]
)

#: Entity kinds the deterministic extractor recognises from
#: "X is a <kind>" sentences.
_KNOWN_KINDS = frozenset(
    [
        "phone",
        "laptop",
        "tablet",
        "smartwatch",
        "tv",
        "camera",
        "product",
        "seller",
        "store",
        "shop",
        "promo",
        "promotion",
        "report",
        "fraud",
        "price",
    ]
)

#: Verb (and inflected form) to canonical relation, used to read
#: triples out of a sentence.
_VERBS = {
    "sells": "sells",
    "sell": "sells",
    "offers": "offers",
    "offer": "offers",
    "lists": "lists",
    "list": "lists",
    "has": "has",
    "have": "has",
    "runs": "runs",
    "run": "runs",
    "owns": "owns",
    "own": "owns",
    "reports": "reports",
    "report": "reports",
    "reported": "reports",
    "warns": "warns",
    "warn": "warns",
    "costs": "costs",
    "cost": "costs",
    "ships": "ships",
    "ship": "ships",
    "flags": "flags",
    "flag": "flags",
    "is": "is_a",
    "are": "is_a",
}


def normalize_entity_name(name: str) -> str:
    """One canonical form for an entity name: lowercased, single spaces."""
    return " ".join(str(name).lower().split())


def hop_distances(adjacency: dict, seeds) -> dict:
    """Shortest hop distance from any seed, by breadth-first walk."""
    distances = {}
    frontier = [(seed, 0) for seed in seeds]
    seen = set()
    while frontier:
        nxt = []
        for node, hops in frontier:
            if node in seen:
                continue
            seen.add(node)
            distances[node] = hops
            for neighbor in adjacency.get(node, set()):
                if neighbor not in seen:
                    nxt.append((neighbor, hops + 1))
        frontier = nxt
    return distances


@dataclass
class Entity:
    """One graph node: a normalized name plus what mentions it."""

    name: str
    kind: str | None = None
    description: str = ""
    source_ids: set = field(default_factory=set)


@dataclass
class Triple:
    """One directed edge: subject, relation, object."""

    subject: str
    relation: str
    object: str
    source_ids: set = field(default_factory=set)


@dataclass
class Extraction:
    """What one extractor run found in one passage."""

    entities: list = field(default_factory=list)
    triples: list = field(default_factory=list)


class EntityExtractor:
    """Interface for index-time extraction.

    ``extract(text, source_id)`` returns an ``Extraction``. Any
    object with that method works: the deterministic extractor,
    the JSON extractor, or a future LLM extractor.
    """

    def extract(self, text: str, source_id: str) -> Extraction:
        raise NotImplementedError


def _sentence_entities(sentence: str) -> list:
    """Entities in one sentence as (name, start, end) spans.

    Longer spans win over overlapping shorter ones, so
    "Samsung Galaxy S24" is one entity, not three.
    """
    spans = []
    for pattern in (_CODE_PHRASE_RE,):
        for match in pattern.finditer(sentence):
            spans.append((match.start(), match.end(), match.group(0)))
    for name in extract_entities(sentence):
        start = sentence.lower().find(name.lower())
        if start >= 0:
            spans.append((start, start + len(name), name))
    for match in _CAPITAL_WORD_RE.finditer(sentence):
        word = match.group(0)
        if word in _CAPITAL_STOPWORDS:
            continue
        spans.append((match.start(), match.end(), word))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept: list[tuple[int, int, str]] = []
    for start, end, name in spans:
        if any(start < k_end and end > k_start for k_start, k_end, _ in kept):
            continue
        kept.append((start, end, name))
    kept.sort(key=lambda s: s[0])
    return [(normalize_entity_name(name), start, end) for start, end, name in kept]


def _sentence_kinds(sentence: str) -> dict:
    """Entity name to kind, from "X is a <kind>" sentences."""
    kinds = {}
    for match in _KIND_RE.finditer(sentence):
        kind = match.group(2).lower()
        if kind in _KNOWN_KINDS:
            kinds[normalize_entity_name(match.group(1))] = kind
    return kinds


class DeterministicExtractor(EntityExtractor):
    """Rule-based extractor: no model, no network, deterministic.

    Finds entities with regexes (model codes, Capitalized phrases,
    single Capitalized words) and reads triples from adjacent
    entity pairs in a sentence: the first known verb between them
    becomes the relation, otherwise the relation is
    "mentioned_with". "X is a <kind>" also records the kind.
    """

    def extract(self, text: str, source_id: str) -> Extraction:
        if not isinstance(text, str) or text == "":
            raise ValueError("text must be a non-empty string")
        entities: dict[str, Entity] = {}
        triples: list[Triple] = []
        for sentence in _SENTENCE_RE.split(text):
            cleaned = sentence.strip()
            if not cleaned:
                continue
            kinds = _sentence_kinds(cleaned)
            spans = _sentence_entities(cleaned)
            for name, _, _ in spans:
                entity = entities.setdefault(name, Entity(name=name, source_ids={source_id}))
                entity.source_ids.add(source_id)
                if name in kinds:
                    entity.kind = kinds[name]
            for (left, _l_start, l_end), (right, r_start, _) in itertools.pairwise(spans):
                between = cleaned[l_end:r_start].lower()
                words = re.findall(r"[a-z]+", between)
                relation = None
                for word in words:
                    if word in _VERBS:
                        relation = _VERBS[word]
                        break
                if relation is None:
                    relation = "mentioned_with"
                triples.append(
                    Triple(subject=left, relation=relation, object=right, source_ids={source_id})
                )
        return Extraction(entities=list(entities.values()), triples=triples)


class JsonTripleExtractor(EntityExtractor):
    """Extractor driven by a callable returning JSON triples.

    ``complete(prompt)`` is any function returning a JSON string
    shaped like ``{"entities": [{"name": ..., "kind": ...}],
    "triples": [{"subject": ..., "relation": ..., "object": ...}]}``.
    This is the seam for a real LLM extractor: pass a completion
    function from the provider layer when a key exists. This class
    itself makes no network call and holds no key.
    """

    def __init__(self, complete):
        if not callable(complete):
            raise ValueError("complete must be a callable")
        self.complete = complete

    def extract(self, text: str, source_id: str) -> Extraction:
        if not isinstance(text, str) or text == "":
            raise ValueError("text must be a non-empty string")
        prompt = (
            "Extract entities and triples from the text below. "
            'Reply with JSON only: {"entities": [{"name": str, '
            '"kind": str}], "triples": [{"subject": str, '
            '"relation": str, "object": str}]}.\n\n' + text
        )
        try:
            parsed = json.loads(self.complete(prompt))
        except (ValueError, TypeError) as exc:
            raise ValueError("extractor reply is not valid JSON triples") from exc
        if not isinstance(parsed, dict):
            raise ValueError("extractor reply must be a JSON object")
        entities = []
        for raw in parsed.get("entities") or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            entities.append(
                Entity(
                    name=normalize_entity_name(raw["name"]),
                    kind=raw.get("kind") or None,
                    source_ids={source_id},
                )
            )
        triples = []
        for raw in parsed.get("triples") or []:
            if not isinstance(raw, dict):
                continue
            if not (raw.get("subject") and raw.get("relation") and raw.get("object")):
                continue
            triples.append(
                Triple(
                    subject=normalize_entity_name(raw["subject"]),
                    relation=str(raw["relation"]).lower(),
                    object=normalize_entity_name(raw["object"]),
                    source_ids={source_id},
                )
            )
        return Extraction(entities=entities, triples=triples)


def split_passages(text: str) -> list[str]:
    """Split a document into passages: one sentence each."""
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


class KnowledgeGraph:
    """In-memory schema-less knowledge graph.

    Nodes and edges are plain dicts; passages keep their text and
    the entities they mention, so a ranked node always leads back
    to a passage with a proof id. The graph is the shared index
    for the HippoRAG retriever and the LightRAG query modes.
    """

    def __init__(self, extractor: EntityExtractor | None = None):
        self.extractor = extractor or DeterministicExtractor()
        self.nodes: dict[str, Entity] = {}
        self.edges: dict[tuple, Triple] = {}
        self.passages: dict[str, str] = {}
        self.passage_entities: dict[str, set] = {}
        self.doc_passages: dict[str, list] = {}

    def add_document(
        self, doc_id: str, text: str, extractor: EntityExtractor | None = None
    ) -> dict:
        """Index one document. Incremental: only this document is
        processed; existing nodes merge by normalized name and gain
        the new source id. Returns counts of what was added.
        """
        if not isinstance(doc_id, str) or doc_id == "":
            raise ValueError("doc_id must be a non-empty string")
        if not isinstance(text, str) or text == "":
            raise ValueError("text must be a non-empty string")
        if doc_id in self.doc_passages:
            raise ValueError(f"document already indexed: {doc_id!r}")
        extractor = extractor or self.extractor
        new_nodes, new_edges = 0, 0
        passage_ids = []
        for index, passage in enumerate(split_passages(text)):
            passage_id = f"{doc_id}#p{index}"
            extraction = extractor.extract(passage, passage_id)
            self.passages[passage_id] = passage
            passage_ids.append(passage_id)
            seen_here = set()
            for entity in extraction.entities:
                seen_here.add(entity.name)
                if entity.name not in self.nodes:
                    self.nodes[entity.name] = Entity(
                        name=entity.name, kind=entity.kind, source_ids=set(entity.source_ids)
                    )
                    new_nodes += 1
                else:
                    node = self.nodes[entity.name]
                    node.source_ids |= entity.source_ids
                    if node.kind is None and entity.kind:
                        node.kind = entity.kind
            self.passage_entities[passage_id] = seen_here
            for triple in extraction.triples:
                for name in (triple.subject, triple.object):
                    if name not in self.nodes:
                        self.nodes[name] = Entity(name=name, source_ids=set(triple.source_ids))
                        new_nodes += 1
                    else:
                        self.nodes[name].source_ids |= triple.source_ids
                key = (triple.subject, triple.relation, triple.object)
                if key not in self.edges:
                    self.edges[key] = Triple(
                        subject=triple.subject,
                        relation=triple.relation,
                        object=triple.object,
                        source_ids=set(triple.source_ids),
                    )
                    new_edges += 1
                else:
                    self.edges[key].source_ids |= triple.source_ids
        self.doc_passages[doc_id] = passage_ids
        return {
            "doc_id": doc_id,
            "passages": len(passage_ids),
            "new_nodes": new_nodes,
            "new_edges": new_edges,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
        }

    def remove_document(self, doc_id: str) -> dict:
        """Remove one document and anything left with no source.

        Passages go away, edges and nodes that lose their last
        source id go away too. The rest of the graph is untouched.
        """
        if doc_id not in self.doc_passages:
            raise KeyError(f"unknown document: {doc_id!r}")
        removed_passages = 0
        for passage_id in self.doc_passages.pop(doc_id):
            self.passages.pop(passage_id, None)
            self.passage_entities.pop(passage_id, None)
            removed_passages += 1
        removed_edges = 0
        for key in list(self.edges):
            triple = self.edges[key]
            triple.source_ids = {s for s in triple.source_ids if not s.startswith(doc_id + "#")}
            if not triple.source_ids:
                del self.edges[key]
                removed_edges += 1
        removed_nodes = 0
        for name in list(self.nodes):
            node = self.nodes[name]
            node.source_ids = {s for s in node.source_ids if not s.startswith(doc_id + "#")}
            if not node.source_ids:
                del self.nodes[name]
                removed_nodes += 1
        return {
            "doc_id": doc_id,
            "removed_passages": removed_passages,
            "removed_edges": removed_edges,
            "removed_nodes": removed_nodes,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
        }

    def adjacency(self) -> dict:
        """Undirected adjacency: node name to neighbor names."""
        adj: dict[str, set] = {name: set() for name in self.nodes}
        for subject, _, obj in self.edges:
            if subject in adj and obj in adj:
                adj[subject].add(obj)
                adj[obj].add(subject)
        return adj

    def neighbors(self, name: str, relation: str | None = None) -> list:
        """Neighbor names of a node, optionally on one relation."""
        name = normalize_entity_name(name)
        out = set()
        for subject, rel, obj in self.edges:
            if relation is not None and rel != relation:
                continue
            if subject == name:
                out.add(obj)
            elif obj == name:
                out.add(subject)
        return sorted(out)

    def relations(self, name: str) -> list:
        """Triples touching a node, as (subject, relation, object)."""
        name = normalize_entity_name(name)
        return sorted(
            (t.subject, t.relation, t.object)
            for t in self.edges.values()
            if name in (t.subject, t.object)
        )

    def passages_for_nodes(self, names) -> list:
        """Passage ids mentioning any of the given nodes."""
        wanted = {normalize_entity_name(n) for n in names}
        return sorted(pid for pid, entities in self.passage_entities.items() if entities & wanted)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    @property
    def passage_count(self) -> int:
        return len(self.passages)
