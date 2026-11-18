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

## Brain phase

One new dependency: `fastembed==0.9.1` (MIT) - local embedding
model for the learning loop and memory retrieval. It is OPTIONAL:
`puspa/memory/embeddings.py` loads it lazily and falls back to the
built-in `HashEmbedding` when fastembed or the model is unavailable.
The model is BAAI/bge-small-en-v1.5, dimension 384, the same as
`EMBEDDING_DIM`, so it is a drop-in replacement with no schema
migration. fastembed 0.9.1 requires `loguru<0.8`, so `loguru` was
downgraded from 0.8.0 to `0.7.3` in `requirements.txt` (which still
satisfies nanobot-ai's `loguru>=0.7.3`). `pip check` after the
change reports only the pre-existing conflict `sse-starlette 3.7.0
requires starlette>=0.49.1, but starlette 0.46.2 is installed`.
The MCP SDK (`mcp==1.30.0`, MIT) was already installed as a nanobot
dependency; nothing was installed for the planner workstream.
Everything else in this phase adapts only PATTERNS (rewritten, not
copied). Full per-workstream detail is in the tables below.

### World model (`puspa/world/`)

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Memory stream plus reflection into higher level inferences plus plans retrieved and revised | joonspk-research/generative_agents (MIT) | The shape only: observations feed a structured model, a missed prediction revises the model, plans are simulated against the model before acting | `puspa/world/model.py`: entities plus relations plus learned price and bill beliefs as plain in-memory data; `predict` before acting; `update` corrects on a miss |
| Automatic curriculum plus self-verification plus a growing skill library | MineDojo/Voyager (MIT) | The self-verification shape only: every prediction is checked against the observation that follows it, and a miss corrects the model and is reported onward | `puspa/world/model.py`: prediction scoring, misprediction records, trend adoption only after a repeated daily move; `puspa/world/simulator.py`: deterministic bill plus price watch world as the verification ground |
| Learning loop as the consumer of mispredictions | NousResearch/hermes-agent (MIT) | The idea that a miss is learning material, not just an error log | The seam in `puspa/world/model.py`: lazy import of `record_misprediction` from `puspa/memory/learning_loop` inside `_report_misprediction`, record dict with keys `task`, `predicted`, `observed`, `error`, `context`; best effort, never raises, model works without the loop module |

Evidence (deterministic simulated world, 3 episodes, 17 scored
predictions each): episode 1 accuracy 0.647 (11 of 17, flat-price
predictions miss both drifting stores until the daily trend is
confirmed after 3 repeated moves); episode 2 accuracy 1.000 (17 of
17, learned daily drifts predict every price exactly); episode 3
accuracy 1.000 (17 of 17, learning persists across episodes).
Test: `test_prediction_accuracy_improves_across_episodes` in
`tests/test_world_model.py` (13 tests).

### Coding harness (`puspa/harness/`)

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Coding agent loop: write code, run tests, read the failure, fix it, repeat until green or a limit | anomalyco/opencode (MIT) | The loop shape itself: attempt, execute, grade by test exit code, feed the failure back to the next attempt | `puspa/harness/loop.py`: `CodingHarness.run()`, `AttemptRecord` (code plus its test result per attempt), `default_verdict()` (grades the exit code only, never the agent's words) |
| Per-tool permission model: allow / ask / deny per tool, not one blanket allow per turn | anomalyco/opencode (MIT) | The three levels and the rule that a child token may only narrow a permission, never widen it | `puspa/policy/tokens.py`: optional `tool_permissions` map on the token payload, `tool_permission()`, `is_subset()` extended with the deny < ask < allow ordering; `puspa/policy/gate.py`: `Gate.authorize_tool()` |
| Per-tool permission model and approval before acting | anthropics/claude-code (PROPRIETARY, DO NOT USE ITS CODE) | The idea only: permissions per tool, and a hard approval step before a consequential action (here: deploy) | Same Puspa code as the opencode row above, plus kind `"deploy"` in `puspa/policy/gate.py` and `CodingHarness.request_deploy()` / `deploy()` (approval, then exactly-once execution through the gate) |
| Harness shape: fixed FAIL_TO_PASS tests decide pass/fail; a task is an issue plus a repo snapshot plus its tests | SWE-bench/SWE-bench (MIT) | The task shape: fixed acceptance tests carried by the task, graded by running them | `puspa/harness/loop.py`: `CodingTask` (description plus fixed `test_code`), pass defined as the generated program (implementation plus those tests) exiting 0; no PASS_TO_PASS set yet, that is future work for the self-develop module evaluation |
| Self-verification: generated code is trusted only after it is actually run | MineDojo/Voyager (MIT, dormant research archive, DO NOT USE AS A DEPENDENCY) | The self-verification step only: test generated code by running it in the sandbox before trusting it | `puspa/harness/executor.py`: `SandboxExecutor` runs every attempt through `puspa/sandbox/runner.py`; the Minecraft code, the curriculum, and the skill library are NOT part of this workstream |
| Readable handoffs between steps: each step leaves a record the next step can read | OpenBMB/ChatDev (Apache-2.0) | The handoff idea: every attempt leaves its code and its test result behind, so the next attempt (and a human reader) can see exactly what failed | `puspa/harness/loop.py`: `AttemptRecord` plus the in-memory event log (`harness_started`, `attempt_started`, `attempt_finished`, `harness_passed`, `harness_failed`, `budget_exhausted`, `sandbox_unavailable`, `deploy_requested`) |
| Fail-closed sandbox execution: no host fallback, ever | puspa/sandbox/runner.py (Puspa's own, Phase 0) | The existing fail-closed contract: `SandboxUnavailable` means nothing runs anywhere | `puspa/harness/executor.py` and `loop.py`: `SandboxUnavailable` ends the run with status `sandbox_unavailable`; `production=True` rejects any executor that is not a `SandboxExecutor`; `create_production_harness()` can only build a sandbox executor |

Environment note (honest limitation): on this dev VM the real
Docker sandbox cannot start (the kernel rejects the `setns`
syscall, so `runc` fails and `Sandbox.check_available()` returns
False). The production path is therefore fail-closed here:
`SandboxExecutor.run()` raises `SandboxUnavailable` and no
generated code runs anywhere. The end-to-end loop test
(`test_e2e_loop_to_green`) runs on the test-only
`LocalTestExecutor`, which executes the generated code in a temp
directory on the host with a scrubbed environment. That executor
is test-only by construction (rejected when `production=True`,
unreachable from `create_production_harness()`), and it is NOT a
production bypass: the production path stays sandbox-only and
fail-closed. A real end-to-end sandbox run still needs a host with
working Docker (for example the Contabo VPS), and has not been done
yet.

### Learning loop + memory hardening (`puspa/memory/`)

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Skills created and repaired automatically; persistent user model; learning loop | NousResearch/hermes-agent (MIT) | The idea of a learning loop that drafts skills from successful tasks | `puspa/memory/learning_loop.py`: curriculum, experience log, correction table, skill library; every learned skill stays inactive until the user approves it; learning never overwrites a manual user edit |
| Automatic curriculum, self-verification, skill library of tested executable code indexed by embeddings | MineDojo/Voyager (MIT, dormant research archive) | The shape only: curriculum proposes the next task, verification tests generated code, successful code is stored with an embedding key | `learning_loop.py` (`propose_next_task`, `verify_skill`, `find_reusable_skill`) plus draft persistence in `puspa/memory/procedural.py` under the `learned_draft:` service prefix; the Minecraft code is not reusable and was not used |
| Self-improvement from experience | letta-ai/letta-code (Apache-2.0) | The idea of improving procedures from experience | Decay in `procedural.py` (`apply_decay`, `decay_stale_procedures`); contradiction handling (`detect_contradiction`, `propose_conflicting_procedure`, `resolve_contradiction`). A demotion rule (one failure drops a verified procedure to trial) was tried in this phase and removed during integration because it broke the revision 7 canary contract (see `procedural.py`). |
| Reflection turns episodes into higher-level inferences | joonspk-research/generative_agents (MIT) | The reflection step as an idea: episodes become inferences | `puspa/memory/consolidate.py` (`reflect_episodes`, `reflect_and_propose`): deterministic, keyword-based inferences routed through Proposals in `puspa/memory/profile.py`, never written to the profile silently; each inference carries its evidence event ids and needs at least 2 |
| Dual-level retrieval: specific entities plus abstract concepts, over a graph plus vectors | HKUDS/LightRAG (MIT) | The dual-level retrieval idea (entity level + concept level + vector similarity) | `puspa/memory/episodic.py` (`extract_entities`, `extract_concepts`, `dual_level_search`, `search_episodes_dual`) and `puspa/graphs/identity.py` (`rank_products`, `search_products_dual`) on top of the existing Postgres + pgvector store; the library was not installed |
| Temporal facts; procedures as templates without personal data | getzep/graphiti (Apache-2.0) | Already the pattern behind `profile.py` and `procedural.py` | Decay reuses the existing trial/verified/broken statuses, so no schema change was needed |
| Memory extraction with provenance; inferred facts held for confirmation | mem0ai/mem0 (Apache-2.0) | Already the pattern behind `profile.py` Proposals | Reflection proposals reuse `profile.add_fact` with source `inferred` (status `proposed`) |

Approval gap, recorded honestly: `puspa/policy/gate.py` has no
kind for skill activation (its kinds cover outbound actions only)
and that file must not be modified by this phase. Skill activation
therefore reuses an approved Gate request record:
`LearningLoop.activate_skill` verifies through the Gate's stored
requests that an approval for the user exists with status
`approved` or `executed`. The caller obtains that approval through
the Gate with the activation described in the request content. A
dedicated Gate kind for skill activation would need a change owned
by the gate workstream.

Database coverage: the DB-backed wrappers (`decay_stale_procedures`,
`propose_conflicting_procedure`, `search_episodes_dual`,
`search_products_dual`, `consolidate_idle` with `reflect=True`) are
covered against Postgres by `tests/test_memory_brain_db.py` (11
tests, added during integration).

### Planner + evaluation + skills/hooks (`puspa/runtime/planner.py`, `puspa/evidence/eval_harness.py`, `puspa/memory/optimizer.py`)

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Goal to plan to tool loop with replanning on failure | FoundationAgents/OpenManus (MIT) and the classic AutoGPT loop (MIT core) | The loop shape: decompose, execute step by step, feed the failure back into a revised plan, with a hard cap on replans | `puspa/runtime/planner.py`: keyword decomposition into `Step` objects, `Planner.run()` driving the existing `TaskRunner`, retry-then-drop replan strategy, `max_replans` default 2 |
| Stateful orchestration with checkpoints and resume | langchain-ai/langgraph (MIT) | The idea of a resumable, checkpointed run only | Nothing from the library: the no-LangGraph decision stands (zero LangGraph imports). Checkpointing here is the append-only ledger plus the replan record, not a graph runtime |
| Task, solver, scorer structure; multi-turn tool use; sandboxed execution; scoring from evidence, not words | UKGovernmentBEIS/inspect_ai (MIT) | The task/solver/scorer shape and the rule that the scorer checks a record, not the agent's words | `puspa/evidence/eval_harness.py`: `EvalTask` (versioned, pinned cassette, expected state, policy), `cassette_solver` (replays recordings), `score_state` (final-state comparison with per-key details) |
| Simulated user plus domain policy plus final database state decides success | sierra-research/tau-bench (MIT) | The simulated-user shape and the final-state check; the policy the agent must not break | `puspa/evidence/eval_harness.py`: `cassette_solver` accepts a simulated-user callable, `derive_expected_state` builds the goal state from the cassette by code, the policy list travels on every task |
| Versioned task configs with a pinned prompt and scorer, so regression runs are reproducible | EleutherAI/lm-evaluation-harness (MIT) | The versioned-config shape only | `puspa/evidence/eval_harness.py`: `HARNESS_VERSION = "1.0.0"` pinned on every report, one `EvalTask` per reference cassette, `EvalReport.summary()` computed by code |
| Programs, not prompts: declarative modules tuned by an optimiser against a metric on a fixed dataset | stanfordnlp/dspy (MIT) | The program-plus-optimiser shape and the rule that the optimiser tunes prompts, never security rules | `puspa/memory/optimizer.py`: `PromptProgram` (planner/reader/checker templates plus tunable knobs), `Optimizer.optimize()` scoring deterministic variants against the 10 cassettes, `plan_coverage` as the metric, hard refusal of any gate target |
| Type-safe structured output: numbers filled by code from evidence, never typed by the model | pydantic/pydantic-ai (MIT) | The type-safe tool and structured-output pattern | `puspa/chassis/tools.py`: `McpToolSpec` as a pydantic model validated at registration; `puspa/evidence/eval_harness.py`: `render_report` fills markers through the existing `puspa/evidence/renderer.py`, and all report numbers are computed by code |
| Official MCP client: the standard way tools are exposed to agents | modelcontextprotocol/python-sdk (MIT) | The client shape: list tools, call a tool with typed arguments, over stdio | `puspa/chassis/tools.py`: `McpClientWrapper` plus `StdioMcpTransport` (official SDK `ClientSession` + `stdio_client`, installed already as `mcp==1.30.0`, MIT, verified from distribution metadata, no install needed) plus `StubMcpTransport` for offline tests. The signed-skill gate and the outbound gate sit in front of every call; the SDK is never given an unguarded path |
| Skill collection plus hooks: a hook point fires when a skill is installed or used | affaan-m/ECC (MIT) | The hook-point idea only | `puspa/chassis/hooks.py`: `SkillHookRegistry` (register, emit, unregister, broken-listener isolation), hook points for install, use, approve, reject, block. Enforcement stays in the signed-skill gate and the outbound gate, never in the hooks |
| Learning loop: skills created from successful tasks, kept inactive until the user approves | NousResearch/hermes-agent (MIT) and MineDojo/Voyager (MIT, dormant research archive) | The gated-learning idea: a learned skill is a draft until approved, and learning never overwrites manual edits | `puspa/chassis/hooks.py`: `LearnedSkill` (`is_active` is False unless status is `approved`), `LearnedSkillStore` (read-only view; `from_procedures` maps `trial` to pending approval and `verified` to approved; no write methods exist), `check_skill_use()` (emits `skill_blocked` for unapproved skills). The learning loop itself (`puspa/memory/learning_loop.py`) is built by the learning workstream; this module only reads its draft store |
| Model-driven agent loop with Cedar-style approval policies | strands-agents/harness-sdk (Apache-2.0) | The approval-policy idea only | No SDK adopted. The approval policy already exists as `puspa/policy/gate.py`; the MCP wrapper routes outbound kinds through it, and the planner never replans around it |

Evidence: reference evaluation through the new harness: 10/10
reference tasks passed (score 1.0); price-injection test passed
(tampered price Rp1.000 against the true cheapest Rp11.999.000,
verifier verdict Uncertain, not Verified). Optimiser on the 10
cassettes: planner program baseline score 1.0 (all 10 reference
tasks covered); best variant equals the baseline, so no prompt
change was adopted.

**Not used in the Brain phase (recorded so it is not repeated):**
- No code was copied from any repo above. No fork was created.
  Voyager is dormant as a research archive and its Minecraft code
  is not reusable; only its self-verification pattern was studied.
- claude code source code (proprietary, all rights reserved):
  pattern study only, from public docs. The leaked March 2026
  source-map code was never looked at and never used.
- opencode as a chassis or dependency (TypeScript coding agent, no
  channels, memory, cron, or per-bot computer): pattern only.
- Kilo-Org/kilocode (MIT): its modes and agents plus MCP pattern
  was studied for `puspa/runtime/subagents.py`, but no mode system
  was built in the harness workstream; the harness loop has no
  modes. That row stays open for the sub-agents workstream.
- letta-ai/letta-code (Apache-2.0): its self-improvement-from-
  experience loop was studied, but no learning loop was built in
  the harness workstream; the harness only verifies code.
- SWE-bench as a benchmark or dependency: only its task shape was
  adapted. No SWE-bench dataset, instance, or score is used, and no
  score is claimed.
- DSPy, inspect_ai, tau-bench, and lm-evaluation-harness as
  libraries (not installed): only their program-plus-optimiser,
  task/solver/scorer, simulated-user, and versioned-config shapes
  were adapted.
- langgraph / langchain as libraries (not installed, by explicit
  decision): only the checkpointed-orchestration idea was noted.
- strands-agents SDK (not installed): only its approval-policy idea
  was noted; Puspa's own gate already implements it.
- LightRAG as a library (not installed): only the dual-level
  retrieval idea was adapted on top of Postgres + pgvector.
- No GPL, AGPL, SSPL, PolyForm, or CC BY-NC code was used.

## Brain wiring phase (18 Nov 2026)

The wiring phase connects the Brain phase patterns into the live
agent loop. No new third-party pattern and no new dependency was
introduced: every pattern used here was already attributed in the
Brain phase section above, and `requirements.txt` is unchanged.

| What was wired | Pattern (already attributed above) | Where it lives |
| --- | --- | --- |
| World model predicts before acting, learns after observing, in the real task cycle | generative_agents reflection shape; LightRAG dual-level retrieval (beliefs are Postgres rows, not a library) | `puspa/runtime/tasks.py` (TaskRunner world_model / learning_loop), `puspa/world/model.py` |
| One brain per (user, agent) across turns | hermes-agent persistent user model idea | `puspa/chassis/agent_session.py` (AgentBrain, get_session_brain), `puspa/chassis/factory.py` |
| Curriculum runs its own learning tasks | Voyager automatic curriculum shape | `puspa/scheduler/curriculum.py`, `puspa/scheduler/scheduler.py` (tick curriculum parameter) |
| User feedback corrects the loop | letta-code self-improvement from experience | `puspa/memory/learning_loop.py` (record_feedback) |
| Schedule and capability beliefs | procedural memory lifecycle (decay, contradiction as separate candidate) | `puspa/world/model.py` (_ScheduleBelief, _CapabilityBelief) |
| Irregular-drift evaluation | inspect_ai / tau-bench simulated-user shape | `puspa/world/noisy_simulator.py` |

**Not used in the wiring phase (recorded so it is not repeated):**
- No code was copied from any repo. No fork was created.
- No new library was installed. The CLI change
  (`cli/puspa_cli/repl.py`, `/umpanbalik` command) is local only
  and is not part of the repo push.
- The deterministic simulator was not changed; the noisy
  simulator is a second, separate world with the same interface.
- No GPL, AGPL, SSPL, PolyForm, or CC BY-NC code was used.

## Brain deepening phase - Wave 1 (18 Nov 2026)

## Skill Signing (sigstore-shaped, local) - fragment for REUSE.md

Pattern studied, rewritten as Puspa's own code, no verbatim copy,
no fork, no new dependency (uses `cryptography`, already in the venv):

- **sigstore** (sigstore/sigstore, Apache-2.0): keyless signing with
  an ephemeral key, identity bound to the signer, DSSE envelope, and
  an append-only transparency log (Rekor) with Merkle inclusion
  proofs. Adapted as a LOCAL implementation in
  `puspa/policy/signing.py`: ephemeral Ed25519 key per signature,
  DSSE v1.0.2 pre-authentication encoding
  (`DSSEv1 SP LEN(type) SP type SP LEN(body) SP body`, verified
  against the spec test vector), payload
  `{identity, skill_sha256, issued_at}`, file-backed JSONL
  transparency log (hash-chained entries, Merkle root, inclusion
  proof generation and verification, no delete or update path).
  Honest scope: this is NOT the public sigstore service. There is no
  Fulcio certificate authority, no Rekor server, and no network call.
  Identity is a Puspa user identity string, trusted through Puspa's
  own authentication, not through an OIDC certificate.
- **DSSE specification** (dsse-lf/dsse, Apache-2.0): envelope shape
  `{payloadType, payload, signatures: [{keyid, sig}]}` and the PAE
  signing rule above. Only the specification was followed; no code
  was taken.
- **Rekor docs** (docs.sigstore.dev/logging/overview): the
  append-only and consistency-checking properties the local log
  mirrors (entries never mutated or removed, chain verified on load,
  inclusion proof checked at verify time).

Wiring: `puspa/policy/skill_signing.py` - `verify_skill` accepts
optional `transparency_log` and `expected_identity`; with a log it
also requires a valid `envelope.json` bound to the manifest bytes and
recorded in the log. Without a log it keeps the old manifest
signature check. `sign_skill_provenance` signs the provenance
envelope and records it in the log.

Not used: the `sigstore` Python client and any Rekor/Fulcio service
(no network dependency for skill trust); no Postgres table for the
log (file-backed by design, so trust stays verifiable without a
database; see the persistence rationale in the `signing.py` module
docstring).
# Reuse fragment: unified harness, Aider + SWE-agent + openai/codex (`puspa/harness/`)

Workstream: one harness, not three (see `docs/brain-depth-triage.md`,
"Traps to avoid": Aider + SWE-agent + Codex are ONE harness:
surgical edit + reproduce-first + sandbox profiles). Built 18 Nov
2026 by the Coding Harness worker. Rules followed (Nero's order, see
`REUSE.md`): study the BIG PATTERNS/IDEAS from surveyed repos, then
REWRITE them as Puspa's own code adapted to the revision 7
architecture, with English identifiers, stored values, and prose.
NEVER copy code verbatim. NEVER use code licensed GPL, AGPL, SSPL,
PolyForm, CC BY-NC, or a custom non-OSI license. NEVER create a
GitHub fork of any repo. No new dependencies were added: this
workstream uses only the standard library plus the existing Puspa
policy and sandbox modules. `puspa/policy/` was NOT edited;
enforcement runs through the existing `Gate.authorize_tool` API.

Source inventory: `docs/brain-depth-triage.md` (Tier 1 Aider,
Tier 1 SWE-agent, Tier 2 openai/codex). Upstream documents read
18 Nov 2026: Aider README (github.com/Aider-AI/aider, Apache-2.0)
and Aider repo map docs (aider.chat/docs/repomap.html: concise map
of classes and functions with types and signatures, token budget
default 1k via `--map-tokens`); SWE-agent README
(github.com/SWE-agent/SWE-agent, MIT) plus the reproduce-first
discipline as documented in the SWE-bench task shape (FAIL_TO_PASS
tests fail before the fix and pass after it); openai/codex README
(github.com/openai/codex, Apache-2.0) plus the Codex sandbox and
approval docs (three modes `read-only` / `workspace-write` /
`danger-full-access`, approval policies `on-request` / `never`,
sandbox applies to every spawned command, `.git` / `.codex` /
`.agents` stay protected under writable roots).

## What was built

- `puspa/harness/repo_map.py` (new): repo map builder. Walks a
  directory of Python files, extracts top-level classes, functions,
  and methods with signatures (arguments, defaults, annotations,
  return annotation, base classes) via stdlib `ast`, and renders a
  compact Aider-like map within a token budget (chars / 4 estimate,
  default 1000). Whole files are dropped when the budget runs out,
  never half a file. Unparseable files are listed as skipped.
- `puspa/harness/diff.py` (new): unified diff generation (stdlib
  `difflib`) and strict application. `apply_diff` validates EVERY
  hunk against the original before changing anything; a hunk whose
  context does not match raises `DiffApplyError` with the failed
  hunk numbers and NOTHING is applied, not even the hunks that did
  match. `is_diff` detects diff text so the loop accepts a full
  program or a diff from the generator.
- `puspa/harness/reproduce.py` (new): reproduce-first state machine
  (`ReproduceFirst`, `Reproduction`, `build_reproduction`,
  `generate_reproduction_stub`). The reproduction test must fail on
  the unpatched program (and match `expected_failure` when one is
  given) before any fix is verified; `mark_fixed` refuses without
  both observations. The reproduction test is frozen once observed,
  so the gate cannot be passed by weakening the test.
- `puspa/harness/sandbox_profiles.py` (new): profiles `read_only` /
  `workspace_write` / `full`, mapped to tool levels. The effective
  level for a tool is always the narrower of the profile level and
  the token level (via `puspa.policy.tokens.tool_permission`), so a
  profile never widens a token and a token never widens a profile.
  `check_tool` enforces through `Gate.authorize_tool`;
  `check_write_path` refuses writes under `read_only`, writes
  outside the workspace root, and writes inside protected paths
  (`.git`, `.codex`, `.agents`, `.aws`). `requires_approval` models
  approval before a write outside the profile boundary. Puspa's
  `full` is NOT Codex `danger-full-access`: it still runs only
  through the sandbox executor, still needs gate approval for
  deploy, and never enables network access.
- `puspa/harness/loop.py` (changed): `CodingHarness` gains
  `profile` (default `workspace_write`), `diff_mode` (default off),
  and `reproduce_first` (default off). In diff mode a generator
  diff is applied to the current program text; a failed diff fails
  that one attempt with the reason recorded. With reproduce-first
  on, starter code plus the acceptance tests runs as the
  reproduction probe before any fix attempt, and a first attempt
  that passes without a prior failing reproduction ends as
  `reproduction_missing` instead of `passed`. `HarnessOutcome`
  gains `profile`, `reproduction_failed`, and
  `reproduction_verified`. Defaults keep the old behaviour exactly.
- `puspa/harness/executor.py` (changed): `Executor.write_file`
  (default refuses: no writable filesystem) and
  `LocalTestExecutor.write_file` (writes under its workdir only,
  profile-enforced via `check_write_path`). `SandboxExecutor`
  keeps the default refusal: the sandbox has no persistent
  filesystem, so production edits run through `run()` only.
- Tests: `tests/test_harness_unified.py` (30 tests, in-memory plus
  the local test executor; no new Postgres tables).

## Pattern table

| Big idea | From which repo (license) | What was adapted | What was rewritten from scratch |
| --- | --- | --- | --- |
| Repo map: concise map of the whole codebase with the most important classes and functions plus types and signatures, fitted to a token budget | Aider-AI/aider (Apache-2.0) | The map shape (file header plus one line per symbol) and the budget idea (default 1k tokens, most relevant parts first) | `puspa/harness/repo_map.py`: `ast` extraction of classes, functions, and methods with full signatures, `render_repo_map` with whole-file budget drops, `estimate_tokens` (chars / 4). No tree-sitter, no PageRank ranking yet: files render in sorted path order, which is the honest gap recorded below |
| Surgical edits as unified diffs, then lint and test after the edit | Aider-AI/aider (Apache-2.0) | The diff as the unit of change instead of a whole-file rewrite, so the change stays reviewable | `puspa/harness/diff.py`: `generate_diff` on stdlib `difflib`, `parse_diff`, `apply_diff` with all-hunks-validated-first and no partial apply, `is_diff` detection in `puspa/harness/loop.py` |
| Reproduce-first: write the failing test before fixing, fix, run the tests, prove the fix | SWE-agent/SWE-agent (MIT) and the SWE-bench task shape (MIT) | The FAIL_TO_PASS discipline: a fix counts only when the same test fails before it and passes after it, and the failure must match the reported symptom | `puspa/harness/reproduce.py`: `ReproduceFirst` state machine with frozen reproduction test, `expected_failure` matching, `mark_fixed` refusal; `puspa/harness/loop.py`: starter probe plus first-failing-attempt rule, `reproduction_missing` outcome |
| Sandbox profiles plus approval before acting outside the boundary | openai/codex (Apache-2.0) | The two-layer split (sandbox mode as the technical boundary, approval policy as when to stop and ask) and the three modes, plus protected paths under writable roots | `puspa/harness/sandbox_profiles.py`: `read_only` / `workspace_write` / `full` mapped onto the existing Puspa token levels, effective level as the narrower of profile and token, `check_tool` through `Gate.authorize_tool`, `check_write_path`, `requires_approval`; `puspa/harness/executor.py`: profile-enforced `write_file` |

## Not used in this workstream (recorded so it is not repeated)

- Aider source code (Apache-2.0): pattern study only, from the public
  README and docs pages listed above. No aider code was read, copied,
  or installed; the repo map here uses stdlib `ast`, not tree-sitter.
- SWE-agent source code (MIT): pattern study only, from the public
  README and the SWE-bench task shape already in the harness. No
  SWE-agent code was read, copied, or installed; mini-SWE-agent is
  noted in the SWE-agent README as the successor, but nothing was
  taken from it either.
- openai/codex source code (Apache-2.0): pattern study only, from the
  public README and docs. No codex code was read, copied, or
  installed. Codex `danger-full-access` was deliberately NOT
  reproduced: Puspa's `full` profile stays inside the sandbox with
  gate approval for deploy and no network in any profile.
- No PASS_TO_PASS regression set yet: the loop grades the acceptance
  tests only, as before. A regression set is future work for the
  self-develop module evaluation.
## Retrieval pipeline (HippoRAG + LightRAG + LlamaIndex reranker)

Date: 18 Nov 2026. Worker: Retrieval Pipeline (brain deepening).
Files: `puspa/retrieval/knowledge_graph.py`, `puspa/retrieval/hipporag.py`,
`puspa/retrieval/modes.py`, `puspa/retrieval/reranker.py`,
`puspa/retrieval/__init__.py`; wiring in `puspa/memory/episodic.py`
(`episodes_to_graph`, `search_episodes_pipeline`) and
`puspa/graphs/identity.py` (`product_passages`,
`rank_products_pipeline`). Tests: `tests/test_retrieval_graph.py`,
`tests/test_retrieval_hipporag.py`, `tests/test_retrieval_modes.py`,
`tests/test_retrieval_reranker.py`, `tests/test_retrieval_eval.py`,
`tests/test_retrieval_wiring.py`, plus the database-backed
`tests/test_retrieval_wiring_db.py` (run by the coordinator only).

### Patterns studied (all MIT, all rewritten, none copied verbatim)

- HippoRAG (OSU-NLP-Group/HippoRAG, MIT): schema-less knowledge
  graph built at index time, multi-hop retrieval by Personalized
  PageRank, HippoRAG 2 style query-to-triple filtering and
  passage re-ranking. Reimplemented here as `KnowledgeGraph`,
  `personalized_pagerank` (power iteration with restart),
  `HippoRAGRetriever`, weighted seeds, and sum-of-node passage
  scoring. No HippoRAG code or dependency is used.
- LightRAG (HKUDS/LightRAG, MIT): dual-level retrieval and the
  five query modes local, global, hybrid, naive, mix, plus
  incremental graph update (add one document without a rebuild)
  and document deletion with orphan cleanup. Reimplemented here
  as `puspa/retrieval/modes.py` over the same dual-level weights
  already in `puspa/memory/episodic.py` (entity 0.5, concept 0.3,
  vector 0.2). No LightRAG code or dependency is used.
- LlamaIndex (MIT): reranker as a node postprocessor between
  retrieval and synthesis (cross-encoder, recommended local
  default `cross-encoder/ms-marco-MiniLM-L6-v2` or a
  `BAAI/bge-reranker-*` model) and query decomposition into
  sub-questions (`SubQuestionQueryEngine`,
  `DecomposeQueryTransform`). Reimplemented here as
  `DeterministicReranker`, the `CrossEncoderReranker` seam (any
  `score_fn` callable), `decompose_query`, and
  `retrieve_with_decomposition` (reciprocal rank fusion). No
  LlamaIndex code or dependency is used.

### Dependencies

No new dependency. Embeddings reuse `HashEmbedding` (offline
default) or the pinned `fastembed` provider. fastembed 0.9.1
ships no reranker, so `CrossEncoderReranker.from_fastembed`
raises `RerankerUnavailable` honestly; a real cross-encoder
(`BAAI/bge-reranker-v2-m3` through FlagEmbedding, or a
`cross-encoder/ms-marco-MiniLM-L6-v2` through
sentence-transformers) is a follow-up dependency, not added here.

### Honest limits

- The default extractor is deterministic and rule-based, so its
  recall is below LLM extraction. A real LLM extractor plugs
  into `JsonTripleExtractor` through any completion callable;
  no LLM call is made by this module and no key is needed.
- A query with no seed entity or concept falls back to plain
  vector ranking; the graph cannot help it.
- Passages with no extracted entity compete on vector and
  lexical score alone, with a zero graph score.
- The deterministic reranker cannot judge relevance the way a
  cross-encoder can: on the hand-written eval set the decisive
  two-hop passage ranks 6-9, so the pipeline wins on reach
  (recall@10) rather than on top-3 precision.
