# Reuse Audit for PuspaBot — what was copied from the chassis

Audit date: 17 November 2026 ~22:40 WIB. The auditor only read
(did not change any code). Scope: the local `build/` tree
(phases 0, 1, 2, 3 in progress, `cli/`, `web/`).
The GitHub repo `mirzasnowy/puspa-ai` as pushed so far only contains
phase 0 (3 commits) — phases 1–3 have not been pushed yet.

## Short answer for Nero

What was actually "copied" (its code used) is only **one**: nanobot,
as a **pip dependency** (git pin), not a copy of code — its local
clone is clean, 0 lines changed, and the PuspaBot package only wraps it
through official extension points (subclass + `super()`).

The rest: **patterns studied and then rewritten** as PuspaBot's own code
(mem0, Graphiti, Letta, elizaOS, LiteLLM, agno,
OpenHands, opencode, Home Assistant, Supabase, LadybugDB, RAGFlow)
or **only studied, nothing taken** (LangGraph,
LangChain, Agent-Reach, hermes-agent, ECC, OpenBot, E2B/Firecracker,
Langflow, Pipecat/LiveKit, superpowers, dusk.bot — design only).

**Zero verbatim copies found** across all PuspaBot code
(see "How it was checked" below for the method + its limits).

## How it was checked (and its limits)

1. Grep all `import`/`from` in `fase-0/puspa`, `fase-0/demo`,
   `fase-1/puspa`, `fase-2/puspa`, `fase-3/puspa`, `cli/puspa_cli`:
   not a single import of `mem0`, `langchain`, `langgraph`,
   `graphiti`, `litellm`, `agno`, `letta`, `eliza`, `opencode`,
   `hermes`, `agent_reach`, `pipecat`, `livekit`, `langflow`,
   `supabase`, `homeassistant`.
2. Grep distinctive source API names (`add_memory`, `StateGraph`,
   `add_node`, `add_edge`, `MemoryClient`, `add_episode`,
   `add_triplet`, `add_messages`, etc.): the only hits were the
   word "Graphiti" in a docstring (a pattern citation) and one false positive
   ("END" inside the word "SENDIRI"). No foreign API surface.
3. Grep `Copyright` across all PuspaBot code: empty. `find` for
   `LICENSE*` and `SKILL.md` files outside `nanobot/`: empty.
4. `git -C fase-0/nanobot status --porcelain`: 0 lines (checked 2x,
   last ~22:40 WIB). `git diff --stat HEAD`: empty.
5. Read the header/docstring of each file citing a pattern, then
   compare PuspaBot's design with its source — the differences are
   recorded in the table (e.g. mem0 stores inferred facts;
   PuspaBot does NOT store them in the profile, but in Usulan instead).
6. Honesty limit: without comparing line by line against
   all source repos (not cloned), absolute zero cannot be
   proven mathematically. But evidence 1–5 above, plus
   PuspaBot code that was almost entirely Indonesian-identified
   with designs diverging from its sources, is strong evidence.

The official reuse log is at `build/REUSE.md` (8,617 bytes, 17 Nov 2026
15:25) — NOT at `fase-0/REUSE.md` as the original brief assumed
(now `docs/reuse.md`). References to `../REUSE.md` from `fase-1/` and `fase-2/` are VALID
(pointing to `build/REUSE.md`). nanobot attribution is in
`build/fase-0/NOTICE.md`; phase 1 pattern attribution is in
`build/fase-1/NOTICE.md` (both now in the root `NOTICE.md`).

## Per-repo table

