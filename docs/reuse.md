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
# google/adk-python (hierarchical multi-agent) - REUSE fragment

Pattern studied from google/adk-python (Apache License 2.0), reimplemented
as Puspa's own code from the documented behavior. No code was copied
verbatim; no fork was made; no new dependency was added.

## What was taken (pattern level only)

- Agent hierarchy: root agent with sub-agents, one parent per agent,
  delegation by role (ADK `LlmAgent.sub_agents` + transfer, single
  parent rule). Puspa: `AgentNode` in `puspa/runtime/hierarchy.py`.
- Workflow compositions: sequential, parallel, loop (ADK
  `SequentialAgent` / `ParallelAgent` / `LoopAgent`; ADK 2.0 also
  offers graph `Workflow`, which Puspa does not adopt). Puspa:
  `HierarchicalPlan.composition` + `HierarchicalRunner`.
- Sessions and state: `SessionService` + `session.state` with
  namespaced keys (plain session keys, `user:`, `app:`, `temp:`
  never persisted). Puspa: `SessionState` + `SessionStore`.
- Memory: `MemoryService` search across sessions. Puspa keeps its
  own rule instead: a node never touches memory storage directly;
  it receives a slice through `puspa.memory.slices.grant_slice`.
  Puspa: `MemoryAccess`.
- Tool confirmation and callbacks: ADK HITL tool confirmation
  (`require_confirmation`) and `before_tool_callback` /
  `after_tool_callback` / `before_model_callback` /
  `after_model_callback`. Puspa: `ToolConfirmation` goes through
  the existing `puspa.policy.Gate` approval flow, and
  `CallbackRegistry` holds the four hooks.
- Handoff: every parent/child delegation goes through the existing
  `puspa.runtime.handoff` contract (max 3 handoffs between agents
  per task, cycle rejection). No new bypass path.

## What was NOT taken

- The ADK package itself is not a dependency; nothing was imported
  from `google.adk`. The model, the Runner, and `VertexAi*`
  services were not adopted. Parallel composition is logical, not
  threaded: children run one after another in a fixed order and
  their results are merged.

## Docs read (18 Nov 2026)

- GitHub README google/adk-python (Apache 2.0, 22,256 stars at read
  time): key features list, Workflow quick start, tool
  confirmation feature.
- Official docs site (adk.dev) workflows overview: graph, dynamic,
  collaborative, and template workflow types.
- ADK multi-agent and state/session/memory guides (search results,
  checked against google-adk 2.13.0 on 2026-11-17): hierarchy
  rules, `SequentialAgent` / `ParallelAgent` / `LoopAgent`,
  `user:` / `app:` / `temp:` state scopes, `InMemorySessionService`
  vs `DatabaseSessionService`, `InMemoryMemoryService`,
  `load_memory` / `preload_memory`, `require_confirmation`.

## Files

- New: `puspa/runtime/hierarchy.py` (AgentNode,
  HierarchicalRunner, SessionState, MemoryAccess, ToolConfirmation,
  CallbackRegistry).
- Changed: `puspa/runtime/planner.py` (`decompose_hierarchical`,
  `ROLE_FOR_STEP`, `Planner.run_hierarchical`; flat `run` untouched),
  `puspa/runtime/subagents.py` (`Subagent.role`, `spawn(role=...)`,
  `SubagentPool.active_by_role`).
- Tests: `tests/test_hierarchy.py` (29 tests, in-memory only).
## NeMo Guardrails (programmable rails) - deepening phase, 18 Nov 2026

Pattern source: NVIDIA NeMo Guardrails, Apache License 2.0
(https://github.com/NVIDIA/NeMo-Guardrails, README on the develop
branch, and https://docs.nvidia.com/nemo/guardrails, read 18 Nov
2026). No NeMo Guardrails code was copied, the library is not a
dependency, and no fork was created. The pattern was rewritten from
scratch as Puspa code from the documented behavior: five rail types
(input, retrieval, dialog, execution, output), rails as testable
rules that run before, during, and after the model call, Colang as
the dialog-flow language, and the rule that the application runs
the checks and uses their results to decide whether to continue.

What was built:

