# Auto-curated knowledge wiki (COW-06)

The agent curates valuable information from conversations into a
topic-organized Markdown wiki with cross-references, plus a
read-only graph view of the same topics.

## What it is

- **Curation pass** (`puspa/memory/wiki.py`): after consolidation
  (or on demand), durable facts, decisions, and procedures worth
  keeping are picked from the signals consolidation already
  produces — consolidation summaries, procedures, active profile
  facts — grouped into topics, and written as one Markdown page
  per topic under `<puspa home>/wiki/<user_id>/`, plus an
  `index.md` and a small `_index.json` manifest.
- **Topics**: the entity or concept shared by the most entries
  becomes the topic, so entries about the same thing land on the
  same page. Deterministic, no model call, no network, no cost.
- **Cross-references**: a page links to the topics it shares an
  entity or concept with, under "Related topics".
- **Sources**: every entry names its source (`fact:<key>`,
  `summary:<id>`, `procedure:<id>`, `episode-event:<id>`), so
  nothing on a page is unattributed.
- **PWA screen** (`/wiki`): topic list, page rendering
  (server-rendered HTML from the stored Markdown), a rebuild
  button, and a read-only graph tab showing the topic's nodes and
  edges from the existing KnowledgeGraph.
- **CLI**: `puspa wiki build --user <id>` runs one curation pass;
  `puspa wiki list --user <id>` lists the built topics.

## What it is not

- **Not the editable second brain (B18).** B18 is the
  user-visible, user-editable graph view — a separate post-beta
  item. The wiki graph here is read-only: it renders the existing
  KnowledgeGraph nodes and edges for a topic, with no edit
  controls and no edit routes.
- **Not a second memory store.** The wiki reads consolidation
  signals only; it never writes episodes, facts, or procedures.
  Rebuilding overwrites the generated files (the pages say so in
  their header); hand edits belong in the second-brain view.
- **Not live.** A conversation curated after the last build only
  appears after the next build. The screen says so honestly.

## Files

- `puspa/memory/wiki.py` — collection, topic assignment, page
  grouping, Markdown rendering, storage, the safe Markdown-to-HTML
  subset renderer.
- `puspa/pwa/wiki_api.py` — `GET /api/wiki/topics`,
  `GET /api/wiki/pages/{slug}`, `POST /api/wiki/build`,
  `GET /api/wiki/graph?topic={slug}` (all login-required).
- `apps/pwa/templates/wiki.html` — Jinja fallback screen (what CI
  serves without `dist/`); `apps/pwa/src/screens/WikiScreen.tsx`
  is the SPA screen.
- `docs/openapi-wiki.yaml` — API contract (split out of the main
  spec, same precedent as the graph contract).
- Tests: `tests/test_memory_wiki.py`, `tests/test_pwa_wiki_api.py`,
  `apps/pwa/src/screens/WikiScreen.test.tsx`.
