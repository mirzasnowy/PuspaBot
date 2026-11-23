"""Size budgets for the code-line ratchet (``scripts/code_size.py``).

The tables are what changes most, so they live apart from the check.
Each entry is one file's budget: the code lines it may reach, and one
short ``why`` (at most ``MAX_WHY`` characters, today's reason only;
what the file was is in git). Raising a budget is allowed, but the
``why`` must be rewritten to say what the lines are and why they
belong in this file.

Code lines are counted by ``code_size.count_code_lines``: blank lines
and comment / docstring lines do not count. Budgets were recorded
22 Nov 2026 against public main 768cd5ed.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The longest a budget's ``why`` may be: one reason, today's. A
#: reason that gained a clause at every raise read as a changelog,
#: and a raise nobody had to argue for again was a raise nobody
#: questioned.
MAX_WHY = 200

#: How far above its file a budget may sit: a few lines for the next
#: small change, not room to grow back into.
HEADROOM = 10


@dataclass(frozen=True)
class Budget:
    """One file's budget: the code lines it may reach, and why."""

    lines: int
    why: str


#: Source files over ``MAX_CODE_LINES``. A file over the cap without
#: an entry here fails the ratchet; an entry here may only shrink.
# One entry per line: this is a data table, not code to wrap.
# fmt: off
BUDGET: dict[str, Budget] = {
    "docs/openapi-pwa.yaml": Budget(3123, "main PWA API contract: every route plus $ref stubs; +61 chat/auth routes (COW-10); +17 GET /api/desktop/config (M8-fix)"),
    "puspa/router/provider_login.py": Budget(1692, "subscription-provider login for Grok, Copilot and Codex in one place: OAuth device flow, token storage and the login CLI; a new provider is a new login handler"),
    "apps/pwa/src/api/client.ts": Budget(1376, "the typed fetch wrapper for every PWA screen plus its type aliases; split per feature area when it grows again, keeping the aliases here"),
    "puspa/memory/importer.py": Budget(1289, "memory import from Kosmos archives, exports and plain files into the durable layers; a new source format gets its own importer module"),
    "puspa/computer/hands.py": Budget(1265, "desktop-hands core: snapshot, click, verify through the accessibility tree; backends and input live in their own modules, this is the loop only"),
    "apps/pwa/src/screens/SettingsScreen.tsx": Budget(1212, "the Settings screen: model choice, provider keys, quota bars, persona and telemetry settings in one screen; a new settings area is its own section component"),
    "docs/reuse.md": Budget(1110, "the reuse audit: license plus scope notes per studied repo; grows by one entry per studied repo, old entries are archived, never deleted"),
    "puspa/world/model.py": Budget(1039, "the world model: prediction, planning and belief update in one module; the data holders already moved to beliefs.py and the causal path to causal.py"),
    "puspa/turns/loop.py": Budget(944, "the turn loop: runner, model calls, tool calls and per-turn state in one place; +2 for the hold check, whose db-error alias needs its own except clause; the model-call step is the seam"),
    "puspa/pwa/app.py": Budget(1035, "PWA app factory: screens, bootstrap, API routes; +91 Google callback, desktop session exchange, chat read (COW-10); +14 HTML OAuth error pages + GET /api/desktop/config (M8-fix)"),
    "puspa/cli.py": Budget(821, "the console entry point: `puspa` and `puspa web` plus the command groups behind them; a new top-level command is its own module"),
    "NOTICE.md": Budget(812, "license attributions for reused work, one entry per work; it grows only when new reused work lands"),
    "puspa/computer/cli.py": Budget(810, "`puspa computer` command group: snapshot, click, verify and the visual-fallback commands; a new command is its own handler module"),
    "puspa/policy/agent_scan.py": Budget(804, "the community skill scanner for the CI gate: reads a skill directory and reports policy drift; a new check is a new scanner rule, not a bigger file"),
    "REUSE.md": Budget(795, "the reuse rules for studying other repos: what may be taken and how it is attributed; grows by one rule per decision"),
    "docs/split-public-private.md": Budget(765, "the public-private split design: the demarcation rule plus the file map; the live boundary is enforced by the ratchet, this record stays as written"),
    "puspa/memory/commit_turn.py": Budget(744, "write-through turn commit: ledger entry plus durable memory in one transaction; the per-layer writers are the seam if it grows again"),
    "puspa/workflow/runner.py": Budget(742, "the workflow runner: every workflow step runs as a tracked task with evidence rendering; a new step kind is its own step module"),
    "puspa/computer/mcp.py": Budget(720, "the MCP tool definitions for desktop hands: every hands capability as one tool; a new capability is a new tool definition"),
    "puspa/memory/learning_loop.py": Budget(707, "the learning loop: experience recording, feedback, quarantine, holdout and skill activation in one module; the quarantine store is the seam if it grows again"),
    "puspa/pwa/graph_api.py": Budget(703, "the second-brain graph API: read plus edit over the knowledge graph; the graph queries are the seam if it grows again"),
    "puspa/router/translate.py": Budget(696, "format translation between the providers the router serves: one translator per provider pair; a new provider is a new translator"),
    "puspa/policy/approvals.py": Budget(694, "the approval taxonomy plus the narrowed always-bindings and the fatigue tracker; the fatigue tracker is the seam if it grows again"),
    "puspa/retrieval/embedding_health.py": Budget(692, "visible embedding degradation: health checks, banners and the operator status fields; a new embedder check is its own probe"),
    "puspa/world/agents.py": Budget(673, "other-agent goal models: the types, the store, the keyword inferrer and the mixin in one place; the inferrer is the seam if it grows again"),
    "puspa/memory/ontology_evolve.py": Budget(669, "automatic ontology repair: contradiction fixes plus proposal review over the profile facts; the repair rules are the seam if they grow again"),
    "puspa/pwa/rooms_api.py": Budget(666, "the shared-rooms HTTP layer: rooms, board, memory, invite and job routes; a new room surface is its own API module"),
    "puspa/router/pool.py": Budget(663, "the multi-account provider pool: rotation, quota windows, preflight and failover; a new strategy is a new function, not a bigger module"),
    "puspa/memory/durable.py": Budget(638, "the durable MEMORY.md store: the human-readable long-term layer over the five memory layers; the per-layer writers are the seam"),
    "puspa/pwa/chat_widgets.py": Budget(638, "the five interactive chat widgets as declarative JSON payloads; a new widget kind is its own renderer, not a bigger module"),
    "puspa/pwa/replay.py": Budget(406, "task replay: the event timeline plus the last-type subquery, rewritten as a correlated subquery because SQLite has no array subscript"),
    "puspa/marketplace/skill_creator.py": Budget(626, "create skills by talking: conversation plus authoring plus validation of a new skill; the validators are the seam if they grow again"),
    "puspa/turns/messages.py": Budget(612, "one internal message format for all three provider API modes; a new API mode is a new adapter, not a bigger module"),
    "puspa/marketplace/discovery.py": Budget(587, "skill discovery through the skills.sh registry: search, ranking and install preview; the registry client is the seam if it grows again"),
    "puspa/computer/web.py": Budget(586, "the web bridge for desktop hands: route web targets to browser automation; a new browser backend is its own module"),
    "docs/openapi-memory.yaml": Budget(583, "the memory API contract: every memory route plus its schemas; a new memory surface gets its own spec file"),
    "docs/openapi-graph.yaml": Budget(582, "the second-brain graph API contract: graph read plus edit routes and schemas; a new graph surface gets its own spec file"),
    "puspa/db.py": Budget(416, "the database factory: SQLite wrapper plus psycopg passthrough, SQL translator wiring, type converters and param adaptation; the translator itself lives in db_translate.py"),
    "puspa/db_translate.py": Budget(531, "the Postgres-to-SQLite SQL translator: placeholders, casts, now(), intervals, ANY, advisory locks, TRUNCATE, DDL drops; one rewrite rule per function, a new dialect gap is a new rule"),
    "puspa/policy/gateway.py": Budget(596, "the fail-closed action gateway: CEL-style rules as the single action boundary; +9 for the SQLite watcher branch, which sleeps instead of LISTEN/NOTIFY; a new rule family is its own module"),
    "puspa/retrieval/code_graph.py": Budget(578, "the Python code graph over the knowledge graph: parse source files into functions, classes, imports and calls; a new language is a new parser"),
    "apps/pwa/src/screens/RoomsScreen.tsx": Budget(577, "the Rooms screen: room list, board, tasks and delegation in one screen; the board is the seam if it grows again"),
    "puspa/governor/compiler.py": Budget(577, "the skill compiler: generalise repeated deep runs into versioned skills; a new generalisation pass is its own module"),
    "puspa/evidence/bfcl_tasks.py": Budget(557, "the BFCL-style held-out task set for the eval harness: task definitions plus fixtures; a new task family is its own file"),
    "docs/openapi-mockup.yaml": Budget(547, "the mockup editor API contract: mockup paths plus schemas, split out of the main spec; a new editor surface gets its own spec file"),
    "docs/openapi-chat-widgets.yaml": Budget(541, "the chat-widgets API contract: widget paths plus schemas, split out of the main spec; a new widget kind extends the schema"),
    "puspa/runtime/hierarchy.py": Budget(540, "the hierarchical multi-agent runtime: agent hierarchy, delegation and supervision in one module; the supervisor is the seam if it grows again"),
    "puspa/policy/conflicts.py": Budget(539, "the anti-manipulation rule: what the agent may optimise for, checked against the user's real goals; a new check is a new rule"),
    "puspa/self/initiative.py": Budget(536, "self-directed initiative: derive sub-goals from standing goals and open loops, act, report; the goal derivation is the seam if it grows again"),
    "apps/pwa/src/screens/MockupScreen.tsx": Budget(532, "the mockup editor screen: canvas, components and asset handling in one screen; the canvas is the seam if it grows again"),
    "puspa/pwa/memory_api.py": Budget(526, "the Memory screen read API: stored memory plus data-subject rights actions; a new memory surface is its own API module"),
    "puspa/memory/memory_export.py": Budget(524, "portable memory export: one archive covering every memory layer; a new layer is a new exporter, not a bigger module"),
    "puspa/guardrails/rails.py": Budget(521, "programmable rails between the app and the model: input, output and retrieval rails; a new rail is its own module"),
    "puspa/evidence/bfcl_checker.py": Budget(517, "the AST-based tool-call checker for BFCL-style eval tasks; a new check kind is a new checker, not a bigger module"),
    "docs/openapi-voice.yaml": Budget(514, "the voice plus share-target API contract: voice paths plus schemas, split out of the main spec; a new voice surface gets its own spec file"),
    "puspa/scheduler/scheduler.py": Budget(525, "the scheduler: trigger table plus the process that fires jobs through the task queue; +7 for the SQLite BEGIN IMMEDIATE guard and the general interval rewrite it relies on"),
    "puspa/marketplace/hub.py": Budget(513, "the skill marketplace hub: browse, search and one-click install over the local registry; the hosted listings live in the private repo"),
    "docs/openapi-morning.yaml": Budget(511, "the morning summary API contract: morning paths plus schemas, split out of the main spec; a new morning surface gets its own spec file"),
    "puspa/drafting/drafting.py": Budget(511, "co-drafting: draft messages, documents and emails in the user's voice from memory facts; a new draft kind is its own module"),
    "puspa/harness/loop.py": Budget(502, "the coding harness: a real engineering loop (detect, fix, prove) for the brain; a new loop phase is its own module"),
    "puspa/sessions/store.py": Budget(491, "the session store: one SQLite database per profile, the only class that touches state.db; the exporters are the seam if they grow again"),
    "puspa/evidence/proof.py": Budget(485, "the trust surface: ledger, hash chain, approval receipts and audit trail shown to the user; a new proof kind is its own module"),
    "puspa/self/repair_loop.py": Budget(485, "the repair loop: detect, diagnose, fix, prove for every failure class; a new failure class is a new repair, not a bigger module"),
    "puspa/tools/browser.py": Budget(483, "browser and web tools for the agent: open, click and fetch as registry tools; a new browser capability is a new tool"),
    "puspa/router/compression.py": Budget(478, "lossless tool-result compression before results reach the model; a new compression pass is its own function"),
    "puspa/sentinel/detectors.py": Budget(476, "sentinel detectors: six pure detectors over ledger events plus the outcome watcher; a new detector is a new function"),
    "puspa/world/store.py": Budget(483, "Postgres persistence for the world model: one row per agent, JSON snapshots; +4 for the DISTINCT ON rewrite as a window function, which runs on both backends; a new snapshot kind is its own writer"),
    "puspa/memory/episodic.py": Budget(435, "episodic memory: record plus vector search; +30 for the SQLite branch, which scores the newest candidates with brute-force cosine in Python instead of pgvector"),
    "puspa/computer/backend.py": Budget(469, "accessibility backends for desktop hands: one backend per OS behind the same seam; a new OS is a new backend module"),
    "puspa/pwa/capability_routing.py": Budget(469, "per-capability model routing: each capability routes to its own provider; a new capability is a new routing entry"),
    "puspa/check_before_buy/answer.py": Budget(465, "one structured, evidence-backed answer for Check Before You Buy: fair price plus verdict from recorded observations; a new evidence rule is its own function"),
    "apps/pwa/src/components/ChatWidget.tsx": Budget(464, "the ChatWidget component: the five interactive widgets rendered inside a chat message; a new widget kind is its own component"),
    "puspa/billing/ports.py": Budget(462, "the local billing ports: budget, insights and entitlements as the public side of the billing boundary; the hosted ledger lives in the private repo"),
    "puspa/mind/felt_state.py": Budget(454, "felt state: the regulated viability variables plus appraisal into them; the appraisal rules are the seam if they grow again"),
    "puspa/migrations/versions/0010_rooms.sql": Budget(451, "migration 0010: rooms, group chat, tasks and splits in one schema; migrations are frozen, never edited or split"),
    "puspa/memory/longterm.py": Budget(450, "long-term consolidation: what rises from episodic to long-term and how cold items lose priority; the consolidation policy is the seam"),
    "puspa/computer/input.py": Budget(448, "full input for desktop hands: typing, shortcuts, scroll, drag and clipboard under the same permission discipline; a new input kind is its own handler"),
    "puspa/scheduler/cron.py": Budget(446, "the general cron scheduler: four schedule formats over one job model; a new schedule format is its own parser"),
    "puspa/evidence/agentbench_tasks.py": Budget(438, "the AgentBench-style held-out task set for the eval harness: task definitions plus fixtures; a new task family is its own file"),
    "puspa/scheduler/curriculum.py": Budget(431, "the curriculum runner: idle-time learning tasks proposed by the learning loop; a new task source is its own module"),
    "puspa/memory/wiki.py": Budget(424, "the auto-curated knowledge wiki: topics plus cross-references plus a read-only graph view; the curators are the seam if they grow again"),
    "puspa/ledger/ledger.py": Budget(436, "the Buku Kerja: the append-only hash-chained log every event flows through; the SQLite branch (PRAGMA catalog check, BEGIN IMMEDIATE, timestamp normalisation) lives here"),
    "puspa/sessions/export.py": Budget(419, "conversation export and import: one session plus every message in order; a new export format is its own writer"),
    "docs/openapi-approval-overlay.yaml": Budget(417, "the approval-overlay API contract: overlay paths plus schemas, split out of the main spec; a new overlay surface gets its own spec file"),
    "puspa/memory/facts.py": Budget(416, "versioned profile facts: the profile_facts table widened with status and supersession; the fact lifecycle is the seam"),
    "puspa/router/fallback.py": Budget(413, "smart 3-tier fallback with quota-aware routing: chains providers into an ordered combo; a new tier is a new strategy"),
    "puspa/memory/skill_repair.py": Budget(406, "skill repair plus procedure change as one learning loop: both read and write the same experience; the repair rules are the seam"),
    "puspa/router/complete.py": Budget(404, "live completion through the router's cheapest model for a role: the first live caller of the router; a new role is a new call site"),
    "puspa/mockup/doc.py": Budget(401, "the design document for the mockup editor: the declarative JSON document plus its validation; a new component kind extends the whitelist"),
}
# fmt: on

