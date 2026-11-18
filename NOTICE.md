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
  `puspa/retrieval/knowledge_graph.py` and
  `puspa/retrieval/hipporag.py`.
- **HKUDS/LightRAG** (MIT License) - pattern: dual-level retrieval
  and the five query modes local, global, hybrid, naive, mix, plus
  incremental graph update. Used as a pattern in
  `puspa/retrieval/modes.py`.
- **LlamaIndex** (MIT License) - pattern: reranker as a node
  postprocessor between retrieval and synthesis, and query
  decomposition into sub-questions. Used as a pattern in
  `puspa/retrieval/reranker.py`.
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

## Puspa license

The code in this directory is Puspa's own work (Nero / Luna). The Puspa
repo license follows the `mirzasnowy/puspa-ai` repo on push.
