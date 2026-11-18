# NOTICE — Third-party code attribution in the Puspa repo

This is the standalone Puspa repo (mirzasnowy/puspa-ai), NOT a fork
of any repo. Third-party code that is reused is recorded here,
with its original license kept alongside. Non-commercial or
copyleft licenses (GPL, CC BY-NC-SA, PolyForm Noncommercial) are not used.

## nanobot (chassis) — MIT

- **Source:** https://github.com/HKUDS/nanobot
- **Version used as reference (local clone, not yet vendored):** HEAD `e767174` (17 Nov 2026), package `nanobot-ai` 0.4.1
- **Location in this repo:** the `nanobot/` directory (a full copy from the clone,
  including its original `LICENSE` file)
- **License:** MIT License
- **Copyright:** Copyright (c) 2025-present Xubin Ren and the nanobot contributors
- **Full license text:** `nanobot/LICENSE`
- **Third-party attribution inside nanobot:** `nanobot/THIRD_PARTY_NOTICES.md`
  (e.g. Tabler Icons, MIT). If nanobot code is reused outside this
  repo, the attribution above plus the LICENSE file must travel with it.

## Reuse rules (Nero's order)

- Never create a GitHub fork of any repo.
- Other people's code is used as a pip dependency, or vendored with its
  original LICENSE file + attribution in this file and in the README.
- Every new vendored entry must be recorded in this file before it is used.


# NOTICE — Puspa Phase 1

## Pattern attribution (not code)

Phase 1 studied the PATTERNS of the following repos and then REWROTE them
as Puspa's own code (no code copied verbatim, no new dependencies):

- **mem0ai/mem0** (Apache License 2.0) — pattern: conversational memory
  layer, sourced facts, scoped per user/agent/app.
  Used as a pattern in `puspa/memory/profile.py`.
- **getzep/graphiti** (Apache License 2.0) — pattern: temporal knowledge
  graph (facts have a validity period); procedures as templates without
  personal data. Used as a pattern in `puspa/memory/profile.py`
  and `puspa/memory/procedural.py`.
- **letta-ai/letta** (Apache License 2.0) — pattern: tiered memory +
  sleep-time compute (tidying memory while idle). Used as a
  pattern in `puspa/memory/consolidate.py`.
- **elizaOS/eliza** (MIT License) — pattern: character file (persona
  as data: name, traits, speaking style). Used as a pattern in
  `puspa/persona/persona.py`.

The full license of each repo lives in its source repo (links in
`docs/reuse.md`). No third-party LICENSE file needs to be
included here because no third-party code is vendored.

# NOTICE — Puspa Brain phase

## Pattern attribution (not code)

The Brain phase studied the PATTERNS of the following repos and then
REWROTE them as Puspa's own code (no code copied verbatim, no new
dependencies except fastembed, listed below):

- **joonspk-research/generative_agents** (MIT License) — pattern:
  memory stream plus reflection into higher-level inferences plus
  plans retrieved and revised. Used as a pattern in
  `puspa/world/model.py` and `puspa/memory/consolidate.py`.
- **MineDojo/Voyager** (MIT License) — pattern: automatic
  curriculum, self-verification, skill library of tested
  executable code indexed by embeddings. Used as a pattern in
  `puspa/world/model.py`, `puspa/harness/executor.py`, and
  `puspa/memory/learning_loop.py`. Voyager is a dormant research
  archive; its Minecraft code was not used.
- **NousResearch/hermes-agent** (MIT License) — pattern: skills
  created and repaired automatically, persistent user model,
  learning loop. Used as a pattern in
  `puspa/memory/learning_loop.py` and `puspa/chassis/hooks.py`.
- **letta-ai/letta-code** (Apache License 2.0) — pattern:
  self-improvement from experience. Used as a pattern in
  `puspa/memory/procedural.py` (decay, contradiction).
- **HKUDS/LightRAG** (MIT License) — pattern: dual-level retrieval
  (specific entities plus abstract concepts, over a graph plus
  vectors). Used as a pattern in `puspa/memory/episodic.py` and
  `puspa/graphs/identity.py`. The library was not installed.
- **anomalyco/opencode** (MIT License) — pattern: coding agent loop
  (write code, run tests, fix, repeat) and per-tool permission
  model. Used as a pattern in `puspa/harness/loop.py` and
  `puspa/policy/tokens.py`. The tool was not installed.
- **SWE-bench/SWE-bench** (MIT License) — pattern: harness task
  shape (fixed FAIL_TO_PASS tests decide pass/fail). Used as a
  pattern in `puspa/harness/loop.py`. No dataset or score is used.
- **OpenBMB/ChatDev** (Apache License 2.0) — pattern: readable
  handoffs between steps. Used as a pattern in
  `puspa/harness/loop.py`.