- `puspa/guardrails/rails.py`: `Rail` (one condition plus an
  allow/block action, fail-closed when its check raises), `RailSet`
  (ordered rails, first block wins, in-memory violation log),
  input rails (topic allow/deny control, injection detection that
  combines the existing quarantine scan with structural signals:
  agent-directed imperative plus money or secret target, secrecy
  instruction, urgency, and instructions hidden inside a
  `[DATA: ...]` marked span; stricter for untrusted tool and
  retrieved text than for user text), output rails (numbers must
  come from evidence markers per revision 7 section 2.5, unknown
  evidence ids block, secret values in a draft block), execution
  rails (spending limit with fail-closed no-limit and no-nominal
  cases, tool allowlist, cross-user ownership, unsigned deploy,
  tampered content hash, injected send content), and `DialogFlow` /
  `FlowController` (the Colang `define flow` idea as a small state
  machine: idle, collecting, awaiting approval, presenting, done;
  presenting while approval is pending blocks).
- `puspa/guardrails/__init__.py`: public exports.
- `puspa/chassis/hooks.py` (hook point only, minimal change):
  `PuspaHook` takes an optional `rail_set`; `check_input`,
  `check_output`, and `check_action` run the rails and record a
  block through an existing ledger type (`task_failed`,
  `verification_completed`, `approval_rejected`, all inside the
  closed set of 31); `finalize_content` replaces a blocked answer
  with the refusal text, which is the hard enforcement point in
  the nanobot runner; `make_puspa_hook_factory` passes the rail
  set through. No new ledger event type was added.
- `tests/test_guardrails.py`: 68 tests, all in-memory.

Measured results: all 8 attack paths from `tests/test_attacks.py`
still block at the rail layer, with zero actions executed by the
counting executor when a rail blocks. False positives on the
hand-written benign sets are zero in every group: 0 of 12 user
inputs, 0 of 6 tool inputs, 0 of 12 outputs, 0 of 4 actions.

Honest limits: `before_run` cannot veto a nanobot run (the hook
interface has no veto), so a blocked input is enforced by
refusing every answer of that turn through `finalize_content`
and by callers checking `check_action` before executing; the
turn-setup caller still has to skip running a blocked turn. No
model-backed check exists (no self-check rail, no classifier):
detection is deterministic code only, so paraphrased injections
outside the signal set will pass. Retrieval rails are not a
separate kind here: retrieved text is checked as input with
source "retrieved" or "tool". The `puspa/chassis/factory.py`
wiring is done by the coordinator: `PuspaChassis` carries an
optional `rail_set`, `build_hook`/`build_hook_factory` pass it
through, and `assemble` builds the default rail set when the
chassis carries none, so rails are live in the assembled loop
(tests in `tests/test_factory_rails.py`).

## Brain deepening phase - Tier 2 Batch A (18 Nov 2026)

Seven workstreams built in parallel on disjoint files by seven
workers; the coordinator ran the full suite SOLO centrally and
pushed each workstream as its own commit. All seven received the
new depth bar (18 Nov 2026 ~10:52 WIB): the core mechanism plus
its flagship part must really exist, not a seam without a
schedule, and every unavoidable stand-in is named in the report
with its dependency and follow-up.

### hermes-agent + letta-code: ONE learning loop

Patterns: NousResearch/hermes-agent (MIT), letta-ai/letta-code
(Apache-2.0). No code copied, no fork. `puspa/memory/skill_repair.py`
(new, 622 lines): `diagnose_skill_failure` classifies every
failing case of a skill (wrong output, exception, not
executable) with expected vs observed values plus the loop's
correction as read-only context; `SkillRepairer.repair` runs
diagnosis, rewrite, re-verify up to `max_attempts`, every attempt
a real new draft verified with `LearningLoop.verify_skill`
against the same test cases, and honestly reports
"unrepairable" when no rewrite passes; `ProcedureUpdater.record_outcome`
creates a real new procedure version (version number,
`supersedes` link, trial status) on repeated successful outcomes
with a new step, follows the revision 7 rule (broken after 2
consecutive failures), and decays through
`procedural.apply_decay`. Both read and write the same state the
learning loop already owns (experience log, correction table,
procedure store), so it is ONE loop, not two. A repair never
overwrites a manual user edit: the repair is always a NEW draft,
the original skill and the manual edit are left byte-identical.
Tests: `tests/test_skill_repair.py`, 26 tests. Honest limits:
the production rewriter is an LLM call, so this module takes it
as an injected callable and ships only the deterministic
`lookup_rewriter` as the built-in (it memorises corrected outputs
for failing inputs; a real repair, but a narrow one). A
model-based rewriter is the follow-up.

