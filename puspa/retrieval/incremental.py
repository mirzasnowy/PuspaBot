"""Incremental graph update (LightRAG pattern, rewritten).

Wave 1 built one retrieval path over a schema-less knowledge
graph: entities and relations are extracted at index time and
merged by normalized name. What it did not have was a real
incremental update path. ``KnowledgeGraph.add_document`` already
indexes one document at a time, but it has no content dedup, no
way to replace one passage without touching the rest, no
exposed edge or node weight, and no report of what merged
versus what was new. This module adds exactly that, on top of
the existing graph, without rebuilding anything:

- ``add_passage`` / ``add_passages`` / ``add_document`` merge
  new text into the live graph. Only the new passages are
  extracted. A node or edge that already exists keeps its
  identity and gains the new passage as a source; only
  genuinely new nodes and edges are created.
- Content dedup: a passage whose normalized text is already in
  the graph is recognised by hash and skipped, so adding the
  same passage twice (same id, or a different id with the same
  text) cannot explode duplicates. Dedup is exact-text only:
  near-duplicates with different wording are not detected.
- Weights: a node weight is the number of distinct passages
  mentioning it, an edge weight the number of distinct passages
  supporting that triple. Both are the size of the ``source_ids``
  set the graph already keeps. Adding a passage that repeats an
  existing triple raises that edge weight by one instead of
  creating a second edge. Every merge reports the weight
  before and after, so the update is checkable.
- ``update_passage`` / ``update_document`` replace one passage
  or one document in place: the old extraction is detached
  (its passage id is dropped from the nodes and edges it
  supported, orphans with no source left are removed) and the
  new text is merged. The rest of the graph is untouched.
- ``remove_passage`` / ``remove_document`` detach one passage
  or document the same way.

Detach is exact because the extraction that was indexed for a
passage is stored at index time and reused for the detach, so
it does not depend on re-running the extractor. That holds for
the deterministic extractor and for any LLM extractor plugged
in through ``JsonTripleExtractor`` alike.

Honest limits, stated here and not hidden: extraction itself
is still the rule-based ``DeterministicExtractor`` unless the
caller passes a real extractor, so a new passage only yields
the entities and triples those rules can see. There is no
community detection or summary regeneration on update (the
LightRAG global mode here walks relations, not communities),
and no vector re-embedding: passages keep the hash embedder
scores the retriever computes at query time. A passage added
through ``KnowledgeGraph.add_document`` directly, bypassing
this updater, is visible to later updates (its nodes and edges
are in the graph) but has no stored extraction or hash here
until it is re-extracted on detach, which the updater does
lazily in that case.
"""

from __future__ import annotations

import hashlib

from .knowledge_graph import (
    Entity,
    KnowledgeGraph,
    Triple,
    normalize_entity_name,
    split_passages,
)


def normalize_text(text: str) -> str:
    """One canonical form for passage text: lowercased, single spaces."""
    return " ".join(str(text).lower().split())