- **FoundationAgents/OpenManus** (MIT License) — pattern: goal to
  plan to tool loop with replanning on failure. Used as a pattern
  in `puspa/runtime/planner.py`.
- **UKGovernmentBEIS/inspect_ai** (MIT License) — pattern: task,
  solver, scorer structure; scoring from evidence, not words. Used
  as a pattern in `puspa/evidence/eval_harness.py`.
- **sierra-research/tau-bench** (MIT License) — pattern: simulated
  user plus domain policy plus final database state decides
  success. Used as a pattern in `puspa/evidence/eval_harness.py`.
- **EleutherAI/lm-evaluation-harness** (MIT License) — pattern:
  versioned task configs with a pinned prompt and scorer. Used as
  a pattern in `puspa/evidence/eval_harness.py`.
- **stanfordnlp/dspy** (MIT License) — pattern: programs, not
  prompts; declarative modules tuned by an optimiser against a
  metric on a fixed dataset. Used as a pattern in
  `puspa/memory/optimizer.py`. The library was not installed.
- **pydantic/pydantic-ai** (MIT License) — pattern: type-safe
  structured output. Used as a pattern in `puspa/chassis/tools.py`
  and `puspa/evidence/eval_harness.py`.
- **modelcontextprotocol/python-sdk** (MIT License) — pattern:
  official MCP client (list tools, call a tool with typed
  arguments, over stdio). Used in `puspa/chassis/tools.py` through
  the installed SDK (`mcp==1.30.0`), behind the signed-skill gate
  and the outbound gate.
- **affaan-m/ECC** (MIT License) — pattern: skill collection plus
  hooks. Used as a pattern in `puspa/chassis/hooks.py`.
- **strands-agents/harness-sdk** (Apache License 2.0) — pattern:
  model-driven agent loop with Cedar-style approval policies. The
  approval-policy idea only; the SDK was not installed.
- **langchain-ai/langgraph** (MIT License) — pattern: stateful
  orchestration with checkpoints and resume, idea only. The
  library was not installed and no LangGraph code is used.
- **anthropics/claude-code** (PROPRIETARY) — pattern study only,
  from public docs: per-tool permissions and approval before a
  consequential action. No claude-code code was used; the leaked
  March 2026 source-map code was never looked at.

## New dependency: fastembed (MIT License)

- **Source:** https://github.com/qdrant/fastembed
- **Version:** `fastembed==0.9.1`, installed via pip (see
  `requirements.txt`). The package is a pip dependency, not vendored.
- **Model:** BAAI/bge-small-en-v1.5 (ONNX, about 65 MB), downloaded
  from HuggingFace at first use and cached locally.
- **License:** MIT License
- **Use in this repo:** optional local embedding provider in
  `puspa/memory/embeddings.py`; falls back to the built-in
  `HashEmbedding` when fastembed or the model is unavailable.

The full license of each repo lives in its source repo (links in
`docs/reuse.md` and `REUSE.md`). No third-party LICENSE file needs
to be included here because no third-party code is vendored.

# NOTICE - Puspa Brain deepening phase, Wave 1 (18 Nov 2026)

## Pattern attribution (not code)

- **sigstore/sigstore** (Apache License 2.0) - pattern: keyless
  signing with an ephemeral key, DSSE envelope, append-only
  transparency log with Merkle inclusion proofs. Used as a pattern
  in `puspa/policy/signing.py` (local implementation, not the
  public sigstore service; no Fulcio, no Rekor, no network call).
- **sigstore/sigstore-python** (Apache License 2.0) - dependency:
  real Sigstore bundle verification
  (`puspa/policy/sigstore_verify.py`, `sigstore==4.5.0`); test
  fixture `bundle_v3` from its public test assets at commit
  `7ee385ac8be192771bff1cc553434b11282ecce6`, embedded unchanged
  in `tests/test_sigstore_verify.py`.
- **dsse-lf/dsse** (Apache License 2.0) - pattern: DSSE envelope
  shape and the pre-authentication encoding rule. Only the
  specification was followed; no code was taken.
- **Aider-AI/aider** (Apache License 2.0) - pattern: concise repo
  map with classes/functions plus signatures under a token budget;
  surgical edits as unified diffs with lint/test after the edit.
  Used as a pattern in `puspa/harness/repo_map.py` and
  `puspa/harness/diff.py`.
- **SWE-agent/SWE-agent** (MIT License) - pattern: reproduce-first
  coding loop (failing reproduction test before the fix, proof of
  fix after). Used as a pattern in `puspa/harness/reproduce.py`
  and `puspa/harness/loop.py`.
- **openai/codex** (Apache License 2.0) - pattern: sandbox profiles
  (read-only / workspace-write / full) plus approval before acting
  outside the boundary. Used as a pattern in
  `puspa/harness/sandbox_profiles.py`. Puspa's `full` is NOT Codex
  `danger-full-access`.
