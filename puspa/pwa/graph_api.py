"""Second-brain graph view (B18): read + edit API over the knowledge graph.

The ``KnowledgeGraph`` already feeds the AI's memory (episodic
memory writes triples through it, retrieval reads it with PPR
multi-hop), but the user could not see it: the PWA Memory screen
showed text only. This module is the visible second brain:

- a bounded read endpoint: nodes and edges, paginated, capped per
  request, filterable by node kind and by recency;
- node inspect: the node's facts (triples) and provenance (the
  passages the facts came from);
- edit endpoints: rename a node, add or delete a node, add /
  change / delete an edge, correct a wrong fact. Every edit writes
  back to the same ``KnowledgeGraph`` the retriever reads, and is
  recorded in the ledger as a user correction — never a silent
  mutation, because the graph is the agent's evidence base.

Ledger event type: the closed set of 31 revision-7 types stays
closed (no 32nd type). Corrections are ``evidence_recorded``
events whose content carries ``"kind": "user_correction"`` plus
the operation and the before/after values, on one ledger task
per user (``graph-corrections-<user_id>``). The same task id is
what lets a fresh process replay the corrections on top of the
episode seed, so an edit survives a server restart.

Where the graph lives: one ``KnowledgeGraph`` per user, held in
server process memory by :class:`GraphViewService` and seeded
lazily from that user's episodic memory (``list_episodes`` +
``episodes_to_graph``, limit 200 episodes) the first time it is
read, then replaying that user's ledger corrections in order.
Retrieval after an edit (``GraphViewService.retrieve``) runs the
HippoRAG retriever over this same graph object, so a correction
changes the next answer. Honest limit, stated here and in
``docs/graph-view.md``: the graph is process memory, not a
database table — a restart rebuilds it from episodes plus the
ledger replay, and episodes recorded after the seed do not
appear until the process restarts (or the service is reset).
The episodic pipeline (``search_episodes_pipeline``) rebuilds
its own graph from episodes per query, so a correction made
here does not change that pipeline's answers until the
correction is also an episode; that write-back is follow-up
work, not part of this ticket.

Code graph nodes are read-only here (G29): nodes whose kind is
one of ``file`` / ``function`` / ``class`` / ``module`` /
``table`` / ``config``, or whose name ends in ``.py``, belong
to the code graph. Any edit touching one — directly, or
through an edge — is refused with 403. The user sees code
nodes in the view but cannot rename, delete, or rewire them.

Recency: the graph itself carries no timestamps, so the
service tracks one per node. A node seeded from an episode
takes the newest ``created_at`` of the episodes that mention
it; a node added or touched by a correction takes the
correction's time. The ``since`` filter compares against that.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..i18n.server_lines import render as render_server_line
from ..ledger import Ledger
from ..memory.episodic import episodes_to_graph, list_episodes
from ..retrieval.hipporag import HippoRAGRetriever
from ..retrieval.knowledge_graph import Entity, KnowledgeGraph, Triple, normalize_entity_name

__all__ = [
    "CODE_NODE_KINDS",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "CodeGraphReadOnlyError",
    "GraphViewService",
    "correction_task_id",
    "is_code_node",
    "register_routes",
]

#: Hard cap: no read response ever carries more than this many nodes.
MAX_LIMIT = 100
#: Default page size for the graph read.
DEFAULT_LIMIT = 50
#: How many of the user's episodes seed the graph on first read.
SEED_EPISODE_LIMIT = 200
#: Source id stamped on nodes and edges the user creates.
USER_SOURCE_ID = "user_correction"
#: Ledger event type for corrections (closed 31-type set; no new type).
CORRECTION_EVENT_TYPE = "evidence_recorded"

#: Node kinds written by the G29 code graph. These nodes are
#: read-only through this API; see ``is_code_node``.
CODE_NODE_KINDS = frozenset({"file", "function", "class", "module", "table", "config"})


class CodeGraphReadOnlyError(ValueError):
    """An edit touched a code-graph node, which is read-only here."""


def correction_task_id(user_id: str) -> str:
    """Ledger task id holding one user's graph corrections."""
    return f"graph-corrections-{user_id}"