def content_hash(text: str) -> str:
    """SHA-256 of the normalized text, the dedup key for a passage."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def _empty_stats() -> dict:
    return {
        "added_passages": 0,
        "duplicate_passages": 0,
        "already_indexed": False,
        "new_nodes": 0,
        "merged_nodes": 0,
        "new_edges": 0,
        "merged_edges": 0,
        "removed_passages": 0,
        "removed_nodes": 0,
        "removed_edges": 0,
        "weight_changes": [],
        "nodes": 0,
        "edges": 0,
        "passages": 0,
    }


def _finish(stats: dict, graph: KnowledgeGraph) -> dict:
    stats["nodes"] = graph.node_count
    stats["edges"] = graph.edge_count
    stats["passages"] = graph.passage_count
    return stats


class IncrementalUpdater:
    """Merge new passages into a live knowledge graph, in place.

    ``graph`` is the index; when omitted a fresh graph is built.
    The updater never rebuilds the graph: every method processes
    only the passages it is given, and old nodes, edges and
    passages keep their identity unless the method explicitly
    removes them.
    """

    def __init__(self, graph: KnowledgeGraph | None = None,
                 extractor=None):
        self.graph = graph if graph is not None else KnowledgeGraph(
            extractor=extractor)
        self.extractor = extractor or self.graph.extractor
        # passage id -> content hash, and hash -> first passage id
        # that indexed that text. Both are the dedup registry.
        self._passage_hash: dict[str, str] = {}
        self._hash_to_passage: dict[str, str] = {}
        # passage id -> the extraction indexed for it, kept so a
        # later detach removes exactly what was added.
        self._passage_extraction: dict = {}
        # passage id -> owning document id, or None for a bare
        # passage added outside a document.
        self._passage_doc: dict[str, str | None] = {}

    # -- weights and snapshots ------------------------------------

    def node_weight(self, name: str) -> int:
        """Passages mentioning a node: 0 when the node is absent."""
        node = self.graph.nodes.get(normalize_entity_name(name))
        return len(node.source_ids) if node is not None else 0

    def edge_weight(self, subject: str, relation: str,
                    object: str) -> int:
        """Passages supporting a triple: 0 when the edge is absent."""
        key = (normalize_entity_name(subject), relation,
               normalize_entity_name(object))
        edge = self.graph.edges.get(key)
        return len(edge.source_ids) if edge is not None else 0

    def snapshot(self) -> dict:
        """The graph identity right now: node names, edge keys,
        passage ids. A caller keeps this to prove later that an
        update merged instead of rebuilding: every old name, key
        and id must still be present afterwards.
        """
        return {
            "nodes": frozenset(self.graph.nodes),
            "edges": frozenset(self.graph.edges),
            "passages": frozenset(self.graph.passages),
        }

    def added_since(self, before: dict) -> dict:
        """What a snapshot does not contain any more: new node
        names, edge keys and passage ids, each sorted."""
        return {
            "nodes": sorted(
                set(self.graph.nodes) - set(before["nodes"])),
            "edges": sorted(
                set(self.graph.edges) - set(before["edges"])),
            "passages": sorted(
                set(self.graph.passages) - set(before["passages"])),
        }

    # -- adding ----------------------------------------------------

    def add_passage(self, passage_id: str, text: str,
                    extractor=None) -> dict:
        """Merge one passage into the graph.

        Same id with the same text is a no-op (already indexed).
        Same id with different text replaces the old passage in
        place. Text already indexed under another id is a
        duplicate and is skipped, so it adds no passage, node or
        edge. Anything else is extracted and merged.
        """
        if not isinstance(passage_id, str) or passage_id == "":
            raise ValueError("passage_id must be a non-empty string")
        if not isinstance(text, str) or text == "":
            raise ValueError("text must be a non-empty string")
        digest = content_hash(text)
        if passage_id in self.graph.passages:
            if self._passage_hash.get(passage_id) == digest or (
                    normalize_text(self.graph.passages[passage_id])
                    == normalize_text(text)):
                stats = _empty_stats()
                stats["duplicate_passages"] = 1
                stats["already_indexed"] = True
                return _finish(stats, self.graph)
            return self.update_passage(passage_id, text, extractor)
        if digest in self._hash_to_passage:
            stats = _empty_stats()
            stats["duplicate_passages"] = 1
            stats["duplicate_of"] = self._hash_to_passage[digest]
            return _finish(stats, self.graph)
        return _finish(
            self._merge(passage_id, text, digest, extractor), self.graph)

    def add_passages(self, passages, extractor=None) -> dict:
        """Merge several passages: a dict id to text, or an
        iterable of (passage_id, text) pairs. Counts add up
        across the batch; a duplicate inside the batch counts
        as a duplicate, not as an addition.
        """
        if isinstance(passages, dict):
            items = list(passages.items())
        else:
            items = list(passages)
        total = _empty_stats()
        for passage_id, text in items:
            stats = self.add_passage(passage_id, text, extractor)
            total["added_passages"] += stats["added_passages"]
            total["duplicate_passages"] += stats["duplicate_passages"]
            total["new_nodes"] += stats["new_nodes"]
            total["merged_nodes"] += stats["merged_nodes"]
            total["new_edges"] += stats["new_edges"]
            total["merged_edges"] += stats["merged_edges"]
            total["removed_passages"] += stats["removed_passages"]
            total["removed_nodes"] += stats["removed_nodes"]
            total["removed_edges"] += stats["removed_edges"]
            total["weight_changes"] += stats["weight_changes"]
            total["already_indexed"] = (
                total["already_indexed"] or stats["already_indexed"])
        return _finish(total, self.graph)

    def add_document(self, doc_id: str, text: str,
                     extractor=None) -> dict:
        """Merge one document, passage by passage.

        The document is split the same way the graph splits it,
        and each passage goes through ``add_passage``, so a
        document that repeats a passage already in the graph
        merges instead of duplicating it. Adding the same
        document id twice is a no-op when the text matches, and
        an in-place replace when it does not.
        """
        if not isinstance(doc_id, str) or doc_id == "":
            raise ValueError("doc_id must be a non-empty string")
        if not isinstance(text, str) or text == "":
            raise ValueError("text must be a non-empty string")
        if doc_id in self.graph.doc_passages:
            old_text = " ".join(
                self.graph.passages[pid]
                for pid in self.graph.doc_passages[doc_id])
            if normalize_text(old_text) == normalize_text(text):
                stats = _empty_stats()
                stats["duplicate_passages"] = len(
                    self.graph.doc_passages[doc_id])
                stats["already_indexed"] = True
                stats["doc_id"] = doc_id
                return _finish(stats, self.graph)
            return self.update_document(doc_id, text, extractor)
        total = _empty_stats()
        total["doc_id"] = doc_id
        owned = []
        for index, passage in enumerate(split_passages(text)):
            passage_id = f"{doc_id}#p{index}"
            stats = self.add_passage(passage_id, passage, extractor)
            if stats["added_passages"]:
                owned.append(passage_id)
                self._passage_doc[passage_id] = doc_id
            total["added_passages"] += stats["added_passages"]
            total["duplicate_passages"] += stats["duplicate_passages"]
            total["new_nodes"] += stats["new_nodes"]
            total["merged_nodes"] += stats["merged_nodes"]
            total["new_edges"] += stats["new_edges"]
            total["merged_edges"] += stats["merged_edges"]
            total["weight_changes"] += stats["weight_changes"]
        self.graph.doc_passages[doc_id] = owned
        return _finish(total, self.graph)

    def add_documents(self, documents, extractor=None) -> dict:
        """Merge several documents: a dict doc id to text, or an
        iterable of (doc_id, text) pairs."""
        if isinstance(documents, dict):
            items = list(documents.items())
        else:
            items = list(documents)
        total = _empty_stats()
        for doc_id, text in items:
            stats = self.add_document(doc_id, text, extractor)
            total["added_passages"] += stats["added_passages"]
            total["duplicate_passages"] += stats["duplicate_passages"]
            total["new_nodes"] += stats["new_nodes"]
            total["merged_nodes"] += stats["merged_nodes"]
            total["new_edges"] += stats["new_edges"]
            total["merged_edges"] += stats["merged_edges"]
            total["removed_passages"] += stats["removed_passages"]
            total["removed_nodes"] += stats["removed_nodes"]
            total["removed_edges"] += stats["removed_edges"]
            total["weight_changes"] += stats["weight_changes"]
            total["already_indexed"] = (
                total["already_indexed"] or stats["already_indexed"])
        return _finish(total, self.graph)

    # -- replacing -------------------------------------------------

    def update_passage(self, passage_id: str, new_text: str,
                       extractor=None) -> dict:
        """Replace one passage in place: detach the old text,
        then merge the new text. The rest of the graph is
        untouched. A passage that does not exist yet is simply
        added; the same text is a no-op.
        """
        if not isinstance(passage_id, str) or passage_id == "":
            raise ValueError("passage_id must be a non-empty string")
        if not isinstance(new_text, str) or new_text == "":
            raise ValueError("text must be a non-empty string")
        if passage_id not in self.graph.passages:
            return self.add_passage(passage_id, new_text, extractor)
        if normalize_text(self.graph.passages[passage_id]) == (
                normalize_text(new_text)):
            stats = _empty_stats()
            stats["duplicate_passages"] = 1
            stats["already_indexed"] = True
            return _finish(stats, self.graph)
        digest = content_hash(new_text)
        owner = self._passage_doc.get(passage_id)
        removed = self._detach(passage_id)
        if digest in self._hash_to_passage:
            removed["duplicate_passages"] = 1
            removed["duplicate_of"] = self._hash_to_passage[digest]
            return _finish(removed, self.graph)
        added = self._merge(passage_id, new_text, digest, extractor)
        if owner is not None:
            self._passage_doc[passage_id] = owner
        for key in ("added_passages", "new_nodes", "merged_nodes",
                    "new_edges", "merged_edges"):
            removed[key] += added[key]
        removed["weight_changes"] += added["weight_changes"]
        return _finish(removed, self.graph)

    def update_document(self, doc_id: str, new_text: str,
                        extractor=None) -> dict:
        """Replace one document in place: only that document is
        detached and re-merged. Every other document keeps its
        nodes, edges and passages exactly as they were.
        """
        if not isinstance(doc_id, str) or doc_id == "":
            raise ValueError("doc_id must be a non-empty string")
        if not isinstance(new_text, str) or new_text == "":
            raise ValueError("text must be a non-empty string")
        if doc_id not in self.graph.doc_passages:
            return self.add_document(doc_id, new_text, extractor)
        old_text = " ".join(
            self.graph.passages[pid]
            for pid in self.graph.doc_passages[doc_id])
        if normalize_text(old_text) == normalize_text(new_text):
            stats = _empty_stats()
            stats["duplicate_passages"] = len(
                self.graph.doc_passages[doc_id])
            stats["already_indexed"] = True
            stats["doc_id"] = doc_id
            return _finish(stats, self.graph)
        removed = self.remove_document(doc_id)
        added = self.add_document(doc_id, new_text, extractor)
        for key in ("added_passages", "duplicate_passages",
                    "new_nodes", "merged_nodes", "new_edges",
                    "merged_edges"):
            removed[key] += added[key]
        removed["weight_changes"] += added["weight_changes"]
        removed["doc_id"] = doc_id
        return _finish(removed, self.graph)

    # -- removing --------------------------------------------------

    def remove_passage(self, passage_id: str) -> dict:
        """Detach one passage. Nodes and edges that lose their
        last supporting passage go away; anything still
        supported by another passage stays.
        """
        if passage_id not in self.graph.passages:
            raise KeyError(f"unknown passage: {passage_id!r}")
        return _finish(self._detach(passage_id), self.graph)

    def remove_document(self, doc_id: str) -> dict:
        """Detach one document, passage by passage."""
        if doc_id not in self.graph.doc_passages:
            raise KeyError(f"unknown document: {doc_id!r}")
        total = _empty_stats()
        total["doc_id"] = doc_id
        for passage_id in list(self.graph.doc_passages[doc_id]):
            stats = self._detach(passage_id)
            total["removed_passages"] += stats["removed_passages"]
            total["removed_nodes"] += stats["removed_nodes"]
            total["removed_edges"] += stats["removed_edges"]
        self.graph.doc_passages.pop(doc_id, None)
        return _finish(total, self.graph)

    # -- internals -------------------------------------------------

    def _merge(self, passage_id, text, digest, extractor) -> dict:
        """Extract one new passage and merge it into the graph.

        Only this passage is extracted. Existing nodes and edges
        gain the passage id in their source set (weight plus
        one); new ones are created. The extraction is stored so
        a later detach removes exactly this.
        """
        stats = _empty_stats()
        extractor = extractor or self.extractor
        extraction = extractor.extract(text, passage_id)
        self.graph.passages[passage_id] = text
        self._passage_hash[passage_id] = digest
        self._hash_to_passage[digest] = passage_id
        self._passage_extraction[passage_id] = extraction
        seen_here = set()
        counted_nodes: set = set()
        for entity in extraction.entities:
            seen_here.add(entity.name)
            if entity.name not in self.graph.nodes:
                self.graph.nodes[entity.name] = Entity(
                    name=entity.name, kind=entity.kind,
                    source_ids=set(entity.source_ids))
                stats["new_nodes"] += 1
            else:
                node = self.graph.nodes[entity.name]
                before = len(node.source_ids)
                node.source_ids |= entity.source_ids
                if node.kind is None and entity.kind:
                    node.kind = entity.kind
                stats["merged_nodes"] += 1
                stats["weight_changes"].append({
                    "kind": "node", "name": entity.name,
                    "weight_before": before,
                    "weight_after": len(node.source_ids)})
            counted_nodes.add(entity.name)
        for triple in extraction.triples:
            for name in (triple.subject, triple.object):
                seen_here.add(name)
                if name not in self.graph.nodes:
                    self.graph.nodes[name] = Entity(
                        name=name, source_ids=set(triple.source_ids))
                    stats["new_nodes"] += 1
                    counted_nodes.add(name)
                elif name not in counted_nodes:
                    node = self.graph.nodes[name]
                    before = len(node.source_ids)
                    node.source_ids |= triple.source_ids
                    if len(node.source_ids) != before:
                        stats["merged_nodes"] += 1
                        stats["weight_changes"].append({
                            "kind": "node", "name": name,
                            "weight_before": before,
                            "weight_after": len(node.source_ids)})
                    counted_nodes.add(name)
        self.graph.passage_entities[passage_id] = seen_here
        for triple in extraction.triples:
            key = (triple.subject, triple.relation, triple.object)
            if key not in self.graph.edges:
                self.graph.edges[key] = Triple(
                    subject=triple.subject, relation=triple.relation,
                    object=triple.object,
                    source_ids=set(triple.source_ids))
                stats["new_edges"] += 1
            else:
                edge = self.graph.edges[key]
                before = len(edge.source_ids)
                edge.source_ids |= triple.source_ids
                stats["merged_edges"] += 1
                stats["weight_changes"].append({
                    "kind": "edge",
                    "subject": triple.subject,
                    "relation": triple.relation,
                    "object": triple.object,
                    "weight_before": before,
                    "weight_after": len(edge.source_ids)})
        stats["added_passages"] = 1
        return stats

    def _detach(self, passage_id: str) -> dict:
        """Drop one passage's contributions from the graph.

        Uses the extraction stored at index time, so the detach
        removes exactly what the passage added. When the passage
        was indexed outside this updater (no stored extraction),
        it is re-extracted from its stored text instead.
        """
        stats = _empty_stats()
        extraction = self._passage_extraction.get(passage_id)
        if extraction is None:
            text = self.graph.passages[passage_id]
            extraction = self.extractor.extract(text, passage_id)
        names = set()
        for entity in extraction.entities:
            names.add(entity.name)
        for triple in extraction.triples:
            names.add(triple.subject)
            names.add(triple.object)
        for name in names:
            node = self.graph.nodes.get(name)
            if node is None:
                continue
            node.source_ids.discard(passage_id)
            if not node.source_ids:
                del self.graph.nodes[name]
                stats["removed_nodes"] += 1
        for triple in extraction.triples:
            key = (triple.subject, triple.relation, triple.object)
            edge = self.graph.edges.get(key)
            if edge is None:
                continue
            edge.source_ids.discard(passage_id)
            if not edge.source_ids:
                del self.graph.edges[key]
                stats["removed_edges"] += 1
        self.graph.passages.pop(passage_id, None)
        self.graph.passage_entities.pop(passage_id, None)
        digest = self._passage_hash.pop(passage_id, None)
        if digest is not None and self._hash_to_passage.get(digest) == (
                passage_id):
            del self._hash_to_passage[digest]
        self._passage_extraction.pop(passage_id, None)
        doc_id = self._passage_doc.pop(passage_id, None)
        if doc_id is not None and doc_id in self.graph.doc_passages:
            owned = self.graph.doc_passages[doc_id]
            if passage_id in owned:
                owned.remove(passage_id)
        stats["removed_passages"] = 1
        return stats