- **OSU-NLP-Group/HippoRAG** (MIT License) - pattern: schema-less
  knowledge graph built at index time, multi-hop retrieval by
  Personalized PageRank, HippoRAG 2 style filtering and
  re-ranking. Used as a pattern in
  `puspa/retrieval/knowledge_graph.py`,
  `puspa/retrieval/hipporag.py`, and
  `puspa/retrieval/triple_filter.py`.
- **HKUDS/LightRAG** (MIT License) - pattern: dual-level retrieval
  and the five query modes local, global, hybrid, naive, mix, plus
  incremental graph update. Used as a pattern in
  `puspa/retrieval/modes.py`.
- **LlamaIndex** (MIT License) - pattern: reranker as a node
  postprocessor between retrieval and synthesis, and query
  decomposition into sub-questions. Used as a pattern in
  `puspa/retrieval/reranker.py`. The cross-encoder itself is
  `cross-encoder/ms-marco-MiniLM-L-6-v2` (Apache License 2.0)
  via sentence-transformers 6.3.0 (Apache License 2.0).
  Query decomposition in `puspa/retrieval/reranker.py` is
  inspired by the LlamaIndex sub-question query engine
  pattern. The implementation is original Puspa code; no
  LlamaIndex source code is included.
- **google/adk-python** (Apache License 2.0) - pattern:
  hierarchical multi-agent composition (sequential/parallel/loop),
  session state with user/app/temp scopes, memory service, tool
  confirmation callbacks. Used as a pattern in
  `puspa/runtime/hierarchy.py`.
- **NVIDIA/NeMo-Guardrails** (Apache License 2.0) - pattern:
  programmable input/output rails, dialog flows as state machines,
  fail-closed checks. Used as a pattern in
  `puspa/guardrails/rails.py`.

# NOTICE - Puspa Brain deepening phase, Tier 2 Batch A (18 Nov 2026)

## Pattern attribution (not code)

- **NousResearch/hermes-agent** (MIT License) - pattern:
  automatic skill repair (a failing skill is diagnosed,
  rewritten, and re-verified) as part of one learning loop.
  Used as a pattern in `puspa/memory/skill_repair.py` (a repair
  always creates a NEW draft and never overwrites a manual edit;
  the production rewriter is an injected LLM callable, the
  built-in is a deterministic lookup rewriter).
- **letta-ai/letta-code** (Apache License 2.0) - pattern:
  repeated task results change the agent's own procedures (a new
  version with trial status and a supersedes link; conflicts held
  as candidates, never overwriting). Used as a pattern in
  `puspa/memory/skill_repair.py` (`ProcedureUpdater`).
- **HKUDS/LightRAG** (MIT License) - pattern: incremental graph
  update (merge new passages without a full rebuild). Used as a
  pattern in `puspa/retrieval/incremental.py`.
- **Kilo-Org/kilocode** (MIT License, verified via the GitHub API
  18 Nov 2026) - pattern: sub-agent working modes (Architect /
  Code / Debug / Ask) with behavior enforced per mode. Used as a
  pattern in `puspa/harness/modes.py` and `puspa/harness/loop.py`.
- **THUDM/AgentBench** (Apache License 2.0) - pattern:
  multi-environment held-out task set (DB, KG, OS, WebShop
  shapes) with final-state scoring, disjoint from the dev set.
  Used as a pattern in `puspa/evidence/agentbench_tasks.py`.