def is_code_node(entity: Entity | None) -> bool:
    """True when a node belongs to the G29 code graph, not the memory graph."""
    if entity is None:
        return False
    if entity.kind in CODE_NODE_KINDS:
        return True
    return entity.name.endswith(".py")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _node_dict(graph: KnowledgeGraph, entity: Entity, seen_at: datetime | None) -> dict:
    return {
        "name": entity.name,
        "kind": entity.kind,
        "description": entity.description,
        "sources": sorted(entity.source_ids),
        "fact_count": sum(
            1 for triple in graph.edges.values() if entity.name in (triple.subject, triple.object)
        ),
        "seen_at": seen_at.isoformat() if seen_at is not None else None,
        "is_code_node": is_code_node(entity),
    }


def _edge_dict(triple: Triple) -> dict:
    return {
        "subject": triple.subject,
        "relation": triple.relation,
        "object": triple.object,
        "sources": sorted(triple.source_ids),
    }


def _obsidian_link(name: str) -> str:
    """A node name safe inside an Obsidian [[wikilink]].

    ``|`` starts an alias and ``]`` closes the link, so both
    are replaced; anything else in a normalized entity name is
    link-safe.
    """
    return name.replace("|", "/").replace("]", "").replace("[", "")


class GraphViewService:
    """One user's visible, editable knowledge graph.

    ``dsn`` is the Postgres DSN for the episode seed and the
    ledger. Pass a ``ledger`` (a :class:`Ledger` or a test
    double with ``append_event`` / ``events``) to override it.
    """

    def __init__(self, dsn=None, ledger: Ledger | None = None):
        self.dsn = dsn
        self._ledger_override = ledger
        self._graphs: dict[str, KnowledgeGraph] = {}
        self._seen_at: dict[str, dict[str, datetime]] = {}

    @property
    def _ledger(self) -> Ledger:
        if self._ledger_override is None:
            self._ledger_override = Ledger(self.dsn)
        return self._ledger_override

    # -- seeding ------------------------------------------------------

    def graph_for(self, user_id: str, *, agent_id: str | None = None) -> KnowledgeGraph:
        """The user's graph: seeded from episodes, then corrections replayed."""
        if user_id not in self._graphs:
            self._seed(user_id)
        return self._graphs[user_id]

    def reset(self, user_id: str) -> None:
        """Drop the cached graph so the next read re-seeds (and replays)."""
        self._graphs.pop(user_id, None)
        self._seen_at.pop(user_id, None)

    def _seed(self, user_id: str) -> None:
        episodes = list_episodes(user_id, limit=SEED_EPISODE_LIMIT, dsn=self.dsn)
        graph = episodes_to_graph(episodes)
        seen: dict[str, datetime] = {}
        by_passage: dict[str, datetime] = {}
        for episode in episodes:
            created = _as_utc(episode.get("created_at")) or _now()
            by_passage[str(episode.get("id"))] = created
        for name, entity in graph.nodes.items():
            times = [
                by_passage[pid.split("#p")[0]]
                for pid in entity.source_ids
                if pid.split("#p")[0] in by_passage
            ]
            seen[name] = max(times) if times else _now()
        self._graphs[user_id] = graph
        self._seen_at[user_id] = seen
        self._replay_corrections(user_id)

    def _replay_corrections(self, user_id: str) -> None:
        """Re-apply the user's ledger corrections on top of the seed.

        Best effort: a correction whose target vanished (the
        episode behind it was deleted) is skipped, never fatal.
        """
        try:
            events = self._ledger.events(
                correction_task_id(user_id), event_type=CORRECTION_EVENT_TYPE
            )
        except Exception:
            return
        for event in events:
            content = event.get("content") or {}
            if content.get("kind") != "user_correction":
                continue
            detail = content.get("detail") or {}
            try:
                self._apply_operation(
                    user_id,
                    str(content.get("operation") or ""),
                    detail,
                    seen_at=_as_utc(event.get("created_at")),
                )
            except Exception:
                continue

    # -- read ---------------------------------------------------------

    def list_graph(
        self,
        user_id: str,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        kind: str | None = None,
        since: datetime | None = None,
        agent_id: str | None = None,
    ) -> dict:
        """Bounded node/edge page: capped, paginated, filterable.

        ``limit`` is clamped to ``MAX_LIMIT``; ``offset`` pages
        through the nodes sorted by name. Edges are only the ones
        touching a returned node, also capped at ``limit``. ``kind``
        keeps nodes of one kind; ``since`` keeps nodes seen at or
        after that time.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        limit = min(limit, MAX_LIMIT)
        since = _as_utc(since)
        graph = self.graph_for(user_id, agent_id=agent_id)
        seen = self._seen_at.get(user_id, {})
        names = sorted(graph.nodes)
        if kind is not None:
            names = [n for n in names if graph.nodes[n].kind == kind]
        if since is not None:
            names = [n for n in names if (seen.get(n) or _now()) >= since]
        total_nodes = len(names)
        page = names[offset : offset + limit]
        page_set = set(page)
        edges = [
            _edge_dict(triple)
            for triple in sorted(
                graph.edges.values(), key=lambda t: (t.subject, t.relation, t.object)
            )
            if triple.subject in page_set or triple.object in page_set
        ][:limit]
        return {
            "nodes": [_node_dict(graph, graph.nodes[name], seen.get(name)) for name in page],
            "edges": edges,
            "total_nodes": total_nodes,
            "total_edges": len(graph.edges),
            "limit": limit,
            "offset": offset,
        }

    def export_obsidian(
        self, user_id: str, *, agent_id: str | None = None
    ) -> str:
        """The whole graph as one Obsidian markdown file.

        One section per node (``## [[name]]``), each fact a bullet
        with the other end as a ``[[wikilink]]`` so every
        relationship is a clickable link in Obsidian, both ways:
        outgoing facts link their object, incoming facts link
        their subject. Nodes are capped at 1000 (the file states
        the cap when it bites); the export reads the same graph
        the screen shows, corrections included.
        """
        graph = self.graph_for(user_id, agent_id=agent_id)
        names = sorted(graph.nodes)
        capped = len(names) > 1000
        names = names[:1000]
        lines = [
            "# Puspa knowledge graph",
            "",
            "Exported from PuspaBot for Obsidian. Each section is one",
            "note; [[wikilinks]] jump between notes.",
            "",
        ]
        if capped:
            lines += [
                "Only the first 1000 nodes are included in this export.",
                "",
            ]
        if not names:
            lines += ["Graf masih kosong.", ""]
        triples = sorted(
            graph.edges.values(),
            key=lambda t: (t.subject, t.relation, t.object),
        )
        for name in names:
            lines.append(f"## [[{_obsidian_link(name)}]]")
            entity = graph.nodes[name]
            if entity.kind:
                lines.append(f"Kind: {entity.kind}")
            outgoing = [t for t in triples if t.subject == name]
            incoming = [t for t in triples if t.object == name]
            for triple in outgoing:
                lines.append(
                    f"- {name} — {triple.relation} — "
                    f"[[{_obsidian_link(triple.object)}]]"
                )
            for triple in incoming:
                lines.append(
                    f"- [[{_obsidian_link(triple.subject)}]] — "
                    f"{triple.relation} — {name}"
                )
            if not outgoing and not incoming:
                lines.append("- (belum ada hubungan)")
            lines.append("")
        return "\n".join(lines)

    def inspect_node(self, user_id: str, name: str, *, agent_id: str | None = None) -> dict:
        """One node: its facts (triples) and provenance (passages)."""
        graph = self.graph_for(user_id, agent_id=agent_id)
        normalized = normalize_entity_name(name)
        entity = graph.nodes.get(normalized)
        if entity is None:
            raise KeyError(f"unknown node: {name!r}")
        facts = [
            _edge_dict(triple)
            for triple in sorted(
                graph.edges.values(), key=lambda t: (t.subject, t.relation, t.object)
            )
            if normalized in (triple.subject, triple.object)
        ]
        passages = [
            {"passage_id": pid, "text": graph.passages[pid]}
            for pid in graph.passages_for_nodes([normalized])
            if pid in graph.passages
        ]
        out = _node_dict(graph, entity, self._seen_at.get(user_id, {}).get(normalized))
        out["facts"] = facts
        out["provenance"] = passages
        return out

    def retrieve(
        self, user_id: str, query: str, *, top_k: int = 5, agent_id: str | None = None
    ) -> list:
        """HippoRAG retrieval over this same graph object.

        The proof surface for B18: after an edit, the next
        retrieval reads the edited graph, so answers change.
        """
        if not isinstance(query, str) or query == "":
            raise ValueError("query must be a non-empty string")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        graph = self.graph_for(user_id, agent_id=agent_id)
        results = HippoRAGRetriever(graph).retrieve(query, top_k=top_k)
        return [
            {
                "passage_id": result.get("passage_id"),
                "text": result.get("text"),
                "score": result.get("score"),
                "graph_score": result.get("graph_score"),
                "seed_nodes": result.get("seed_nodes"),
                "evidence_id": result.get("evidence_id"),
            }
            for result in results
        ]

    # -- edits ----------------------------------------------------------

    def rename_node(
        self, user_id: str, old_name: str, new_name: str, *, agent_id: str | None = None
    ) -> dict:
        """Rename a node; every edge and passage link follows it."""
        return self._edit(
            user_id,
            "rename_node",
            {"old_name": old_name, "new_name": new_name},
            agent_id=agent_id,
        )

    def add_node(
        self,
        user_id: str,
        name: str,
        *,
        kind: str | None = None,
        description: str = "",
        agent_id: str | None = None,
    ) -> dict:
        """Add a node the user states (never a code-graph kind)."""
        return self._edit(
            user_id,
            "add_node",
            {"name": name, "kind": kind, "description": description},
            agent_id=agent_id,
        )

    def delete_node(self, user_id: str, name: str, *, agent_id: str | None = None) -> dict:
        """Delete a node and every edge touching it."""
        return self._edit(user_id, "delete_node", {"name": name}, agent_id=agent_id)

    def add_edge(
        self, user_id: str, subject: str, relation: str, obj: str, *, agent_id: str | None = None
    ) -> dict:
        """Add an edge; missing endpoint nodes are created (kind unknown)."""
        return self._edit(
            user_id,
            "add_edge",
            {"subject": subject, "relation": relation, "object": obj},
            agent_id=agent_id,
        )

    def change_edge(
        self,
        user_id: str,
        subject: str,
        relation: str,
        obj: str,
        *,
        new_relation: str | None = None,
        new_object: str | None = None,
        agent_id: str | None = None,
    ) -> dict:
        """Change an edge's relation and/or object."""
        return self._edit(
            user_id,
            "change_edge",
            {
                "subject": subject,
                "relation": relation,
                "object": obj,
                "new_relation": new_relation,
                "new_object": new_object,
            },
            agent_id=agent_id,
        )

    def correct_fact(
        self,
        user_id: str,
        subject: str,
        relation: str,
        obj: str,
        *,
        new_relation: str | None = None,
        new_object: str | None = None,
        agent_id: str | None = None,
    ) -> dict:
        """Correct a wrong fact: same as changing its edge, but the
        ledger records the operation as ``correct_fact`` so the
        user's intent (a correction, not a rewording) stays visible.
        """
        return self._edit(
            user_id,
            "correct_fact",
            {
                "subject": subject,
                "relation": relation,
                "object": obj,
                "new_relation": new_relation,
                "new_object": new_object,
            },
            agent_id=agent_id,
        )

    def delete_edge(
        self, user_id: str, subject: str, relation: str, obj: str, *, agent_id: str | None = None
    ) -> dict:
        """Delete one edge."""
        return self._edit(
            user_id,
            "delete_edge",
            {"subject": subject, "relation": relation, "object": obj},
            agent_id=agent_id,
        )

    def _edit(self, user_id: str, operation: str, detail: dict, *, agent_id: str | None) -> dict:
        """One edit: apply to the graph, then record the correction.

        The ledger entry is written right after the in-memory
        mutation, and a failed ledger write rolls the graph back
        (drop the cache; the next read re-seeds from episodes and
        replays only the corrections actually recorded). An edit
        can therefore never land without its correction record,
        and a failed edit never leaves a stray record behind.
        """
        self.graph_for(user_id, agent_id=agent_id)
        result = self._apply_operation(user_id, operation, detail, seen_at=None)
        try:
            self._record_correction(user_id, operation, detail, agent_id=agent_id)
        except Exception:
            self.reset(user_id)
            raise
        return result

    def _record_correction(
        self, user_id: str, operation: str, detail: dict, *, agent_id: str | None
    ) -> dict:
        return self._ledger.append_event(
            task_id=correction_task_id(user_id),
            agent_id=agent_id or "puspa",
            event_type=CORRECTION_EVENT_TYPE,
            content={
                "kind": "user_correction",
                "operation": operation,
                "evidence_id": uuid.uuid4().hex,
                "user_id": user_id,
                "detail": detail,
            },
        )

    def _apply_operation(
        self,
        user_id: str,
        operation: str,
        detail: dict,
        *,
        seen_at: datetime | None = None,
    ) -> dict:
        """Apply one correction to the graph (shared by edits and replay)."""
        graph = self.graph_for(user_id)
        seen = self._seen_at.setdefault(user_id, {})
        stamp = seen_at or _now()
        if operation == "rename_node":
            old = normalize_entity_name(detail.get("old_name") or "")
            new = normalize_entity_name(detail.get("new_name") or "")
            if not old or not new:
                raise ValueError("old_name and new_name must be non-empty strings")
            if old == new:
                raise ValueError("new_name must differ from old_name")
            entity = graph.nodes.get(old)
            if entity is None:
                raise KeyError(f"unknown node: {old!r}")
            if is_code_node(entity):
                raise CodeGraphReadOnlyError(f"code-graph node is read-only: {old!r}")
            if new in graph.nodes and is_code_node(graph.nodes[new]):
                raise CodeGraphReadOnlyError(f"code-graph node is read-only: {new!r}")
            if new in graph.nodes:
                target = graph.nodes[new]
                target.source_ids |= entity.source_ids
                if target.kind is None:
                    target.kind = entity.kind
                if not target.description and entity.description:
                    target.description = entity.description
            else:
                graph.nodes[new] = Entity(
                    name=new,
                    kind=entity.kind,
                    description=entity.description,
                    source_ids=set(entity.source_ids),
                )
            del graph.nodes[old]
            merged: dict[tuple, Triple] = {}
            for triple in graph.edges.values():
                subject = new if triple.subject == old else triple.subject
                obj = new if triple.object == old else triple.object
                key = (subject, triple.relation, obj)
                if key in merged:
                    merged[key].source_ids |= triple.source_ids
                else:
                    merged[key] = Triple(
                        subject=subject,
                        relation=triple.relation,
                        object=obj,
                        source_ids=set(triple.source_ids),
                    )
            graph.edges = merged
            for names in graph.passage_entities.values():
                if old in names:
                    names.discard(old)
                    names.add(new)
            seen[new] = seen.pop(old, stamp)
            return _node_dict(graph, graph.nodes[new], seen[new])
        if operation == "add_node":
            name = normalize_entity_name(detail.get("name") or "")
            kind = detail.get("kind")
            description = detail.get("description") or ""
            if not name:
                raise ValueError("name must be a non-empty string")
            if kind is not None and not isinstance(kind, str):
                raise ValueError("kind must be a string")
            if kind in CODE_NODE_KINDS:
                raise CodeGraphReadOnlyError(f"kind belongs to the code graph: {kind!r}")
            if name in graph.nodes:
                raise ValueError(f"node already exists: {name!r}")
            if name.endswith(".py"):
                raise CodeGraphReadOnlyError(f"code-graph node is read-only: {name!r}")
            entity = Entity(
                name=name, kind=kind, description=description, source_ids={USER_SOURCE_ID}
            )
            graph.nodes[name] = entity
            seen[name] = stamp
            return _node_dict(graph, entity, seen[name])
        if operation == "delete_node":
            name = normalize_entity_name(detail.get("name") or "")
            if not name:
                raise ValueError("name must be a non-empty string")
            entity = graph.nodes.get(name)
            if entity is None:
                raise KeyError(f"unknown node: {name!r}")
            if is_code_node(entity):
                raise CodeGraphReadOnlyError(f"code-graph node is read-only: {name!r}")
            del graph.nodes[name]
            graph.edges = {
                key: triple
                for key, triple in graph.edges.items()
                if name not in (triple.subject, triple.object)
            }
            for names in graph.passage_entities.values():
                names.discard(name)
            seen.pop(name, None)
            return {"deleted": name}
        if operation in ("add_edge", "change_edge", "correct_fact", "delete_edge"):
            return self._apply_edge_operation(user_id, operation, detail, stamp=stamp)
        raise ValueError(f"unknown graph operation: {operation!r}")

    def _apply_edge_operation(
        self, user_id: str, operation: str, detail: dict, *, stamp: datetime
    ) -> dict:
        graph = self.graph_for(user_id)
        seen = self._seen_at.setdefault(user_id, {})
        subject = normalize_entity_name(detail.get("subject") or "")
        relation = str(detail.get("relation") or "").strip().lower()
        obj = normalize_entity_name(detail.get("object") or "")
        if not subject or not relation or not obj:
            raise ValueError("subject, relation, and object must be non-empty strings")
        if operation == "add_edge":
            for name in (subject, obj):
                if name in graph.nodes and is_code_node(graph.nodes[name]):
                    raise CodeGraphReadOnlyError(f"code-graph node is read-only: {name!r}")
            key = (subject, relation, obj)
            if key in graph.edges:
                raise ValueError(f"edge already exists: {key!r}")
            for name in (subject, obj):
                if name not in graph.nodes:
                    graph.nodes[name] = Entity(name=name, source_ids={USER_SOURCE_ID})
                    seen[name] = stamp
            graph.edges[key] = Triple(
                subject=subject, relation=relation, object=obj, source_ids={USER_SOURCE_ID}
            )
            seen[subject] = seen.get(subject, stamp)
            seen[obj] = seen.get(obj, stamp)
            return _edge_dict(graph.edges[key])
        if operation in ("change_edge", "correct_fact"):
            key = (subject, relation, obj)
            triple = graph.edges.get(key)
            if triple is None:
                raise KeyError(f"unknown edge: {key!r}")
            for name in (subject, obj):
                if name in graph.nodes and is_code_node(graph.nodes[name]):
                    raise CodeGraphReadOnlyError(f"code-graph node is read-only: {name!r}")
            new_relation = detail.get("new_relation")
            new_object = detail.get("new_object")
            new_relation = str(new_relation).strip().lower() if new_relation else relation
            new_object = normalize_entity_name(new_object) if new_object else obj
            if not new_relation:
                raise ValueError("new_relation must be a non-empty string")
            if not new_object:
                raise ValueError("new_object must be a non-empty string")
            if new_object in graph.nodes and is_code_node(graph.nodes[new_object]):
                raise CodeGraphReadOnlyError(f"code-graph node is read-only: {new_object!r}")
            new_key = (subject, new_relation, new_object)
            if new_key != key and new_key in graph.edges:
                raise ValueError(f"edge already exists: {new_key!r}")
            if new_object not in graph.nodes:
                graph.nodes[new_object] = Entity(name=new_object, source_ids={USER_SOURCE_ID})
                seen[new_object] = stamp
            del graph.edges[key]
            graph.edges[new_key] = Triple(
                subject=subject,
                relation=new_relation,
                object=new_object,
                source_ids=set(triple.source_ids) | {USER_SOURCE_ID},
            )
            seen[subject] = seen.get(subject, stamp)
            seen[new_object] = seen.get(new_object, stamp)
            return _edge_dict(graph.edges[new_key])
        # delete_edge
        key = (subject, relation, obj)
        triple = graph.edges.get(key)
        if triple is None:
            raise KeyError(f"unknown edge: {key!r}")
        for name in (subject, obj):
            if name in graph.nodes and is_code_node(graph.nodes[name]):
                raise CodeGraphReadOnlyError(f"code-graph node is read-only: {name!r}")
        del graph.edges[key]
        return {"deleted": {"subject": subject, "relation": relation, "object": obj}}


# -- HTTP layer ---------------------------------------------------------
# Follows the rooms_api.py shape: plain functions over the
# service plus register_routes(app, need_user), mounted by
# puspa.pwa.app.create_app. All routes require login.


def _service(request: Request) -> GraphViewService:
    return request.app.state.graph_views


def _agent_id(request: Request, user_id: str) -> str:
    try:
        from puspa.runtime.agents import ensure_puspa_agent

        return ensure_puspa_agent(user_id, dsn=request.app.state.dsn)
    except Exception:
        return "puspa"


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"since must be an ISO-8601 timestamp: {raw!r}") from exc
    return parsed