#: Test files over ``MAX_TEST_CODE_LINES``. Empty today: the largest
#: test file is 926 code lines (``tests/test_router_oauth_translate.py``),
#: under the 1000-line test cap. Tests used to be exempt, and six grew
#: past a thousand total lines; a test file that size is one nobody
#: reads before adding to it, so new cases go in a file of their own.
TEST_BUDGET: dict[str, Budget] = {}

#: Files split on their seams that may not be budgeted again. Each is
#: a composition of parts now, and a budget would let it grow back
#: into the one file it was: past the cap, the new part goes in a
#: file of its own. The value is the split it came from.
NEVER_BUDGETED: dict[str, str] = {
    "puspa/world/beliefs.py": "belief data holders split out of model.py (K4 ratchet fix)",
    "puspa/world/causal.py": "causal read path split out of model.py (K4 ratchet fix)",
    "docs/openapi-pages.yaml": "HTML page routes split out of openapi-pwa.yaml (P-04 ratchet fix)",
    "docs/openapi-controls.yaml": "control paths split out of openapi-pwa.yaml (P-02 ratchet fix)",
    "docs/openapi-receipts.yaml": "receipt paths split out of openapi-pwa.yaml (P-04 ratchet fix)",
    "docs/openapi-live-view.yaml": "live-view paths split out of openapi-pwa.yaml (P-09 ratchet fix)",
    "docs/openapi-replay.yaml": "replay paths split out of openapi-pwa.yaml (P-10 ratchet fix)",
    "docs/openapi-artifacts.yaml": "artifact paths split out of openapi-pwa.yaml",
    "docs/openapi-personas.yaml": "personas paths split out of openapi-pwa.yaml",
    "docs/openapi-telemetry.yaml": "telemetry path split out of openapi-pwa.yaml",
    "docs/openapi-gallery.yaml": "gallery paths split out of openapi-pwa.yaml",
    "docs/openapi-missions.yaml": "mission paths split out of openapi-pwa.yaml; webhook surface split out of this file",
    "docs/openapi-missions-webhook.yaml": "webhook wake plus token rotation split out of openapi-missions.yaml",
    "docs/openapi-chat-cards.yaml": "chat-card paths split out of openapi-pwa.yaml (P-12 ratchet fix)",
}