| Repo (license) | What was taken | File evidence |
|---|---|---|
| **nanobot** — HKUDS/nanobot (MIT) | **Pip dependency only** (git pin `e767174`, package `nanobot-ai`), NOT a code copy. Local clone is read-only reference. Wiring only through official extension points: subclass `ToolRegistry`/`SkillsLoader`/`AgentHook` + `super()`. 0 lines of nanobot changed (clean git status). | `fase-0/requirements.txt`; `fase-0/NOTICE.md`; `fase-0/puspa/chassis/*.py` (import `nanobot.agent.*`, 942 lines); `git -C fase-0/nanobot status` = 0 |
| **opencode** — anomalyco/opencode (MIT, TypeScript) | **No code taken.** UX pattern (chat/task/approval screens as one flow) used as a design reference in the phase 2 PWA, rewritten as FastAPI + Jinja + HTMX + SSE. Note: the word "opencode" in `demo/MATRIKS_BYOK.md:169` is a provider name in nanobot's own registry (`fase-0/nanobot/nanobot/providers/registry.py:334-346`), NOT this repo. | `fase-2/puspa/pwa/app.py` (no opencode import); `build/REUSE.md` (Phase 2); `fase-2/STATUS_FASE2.md:297` |
| **langgraph** — langchain-ai/langgraph (MIT) | **Nothing taken.** Zero imports, zero code. PuspaBot orchestration = nanobot `AgentLoop` + PuspaBot's own 5-stage task cycle on top of Postgres + the ledger. | `fase-2/puspa/runtime/tasks.py` (header §2.4 revision 7, no mention of LangGraph); grep `langgraph` across all code = empty |
| **langchain** — langchain-ai/langchain (MIT) | **Nothing taken.** Zero imports, zero code. | grep `langchain` across all code = empty |
| **mem0** — mem0ai/mem0 (Apache-2.0) | **Pattern only, rewritten** (phase 1): sourced facts, scoped per user, inferred facts do NOT enter the profile directly (held in Usulan). Library NOT installed. Differs from the original: revision 7 three-valued provenance (`diucapkan_user`/`dikonfirmasi_user`/`disimpulkan`), statuses `usulan`/`berlaku`/`ditolak`/`diganti`, deterministic UNION merge. | `fase-1/puspa/memory/profile.py:3-9`; `fase-1/NOTICE.md:9-11`; `build/REUSE.md` (Phase 1); `fase-1/STATUS_FASE1.md:200` |
| **agent-reach** — (Nero's contribution repo) | **Nothing taken.** Zero mentions across all PuspaBot code + documents. PR #880 is Nero's contribution TO that repo, not a source for PuspaBot. | grep `agent.?reach` in `fase-0/`, `fase-1/`, `fase-2/`, `fase-3/`, `cli/`, `web/` = empty |
| **hermes-agent** — NousResearch/hermes-agent (MIT) | **Nothing taken.** Zero mentions in the code. Learning loop + `hermes claw migrate` are only design references in survey 1 for the self-develop module (phase 6, not yet built). | grep `hermes` across all code + documents = empty; survey 1 `repo-reuse-puspa-2026-11-17.md:45-48` |
| **agno** — agno-agi/agno, AgentOS (Apache-2.0) | **Pattern only, rewritten** (phase 2): durable execution, approval, scheduling, observability → tasks as durable units in Postgres (SKIP LOCKED), scheduler, evidence projections. Library NOT installed. | `fase-2/puspa/runtime/tasks.py`; `fase-2/puspa/scheduler/scheduler.py`; `fase-2/puspa/evidence/projections.py`; `build/REUSE.md` (Phase 2); `fase-2/STATUS_FASE2.md:293-294` |
| **langflow** — langflow-ai/langflow (MIT) | **Nothing taken yet.** The visual workflow/module builder = phase 4, not yet built. | `build/REUSE.md` (Phase 2, "Not used"); `fase-2/STATUS_FASE2.md:306` |
| **LiteLLM** — BerriAI/litellm (MIT) | **Pattern only, rewritten** (phase 2): multi-provider router + spend tracking → `puspa/router/router.py`, built ON TOP of the phase 0 BYOK factory (`demo/byok_provider.py`), not on top of litellm. Library NOT installed; zero mentions of "litellm" in `demo/`. Differs from the original: revision 7 roles + the Verifier family rule + Rupiah rates. | `fase-2/puspa/router/router.py:3-4,106-108`; `build/REUSE.md` (Phase 2); grep `litellm` in `fase-0/demo/` = empty |
| **pipecat** (BSD-2-Clause) / **livekit/agents** (Apache-2.0) | **Nothing taken yet.** The Voice module is NOT in the revision 7 definition; post-MVP. | `build/REUSE.md` (Phase 2, "Not used") |
| **home-assistant** — home-assistant/core (Apache-2.0) | **Pattern only, rewritten** (phase 2): per-entity connectors with minimal scope + trigger-based automation → Google connectors (Gmail/Drive/Calendar on top of Phase 1 OAuth) + per-agent grants + scheduler. Library NOT used. | `fase-2/puspa/connectors/google.py` (header); `fase-2/puspa/connectors/grants.py`; `build/REUSE.md` (Phase 2) |
| **ladybugdb** — LadybugDB/ladybug (MIT) | **Pattern only, rewritten** (phase 3, in progress): embedded graph — nodes and edges as relational rows, not a graph server → identity + price graph on top of Postgres. Library NOT used. | `fase-3/puspa/graphs/identity.py` (header); grep import `ladybug`/`kuzu`/`networkx` = empty |
| **supabase** — supabase/supabase (Apache-2.0) | **Pattern only, rewritten** (phase 2): RLS + realtime → Postgres RLS (`app.current_user_id`) + SSE instead of realtime. Library NOT used. | `fase-2/infra/schema_fase2.sql:138-157`; `build/REUSE.md` (Phase 2); `fase-2/STATUS_FASE2.md:300-301` |
| **openhands** — OpenHands/OpenHands (MIT) | **Pattern only, rewritten** (phase 2): sandbox hardening (fail-closed + domain allowlist) → the phase 0 allowlist reused, enforced in connectors. Library NOT used. PuspaBot's sandbox = Docker (`docker run`), not microVM. | `fase-0/puspa/sandbox/runner.py` (`docker run`, not Firecracker); `fase-2/STATUS_FASE2.md:295-296`; `build/REUSE.md` (Phase 2) |
| **dusk.bot** — DuskBotAI/duskbot (PolyForm Noncommercial 1.0.0) | **Design only, not code.** Studied in `web/STUDI_DUSK.md`: section rhythm, mock panels, price cards. No code, text, or images copied, downloaded, or hotlinked. No external URLs in the web files. `web/assets/` contains only the PuspaBot logo + generated icons. | `web/STUDI_DUSK.md`; grep `https?://` in `web/` (outside w3.org/localhost) = empty; `ls web/assets/` |
| **ECC** — affaan-m/ECC (MIT) | **Nothing taken** (studied only). Zero mentions in PuspaBot code. The SKILL.md skill pattern + hooks + continuous learning are recorded in the survey, but PuspaBot's implementation cites agentskills/Graphiti/Letta, not ECC. AgentShield: zero mentions. | grep `ECC`/`AgentShield`/`shield` across all code = empty (only mentioned in `fase-0/STATUS.md:10` as the no-fork rule) |
| **OpenBot** — CopilotKit/OpenBot (MIT) | **Design pattern only, no code.** The fail-closed gateway concept informed PuspaBot's gate design, but `gate.py` was written by hand (token + spending limit + approval + idempotency) WITHOUT CEL (grep `cel` = empty). | `fase-0/puspa/policy/gate.py` (header + imports: only `hashlib`/`time`/`uuid`/`dataclasses` + `puspa.policy.tokens`); survey 1 lines 52-57 |
| **E2B / Firecracker** (Apache-2.0) | **Nothing taken.** The "one computer per bot" concept is only a design influence. PuspaBot's sandbox = Docker, fail-closed. | `fase-0/puspa/sandbox/runner.py`; survey 1 lines 102-148 |
| **agentskills** — agentskills/agentskills (Apache-2.0) | **Standard format only, not code.** SKILL.md + frontmatter used as PuspaBot's skill format (for ecosystem compatibility). The `skills-ref` validator is NOT used. No code vendored, so no LICENSE file is required. | `fase-0/puspa/chassis/skills.py:22` ("frontmatter agentskills"); grep `skills-ref` = empty; survey 1 lines 192-195 |
| **superpowers / mattpocock/skills / anthropics/skills** (MIT) | **Nothing taken.** No SKILL.md files from these collections exist in PuspaBot. | `find . -name SKILL.md` outside `nanobot/` = empty |
| **Graphiti** — getzep/graphiti (Apache-2.0) | **Pattern only, rewritten** (phases 1 + 3): temporal facts (`berlaku_dari`/`berlaku_sampai`, one active version) → `profile.py`; procedures as templates without personal data → `procedural.py` + `graphs/service.py`. Library NOT installed (needs Neo4j/FalkorDB — too heavy for the MVP; recorded in REUSE.md). | `fase-1/puspa/memory/profile.py:7-9`; `fase-1/puspa/memory/procedural.py:13-14`; `fase-3/puspa/graphs/service.py:8`; `build/REUSE.md` (Phase 1, "Not used") |
| **Letta** — letta-ai/letta (Apache-2.0) | **Pattern only, rewritten** (phase 1): sleep-time compute / consolidation while idle → `consolidate.py`. Differs from the original: PuspaBot's summaries are DETERMINISTIC and extractive (no model, Rp0); Letta uses a model. | `fase-1/puspa/memory/consolidate.py` (header); `fase-1/NOTICE.md:16-18`; `build/REUSE.md` (Phase 1) |
| **elizaOS** — elizaOS/eliza (MIT) | **Pattern only, rewritten** (phase 1): character file (persona as data) → speaking style as profile facts + message examples (max 20) + corrections that apply immediately. Not a JSON character file. | `fase-1/puspa/persona/persona.py:3-7`; `fase-1/NOTICE.md:19-21`; `build/REUSE.md` (Phase 1) |
| **RAGFlow** — infiniflow/ragflow (Apache-2.0) | **Pattern only, rewritten** (phase 3, in progress): work steps as reusable data → service procedures on top of Postgres. Library NOT used. | `fase-3/puspa/graphs/service.py` (header) |
| **pgvector** (PostgreSQL license) | **Database extension, not code.** `vector(384)` column + HNSW index; the `HashEmbedding` embedding written by hand (token hash, Rp0). | `fase-1/puspa/memory/embeddings.py` (imports: `hashlib`/`math`/`re`); `fase-1/puspa/memory/episodic.py` (`::vector`, pgvector cosine); `build/REUSE.md` (Phase 1) |

## Pip dependencies (used as libraries, NOT code copying)

`nanobot-ai` (git pin `e767174`, MIT) · `psycopg[binary]` · `argon2-cffi` ·
`cryptography` · `pytest` · `pydantic` · `pydantic-settings` · `loguru` ·
`pyyaml` · `json_repair` · `tiktoken` · `jinja2` · `regex` · `jsonschema` ·
`filelock` · `httpx` · `rich` · `pillow` · `fastapi==0.163.1` (MIT, phase 2) ·
`uvicorn` · `playwright==1.65.2` (MIT, phase 3).
Evidence: `fase-0/requirements.txt`, `fase-1/requirements.txt`,
`fase-2/requirements.txt`, `fase-3/requirements.txt`,
`cli/requirements.txt`.

## Findings — what's not yet resolved

1. **REUSE.md has no Phase 3 section yet.** The new dependency
   `playwright==1.65.2` and the RAGFlow + LadybugDB patterns are only recorded
   in code headers (`fase-3/puspa/graphs/service.py`,
   `fase-3/puspa/graphs/identity.py`, `fase-3/requirements.txt`),
   not yet in `build/REUSE.md`. Phase 3 also has no STATUS file yet.
2. **The Phase 0 section of REUSE.md doesn't record design influences.**
   The Phase 1 and Phase 2 sections record each pattern + its source, but
   the Phase 0 section only lists nanobot. Unrecorded phase 0 design
   influences: the OpenBot fail-closed gateway pattern,
   the E2B/Firecracker per-bot computer concept, the agentskills format,
   the ECC skill/learning pattern, and the hermes-agent learning loop.
   This is a LOG gap, not a violation — there is no code from those
   repos in phase 0.
3. **Survey 2's sentence about LangGraph is wrong.** The langchain entry in
   `chassis-tambahan-2026-11-17.md` writes "PuspaBot already uses
   LangGraph for orchestration" — not true: zero imports and zero
   LangGraph code in the whole tree. PuspaBot orchestration = nanobot
   `AgentLoop` + PuspaBot's own task cycle. That sentence needs
   correcting in the survey (or recording as an error).
4. **The `get_definitions` gap in `PuspaRegistry` was still open when the
   audit was read.** `fase-0/puspa/chassis/registry.py` (re-read
   ~22:40 WIB, unchanged) still documents
   "`get_definitions` is not checked — the tool schema the model
   sees is not execution." The model sees 18 built-in nanobot tools
   even though the token only allows 5 (proven by live run 1 of the CLI).
   A fix is being worked on by another agent; a local CLI fix already
   exists (`cli/puspa_cli/agent.py:460-464`, `unregister` tools outside
   the token). This is NOT a reuse issue — but it is recorded because it relates
   to wrapping nanobot.
5. **No license violations found.** No GPL/AGPL/SSPL/PolyForm/CC BY-NC code in the PuspaBot tree. dusk.bot
   (PolyForm) was only studied for its design. No GitHub fork.

## Translator's note (18 Nov 2026)

This is a dated audit record (17 Nov 2026). Quoted old identifiers,
paths, and file evidence are kept verbatim as they were at audit time.
Two things have changed since: (1) the language policy this audit
assumes — Indonesian identifiers and prose — is superseded by the
English-only refactor ordered by Nero on 18 Nov 2026 (see
`docs/GLOSSARY.md`); (2) the `build/` / `fase-N/` tree the audit cites
is now the repo root, `build/REUSE.md` is `docs/reuse.md`, and the
`fase-0/` + `fase-1/` NOTICE files are the single root `NOTICE.md`.
