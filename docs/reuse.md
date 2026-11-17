# REUSE — Cross-phase reuse log for Puspa

Rules (all phases, Nero's order): study the BIG PATTERNS/IDEAS
from surveyed repos, then REWRITE them as Puspa's own code adapted
to the revision 7 architecture + English domain terms. NEVER copy
code verbatim. NEVER use code licensed GPL, AGPL, SSPL, PolyForm,
CC BY-NC, or a custom non-OSI license. NEVER create a GitHub fork
of any repo. Dependencies only via pip with a compatible license
(MIT/Apache-2.0/BSD) — record the license here per phase.

> Note: the original rule above said "Indonesian domain terms". That
> language policy is superseded by the English-only refactor ordered
> by Nero on 18 Nov 2026: code identifiers, stored domain values, and
> prose are all English now (see `docs/GLOSSARY.md`).

Reference surveys: `~/workspace/match-hunt/repo-reuse-puspa-2026-11-17.md`
(81 repos) and `~/workspace/match-hunt/chassis-tambahan-2026-11-17.md`
(76 entries).

## Phase 0 (done — brief, details in docs/status-phase-0.md Phase 0)

- **nanobot** (HKUDS/nanobot, MIT): agent chassis — used as a
  git-pinned dependency (`nanobot-ai @ git+https://github.com/HKUDS/nanobot@e767174`),
  NOT a fork; wiring only through nanobot's official extension points; ZERO
  lines of nanobot code changed. The local clone is read-only reference.
- New Phase 0 dependencies: psycopg, argon2-cffi, cryptography, pytest
  (+ nanobot's dependencies).

## Phase 1 (Persona + Memory + Onboarding)

No new dependencies (all from the Phase 0 venv). Only PATTERNS were
adapted (rewritten, not copied):

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Sourced memory facts, scoped per user; inferred facts do not enter the profile directly | mem0ai/mem0 (Apache-2.0) | The idea of fact provenance + Proposals as the holding place for inferred facts | `puspa/memory/profile.py`: revision 7 three-valued provenance (`user_stated`, `user_confirmed`, `inferred`), statuses `proposed`/`active`/`rejected`/`replaced`, deterministic UNION merge, conflicts never silently overwritten |
| Temporal facts (validity period); procedures = templates without personal data | getzep/graphiti (Apache-2.0) | The `valid_from`/`valid_until` idea + one active version; procedures without customer numbers | `profile.py` (fact versions) + `puspa/memory/procedural.py` (the `trial`/`verified`/`broken` cycle, revision 7 §3.3) |
| Tiered memory + sleep-time compute (tidying memory while idle) | letta-ai/letta (Apache-2.0) | The idea of consolidating when quiet + summaries pointing to the source event | `puspa/memory/consolidate.py`: DETERMINISTIC extractive summaries (no model, Rp0), promotion of successful patterns to procedural |
| Persona as data (character file: name, traits, speaking style) | elizaOS/eliza (MIT) | The idea of speaking style as data, not a hardcoded prompt | `puspa/persona/persona.py`: style as profile facts + message examples (max 20) + `user_confirmed` corrections that apply immediately; `render_reply` deterministic for now |
| Vector index for memory search | pgvector (PostgreSQL, PostgreSQL license — free to use) | The idea of a vector-similarity index | Schema `episodic.embedding vector(384)` + HNSW index; the `HashEmbedding` embedding written by hand (token hash, Rp0) — a replacement seam prepared for a local/cheap paid embedding model |

**Not used in Phase 1 (recorded so it isn't repeated):**
- mem0 as a LIBRARY (not installed): Puspa needs revision 7 three-valued
  provenance + Proposals + version history — mem0's behaviour does not fit
  directly; only its pattern was used.
- Graphiti as a LIBRARY (needs Neo4j/FalkorDB): too heavy
  for an 11-user MVP; only its temporal pattern was used on top of
  Postgres + pgvector.
- Letta as a SERVER (not used): only its sleep-time
  compute pattern.
- supermemory / cognee / openmemory: read in the survey, no pattern
  taken in Phase 1 (supermemory recorded as an alternative
  if mem0 felt heavy — it didn't happen).
- Zep Community Edition: no longer developed since 2025 (survey
  note) — not used.

## Phase 3 (Dual wedge + Moat)

One new dependency: `playwright==1.65.2` (MIT) — read-only browser
§2.8 (package installed; the Chromium browser revision 1255 download
failed repeatedly from the Playwright CDN during the build — see docs/status-phase-3.md).
Only PATTERNS were adapted (rewritten, not copied):

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Embedded graph: nodes + edges as relational rows, no separate graph server | LadybugDB/ladybug (MIT) — a living Kuzu fork | The idea of a graph embedded inside its own process/host (node = canonical product, edge = listing link) | `puspa/graphs/identity.py`: Postgres schema (§7: canonical_products + listing_links + price_series), tiered deterministic model-key matching, revision 7 link statuses (`automatic`/`user_confirmed`/`user_rejected`) |
| An engine that stores work steps as reusable data | infiniflow/ragflow (Apache-2.0) | The idea of work steps as data (not code rewritten each time) + reuse across agents | `puspa/graphs/service.py`: service procedures as parameterised templates without personal data, the trial→verified→broken cycle, daily canary, versions + rollback — as a MOAT component, not a document product (documents are a banned topic for new ideas; RAGFlow is used as a memory/moat component) |
| Read-only browser for reading pages (not clicking/filling forms) | Playwright (MIT) | The idea of an automated browser as a read-only reader | `puspa/pricing/readers.py`: `PlaywrightReader` (fail-closed if the browser is unavailable) + `HttpReader` (httpx, generic JSON-LD/meta parser) — without copying anyone's selectors |

**Not used in Phase 3 (recorded so it isn't repeated):**
- LadybugDB as a LIBRARY (not installed): Puspa's graph runs on
  Postgres + pgvector (§7) — only its pattern was used.
- RAGFlow as a LIBRARY (not installed): too heavy for an
  11-user MVP; only its pattern of storing steps as data was
  used on top of Postgres + the ledger.
- Langflow (visual workflow builder) → the workflow/module builder UX is
  PHASE 4, not Phase 3 — not built here.
- Pipecat / LiveKit (voice) → the Voice module is NOT in the
  revision 7 definition; recorded as post-MVP, not built.
- LangGraph → NOT used and NOT claimed anywhere in Phase 3
  (correction: the survey sentence "Puspa already uses LangGraph for
  orchestration" is wrong — zero LangGraph imports in the code; orchestration =
  nanobot's AgentLoop + Puspa's own task cycle).
- Shopee Open Platform / official marketplace APIs → the OFFICIAL route
  that complies with the terms of service, but it needs a partner account + approval —
  not part of the MVP; awaiting Nero's decision (decision 15).

## Phase 4 (Custom workflows + modules)

No new dependencies (all from the Phase 0/2/3 venvs). Only PATTERNS were
adapted (rewritten, not copied):

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Visual builder: node canvas → flow → API; flow as executable JSON | langflow-ai/langflow (MIT, 156,719 stars) | The idea of flow as declarative data + a composer built from blocks + preview before activation | `puspa/workflow/composer.py` (deterministic chat composer — library blocks only, not the model), `puspa/workflow/blocks.py` (15 certified blocks + MVP limits), `puspa/workflow/panel.py` (step cards in the Workflow panel, NOT a Langflow UI clone), `puspa/workflow/manifest.py` (derived permission manifest + subset check), `puspa/modules/manifest.py` (5-layer manifest gate) — security Langflow doesn't have (approval gate, permission token, dry run) |
| Trigger-based automation (schedule / threshold / due date) | Home Assistant (Apache-2.0) | The idea of scheduled trigger-based automation + actions that wait for approval | `puspa/workflow/triggers.py` (4 MVP triggers; email/webhook rejected) + `puspa/workflow/runner.py:tick_workflow` (Phase 2 schedule trigger → workflow run; a missed trigger runs once + is reported) |
| Approval + scheduled-action expiry pattern | agno-agi/agno — AgentOS (Apache-2.0, pattern already used in Phase 2) | The idea of an approval queue as a gate before outbound actions | `puspa/workflow/approvals.py`: content-bound approval (hash) + expiry after 24 hours then CANCELLED (not executed late) — a special §2.12 rule, not an agno default |

**Not used in Phase 4 (recorded so it isn't repeated):**
- Langflow as a LIBRARY (not installed): Puspa's builder needs
  certified blocks + a derived permission manifest + an approval gate —
  Langflow's behaviour (a builder for developers, no approval gate)
  does not fit directly; only its pattern was used.
- n8n (Sustainable Use License — fair code, NOT OSI, on the
  survey's trap list): NOT used, its pattern not adapted either.
- Flowise (archived 13 August 2026, end of life 31 August 2026):
  NOT used — already dead; the lesson (don't depend on a
  single builder vendor) is what was used: Puspa's manifest is its own format.
- Pipecat / LiveKit (voice) → post-MVP, deliberately not built.
- LangGraph → NOT used and NOT claimed anywhere in Phase 4
  (zero LangGraph imports in the code; orchestration = Puspa's task cycle +
  its own workflow runner).

## Glossary of new Phase 4 terms (not from revision 7)

- `executor_fixture` — internal TECHNICAL name (test-data executor:
  bill checks from Phase 3 TEST_DATA, live prices held by the ToS gate).
  Standard technical terms ("fixture", "executor") — not a new domain term.
- `tick_workflow` — internal TECHNICAL name (one scheduler round for
  workflows, §2.14). "Tick" is a standard technical term.
- `workflow_runs` — the workflow-runs table (§2.12: "each workflow run is
  an ordinary task"). A technical table name, not a domain term.
- `workflow_approvals` — the workflow approval-queue table (§2.12:
  "outbound actions enter the approval queue"). A technical table name.
- `permission_manifest` — from revision 7 §2.12 ("derived permission manifest").
- No new domain terms were introduced into the definition.

## Glossary of new Phase 3 terms (not from revision 7)

- `SitusTest` — internal TECHNICAL name (local bill test site,
  `puspa/billing/fixture.py`). A standard technical term ("fixture") —
  not a new domain term.
- `reread` — the Verifier callable (§2.5: "rereading the source").
  A technical name, not a domain term.
- `pola:<name>` — service procedures for subagent patterns (§3.3:
  "Subagent patterns are also stored here"). A technical prefix for
  telling patterns apart from service procedures; not a new domain term.
- No new domain terms were introduced into the definition.

## Phase 2 (Team runtime + PWA + Connectors + Scheduler)

One new dependency: `fastapi==0.163.1` (MIT) — PWA. Only
PATTERNS were adapted (rewritten, not copied):

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Tiered model router (pick provider/model per role, BYO key, fallback) | LiteLLM / BerriAI/litellm (MIT) | The idea of a router + fallback across providers + per-model price as configuration | `puspa/router/router.py`: revision 7 roles (planner/presenter/reader/searcher/verifier/vision), a different-family rule for the Verifier, Rp/1,000-token rates; built ON TOP of the Phase 0 BYOK factory (`demo/byok_provider.py`), not a new client |
| Durable execution, approval, scheduling, observability | agno-agi/agno — AgentOS (Apache-2.0) | The idea of a task as a durable unit resumable from the log + an approval queue + the scheduler as a task trigger | `puspa/runtime/tasks.py` (5-stage cycle, SKIP LOCKED queue), `puspa/scheduler/scheduler.py` (triggers + idempotency + missed), projections in `puspa/evidence/projections.py` |
| Sandbox hardening (fail-closed, network allowlist) | OpenHands (MIT) | The idea of a sandbox that stops when unavailable + a domain allowlist | Phase 0 reused (`puspa/sandbox/allowlist.py`); Phase 2 enforces it in connectors (content read only via `QuarantinedReader` + domain allowlist) — no new sandbox code |
| Agent UX: chat + task list + approval queue | opencode (MIT) | The idea of chat/task/approval screens as one agent workflow | `puspa/pwa/app.py` + `apps/pwa/templates/` (7 revision 7 screens, SSE from ledger projections, not WebSocket) |
| Connectors + scheduled automation (integrations as data, not code) | Home Assistant (Apache-2.0) | The idea of per-entity connectors with minimal scope + scheduled trigger-based automation | `puspa/connectors/google.py` (Gmail/Drive/Calendar on top of Phase 1 OAuth) + `puspa/connectors/grants.py` (per-agent grants) + `puspa/scheduler/` |
| Cloud pattern: RLS + auth + realtime | Supabase (Apache-2.0) | The idea of per-user row isolation + change subscriptions | Phase 2 RLS (`infra/schema_runtime.sql`, `current_setting('app.current_user_id')` policies) + SSE (`/sse`) instead of realtime — Supabase as a LIBRARY is not used |

**Not used in Phase 2 (recorded so it isn't repeated):**
- LiteLLM as a LIBRARY (not installed): Puspa's router needs
  revision 7 roles + the Verifier family rule + Rupiah rates —
  LiteLLM's behaviour does not fit directly; only its pattern was used.
- agno/AgentOS as a LIBRARY (not installed): too heavy
  for an 11-user MVP; only its durable + approval + scheduling patterns
  were used on top of Postgres + the ledger.
- Langflow (visual workflow builder) → the workflow/module builder UX is
  PHASE 4, not Phase 2 — not built here.
- Pipecat / LiveKit (voice) → the Voice module is NOT in the
  revision 7 definition; recorded as post-MVP, not built.
- Supabase as a LIBRARY (not used): only its pattern
  (RLS + SSE).
- OpenHands as a LIBRARY (not used): only its
  sandbox-hardening pattern; the Phase 0 sandbox is reused.

## Glossary of new Phase 2 terms (not from revision 7)

- `triggers` / `trigger_runs` — from revision 7 §2.14 (trigger table +
  idempotency key). Technical table names, not domain terms.
- `CassettePlayer` — internal TECHNICAL name (cassette player, §2.11).
  Standard technical terms ("cassette" = test cassette) — not a
  new domain term.
- `BudgetTracker` — internal TECHNICAL name (budget tracker §2.4).
- `SubagentPool` — internal TECHNICAL name (subagent pool §2.2).
- `EvidenceRecord` — internal TECHNICAL name (evidence record §2.5).
- No new domain terms were introduced into the definition.

## Glossary of new Phase 1 terms (not from revision 7)

Per Nero's order 21:53 WIB: don't create new terms that aren't in
revision 7 and aren't standard technical terms; if one is needed, record
it here.

- `HashEmbedding` — internal TECHNICAL name (not a domain term):
  deterministic embedding from token hashes. Standard technical terms
  ("hash", "embedding") — not a new domain term.
- `StubNotifier` — internal TECHNICAL name: a stand-in notifier
  until Web Push arrives in Phase 2. Not a domain term.
- `Proposals` — from revision 7 §2.7 (inferred facts "appear in Proposals").
- `style_examples` — table for persona message examples (§2.16: "attach
  10 to 20 example messages"). A technical table name, not a domain term.
- `slice_grants` — audit table for slice grants (§2.7: "slice
  grants are recorded in the ledger"). A technical table name.
- `summaries` — consolidation output (§2.7: "job summarising old
  episodic [memory]"). A common word.
- No new domain terms were introduced into the definition.