### LightRAG: incremental graph update

Pattern: HKUDS/LightRAG (MIT). No code copied, no fork.
`puspa/retrieval/incremental.py` (new, 528 lines):
`IncrementalUpdater` over the existing `KnowledgeGraph`, a real
merge, not a disguised rebuild: `add_passage` / `add_passages` /
`add_document` / `add_documents` extract only the new passage
(old nodes and edges keep their identity and gain the new
passage as a source); SHA-256 content dedup on normalised text
(same text under a different id is recognised and skipped);
weights are passage counts (a triple repeated by a new passage
raises the edge weight from 1 to 2 instead of making a second
edge); `update_passage` / `update_document` detach the old
passage first (orphans without a source are removed) then merge
the new text; `remove_passage` / `remove_document`;
`snapshot()` / `added_since()` prove the merge. Tests:
`tests/test_retrieval_incremental.py`, 15 tests. Measured recall
on the 8 Wave 1 multi-hop queries: main update keeps recall@10
at 1.0 and recall@5 at 0.375, but recall@3 drops 0.375 to 0.25
(a ranking effect, not data loss; the determining passage ranked
6-9 in Wave 1); a second case (new promo on a seller with a
fraud report) drops recall@10 from 1.0 to 0.875. Both drops are
locked in tests. Honest limits: extraction is still the
deterministic regex stand-in inherited from Wave 1 (follow-up
via the `JsonTripleExtractor` seam, no schedule yet); dedup is
exact-text only; weights are passage counts, not LLM weights; no
community detection or summary regeneration on update; no
re-embedding.

### KiloCode: working modes

Pattern: Kilo-Org/kilocode (MIT, verified via the GitHub API
18 Nov 2026; the "Apache 2.0" claim found in secondary sources
refers to Kilo's extension/CLI packaging, not the repo LICENSE
as reported by the API). No code copied, no fork.
`puspa/harness/modes.py` (new): `ModeSpec` plus four modes
(`architect`, `code`, `debug`, `ask`), each bound to an existing
sandbox profile (Architect and Ask use `read_only`; Code and
Debug use `workspace_write` plus `diff_mode=True`; Debug adds
`reproduce_first=True`); `enforce_read_only_tools` proves
through Gate plus profile that `write_code` and `run_tests` are
refused before a read-only mode runs. `puspa/harness/loop.py`
(mode wiring only): a new `mode` parameter (default `None` keeps
the old behavior exactly); Architect builds a real repo map and
plans from it without ever calling the executor; Ask answers
once; Code and Debug fail an attempt whose generator answer is
not a unified diff instead of falling back to a full program.
`puspa/harness/__init__.py` exports the mode names (added by the
coordinator). Tests: `tests/test_harness_modes.py`, 16 tests.
Honest limits: no hidden stand-in (the Architect repo map is
real, enforcement is real); the generator stays caller-supplied
as before, no LLM is wired here. Architect without a
`workspace_root` and without an executor workdir plans without a
repo map (`repo_map_text=None`, tested).

### AgentBench: held-out eval

Pattern: THUDM/AgentBench (Apache-2.0). No code copied, no fork.
`puspa/evidence/agentbench_tasks.py` (new): 8 real held-out
tasks, 2 per environment (DB, KG, OS, WebShop), each with a
complete inline recording (not an empty template); expected state
derived from the recording by `derive_heldout_state` (code, not
hand-typed numbers); `score_agentbench` reuses the existing
`score_state` plus partial credit (fraction of matching keys);
`run_heldout_eval` and `summarize_by_environment` report the
held-out score per environment and in total, never mixed with
the dev score. `puspa/evidence/eval_harness.py` (wiring only):
`environment` plus inline `cassette` on `EvalTask`,
`environment` on `EvalResult`, `run_task` uses the inline
cassette when present and takes the score from
`details["score"]`. Tests: `tests/test_agentbench_eval.py`,
18 tests. Disjointness proven in code (`check_disjoint`):
task ids, cassette names, and tools do not overlap the 10 dev
cassettes, and the maximum prompt Jaccard against dev prompts is
0.087 (limit 0.5). Honest limits: the environments are simulated
(inline cassette recordings following the existing replay
mechanism), not live databases, shells, or shops; the follow-up
is a sandbox-based solver with live environments (needs a
sandbox host, no outside service or new dependency).

