"""Auto-curated knowledge wiki (COW-06): read + build API.

The wiki itself is Markdown files under the user's data dir
(``puspa.memory.wiki``); this module is only the HTTP layer,
following the ``rooms_api.py`` shape: plain functions over the
wiki helpers plus ``register_routes(app, need_user)``, mounted by
``puspa.pwa.app.create_app``. All routes require login.

- ``GET /api/wiki/topics`` — the topic list (from the manifest).
- ``GET /api/wiki/pages/{slug}`` — one page: Markdown, rendered
  HTML, and its sources.
- ``POST /api/wiki/build`` — run one curation pass now (the same
  pass the CLI trigger runs); the wiki is otherwise rebuilt after
  consolidation, not on every read.
- ``GET /api/wiki/graph?topic={slug}`` — a READ-ONLY subgraph of
  the existing KnowledgeGraph for one topic: the nodes the page's
  entities name, and the edges between them. No editing lives
  here; editing is B18's graph view, a separate post-beta item.

The graph endpoint reuses the B18 ``GraphViewService`` already
held on ``app.state.graph_views`` (seeded from the user's
episodic memory), so the wiki graph and the second-brain graph
are the same graph, never a second copy.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..i18n.server_lines import render as render_server_line
from ..memory import wiki as wiki_store
from ..retrieval.knowledge_graph import DeterministicExtractor
from .graph_api import _edge_dict, _node_dict

__all__ = ["register_routes"]


def _wiki_home(request: Request):
    """Puspa home override for tests (``app.state.wiki_home``)."""
    return getattr(request.app.state, "wiki_home", None)


def _dsn(request: Request):
    return getattr(request.app.state, "dsn", None)


def register_routes(app: FastAPI, need_user) -> FastAPI:
    """Mount the wiki routes on ``app`` using its login resolver."""

    @app.get("/api/wiki/topics")
    def topics_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            return {"topics": wiki_store.list_pages(user_id, home=_wiki_home(request))}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/wiki/pages/{slug}")
    def page_endpoint(request: Request, slug: str):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            return wiki_store.read_page(user_id, slug, home=_wiki_home(request))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/wiki/build")
    def build_endpoint(request: Request):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            return wiki_store.build_wiki(user_id, dsn=_dsn(request), home=_wiki_home(request))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/api/wiki/graph")
    def graph_endpoint(request: Request, topic: str = ""):
        user_id = need_user(request)
        if user_id is None:
            return JSONResponse(
                {"error": render_server_line("error.not_logged_in")}, status_code=401
            )
        try:
            page = wiki_store.read_page(user_id, topic, home=_wiki_home(request))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        service = getattr(request.app.state, "graph_views", None)
        if service is None:
            return JSONResponse({"error": "graph view is not available"}, status_code=503)
        graph = service.graph_for(user_id)
        # The topic's nodes: the page's recorded entities, plus the
        # entities the graph's own extractor reads out of the page
        # text — the two extractors tokenize model codes
        # differently ("samsung galaxy" vs "samsung galaxy s24"),
        # and the graph's reading is the one its nodes carry.
        wanted = {name for name in page["entities"]}
        wanted.update(
            entity.name
            for entity in DeterministicExtractor().extract(page["markdown"], "wiki").entities
        )
        nodes = [
            _node_dict(graph, graph.nodes[name], None)
            for name in sorted(wanted)
            if name in graph.nodes
        ]
        node_names = {node["name"] for node in nodes}
        edges = [
            _edge_dict(triple)
            for triple in sorted(
                graph.edges.values(), key=lambda t: (t.subject, t.relation, t.object)
            )
            if triple.subject in node_names and triple.object in node_names
        ]
        return {
            "topic": page["slug"],
            "title": page["title"],
            "nodes": nodes,
            "edges": edges,
            "total_nodes": len(graph.nodes),
            "total_edges": len(graph.edges),
        }

    return app