- **mem0ai/mem0** (Apache License 2.0) - pattern: memory
  extraction (deciding what becomes a memory), dedup of the same
  fact, conflict detection at extraction time, and ranked recall.
  Used as a pattern in `puspa/memory/extraction.py`
  (deterministic heuristics, not mem0's LLM extractor).
- **getzep/graphiti** (Apache License 2.0) - pattern: temporal
  graph, every fact with a validity period (`valid_from` /
  `valid_until`), expired facts no longer counted as true, new
  facts superseding old ones with history kept. Used as a pattern
  in `puspa/graphs/temporal.py` (the library is not installed; it
  needs Neo4j or FalkorDB, too heavy for the MVP).
- **snyk/agent-scan** (Apache License 2.0) - pattern: scan of MCP
  servers and skills for tool poisoning, rug-pull, prompt
  injection, and toxic flow, as a gate before the signed-skill
  gate. Used as a pattern in `puspa/policy/agent_scan.py`
  (static text matching, not execution and not an LLM judge).
- **ShishirPatil/gorilla** (Apache License 2.0) - pattern:
  BFCL-style evaluation format (multi-turn tasks, per-turn
  reference function calls, exact-match scoring on function
  name, argument values and types, call order, missing and
  extra calls). Used as a pattern in
  `puspa/evidence/bfcl_checker.py` and
  `puspa/evidence/bfcl_tasks.py`; all Puspa task data is
  original, no BFCL task data was copied.
- **rtk-ai/rtk** (Apache License 2.0) - pattern: per-command
  output filters with fail-safe to raw output ("never blocks,
  never worse"). Used as a pattern in
  `puspa/router/compression.py` and the wiring in
  `puspa/chassis/tools.py`. The BUSL-1.1 engine and proxy were
  not used; no RTK code was copied.
- **decolua/9router** (MIT) - pattern: smart 3-tier fallback
  (subscription -> cheap -> free, automatic fallthrough on
  quota exhaustion / 429 / 5xx) plus per-provider quota
  tracking, reset countdowns, and cost estimates. Used as a
  pattern in `puspa/router/fallback.py` and
  `puspa/router/quota.py`; no 9Router code was copied. Puspa's
  tiers are cost bands over its own OpenRouter catalog (no
  flat-rate subscription or zero-cost provider is wired yet),
  and quota state is in-memory until Postgres persistence
  lands.
- **decolua/9router** (MIT) - pattern: OAuth auto-refresh and
  provider format translation. Used as a pattern in
  `puspa/router/oauth.py` and `puspa/router/translate.py`;
  no 9Router code was copied.

# NOTICE - Puspa Brain deepening, rework 1 follow-up: bge reranker swap (18 Nov 2026)

## New dependency: FlagEmbedding (MIT License)

- **Source:** https://github.com/FlagOpen/FlagEmbedding
- **Version:** `FlagEmbedding==1.4.2`, installed via pip (see
  `requirements.txt`), with its direct dependencies
  `datasets==5.2.0`, `accelerate==1.16.0`, `peft==0.21.3`,
  `ir-datasets==0.6.3`, `sentencepiece==0.2.3`. The package is
  a pip dependency, not vendored.
- **License:** MIT License (verified from the GitHub repo
  LICENSE file, "MIT License, Copyright (c) 2022 staoxiao",
  and the GitHub API SPDX tag `MIT`, 18 Nov 2026)
- **Use in this repo:** the multilingual cross-encoder
  backend in `puspa/retrieval/reranker.py`
  (`CrossEncoderReranker.from_flagembedding`, default backend
  of `make_reranker(kind="cross_encoder")`), scoring
  (query, passage) pairs through `FlagReranker.compute_score`.

## Model: BAAI/bge-reranker-v2-m3 (Apache License 2.0)

- **Source:** https://huggingface.co/BAAI/bge-reranker-v2-m3
- **License:** Apache License 2.0 (verified from the
  HuggingFace API tag `license:apache-2.0`, 18 Nov 2026)
- **Use in this repo:** the default cross-encoder model
  (`DEFAULT_BGE_RERANKER_MODEL`); weights about 2.3 GB
  (fp32), downloaded from HuggingFace at first use and cached
  locally in `~/.cache/huggingface`. No model code is vendored
  and no BAAI code was copied.

# NOTICE - Puspa maturity phase, G11 (18 Nov 2026)

## Pattern attribution (not code)

- **DuskBot** (PolyForm Noncommercial License 1.0.0 — study only,
  no code used, no dependency) - patterns only, rewritten as Puspa
  Python code (DuskBot is TypeScript; nothing was copied verbatim):
  loopback trust (Host + Origin loopback, browsers never trusted)
  and the explicit `SERVICE_ALLOW` list from
  `server/request-auth.ts`; the HPKE card seal (per-start sealing
  key never written to disk, each seal signed by the long-term
  server identity key pinned by the app) from
  `server/card-seal.ts`; pairing (short code, 30-day session,
  lockout after wrong codes) from `server/sessions.ts`; the
  7-character link code (no l/o/0/1), rate limits (30/hour per
  address, 2,000/hour total), AES-256-GCM relay frames, and the
  fallback order direct → Cloudflare tunnel → relay from
  `apps/link-service/` and `apps/cloudflare/link-relay/`. Used as
  patterns in `puspa/hosting/` (`loopback.py`, `service_allow.py`,
  `gateway.py`, `pairing.py`, `identity.py`, `seal.py`, `link.py`,
  `relay.py`, `fallback.py`). Because the license is PolyForm
  Noncommercial, no DuskBot code is vendored, linked, or depended
  on, and the patterns are not used to build a competing DuskBot
  product — they implement Puspa's own multi-tenant hosting auth.
  No new pip dependency was added for G11 (`cryptography==50.0.2`,
  already pinned, provides the HPKE suite).

# NOTICE - Puspa maturity phase, G12 (18 Nov 2026)

## Pattern attribution (not code)

- **DuskBot** (PolyForm Noncommercial License 1.0.0 — study only,
  no code used, no dependency) - patterns only, rewritten as Puspa
  Python code (DuskBot is TypeScript; nothing was copied verbatim):
  the periodic sentinel scanner over the server's own records with
  five detectors (`toolError`, `turnStop`, `queueWait`, `cost`,
  `retryLoop`), default thresholds (queue 5 minutes, cost $5,
  retry minimum 3, 7-day lookback, 30-minute rescan), and the
  state file (`readState` / `readIssues` / `markCounted` /
  `upsertIssues` / `pruneSeen` / `writeSentinelFiles`) from
  `server/sentinel/`; the decision log (actor, source, via into
  daily files, 180-day retention) from `server/decision-log.ts`;
  usage traces as span trees from `server/usage-trace/`; anonymous
  analytics + crash reports that can be switched off; and the
  verification discipline (isolated fixtures, never verify a
  mutation on live user data) from `docs/verification/`. Used as
  patterns in `puspa/sentinel/` (`detectors.py`, `thresholds.py`,
  `state.py`, `scanner.py`, `decision_log.py`, `traces.py`,
  `analytics.py`) and in the "Sentinel self-checks" section of
  `docs/verification.md`. Because the license is PolyForm
  Noncommercial, no DuskBot code is vendored, linked, or depended
  on, and the patterns are not used to build a competing DuskBot
  product — they implement Puspa's own self-observability.
- **NousResearch/hermes-agent** (MIT License) - patterns only,
  rewritten as Puspa Python code (nothing copied verbatim): the
  batch trajectory runner (`batch_runner.py` — checkpoint +
  resume, aggregate tool usage) and trajectory storage with
  redaction before saving (`agent/trajectory.py`) plus the
  ShareGPT trajectory format (`trajectory-format.md`), used as
  patterns in `puspa/sentinel/trajectory.py` and
  `docs/trajectory-format.md`; session insights from `state.db`
  (tokens, cost estimate with sub-cent labels at 4 decimals,
  tool/skill usage, activity, model/platform breakdown) from
  `agent/insights.py`, used as a pattern in
  `puspa/sentinel/insights.py`. No new pip dependency was added
  for G12.

# NOTICE - Puspa maturity phase, G1 (18 Nov 2026)

## Pattern attribution (not code)

- **OpenBot** (MIT License) - patterns only, rewritten as Puspa
  TypeScript code (nothing copied verbatim): the API-first order
  (OpenAPI spec first, TypeScript client generated from it) and
  the product primitives as a consistent menu structure. Used as
  patterns in `docs/openapi-pwa.yaml`,
  `apps/pwa/src/api/generated-types.ts` (generated with
  openapi-typescript), and the 7-screen menu in
  `apps/pwa/src/api/bootstrap.ts` / `src/components/Nav.tsx`.
- **DuskBot** (PolyForm Noncommercial License 1.0.0 — study only,
  no code used, no dependency) - patterns only, rewritten as Puspa
  TypeScript code (nothing copied verbatim): the
  `components/` / `lib/` / per-feature `state/` split and the
  one-tap approval card (`ApprovalCard` / `AskCard`). Used as
  patterns in `apps/pwa/src/components/`,
  `apps/pwa/src/state/` (one store file per screen), and
  `apps/pwa/src/api/`. Because the license is PolyForm
  Noncommercial, no DuskBot code is vendored, linked, or depended
  on, and the patterns are not used to build a competing DuskBot
  product — they implement Puspa's own PWA surface.
- **NousResearch/hermes-agent** (MIT License) - patterns only,
  rewritten as Puspa TypeScript code (nothing copied verbatim):
  the React + Vite dashboard page list (Chat, Channels, Config,
  Cron, Docs, Files, Logs, MCP, Models, Pairing, Plugins,
  ProfileBuilder, Profiles, Sessions, Skills, Analytics, System,
  Webhooks) used as a checklist; the relevant pages were folded
  into Puspa's 7 screens as sections, not new screens.

## New npm dependencies (G1)

All MIT except TypeScript (Apache-2.0); licenses read from each
package's own `package.json`: `react@19.3.1` (MIT),
`react-dom@19.3.1` (MIT), `vite@6.4.6` (MIT),
`@vitejs/plugin-react@4.7.0` (MIT), `typescript@5.9.3`
(Apache-2.0), `vitest@3.2.7` (MIT), `jsdom@26.1.0` (MIT),
`@testing-library/react@16.3.3` (MIT),
`@testing-library/user-event@14.6.12` (MIT),
`openapi-typescript@7.13.0` (MIT), `@types/react@19.3.0` (MIT),
`@types/react-dom@19.3.0` (MIT). No dependency with a
non-commercial or copyleft license was added.

# NOTICE - Puspa maturity phase, G13 (18 Nov 2026)

## Pattern attribution (not code)

- **DuskBot** (PolyForm Noncommercial License 1.0.0 — study only,
  no code used, no dependency) - patterns only, rewritten as Puspa
  Python code (DuskBot is TypeScript; nothing was copied verbatim):
  the team as its own entity, separate from billing, with
  owner/member roles, from `server/cloud-team*.ts` and
  `server/team-*.ts`; invitations via token; visibility as a
  function of (thread, member); per-member bot access
  (`bot-access-gate.ts`, `access-requests.ts`); the signed
  entitlement per machine-id as the "may this home run" gate
  (`cloud-entitlement.ts`: without a valid entitlement only a
  signed admin and the liveness probe are answered); and workspace
  moves with move-freeze (`bot-moves.ts`, `move-freeze.ts`). Used
  as patterns in `puspa/teams/` (`teams.py`, `invitations.py`,
  `visibility.py`, `entitlements.py`, `moves.py`) and
  `infra/schema_teams.sql`. Because the license is PolyForm
  Noncommercial, no DuskBot code is vendored, linked, or depended
  on, and the patterns are not used to build a competing DuskBot
  product — they implement Puspa's own multi-tenant teams.
- **OpenBot** (MIT License) - patterns only, rewritten as Puspa
  Python code (nothing copied verbatim): organisations, roles, and
  org audit. Used as a pattern for the team roles and the durable
  `bot_access_requests` audit table (`puspa/teams/visibility.py`,
  `infra/schema_teams.sql`).
- **NousResearch/hermes-agent** (MIT License) - patterns only,
  rewritten as Puspa code (nothing copied verbatim): the multiplex
  + hosted room shape and the multi-tenant deploy shape —
  multi-stage `Dockerfile` (s6-overlay as PID 1, UID/GID remap,
  dashboard bound to 127.0.0.1 by default, remote access only via
  SSH tunnel or authenticated reverse proxy, API server dead unless
  its key is uncommented) and `docker-compose.yml` (gateway +
  dashboard). Used as patterns in `Dockerfile`,
  `docker-compose.yml`, and `puspa/teams/deploy.py` (offline
  validation of both files). No new pip dependency was added for
  G13 (`cryptography==50.0.2`, `psycopg`, `PyYAML`, all already
  pinned).

## Puspa license

The code in this directory is Puspa's own work (Nero / Luna). The Puspa
repo license follows the `mirzasnowy/puspa-ai` repo on push.

# NOTICE - Puspa maturity phase, G14 (18 Nov 2026)

## Pattern attribution (not code)

- **NousResearch/hermes-agent** (MIT License) - patterns only,
  rewritten as Puspa Python code (nothing copied verbatim): the
  multi-channel gateway shape — one core, many adapters, platform
  events normalised to `MessageEvent`, then the same flow
  (authorise -> resolve session key -> agent -> reply through the
  origin adapter); the `GatewayRunner` facade with one module per
  topic (inbound, delivery, notification, heartbeat); the
  delivery ledger + bot-loop guard + heartbeat/restore; DM pairing
  as authorisation; the single-writer gateway profile; and the
  step-by-step adapter guide (`ADDING_A_PLATFORM.md`). Used as
  patterns in `puspa/channels/` (16 modules), `docs/channels.md`,
  and `docs/adding-a-platform.md`.
- **DuskBot** (PolyForm Noncommercial License 1.0.0 — study only,
  no code used, no dependency) - patterns only, rewritten as Puspa
  Python code (nothing copied verbatim): notifications carry only
  the bot name, never message content (`server/phones/`), and
  pairing via a short code. Used as patterns in
  `puspa/channels/notify.py` and `puspa/channels/authz.py`
  (`ChannelPairing`: 6-digit codes, hash-only, 5-minute TTL,
  single use, per-chat lockout). Because the license is PolyForm
  Noncommercial, no DuskBot code is vendored, linked, or depended
  on, and the patterns are not used to build a competing DuskBot
  product — they implement Puspa's own Telegram/WhatsApp channels.
- **OpenBot** (MIT License) - patterns only, rewritten as Puspa
  Python code (nothing copied verbatim): channels as access
  surfaces (the home stays the PWA). Used as a pattern for the
  channel package itself (`puspa/channels/`).

## New dependency: none

No new dependency was added for G14. Both adapters use the
already-pinned `httpx==0.28.1`. python-telegram-bot was NOT used
(it is LGPL); the Telegram Bot API is plain HTTPS and needs no
library.

# NOTICE - Puspa maturity phase, G21 (18 Nov 2026)

## Pattern attribution (not code)

- **NousResearch/hermes-agent** (MIT License) - patterns only,
  rewritten as Puspa Python code (nothing copied verbatim): the
  thin facade + one module per turn phase (iteration prep, API
  call, API error, overflow, truncation, recovery); the
  interruptible model call (request on a background thread, main
  thread waits for response / interrupt event / timeout, reply
  discarded on interrupt, no partial response in history); one
  internal message format for three API modes (chat completions /
  Responses / Anthropic Messages); layered fallback (backup
  providers in order on 429/5xx, auth errors refresh credentials
  first, auxiliary tasks with their own independent chain);
  iteration budgets (500 per agent, 50 per subagent, separate
  cap); message alternation enforced in code; tools intercepted
  before the registry (`todo`, `memory`, `session_search`,
  `delegate_task`). Used as patterns in `puspa/turns/` (14
  modules), `puspa/router/chains.py`, the `auth_error` kind in
  `puspa/router/fallback.py`, `complete_messages` in
  `puspa/router/complete.py`, and `docs/turn-loop.md`. The loop
  that was split is Puspa's own `TaskRunner`, not hermes-agent's
  agent: the turn loop is the model-call engine inside
  `TaskRunner`'s `run` stage, wired via `make_task_executor`.

## New dependency: none

No new dependency was added for G21. No hermes-agent code was
copied, vendored, or depended on; only the documented patterns
above were followed, rewritten as Puspa's own code.
# NOTICE - Puspa maturity phase, G4 + G5 + G6 (18 Nov 2026)

## Pattern attribution (not code)

- **OpenBot** (MIT License for the repo; the Intelligence
  component is separately licensed and the enforcement server
  image is closed — neither was used) - patterns only, rewritten
  as Puspa Python code (nothing copied verbatim): the approval
  taxonomy (action kinds that always ask, tiered answers Allow
  once / for this conversation / always, admin closure per kind,
  strictest-wins rules, auto-review, extra vigilant switch,
  check-in tasks asking before every change, action recognition
  from the tool name's first verb and the button label, payment
  recognition from the word `payment` / payment tool names /
  payment button labels, credential changes never approvable,
  anti-double-approval via the `ASKING_GUIDANCE` marker,
  destructive tools marked in their description) from
  `docs/approvals.md` + `app/src/lib/approval-rules/`; the
  fail-closed gateway (single action boundary: resolve target
  from the server snapshot, evaluate the action policy, audit
  the decision, call the computer only when forwarded, audit a
  second line when a forwarded action fails; CEL-style rules
  with case-insensitive `contains()` / `matches()` over
  `tool.name`, `intent`, `bot.id`, `actor.id`, `page.url`,
  `page.host`, `element.ref/role/name/type`, `key`, `command`,
  `file.path/name/extension`, `mcp.server/tool/effect`,
  `initiator.kind/id`; deny before allow; missing/empty policy
  allows nothing; broken deny rule denies; broken allow rule
  does not allow; malformed policy halts startup; dry-run
  decides + records but still forwards; snapshot kept fresh via
  Postgres `LISTEN`/`NOTIFY`; never-loaded snapshot refuses
  everything; rule templates carrying their cost; cloud metadata
  address refused in all modes; egress filtering does not stop
  raw sockets) from `docs/architecture.md` +
  `app/src/lib/boundaries/rules.ts` — the enforcing CEL code is
  not in the OpenBot repo, so the evaluator is Puspa's own,
  written from the documented semantics; the audit trail with
  initiator (`person`, `deployment`, `routine`, `responsibility`,
  `memory`, `handoff`; "Nobody watching" = routine + handoff +
  responsibility + memory; handover `help_requested` /
  `control_taken` / `control_released` audited as control events;
  bot actions rejected, not queued, while a human controls the
  browser; CSV export capped at 5,000 rows with a truncation
  marker, `'` prefix on cells starting with `= + - @`, every
  export itself recorded with who / how many rows / which
  filters; retention via env var, unset = forever; secrets
  redacted; saved files contribute path + size, never content)
  from `app/src/lib/audit/` + `api/openapi/audit-events.yaml` +
  `shared/audit-words.ts`. Used as patterns in
  `puspa/policy/approvals.py`, `puspa/policy/gateway.py`,
  `puspa/policy/rules.py`, `puspa/ledger/audit.py`,
  `docs/approvals.md`, `docs/gateway.md`, `docs/audit-trail.md`.

## New dependency: none

No new dependency was added for G4+G5+G6. No OpenBot code was
copied, vendored, or depended on; only the documented patterns
above were followed, rewritten as Puspa's own code. The rule
language evaluator, the approval taxonomy, and the audit export
log are Puspa's own implementations.
# NOTICE - Puspa maturity phase, G29 (18 Nov 2026)

## Pattern attribution (not code)

- **Graphify-Labs/graphify** (Apache License 2.0) - patterns
  only, rewritten as Puspa Python code (nothing copied
  verbatim): building a knowledge graph from a codebase
  (parse source files into function / class / import / call
  nodes and edges, answer questions from the graph instead
  of re-reading the files). Used as the pattern in
  `puspa/retrieval/code_graph.py` (`CodeExtractor`, a
  two-pass `ast` walk; `CodeGraph`, incremental per-file
  indexing into the shared `KnowledgeGraph`; graph queries
  `callers_of` / `callees_of` / `reachable_from` /
  `reaches` / `call_path` / `defined_in` / `imports_of`)
  and `docs/code-graph.md`. The entities and edges live in
  Puspa's existing shared knowledge graph, not a second
  parallel graph.

## New dependency: none

No new dependency was added for G29. No Graphify code was
copied, vendored, or depended on; only the documented
pattern above was followed, rewritten as Puspa's own code.
The live LLM extractor default (`JsonTripleExtractor`
resolving `"default"` through the router's
`resolve_llm_complete`) reuses Puspa's own G21 completion
seam; no new provider dependency is introduced.

# NOTICE - Puspa maturity phase, G30 (18 Nov 2026)

## Pattern attribution (not code)

- **vercel-labs/skills** (MIT License) - patterns only,
  rewritten as Puspa Python code (nothing copied verbatim):
  discovering installable agent skills from a registry
  (search the skills.sh API, check the install leaderboard
  first, fall back to dropping one query word when exact
  results are under 10 and merge at most 25, verify quality
  before recommending: prefer 1K+ installs, official sources
  trusted, repos under 100 stars suspect, present options,
  install only with user approval). Used as the pattern in
  `puspa/marketplace/discovery.py` (`SkillsShClient`,
  `search_skills`, `assess_quality` / `rank_results`,
  `DiscoveryCache`, `InstallApproval`, `detect_license`,
  `install_discovered`), the built-in
  `skills/find-skills/SKILL.md` + `manifest.json`, and
  `docs/skill-discovery.md`. The Node CLI was not ported;
  installs go through Puspa's own G3 marketplace registry.

## New dependency: none

No new dependency was added for G30. The search client uses
the already-pinned httpx, and all tests use
`httpx.MockTransport` (no live calls). No vercel-labs code was
copied, vendored, or depended on; only the documented pattern
above was followed, rewritten as Puspa's own code.

# NOTICE - Puspa maturity phase, G33 (18 Nov 2026)

## Pattern attribution (not code)

- **No source repo.** G33 implements Nero's own product
  decision, locked in `docs/modes-decision.md`: the user picks
  the model provider in PWA Settings — a BYOK key (stored in
  the vault) vs a local model base URL (Ollama default
  `localhost:11434`) vs the hosted default — and the choice is
  wired through to the router's `llm_complete` path. No pattern
  or code was taken from any other repository for this item.

## New dependency: none

No new dependency was added for G33. The BYOK key reuses the
existing per-user vault (`puspa.accounts.vault`, entry
`byok:<provider>`); the selection persists in a new
`model_settings` table (choice only, no key column); the router
wiring reuses the existing `complete_messages` `base_url` /
`api_key` / `model` seam plus a small optional `provider`
argument for labelling unlisted models. All tests use
`httpx.MockTransport` and a clearly fake key (no live calls).
No code from any other repo was copied, vendored, or depended
on.

# NOTICE - Puspa maturity phase, G32 (19 Nov 2026)

## Pattern attribution (not code)

- **No external source.** G32 (WebUI lite as a real mode) is
  Nero's product decision, locked in `docs/modes-decision.md`:
  one web codebase (PWA), two serve modes, with the Jinja
  templates as the no-install trial surface (one chat page
  plus an upgrade button; trial is an entitlement, not a UI).
  It is not a pattern from another repo, so there is no
  upstream project or license to attribute. The trial check
  reuses Puspa's own G2 billing entitlements
  (`puspa/billing/entitlements.py`, HMAC-signed, Pro tier,
  `trial_days` from `BillingConfig`) and the G1 Jinja
  fallback in `puspa/pwa/app.py`; the lite module
  (`puspa/pwa/lite.py`), the trial routes, the template
  fixes, and the new `templates/trial.html` are Puspa's own
  code, written from scratch.

## New dependency: none

No new dependency was added for G32. The lite module uses
only the standard library plus the already-pinned FastAPI /
Jinja2 / billing stack, and all tests use the FastAPI
`TestClient` with issued-then-verified entitlements (no live
calls). No code from any other repo was copied, vendored, or
depended on.

# NOTICE - Puspa maturity phase, G31 (18 Nov 2026)

## Pattern attribution (not code)

None: the two PWA serve modes (local operator vs hosted end user)
are Nero's own product decision (locked 18 Nov 2026 ~23:00 WIB,
`docs/modes-decision.md`), not a pattern taken from another
repository. No external repo's pattern or code was used for this
item. The operator panels read Puspa's own live state: the pairing
store (`puspa/hosting/pairing.py`, `PairingStore.status_snapshot`),
the action gateway (`puspa/policy/gateway.py`,
`Gateway.policy_summary` + the `GatewayDecisionLog` audit sink in
`puspa/pwa/serve_modes.py`), and the app event log.

## New dependency: none

No new dependency was added for G31. No external code was copied,
vendored, or depended on for this item.