### gorilla: BFCL eval format only

Pattern: ShishirPatil/gorilla, Berkeley Function Calling
Leaderboard (Apache-2.0, verified from the root LICENSE 18 Nov
2026). No code copied, no fork, no BFCL task data copied.
`puspa/evidence/bfcl_checker.py` (new, 508 lines): a real AST
checker, not string matching. `ToolCall` reads from a dict, an
OpenAI-style payload, or Python source parsed with `ast.parse`
(argument values read from the syntax tree via
`ast.literal_eval`); comparison is recursive and strict on
function name, argument values AND types (bool is not int, int
is not float, a digit string is not a number, `None` is not
`""`), missing and extra arguments, down into nested dicts and
lists with paths like `arguments.attendees[1]`; per turn it
checks order (same set in different order is `wrong_order`),
missing and extra calls; per task it scores turn accuracy and
call accuracy, and a task only passes when every turn matches
exactly (the BFCL rule). `puspa/evidence/bfcl_tasks.py` (new,
645 lines): a catalog of 11 real Puspa tools with JSON schemas
plus 8 held-out tasks written by Puspa itself (12 turns, 16
reference calls), including a 3-turn multi-turn task, a user
correction in turn 2, omitted optional arguments, an empty
string argument, a digit-string customer number, a float
threshold, and an odd amount; `reference_agent`,
`make_bfcl_solver` / `bfcl_solver` (runs through the cassette
player, simulated tool output per turn), `score_bfcl` (partial
credit is turn accuracy, pass is all turns correct), and
`summarize_bfcl`. `puspa/evidence/eval_harness.py` (wiring
only): `run_bfcl_eval(agent=None)` plus an `__all__` entry;
AgentBench wiring untouched. Tests:
`tests/test_bfcl_eval.py`, 43 tests, 43 passed. Measured
accuracy on the held-out set: reference agent 8/8 (100%, mean
turn accuracy 1.0, mean call accuracy 1.0); order-reversing
agent 4/8 (50%); number-to-string agent 2/8 (25%, mean turn
accuracy 0.354, mean call accuracy 0.417); silent agent 0/8
(0%). Honest limits: no live model was scored (the reference
agent replays reference calls, so 8/8 proves the checker and
the harness wiring work and the reference calls are
consistent, not that a model would score 1.0; follow-up is a
model client plugged in as `agent` in `make_bfcl_solver`, no
schedule yet); tool output is simulated from inline cassettes,
not live tools (deliberate, consistent with AgentBench); float
comparison is exact, no epsilon tolerance yet.

### 9Router: RTK tool_result compression (mechanism 1 of 3)

Pattern: decolua/9router (MIT) + RTK (rtk-ai/rtk, Apache-2.0
for the CLI, verified from the RTK README 18 Nov 2026: the
`rtk` CLI is Apache-2.0, covering the hook core and all filter
commands; the `rtk-engine` and `rtk-proxy` daemon are BUSL-1.1
and were NOT used). No code copied, no fork.
`puspa/router/compression.py` (new, 736 lines): lossless
compression of tool results before they reach the model,
rewritten from scratch from the RTK pattern. `detect_kind`
auto-detects from the first 1KB; 10 filters per kind (`diff`,
`status`, `grep`, `find`, `ls`, `tree`, `log`, `read`, `search`,
`generic` smart-truncate); fail-safe returns the raw text when
a filter fails or the result grows (`filter_error`,
`no_savings`, `too_short`); `count_tokens` uses tiktoken
`cl100k_base` when available, otherwise a chars/4 estimate
clearly labeled as an estimate. Wiring in
`puspa/chassis/tools.py` (about 24 lines, 2 blocks):
`PuspaTool.execute` compresses after the injection scan and
before returning to nanobot/the model (error results are not
compressed, `error_code` is kept), and `McpClientWrapper.call_
tool` compresses string results. The ledger only records tool
name/status, not content, so the evidence the Checker / Evidence
Book reads is unchanged. Tests:
`tests/test_tool_result_compression.py`, 53 cases from 26
functions, 53 passed. Measured savings with tiktoken
`cl100k_base` on realistic noisy fixtures: log 94.1% tokens,
diff 48.2%, generic 56.4%, grep 40.3%, search 20.0%, status
17.5%, find 16.5%, ls 3.3%, read 7.3%, tree 0.2%. On real tool
output (smaller/cleaner): `git diff -U10` 57.4% tokens, `find`
15.2%, `ls -la` 2.3%, `tree` 0.1%, `cat -n` 0.0%, small `git
diff` 0.5%, `grep` without duplicates 0.2% (an early 64%
figure was a fixture artifact from triplicating the fixture,
honestly corrected). Honest limits: diff context lines are the
ONE exception to the orphan-number rule (numbers that only
appear in folded unchanged context lines are not kept
literally, only their line count in the marker; every `+`/`-`
line is kept whole with its numbers, because unchanged lines
can be re-read from the file); small/clean output barely saves
and the fail-safe returns raw text, which is the correct
behavior, not a failure; tree saves only 0.2% tokens (box-
drawing characters cost almost the same tokenized); this is
tool_result only, no conversation-history compression or prompt
caching (that is G20, separate work). Two real bugs were found
and fixed by the tests (the `ls` filter dropped the link-count
column, the `tree` filter had a global dedup that dropped
`model.py` in different modules). No new dependency: tiktoken
was already pinned in `requirements.txt` (0.14.0).

