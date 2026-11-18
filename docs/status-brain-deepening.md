# Status: Brain deepening phase (Pendalaman Otak)

Date: 18 Nov 2026. Coordinator: deepening phase after the Brain wiring
phase (main `50a8487`, 699 passed). Revision 7 was NOT changed: no
definition text, gate, number, or piece of evidence was altered, no
demotion rule was added, and the verifier, permission token, single
outbound gate, and orphan-number rule were not touched. Per-iteration
commits: one repo (or one inseparable group) per commit, each pushed
only after a green SOLO full-suite run.

## License decisions (18 Nov 2026, before Wave 1)

The three unsure items from `docs/brain-depth-triage.md` were decided
by reading their LICENSE files. Counts moved from 19/80/23/3 to
**20 VERY DEEP / 81 ENOUGH / 24 DO NOT TOUCH / 0 UNSURE** (total 125).

- Kineviz/bighorn: MIT verified, OSI-approved, legally clear for full
  use. Classified ENOUGH: no unique mechanism proven against LadybugDB
  (the verified Kuzu fork already used as a pattern).
- ShishirPatil/gorilla: Apache 2.0 verified, OSI-approved, with an
  explicit patent grant. Added as VERY DEEP Tier 2 item 17, scoped to
  the BFCL eval format only (multi-turn function-calling eval tasks).
  Caveat: only the root LICENSE was read; individual BFCL data files
  were not verified for separate terms.
- meta-llama/PurpleLlama (PromptGuard 2): Llama 3.2 Community License
  verified, NOT OSI, terminable by Meta, bound to its Acceptable Use
  Policy. Classified DO NOT TOUCH: PromptGuard 2 code and weights must
  not enter Puspa.

## Wave 1 (18 Nov 2026) - 7 of 20 VERY DEEP repos done

Full suite SOLO before the pushes: **797 passed, 0 failed, 5 skipped,
1 warning** (baseline 699 + 98 new: signing 29, harness 30, retrieval
39). The 5 skips are the 4 live-container Docker tests plus the
Chromium reader test, same as before. The 1 warning is the pre-existing
starlette testclient DeprecationWarning.

### Iteration 1: sigstore (Tier 1) - commit d74f79f7

Local keyless signing + transparency log following the sigstore design
(DSSE envelope, ephemeral Ed25519 key per signature, file-backed JSONL
transparency log with hash chain, Merkle root, inclusion proofs).
`puspa/policy/signing.py` (new), `puspa/policy/skill_signing.py`
(`verify_skill` gains optional `transparency_log` /
`expected_identity`), `puspa/chassis/skill_loader.py` (loader wiring:
`PuspaSkillsLoader` takes `transparency_log` + `expected_identity`).
Tests: `tests/test_signing.py`, 29 passed, including the DSSE spec
test vector, tamper rejection, restart round trip, and log append-only
proofs. Honest scope: NOT the public sigstore service (no Fulcio, no
Rekor, no network call); identity is a Puspa user identity string.
No revocation yet, no witness/cosigning, single-writer log.

### Iteration 2: Aider + SWE-agent + openai/codex (Tier 1 + Tier 2) - commit f16f5a8e

ONE harness, not three. `puspa/harness/repo_map.py` (repo map via
stdlib `ast`, 1000-token budget, whole-file drops), `puspa/harness/diff.py`
(unified diff generate/apply, all hunks validated first, no partial
apply), `puspa/harness/reproduce.py` (reproduce-first state machine,
frozen reproduction test), `puspa/harness/sandbox_profiles.py`
(profiles read_only / workspace_write / full, effective level is the
narrower of profile and token, enforced through `Gate.authorize_tool`).
`puspa/harness/loop.py` gains `profile`, `diff_mode`,
`reproduce_first` (defaults keep old behavior); `puspa/harness/__init__.py`
exports the new names. Tests: `tests/test_harness_unified.py`,
30 passed, including E2E through the real harness (buggy starter,
reproduction fails first, diff fix to green) and a read-only profile
refusing a write before the generator runs. Honest gaps: no PageRank
relevance ranking in the repo map yet; no PASS_TO_PASS regression set;
real E2E sandbox still fail-closed on this VM. Puspa's `full` is NOT
Codex `danger-full-access`.

### Iteration 3: HippoRAG + LightRAG + LlamaIndex reranker (Tier 1 + Tier 2) - commit 0711a927

ONE retrieval pipeline, not three systems. `puspa/retrieval/`
(knowledge_graph.py: schema-less KG with a pluggable extractor and a
deterministic default; hipporag.py: real Personalized PageRank, power
iteration with restart, HippoRAG 2 style filtering and re-ranking;
modes.py: five query modes local/global/hybrid/naive/mix over the
existing dual-level weights 0.5/0.3/0.2; reranker.py: deterministic
reranker plus a cross-encoder seam). Wiring is additive only:
`puspa/memory/episodic.py` (`episodes_to_graph`,
`search_episodes_pipeline`) and `puspa/graphs/identity.py`
(`product_passages`, `rank_products_pipeline`). Tests: 39 passed
(37 in-memory + 2 Postgres). Eval on 8 hand-written multi-hop
queries (price graph: product, promo, seller, fraud report):
recall@10 pipeline 1.000 vs baseline 0.500; recall@3 0.375 vs 0.250;
recall@1 tied 0.125. The win is reach, not top-3 precision.
Honest gaps: no real cross-encoder backend (fastembed 0.9.1 ships no
reranker); LLM extraction not wired (deterministic extractor is the
default); seedless queries fall back to vector; entity-less passages
score 0 on the graph.

## Remaining (13 of 20)

- Wave 2 (Tier 1): google/adk-python (hierarchical multi-agent),
  NVIDIA NeMo Guardrails (programmable rails).
- Tier 2: hermes-agent + letta-code (ONE learning loop), KiloCode
  (modes), AgentBench (held-out eval), mem0 (extraction quality),
  graphiti (temporal edges), snyk/agent-scan (CI scanner),
  ShishirPatil/gorilla (BFCL eval format).
- Tier 3: openclaw (marketplace), Sahabat-AI (fine-tune), gpt-researcher
  (cited synthesis).

## Known gaps carried forward

- Learning loop corrections are still not persisted (wiring phase
  gap); the Tier 2 learning-loop work should fix it as part of its
  iteration if it touches that code.
- The deterministic reranker cannot judge semantic relevance; the
  real cross-encoder backend (FlagEmbedding `BAAI/bge-reranker-v2-m3`
  or sentence-transformers) is a follow-up dependency, not added.
- The 51 em dashes in the old section of `docs/reuse.md` are
  pre-existing debt; no new em dash was added by this phase
  (verified by grep after each append).