def _parse_int(raw: str | None, name: str, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the graph-view routes on ``app`` using its login resolver."""

    @app.get("/api/graph")
    def graph_endpoint(
        request: Request,
        limit: str | None = None,
        offset: str | None = None,
        kind: str | None = None,
        since: str | None = None,
    ):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            return _service(request).list_graph(
                user_id,
                limit=_parse_int(limit, "limit", DEFAULT_LIMIT),
                offset=_parse_int(offset, "offset", 0),
                kind=kind,
                since=_parse_since(since),
                agent_id=_agent_id(request, user_id),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/graph/export/obsidian")
    async def export_obsidian_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        markdown = _service(request).export_obsidian(
            user_id, agent_id=_agent_id(request, user_id)
        )
        return PlainTextResponse(
            markdown,
            media_type="text/markdown",
            headers={
                "Content-Disposition": 'attachment; filename="puspa-graph.md"',
            },
        )

    @app.get("/api/graph/nodes/{name:path}")
    def node_endpoint(request: Request, name: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            return _service(request).inspect_node(
                user_id, name, agent_id=_agent_id(request, user_id)
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.get("/api/graph/search")
    def search_endpoint(request: Request, q: str = "", top_k: str | None = None):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            return {
                "results": _service(request).retrieve(
                    user_id,
                    q,
                    top_k=_parse_int(top_k, "top_k", 5),
                    agent_id=_agent_id(request, user_id),
                )
            }
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/graph/nodes")
    async def add_node_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("node body must be a JSON object")
            return _service(request).add_node(
                user_id,
                str(data.get("name") or ""),
                kind=data.get("kind"),
                description=str(data.get("description") or ""),
                agent_id=_agent_id(request, user_id),
            )
        except CodeGraphReadOnlyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.patch("/api/graph/nodes/{name:path}")
    async def rename_node_endpoint(request: Request, name: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("node body must be a JSON object")
            return _service(request).rename_node(
                user_id,
                name,
                str(data.get("name") or ""),
                agent_id=_agent_id(request, user_id),
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except CodeGraphReadOnlyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.delete("/api/graph/nodes/{name:path}")
    def delete_node_endpoint(request: Request, name: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            return _service(request).delete_node(
                user_id, name, agent_id=_agent_id(request, user_id)
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except CodeGraphReadOnlyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)

    @app.post("/api/graph/edges")
    async def add_edge_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("edge body must be a JSON object")
            return _service(request).add_edge(
                user_id,
                str(data.get("subject") or ""),
                str(data.get("relation") or ""),
                str(data.get("object") or ""),
                agent_id=_agent_id(request, user_id),
            )
        except CodeGraphReadOnlyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.patch("/api/graph/edges")
    async def change_edge_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("edge body must be a JSON object")
            operation = str(data.get("operation") or "change_edge")
            if operation == "correct_fact":
                call = _service(request).correct_fact
            elif operation == "change_edge":
                call = _service(request).change_edge
            else:
                raise ValueError(f"unknown edge operation: {operation!r}")
            return call(
                user_id,
                str(data.get("subject") or ""),
                str(data.get("relation") or ""),
                str(data.get("object") or ""),
                new_relation=data.get("new_relation"),
                new_object=data.get("new_object"),
                agent_id=_agent_id(request, user_id),
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except CodeGraphReadOnlyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.delete("/api/graph/edges")
    def delete_edge_endpoint(
        request: Request, subject: str = "", relation: str = "", object: str = ""
    ):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            return _service(request).delete_edge(
                user_id, subject, relation, object, agent_id=_agent_id(request, user_id)
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except CodeGraphReadOnlyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return app
