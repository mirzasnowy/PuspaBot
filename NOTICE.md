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

## Puspa license

The code in this directory is Puspa's own work (Nero / Luna). The Puspa
repo license follows the `mirzasnowy/puspa-ai` repo on push.
