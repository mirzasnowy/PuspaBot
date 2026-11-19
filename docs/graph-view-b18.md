# B18 — Second-brain graph view, user-visible and user-editable

## What it is

The KnowledgeGraph already fed the AI's memory, but the user could
not see it: the PWA Memory screen showed text only. B18 adds the
visible second brain: a node-and-edge graph view on the Memory
screen, served by a bounded read endpoint, with all four edit
flows (rename node, add/delete node, add/change/delete edge,
correct a fact). Every edit writes back to the same graph the
retriever reads and is recorded in the ledger as a user
correction, so a correction changes the next answer and stays
traceable.

## Design

### Where the graph lives

`puspa/pwa/graph_api.py` — `GraphViewService`, one
`KnowledgeGraph` per user held in server process memory
(`app.state.graph_views`, created in `puspa/pwa/app.py`
`create_app`). It is seeded lazily on first read from that
user's episodic memory (`list_episodes`, limit 200, then
`episodes_to_graph` with the deterministic extractor), and
then the user's ledger corrections are replayed on top of it
in recorded order. Retrieval after an edit
(`GraphViewService.retrieve`) runs the HippoRAG retriever over
this same graph object.

Routes (all login-gated, registered by `register_routes` in the
`rooms_api.py` shape):

- `GET /api/graph?limit=&offset=&kind=&since=` — bounded page:
  nodes sorted by name, `limit` clamped to 100, `offset` pages
  through them; edges are only the ones touching a returned
  node, also capped at `limit`. `kind` filters by node kind,
  `since` (ISO-8601) by recency.
- `GET /api/graph/nodes/{name}` — inspect: facts (triples) plus
  provenance (passage ids and text).
- `POST /api/graph/nodes`, `PATCH /api/graph/nodes/{name}`
  (rename), `DELETE /api/graph/nodes/{name}`.
- `POST /api/graph/edges`, `PATCH /api/graph/edges`
  (change, or `operation: correct_fact`), `DELETE
  /api/graph/edges?subject=&relation=&object=`.
- `GET /api/graph/search?q=&top_k=` — HippoRAG retrieval over
  the same graph; `graph_score` 0 and `seed_nodes` empty mean
  the query matched no node and fell back to vector ranking.

Node routes use the `:path` converter (`{name:path}`) because
code-graph node names contain `/` (for example
`puspa/billing.py`).

### Ledger: no 32nd event type

Corrections are `evidence_recorded` events (the closed set of
31 revision-7 types stays closed) whose content carries
`"kind": "user_correction"`, the `operation`
(`rename_node`, `add_node`, `delete_node`, `add_edge`,
`change_edge`, `correct_fact`, `delete_edge`), an
`evidence_id`, the `user_id`, and the before/after `detail`.
All of one user's corrections live on one ledger task,
`graph-corrections-<user_id>` — that task id is what makes
replay possible: a fresh process re-seeds from episodes and
re-applies the corrections in order, so an edit survives a
server restart.

Edit ordering inside `_edit`: apply to the graph, then record
the ledger entry; a failed ledger write rolls the graph back
(drop the cache; the next read re-seeds and replays only the
corrections actually recorded). An edit can never land
without its correction record, and a failed edit never leaves
a stray record behind.

### Code graph: read-only

Nodes whose kind is one of `file`, `function`, `class`,
`module`, `table`, `config`, or whose name ends in `.py`,
belong to the G29 code graph. Any edit touching one —
directly, through an edge, or as a `correct_fact` target —
is refused with 403, and adding a node with a code-graph kind
is refused the same way. Code nodes are visible in the view
(marked read-only) but cannot be renamed, deleted, or rewired.

### Recency

The graph carries no timestamps, so the service tracks one
per node: a seeded node takes the newest `created_at` of the
episodes that mention it; a node added or touched by a
correction takes the correction's time (the ledger event's
`created_at` on replay). The `since` filter compares against
that.

### Frontend

- `apps/pwa/src/components/GraphView.tsx` — a real visual
  graph: an SVG drawing with nodes on a circle in name order
  (fixed, deterministic layout — layout research is out of
  scope) and edges drawn as lines with the relation written
  along them. Click a node to inspect it. A plain list under
  the drawing keeps the graph usable when crowded and marks
  code nodes read-only.