### 9Router: smart 3-tier fallback + quota tracking (mechanism 2 of 3)

Pattern: decolua/9router (MIT, verified from three
independent sources 18 Nov 2026: the GitHub README via a
mirror, x-cmd, and FollowAgents/reporadar, all saying MIT).
No code copied, no fork, no sub-project studied or used.
`puspa/router/quota.py` (new, 368 lines): `QuotaStore` +
`ProviderQuota`, per-provider quota tracking (remaining
tokens, usage, reset countdown, Retry-After blocking), cost
estimation, and rate-limit header parsing (`Retry-After`,
`X-RateLimit-Remaining`, `X-RateLimit-Reset`). Windows roll
over automatically (5-hour / daily / weekly, as registered);
the clock is injectable (used by the tests).
`puspa/router/fallback.py` (new, 529 lines): `build_chain()`
(subscription -> cheap -> free from the role candidates in the
router catalog, most expensive first via spread first/median/
last, a different model per tier), `choose_with_quota()`
(quota-aware routing), `call_with_fallback()` (fallback
executor), failure classification (`classify_failure`: 429
-> rate_limited, quota signals -> quota_exhausted, 5xx or no
status -> provider_error, other 4xx -> non_retryable),
`FallbackAttempt` / `FallbackHop` / `FallbackResult` (full
audit: every hop records reason + source tier + target tier),
`AllTiersFailed`, and the `RateLimited` /
`QuotaExhaustedError` / `ProviderError` / `NonRetryableError`
exceptions. Wiring in `puspa/router/router.py` (about 30
lines): `default_chain(role)` and
`choose_with_quota(role, quota_store)` delegating to
`fallback.py` via lazy import (no circular import);
`choose()` and `build_provider()` untouched. The chain is
built from the same `CATALOG` + role rules as
`router.choose()` (planner/presenter = benchmark-passing,
checker = a different family, vision = cheapest vision), and
costs use the same Rupiah formula as `router.compute_cost`
and `BudgetTracker` (Rp per 1,000 tokens, integer Rupiah, no
second currency). The Rp2,000/task cap is unchanged. Tests:
`tests/test_router_fallback_quota.py`, 33 tests, 33 passed,
all in-memory with simulated providers and a fake clock
(regression: `tests/test_router.py` +
`tests/test_budget_tasks.py` still green, 16 passed). What
the tests prove: the chain order [subscription, cheap, free]
with different models and falling prices; a real switch on
429 (tier 1 429 -> tier 2 succeeds, hop recorded with reason
and status), on 5xx, and on quota signals; an exhausted tier
is skipped without ever being called; 429 marks the provider
blocked in the store until Retry-After passes; all tiers
failing raises `AllTiersFailed` naming each tier + model +
reason, never silently; a deterministic 400 raises at once
and the next tier is never tried; quota math (1000 - 500 used
= 500 remaining, countdown shrinking with the clock, full
reset restoring 1000, three missed windows rolling forward,
no window never resetting, `X-RateLimit-Reset` shifting the
reset); cost estimates identical to `compute_cost` per tier;
successful usage recorded in quota + cost reported, failed
tiers charging nothing; quota-aware routing skipping
exhausted tiers and returning to subscription after reset;
and the budget cannot be gamed (exhausted budget before any
attempt calls no tier; a tier 1 success over the cap, Rp56
vs a Rp50 cap, raises `BudgetExhausted`, the cheap tier is
not tried, the charge is refused, usage stays 0). Honest
limits: the quota store is in-memory (dict + lock), it does
not survive restart and is not written to the ledger or
Postgres (follow-up: Postgres persistence with a quota
snapshot + reconciliation against ledger cost events);
subscription and free tiers are cost bands over Puspa's own
OpenRouter catalog, not different billing relations (no
flat-rate subscription provider in the Claude Code/Codex
OAuth style and no zero-cost provider is wired in the
catalog yet; the subscription tier is the best model per
role, drained first per 9Router "maximize subscription", and
the free tier is the cheapest models at Rp1-3/1k tokens,
near-zero, not zero) — wiring a real subscription/OAuth or
free provider (for example local Ollama as the free tier) is
a maturity-phase follow-up and needs Nero's decision on
which provider; there is no live call adapter yet
(`call_with_fallback` takes `call_fn(entry)` from the
caller; no caller of `build_provider()` / `choose()` exists
in `puspa/` today, so a live adapter cannot be proven without
inventing a call path — the `call_fn` + `CallResult`
signature is set here as the contract for the first live
router caller); quota without registration is assumed
available (limits are never guessed: `register()` /
`seed_chain_quotas()` must be given real limits + windows
from the provider's actual plan); costs come from catalog
rates (public OpenRouter prices, Nov 2026), not live
provider bills, the same as the existing `compute_cost`.

### 9Router: auto OAuth refresh + format translation (mechanism 3 of 3)

Pattern: decolua/9router (MIT, verified from three
independent sources 18 Nov 2026: the GitHub README via a
mirror, x-cmd, and FollowAgents/reporadar, all saying MIT;
re-confirmed by web search 18 Nov 2026). No code copied, no
fork, no sub-project studied or used (RTK, Headroom, Ponytail
not touched by this mechanism). `puspa/router/oauth.py` (new,
560 lines): generic OAuth2 refresh-token grant (RFC 6749
section 6) with proactive refresh inside a 300-second leeway
(the same 5-minute margin nanobot uses for xAI), single-flight
refresh per provider (lock plus re-check, two parallel
callers cause exactly one endpoint call), clear
`TokenRefreshError` (provider, status, error code,
`reauth_required` on `invalid_grant`), refresh-token
rotation, and in-memory storage only with token values never
logged, printed, or written (redacted `__repr__`, audit
events carry outcomes only). `puspa/router/translate.py`
(new, 781 lines): request/response translation between the
two formats the BYOK path already serves, OpenAI
chat-completions and Anthropic Messages, with every unmapped
field reported in `TranslationReport` instead of silently
dropped (OpenAI `frequency_penalty` / `response_format` /
`logit_bias` / `n` / `seed`, Anthropic `thinking` blocks and
`cache_control`, cache-creation tokens, extra choices).
Wiring in `puspa/router/router.py` (about 70 lines):
`register_known_oauth_providers()` (xai, github_copilot,
openai_codex as simulated registrations),
`get_valid_oauth_token()`, `translate_chat_request/response()`,
all delegating with lazy imports. Tests:
`tests/test_router_oauth_translate.py`, 39 tests, 39 passed,
all in-memory with a fake token endpoint and a fake clock.
Honest limits: token storage is in-memory (vault persistence
under `byok:oauth:<provider>` is a follow-up, needs user id +
DSN); the three named providers are simulated (xAI carries
its token URL and public client id from nanobot's source,
Copilot has no client id wired, Codex has no token URL wired
— neither was invented); no interactive login flow lives
here (authorization/device code stays in nanobot); Azure
Responses and Bedrock Converse are not translated (outside
the router catalog); Anthropic `max_tokens` defaults to 4096
when the OpenAI request has none (recorded in the report);
`finish_reason=content_filter` maps to
`stop_reason=end_turn` as the nearest neutral equivalent
(reported); no retry/backoff on transport failure (one
attempt, then a clear error).

### mem0: extraction quality + ranked recall

Pattern: mem0ai/mem0 (Apache-2.0). No code copied, no fork.
`puspa/memory/extraction.py` (new, ~360 lines):
`is_memorable` / `extract_facts` decide what becomes a memory
(greetings, thanks, pure questions, and fact-free text are
rejected; sentences are split into fact clauses); `MemoryStore.add`
merges duplicates into one memory (`support` rises, canonical
text is the longest, raw observations are kept) and stores
conflicts separately with status "conflicted" and `conflicts_with`
pointing at the old memory (the old memory is never overwritten);
conflicts come from opposite polarity or different numbers for
the same subject; `rank_memories` / `recall_at_k` /
`measure_recall_at_ks` rank over the episodic dual-level score
plus small support and recency bonuses (at most 0.02 each, so
relevance always dominates), and zero-score memories are dropped
from ranking. Two real bugs were found and fixed while testing
(conflict-before-dedup ordering, zero-score tie-break).
`puspa/memory/episodic.py` was not changed (not needed; the new
module reuses its extractors). Tests:
`tests/test_mem0_extraction.py`, 16 tests. Measured recall@k on
a 6-query / 8-memory set: @1 = 1.0, @3 = 1.0, @5 = 1.0.
Honest limits: extraction is deterministic heuristics, not an
LLM extractor (follow-up: a cheap/local LLM extractor once the
brain has cheap LLM calls); `MemoryStore` is in-memory, no
Postgres persistence yet; the recall set is small (6 queries,
8 memories), so 1.0 proves the mechanism works, not
generalisation to noisy real data.

### graphiti: temporal edges

Pattern: getzep/graphiti (Apache-2.0). No code copied, the
library is not installed (it needs Neo4j or FalkorDB, too heavy
for the MVP). `puspa/graphs/temporal.py` (new, ~360 lines):
`TemporalEdge` with `valid_from` / `valid_until` (the validity
window is the single source of truth, `valid_until` exclusive,
`None` means open); `TemporalGraph.query` / `neighbors` /
`relations` / `current_fact` return only edges still valid at
`as_of` by default; `include_stale=True` returns everything
marked `stale` with a `stale_reason` (expired / superseded /
not_yet_valid); supersede closes the old edge at the start of
the new fact and keeps history via `history()`; `prune_expired`
is opt-in and explicit; `stale_fact_rate`; `from_knowledge_graph`
adapts the retrieval `KnowledgeGraph` without changing
`puspa/retrieval/`, and edges without a validity entry keep
working as before. Supersede defaults to off in `add_edge`
because the retrieval graph is multi-valued (one seller sells
many products); the adapter filters stale facts by window, not
by replacement. Tests: `tests/test_graph_temporal.py`, 16 tests,
all in-memory. Measured stale-fact rate on a fixed 12-fact set:
baseline without validity 0.5 (6 of 12 stale), with validity
enforced 0.0. Honest limits: in-memory only, no Postgres
persistence yet (validity columns for `price_series` /
`listing_links` need a schema migration); validity windows must
be supplied by the caller (no automatic TTL invented); supersede
is deterministic on (subject, relation), not an LLM judge.

### snyk/agent-scan: CI scanner

Pattern: snyk/agent-scan (Apache-2.0). No code copied, no fork.
`puspa/policy/agent_scan.py` (new): scans a community skill
directory BEFORE the signed-skill gate; reads `SKILL.md`, the
declared `manifest.json` (tools, domains, connectors), and every
other text file in the directory, and flags concrete threat
patterns drawn from the 341 malicious ClawHub skills named in
the triage (Tier 2 item 16): exfiltration to an undeclared host,
exfiltration of secret material, credential access, prompt
injection (override, hidden, role reassignment, base64-hidden
instructions), gate bypass, permission overreach, rug-pull
(remote code fetched and executed, or live instructions fetched
at run time), toxic flow (destructive commands). Every rule is a
concrete pattern with a firing fixture, and every finding names
its rule and its location (`file:line`). A network call is only
flagged when its host is not declared in the manifest `domains`
or `allowed_hosts`; a plain documentation link is not flagged.
Reading an environment variable for a named provider API key is
NOT flagged on its own. Tests: `tests/test_agent_scan.py`,
29 tests. Honest limits: static text matching, not execution and
not an LLM judge; the scanner cannot prove intent, only the
pattern, and that boundary is deliberate and covered by tests.
No CI workflow file was added (the repo has no `.github/workflows/`
yet; CI is maturity-phase item G15).