- `apps/pwa/src/screens/MemoryScreen.tsx` — the Memory screen
  gains a "Graf otak kedua" section: the graph view, a detail
  card (facts with an inline correct-the-object field and a
  delete-edge button, provenance passages, rename and delete
  node), add-node and add-edge forms. Every mutation shows the
  server's confirmation and refetches the page.
- `apps/pwa/src/api/client.ts` — typed `fetchGraphPage`,
  `fetchGraphNode`, `addGraphNode`, `renameGraphNode`,
  `deleteGraphNode`, `addGraphEdge`, `changeGraphEdge`,
  `deleteGraphEdge`, `searchGraph`, plus the shared
  `patchJson` / `deleteJson` helpers. Types come from the
  regenerated `generated-types.ts`.
- Contract: `docs/openapi-pwa.yaml` gained the `/api/graph*`
  paths and the `GraphNode`, `GraphEdge`, `GraphPage`,
  `GraphNodeDetail`, `GraphNodeInput`, `GraphEdgeInput`,
  `GraphEdgeChangeInput`, `GraphSearchResult`,
  `GraphSearchResponse` schemas.

## Acceptance mapping

- View renders from the real KnowledgeGraph, never mock data:
  the service seeds from the user's real episodes; the
  frontend fetches only from `/api/graph`. Tested by
  `test_inspect_node_shows_facts_and_provenance` and the
  bounded-read test (seeded episodes, real extractor).
- Click a node to inspect its facts and provenance:
  `GET /api/graph/nodes/{name}` returns triples + passage
  texts; the screen's detail card shows them.
- All four edit flows work end to end with tests: rename,
  add/delete node, add/change/delete edge, correct a fact —
  `test_rename_node_end_to_end_with_ledger_entry`,
  `test_add_and_delete_node_end_to_end`,
  `test_add_change_delete_edge_and_correct_fact`.
- An edit is reflected in a later retrieval answer:
  `test_edit_changes_later_retrieval_answer` — before the
  rename, the query "Pixel 9 Pro" matches no node (vector
  fallback, empty `seed_nodes`, `graph_score` 0); after
  renaming the node, the same query seeds on the new name and
  returns the episode passage with `graph_score > 0`.
- Every edit has a ledger entry:
  each edit test asserts the exact `evidence_recorded` /
  `user_correction` entries on `graph-corrections-<user_id>`;
  `test_edits_survive_fresh_app_through_ledger_replay` proves
  the entries drive replay.
- Code-graph nodes are NOT editable:
  `test_code_graph_nodes_are_not_editable` (rename, delete,
  add/change edge all 403; zero ledger entries).

## Honest limits

- The graph is process memory, not a database table. A
  restart rebuilds it from episodes plus ledger replay, and
  episodes recorded after the seed do not appear until the
  process restarts (or the service is reset). The seed is
  capped at 200 episodes.
- A correction made here does not change the episodic
  pipeline (`search_episodes_pipeline` rebuilds its own graph
  from episodes per query). The edit changes answers that
  read this service's graph (`GET /api/graph/search` and the
  HippoRAG retriever over it); writing a correction back into
  the episodic table is follow-up work, not part of this
  ticket.
- Recency is service-tracked (episode `created_at` / correction
  time), not a timestamp stored on the graph node itself.
- The SVG layout is a circle in name order: readable for tens
  of nodes, crowded for hundreds. The per-request cap (100)
  and the list underneath are the mitigation; a real layout
  algorithm is out of scope.
- Rename merges into an existing node when the new name
  already exists (sources and kind merge); passage text still
  contains the old name — only the graph links are rewritten.
- Replay is best effort: a correction whose target vanished
  (the episode behind it was deleted) is skipped, never fatal.

## B14 notes

Indonesian-context pass for this ticket: none of the graph
content is locale-specific (node names come from the user's
own episodes), and the PWA screen keeps the existing
Indonesian UI wording of the Memory screen. No new
Indonesia-specific behaviour was needed; the only
Indonesia-facing surface is the screen copy, which follows
the screen's existing language.
