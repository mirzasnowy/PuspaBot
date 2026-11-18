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

## Wave 2 (18 Nov 2026) - Tier 1 complete (9 of 20 VERY DEEP repos done)

Full suite SOLO before the pushes: **898 passed, 0 failed, 5 skipped,
1 warning** (Wave 1 total 797 + 101 new: hierarchy 29, guardrails 68,
factory rails wiring 4). The 5 skips are the 4 live-container Docker
tests plus the Chromium reader test, same as before. The 1 warning is
the pre-existing starlette testclient DeprecationWarning.

### Iteration 4: google/adk-python (Tier 1) - commit 426086bf

Hierarchical multi-agent composition. `puspa/runtime/hierarchy.py`
(new): `AgentNode` (single-parent rule, cycle rejection), `HierarchicalRunner`
(root delegates to children by role; sequential / parallel / loop /
delegate composition; every delegation through the existing
`HandoffContract`/`Handoff`, max 3 inter-agent handoffs per task),
`SessionState` (ADK scopes: plain session keys, `user:`, `app:`,
`temp:`), `MemoryAccess` (per-agent memory through the existing
`grant_slice`), `ToolConfirmation` (consequential tools blocked
fail-closed without confirmation, through the existing Gate approval
flow), `CallbackRegistry` (before/after model and tool).
`puspa/runtime/planner.py` gains `decompose_hierarchical` +
`Planner.run_hierarchical`; `puspa/runtime/subagents.py` gains
`Subagent.role` + `active_by_role`; flat behavior untouched.
Tests: `tests/test_hierarchy.py`, 29 passed, all in-memory.
Honest gaps: parallel composition is logical, not threaded;
`SessionState` is in-memory only (no Postgres table yet, a
coordinator decision, not a silent addition); hierarchical plans
with more than 3 delegated steps hit the handoff contract cap;
the runner uses an injected executor, no real model execution yet.

### Iteration 5: NVIDIA NeMo Guardrails (Tier 1) - commit a0149ced

Programmable rails. `puspa/guardrails/rails.py` (new): `Rail` +
`RailSet` (ordered, first-block wins, in-memory violation log),
input rails (topic allow/deny; injection beyond regex: quarantine
high-level scan plus deterministic structural signals, stricter for
tool/retrieved sources than for the user), output rails (numbers
must carry evidence markers per revision 7 section 2.5, unknown
evidence ids blocked, secrets blocked), execution rails (spending
fail-closed without a nominal or a limit, tool allowlist,
cross-user ownership, unsigned deploy, content hash changed after
approval, send content carrying injection), dialog flows
(`DialogFlow`/`FlowController` state machine). `puspa/chassis/hooks.py`
gains the hook points (optional `rail_set`; without one the hook
behaves exactly as before; violations recorded through existing
ledger event types, no new type). Coordinator wiring in
`puspa/chassis/factory.py`: `PuspaChassis` carries an optional
`rail_set`, `build_hook`/`build_hook_factory` pass it through, and
`assemble` builds the default rail set when the chassis carries
none, so rails are live in the assembled loop (tests in
`tests/test_factory_rails.py`, 4 passed). Tests:
`tests/test_guardrails.py`, 68 passed, all in-memory. All 8 attack
paths still blocked at the rail layer. False positives on a small
hand-written benign set: **0 of 34** (honest caveat: the set is small
and written to pass under Puspa's rules, so it measures false
positives against Puspa's rules, not against everyday language).
Honest gaps: `before_run` cannot veto the nanobot run (the hook
interface has no veto); the turn-setup caller still has to skip
running a blocked turn. No model-backed check (deterministic code
only), so paraphrased injections outside the signal set will pass.

## Remaining (11 of 20)

- Wiring gaps ordered by Nero (18 Nov 2026): (1) persist learning
  loop corrections, (2) failed steps teach the model, (3) contact +
  habit beliefs. Each its own iteration with commit + push, before
  Tier 2.
- Tier 2: hermes-agent + letta-code (ONE learning loop), KiloCode
  (modes), AgentBench (held-out eval), mem0 (extraction quality),
  graphiti (temporal edges), snyk/agent-scan (CI scanner),
  ShishirPatil/gorilla (BFCL eval format). LightRAG-deep, LlamaIndex
  reranker, and Codex sandbox profiles are already done (Wave 1).
- Tier 3: openclaw (marketplace), Sahabat-AI (fine-tune), gpt-researcher
  (cited synthesis).

## Known gaps carried forward

- Learning loop corrections are persisted since Gap 1 (commit 279cb361, 18 Nov 2026); the Tier 2 learning-loop work builds on that.
- The deterministic reranker cannot judge semantic relevance; the
  real cross-encoder backend (FlagEmbedding `BAAI/bge-reranker-v2-m3`
  or sentence-transformers) is a follow-up dependency, not added.
- The 51 em dashes in the old section of `docs/reuse.md` are
  pre-existing debt; no new em dash was added by this phase
  (verified by grep after each append).

## Queued after Tier 2: repo maturity phase (Nero's order, 18 Nov 2026 ~10:44 WIB)

Not started. The work list has arrived (18 Nov 2026 ~11:10 WIB):
`~/workspace/tmp/puspa-repo-comparison/report.md` (1,376 lines,
analysis agent c12e226e): 28 gaps G1-G28, each with how
OpenBot, DuskBot, and hermes-agent do it. Order is unchanged:
Tier 2 first, then the 4 rework items, then this phase from
that list. Do not start this phase early.

The 28 gaps (size in the report): G1 full PWA, the main product
surface (BIG); G2 billing/credits + working usage ledger (BIG);
G3 module/skill marketplace, registry, trust, install, rollback
(BIG); G4 approvals taxonomy, OpenBot-grade human-in-the-loop
(MEDIUM); G5 fail-closed gateway + CEL-style rules, deepening
(MEDIUM); G6 audit trail with initiator (SMALL); G7 memory
tightening, where DuskBot is stronger (MEDIUM); G8 mature
scheduler/routines (MEDIUM); G9 decider, a small model for cheap
decisions (SMALL-MEDIUM); G10 layered connectors, architecture
not just a connector list (BIG); G11 auth/relay/pairing for
multi-tenant hosting (MEDIUM); G12 self-observability, Sentinel
+ decision log (SMALL-MEDIUM); G13 multi-tenant/team hosting
(BIG); G14 Telegram/WhatsApp channels + phone (MEDIUM-BIG);
G15 CI + verification discipline + docs (SMALL-MEDIUM, high
leverage); G16 computer use / per-bot sandbox (BIG, deferred
after MVP); G17 skill narrowing + search_tools when tools grow
(SMALL-MEDIUM); G18 i18n, later global expansion (MEDIUM,
deferred); G19 context compression, two thresholds + lineage
(BIG, HIGH importance); G20 layered prompt assembly + sacred
prompt caching (MEDIUM-BIG); G21 interruptible turn loop +
layered fallback + budget (MEDIUM); G22 credential pool +
per-profile secret scope (MEDIUM); G23 tool registry + toolset
+ check_fn (MEDIUM); G24 memory: plug-in provider + background
review + skill curator (MEDIUM); G25 public subagent lifecycle,
capability handle + steer/cancel (MEDIUM); G26 sessions and
persistence, single-writer + FTS5 + lineage (MEDIUM); G27 plugin
system + catalog + boundary lint (BIG); G28 onboarding + cost
insights (SMALL-MEDIUM).

Suggested order from the report (§6): context compression (G19)
+ prompt caching (G20), then memory (G7, G24), then module
marketplace (G3, G27), then billing + usage ledger (G2,
including the hermes client state machine), then scheduler (G8,
general cron), then decider (G9), then connectors (G10), then
credential pool + secret scope (G22), then tool registry (G23),
then subagent lifecycle (G25), then single-writer sessions +
FTS5 (G26), then auth/relay (G11), then Sentinel (G12), then
full PWA (G1, in parallel because it is TypeScript), then
CI/docs (G15, cheap, can go first), then multi-tenant hosting
(G13), then channels (G14, Telegram first then WhatsApp,
hermes gateway pattern), then interruptible turn loop +
fallback (G21), then approvals (G4) + gateway (G5) + audit (G6)
as trust hardening along the way, then onboarding + insights
(G28), then computer use (G16) explicitly after MVP, then i18n
(G18) at global expansion.

Nero's order on this list (18 Nov 2026 11:55 WIB): address ALL
28 gaps independently, one commit each. The maturity phase works
the list item by item, each item its own iteration with its own
commit + push, without asking approval per item. The order
follows the report's §6 priority above, with G15 (CI/docs)
allowed first because it is cheap. Two exceptions: items that
need Nero's decision (the LICENSE choice, and the billing model:
credits vs subscription vs selling compute) must NOT be guessed.
Build the mechanism, make the choice a config / decision point,
and report it to the parent agent so it can be asked to Nero.
Order is unchanged: Tier 2 first, then the 4 rework items,
then this phase. Do not start this phase before the rework is
done.

License notes from the report: DuskBot PolyForm Noncommercial
1.0.0 (study only). OpenBot repo is MIT, but its
memory/learning/governance lives in separately licensed
CopilotKit Intelligence and its enforcement server is a closed
image: patterns from its docs may be used, there is no code to
copy. hermes-agent is MIT, the safest of the three for
commercial boilerplate, with attribution when a substantial part
is adapted very closely; its Nous Portal billing server is
closed.

Report caveats (honest coverage, §5): the OpenBot and DuskBot
parts were rebuilt from the agent's conversation notes after
`/tmp` was wiped when the VM was replaced around 04:01 WIB on
18 Nov 2026 (the original child reports, 324 and 1,162 lines,
were lost); the hermes-agent part was read fresh and survived
(`hermes-agent.md`, written 04:02 WIB). OpenBot: ~45 files read
substantially, ~120 skimmed, ~2,400 handwritten files NOT read
in full; server/gateway/CEL/audit code is NOT in the repo, the
mechanisms are known from docs, not verified code. DuskBot: ~45
files read, ~120 skimmed, ~9,400 files NOT read. hermes-agent:
~15 files read; most of its 9,448 code files unread. Treat the
gap list as a design-level map, not a verified code inventory.

Two findings to respect: Puspa has NO LICENSE file yet (only
NOTICE.md); the license choice is Nero's decision, never pick
it unilaterally. Puspa has NO CI at all; CI is item G15 in this
maturity list, not a side task of the Tier 2 scanner work.

Hard rule on taking: only patterns, design, and architecture are
taken, then rewritten as Puspa's own code. Never copy code
verbatim. DuskBot is PolyForm Noncommercial: study only, its code
must not be used at all. OpenBot's license is checked first by
the analysis agent before anything is taken from it. Copying
their code would contaminate Puspa's repo license and block
commercial use; keeping the repo clean is the point.

Order is unchanged: wiring gaps, then Tier 2, then this phase.
The deepening work does not stop for it (confirmed by Nero
10:41 WIB). Build standard (Nero 10:50 WIB, emphasis added):
this phase is BUILD work, not more analysis. Do not just
theorize. Every missing part from the deficiency list is built
file by file, following the granularity of the comparison repos
(OpenBot, DuskBot, hermes-agent): each area becomes its own
package with focused modules, not one giant file. Honesty rule:
every file must be real working code that runs and is tested
(SOLO suite green before commit + push), NOT an empty file or an
empty scaffold to look numerous. The complexity must be real:
modules + tests + wiring into the existing system. The goal is
to make it as mature as possible, as complex as they are, with
real work.

### Kosmos brain design, folded into this phase (Nero, 18 Nov 2026 11:57 WIB)

Design source of truth: `docs/kosmos-design.md` (English, 134
lines, accepted as the brain design document). Product name
stays Puspa; Kosmos is only the internal name for the brain
design. The K items fold into THIS maturity phase, no new phase
is created, and they do not start before Tier 2 and the rework
are done.

- K1 Intent Engine: intent as a distribution over three levels
  (current desire, ongoing goal, long-term values). NEW.
- K2 Act-or-ask rule: confidence x reversibility x stakes,
  computed, not just a taxonomy. Folds into G4/G5.
- K3 Bitemporal state: valid time vs known time, on beliefs and
  ledger entries. NEW.
- K4 Causal Ledger: hypotheses "X -> Y under condition C" with
  strength, evidence, expiry. Extends the learning loop.
- K5 Other-agent goal models: contact beliefs extended into
  models of what other people want and will do. NEW.
- K6 Ignorance map: track where the model is blind. NEW.
- K7 Three-speed simulator: structure the existing simulator
  into fast / medium / deep. Extends the world model.
- K8 Outcome watcher: keep monitoring after "done". Folds into
  G12 (extends the eval harness + learning loop).
- K9 Daily sleep consolidation + monthly ontology review.
  Folds into G7/G24, extends `consolidate.py`.
- K10 Interruption budget for proactivity. NEW, small.
- K11 Autonomy dial per area, moving with prediction
  calibration; one serious failure in an irreversible area drops
  the dial immediately. NEW.
- K12 Audit log outside the agent's reach + kill switch +
  export. Hardening.
- K13 Anti-manipulation as a design rule: optimise for the
  user's reflective goals, surface conflicts of interest, never
  decide silently. Applies to all K items.

Explicitly NOT taken literally: Presence sensory signals
(silence, pulse, texture, warmth) are poetry, not a buildable
PWA spec (the buildable core is kept: minimal interface,
universal undo, layered "why?", rarely speak, one sentence);
no literal internal prediction market (track-record weights
only); fractal resolution + self-growing ontology are NOT
promised to users (research-level, built incrementally at step
5 of the doc's build order).

## Depth bar (Nero's order, 18 Nov 2026 ~10:52 WIB) and rework queue

New bar "SANGAT DALAM", applies from now to all remaining
Tier 2 items, and to the rework below: the source's core
mechanism is implemented AND its flagship part really exists,
not a seam or stand-in without a schedule. An item may NOT be
claimed done while its core part is still a seam. Every
unavoidable stand-in (needs an LLM, a model, or an outside
service) must be written in the iteration report with the
dependency name and its follow-up schedule, not hidden in a
docstring only. The bar was sent to all seven running Tier 2
workers on 18 Nov 2026, and it will be in the brief of every
worker spawned after that (gorilla, 9Router, rework).

Audit by Luna (read the code herself, 18 Nov 2026 ~10:52 WIB):
total 42,330 lines of puspa + tests. Genuinely deep: HippoRAG
(Personalized PageRank power iteration + restart + dangling-node
fix + combined graph/vector/lexical scores, 355 lines), ADK
hierarchy (AgentNode, SessionState, MemoryAccess,
ToolConfirmation, CallbackRegistry, 792 lines), Guardrails
(RailSet + 10 rails + input/output/action checks, 763 lines),
harness (CodingHarness write-test-fix + deploy gate, 510
lines). The three NotImplementedError cases are interface
methods, not missing features. No TODO in puspa/.

Rework queue (after Tier 2, before the maturity phase; each as
its own iteration with commit + push):
1. Real cross-encoder reranker:
   `CrossEncoderReranker.from_fastembed` only raises
   `RerankerUnavailable` because fastembed 0.9.1 has no
   reranker; only DeterministicReranker runs. Fix: a real
   cross-encoder via FlagEmbedding bge-reranker-v2-m3 or
   sentence-transformers as a dependency.
2. HippoRAG 2 triple filter: deterministic rules today, not an
   LLM judge like the original. Fix: LLM-based filter through
   the provider seam. DONE 18 Nov 2026, commit
   cfd50b7f3495fd314a7d40ed04bd0a17cad99e91 (see Rework progress
   below).
3. LlamaIndex question decomposer: regex split on "and", "vs",
   ";" and "?" today, not an LLM decomposer. Fix: LLM-based
   decomposer through the provider seam.
4. Sigstore: local shape only in `puspa/policy/signing.py`, no
   Fulcio, no Rekor, no real keyless identity. Fix: verify a
   real sigstore bundle OR rename the scope to "local keyless
   signing" and drop the sigstore claim.

## Rework progress

### Rework 2: HippoRAG 2 triple filter, LLM judge (done, 18 Nov 2026)

Commit cfd50b7f3495fd314a7d40ed04bd0a17cad99e91.

- `puspa/retrieval/triple_filter.py` (new, 275 lines) +
  `puspa/retrieval/hipporag.py` (constructor +
  `_filtered_adjacency` + docstring, about 60 lines changed) +
  `tests/test_hipporag_llm_filter.py` (14 tests, 14 passed).
  `TripleDecision` is one verdict per triple (`keep`, one
  sentence `reason`, `source` `"llm"` or `"deterministic"`).
  `LLMTripleJudge` takes a `complete(prompt)` callable (the
  same seam as `JsonTripleExtractor` in `knowledge_graph.py`),
  sends triples in batches (default 50 per call), and requires
  a strict JSON reply (`{"decisions": [{"index", "keep",
  "reason"}]}`); a bad reply (invalid JSON, out-of-range or
  duplicate index, non-boolean `keep`, empty reason,
  incomplete verdicts) raises `ValueError` and that batch
  falls back to the deterministic filter, not the whole graph.
  Call failures fall back per batch too, and the fallback
  reason records why. `DeterministicTripleFilter` is the old
  rules moved unchanged (touch a seed, neighbour of a seed, or
  share a query-concept token) and stays as the fallback.
  The retriever takes `triple_filter=None` (any filter object)
  or `llm_complete=None` (a `complete(prompt)` callable,
  auto-wrapped in `LLMTripleJudge` with deterministic
  fallback); both together raise `ValueError`; neither means
  deterministic as before. `_filtered_adjacency` now judges
  ALL triples through the filter (seeds widened one hop first,
  as before), only `keep` triples become edges, and all
  verdicts are stored in `retriever.last_filter_decisions`.
  What the tests prove: the prompt carries the query and all
  numbered triples; the parser accepts complete verdicts and
  rejects bad JSON, empty lists, duplicate/out-of-range
  indices, non-boolean `keep`, and empty reasons; one LLM call
  judges 6 triples at once, `batch_size=2` means 3 calls, zero
  triples means zero calls; non-JSON replies and raising
  callables fall back per batch with `source="deterministic"`;
  on the same graph the LLM keeps `(promo, is_a, fake promo)`
  and `(fake promo, reported_in, fraud report fr-88)` that the
  rules drop, and drops `(samsung galaxy s24, mentioned_with,
  galaxy tab a9)` and `(galaxy tab a9, sells, toko abal)` that
  the rules keep; without an LLM the fraud-report passage p5
  never appears in results, with a stub LLM p5 appears with
  `graph_score` 0.073 (above 0.05) and beats the irrelevant
  seller passage p7. Regression: 37 old retrieval tests still
  green. Honest limits: the judge is NOT wired to a live LLM
  yet (`llm_complete` must be injected; without it the
  retriever uses the deterministic filter with the status
  recorded) — follow-up: wire a provider-layer completion
  function as `llm_complete` (cheapest router model reader,
  for example `qwen/qwen3-32b` via OpenRouter); verdict quality
  depends on the model and prompt and has not been evaluated
  against a real model (follow-up: test the judge with the
  cheapest router model on the existing eval graph before live
  recall use); no verdict caching (the same triple is judged
  again every retrieval).

## Tier 2 Batch A (done, 18 Nov 2026)

Seven workers in parallel on disjoint files, no pushes from the
workers; the coordinator ran the full suite SOLO centrally and
pushed each workstream as its own commit. All seven received the
new depth bar (18 Nov 2026 ~10:52 WIB). Full suite SOLO
centrally: 1082 passed, 5 skipped, 1 warning (first run had one
non-reproducing ledger flake,
`test_update_delete_rejected_for_application_role`, which passes
alone and in its file; re-run green). Baseline before Batch A: 933 passed. New tests: 149
collected (26 + 15 + 16 + 18 + 16 + 16 + 42).

- AgentBench (held-out eval): worker reported complete.
  `puspa/evidence/agentbench_tasks.py` (new, 8 real held-out
  tasks, 2 per environment: DB, KG, OS, WebShop) +
  `puspa/evidence/eval_harness.py` (wiring only) +
  `tests/test_agentbench_eval.py` (18 tests). Disjointness
  proven in code (Jaccard prompt max 0.087, no id/cassette/tool
  overlap with the 10 dev cassettes). Targeted pytest: 13
  passed, 5 errors from parallel-worker DB deadlocks (conftest
  TRUNCATE on the shared `puspa_test` DB); direct calls without
  conftest: 18/18 passed. Honest stand-in: environments are
  simulated inline cassette recordings, not live; follow-up is
  a sandbox-based solver with live environments. Commit
  fcfa7cc10c41114c60bee18c6713cced25465fdf.
- hermes-agent + letta-code, ONE learning loop: commit
  2159b02baf13c37bc5a47f248dfb45ad7291547c.
  `puspa/memory/skill_repair.py` (new, 622 lines) +
  `tests/test_skill_repair.py` (26 tests). `diagnose_skill_failure`
  classifies every failing case of a skill (wrong output,
  exception, not executable) with expected vs observed values plus
  the loop's correction as read-only context; `SkillRepairer.repair`
  runs diagnosis, rewrite, re-verify up to `max_attempts`, every
  attempt a real new draft verified with
  `LearningLoop.verify_skill` against the same test cases, and
  honestly reports "unrepairable" when no rewrite passes;
  `ProcedureUpdater.record_outcome` creates real new procedure
  versions (version number, `supersedes` link, trial status),
  follows the revision 7 rule (broken after 2 consecutive
  failures), and decays through `procedural.apply_decay`. Both
  read and write the same state the learning loop already owns
  (experience log, correction table, procedure store), so it is
  ONE loop, not two. A repair never overwrites a manual user
  edit: the repair is always a NEW draft, the original skill and
  the manual edit are left byte-identical. Honest stand-in: the
  production rewriter is an LLM call, so the module takes it as
  an injected callable and ships only the deterministic
  `lookup_rewriter` as the built-in (a real repair, but a narrow
  one). A model-based rewriter is the follow-up. Two more
  follow-ups from the worker handoff: repair is NOT wired into
  `propose_next_task` (the gap is never cleared, so a repair
  proposal there would be shadowed forever; it needs a clear-gap
  mechanism first) and is driven through `repair_suspect_skills()`
  by the curriculum runner instead; procedure versioning in
  Postgres is incomplete (`procedural.record_successful_task`
  does not bump the `versi` column, version + `supersedes` travel
  in `output_schema`; full versioning is only proven in
  `InMemoryProcedureStore`), and repair history itself is
  session-only (corrections and drafts persist, the repair trail
  does not).
- LightRAG incremental graph update: worker reported complete.
  `puspa/retrieval/incremental.py` (new, 528 lines:
  IncrementalUpdater with add/update/remove passage and document,
  SHA-256 content dedup, passage-count weights, merge stats) +
  `tests/test_retrieval_incremental.py` (15 tests). Targeted
  pytest: 4 passed, 12 errors from parallel-worker DB deadlocks;
  clean --noconftest run: 15 passed twice. Recall on the 8 Wave 1
  queries: @10 stays 1.0 and @5 stays 0.375 for the main update,
  but @3 drops 0.375 to 0.25 (honestly reported and locked in
  tests; a ranking effect, not data loss: the determining passage
  ranked 6-9 in Wave 1 and new passages shifted tight rankings).
  Second case (same-seller promo): @10 drops 1.0 to 0.875,
  locked in a dilution test. Same ranking weakness Wave 1
  recorded; the real cross-encoder rework is the fix. Honest
  limits: extraction still deterministic regex (inherited Wave 1
  stand-in; follow-up via the JsonTripleExtractor seam, no
  schedule yet); dedup exact-text only; weights are passage
  counts, not LLM weights; no community detection or summary
  regeneration on update; no re-embedding. Commit
  765c488e16b9f653cfc2b91a4adb27b017c12e44.
- KiloCode modes: worker reported complete.
  `puspa/harness/modes.py` (new: ModeSpec + 4 modes architect /
  code / debug / ask, bound to the existing sandbox profiles,
  read-only enforcement proven through Gate + profile) +
  `puspa/harness/loop.py` (mode wiring only: new `mode` param,
  default keeps old behavior; Architect plans from a real repo
  map, Ask answers once, Code/Debug require a unified diff) +
  `tests/test_harness_modes.py` (16 tests, 16 passed with
  --noconftest; under normal conftest the run hit parallel-worker
  DB deadlocks, then Postgres died entirely). No stand-in
  hidden: Architect repo map is real, enforcement is real, the
  generator stays caller-supplied as before. Follow-up for the
  coordinator: export `modes` from `puspa/harness/__init__.py`
  (worker was limited to new files + loop.py). License note:
  worker did NOT verify KiloCode's license from the source;
  coordinator verified MIT via the GitHub API 18 Nov 2026 (the
  Apache 2.0 claim in secondary sources refers to Kilo's
  extension/CLI packaging, not the repo LICENSE as reported by
  the API); reuse.md/NOTICE.md updated. Commit
  6bf15c259648fadc150597586f4b4a277f90aabf.
- mem0 extraction + ranked recall: worker reported complete.
  `puspa/memory/extraction.py` (new, ~360 lines: is_memorable /
  extract_facts, MemoryStore with dedup merge + conflict
  flagging, rank_memories / recall_at_k) +
  `tests/test_mem0_extraction.py` (16 tests, 16 passed; once
  in-repo, then verified from /tmp without conftest after
  Postgres died). Measured recall@k on a 6-query / 8-memory set:
  @1 = 1.0, @3 = 1.0, @5 = 1.0. Two real bugs found and fixed
  while testing (conflict-before-dedup ordering, zero-score
  tie-break). `puspa/memory/episodic.py` untouched (not needed;
  the new module reuses its extractors). Honest limits:
  extraction is deterministic heuristics, not an LLM extractor
  (follow-up: cheap/local LLM extractor once the brain has cheap
  LLM calls); MemoryStore in-memory, no Postgres persistence
  yet; recall set is small (6 queries, 8 memories). Attribution
  text received (mem0, Apache 2.0, pattern only). Commit
  174279c876e0373e6c14ec5771ec7ef354723ab9.
- graphiti temporal edges: worker reported complete.
  `puspa/graphs/temporal.py` (new, ~360 lines: TemporalEdge with
  valid_from/valid_until, TemporalGraph query/neighbors/relations/
  current_fact/history/prune_expired, stale_fact_rate,
  from_knowledge_graph adapter) + `tests/test_graph_temporal.py`
  (16 tests, all in-memory, 16 passed). Stale-fact rate on a
  fixed 12-fact set: baseline 0.5 without validity, 0.0 with
  validity enforced. Honest limits: in-memory only (no Postgres
  persistence yet; validity columns for price_series /
  listing_links need a schema migration); validity windows must
  be supplied by the caller (no automatic TTL invented);
  supersede is deterministic on (subject, relation), not an LLM
  judge; not yet wired into the retrieval query path. Follow-up
  queued after Batch A: schema migration + identity.py wiring +
  retrieval call site. Attribution text for reuse.md/NOTICE.md
  received (Graphiti, Apache 2.0, pattern only, library not
  installed). Commit 934267ce9acc872019ded11121193848b460322a.
- snyk/agent-scan CI scanner: commit
  9354772643413a39bad4aa9e5b13113b13b9af6e.
  `puspa/policy/agent_scan.py` (new, 15 rules) +
  `tests/test_agent_scan.py` (42 tests collected from 29
  functions). Scans a community skill directory BEFORE the
  signed-skill gate: reads `SKILL.md`, the declared
  `manifest.json` (tools, domains, connectors), and every other
  text file, flagging exfiltration to an undeclared host,
  exfiltration of secret material, credential access, prompt
  injection (override, hidden, role reassignment, base64-hidden
  instructions), gate bypass, permission overreach, rug-pull, and
  toxic flow. Every rule is a concrete pattern with a firing
  fixture, and every finding names its rule and its location
  (`file:line`). A network call is only flagged when its host is
  not declared; a plain documentation link is not flagged.
  Honest limits: static text matching, not execution and not an
  LLM judge; the scanner cannot prove intent, only the pattern.
  No CI workflow file was added (the repo has no
  `.github/workflows/` yet; CI is maturity-phase item G15). Two
  more follow-ups from the worker handoff:
  `puspa/policy/__init__.py` was not given an export, so outside
  use is `from puspa.policy.agent_scan import ...` until wired;
  and there is no wiring yet into `PuspaSkillsLoader` or the
  signed-skill gate.

## Tier 2: gorilla, BFCL eval format (done, 18 Nov 2026)

Sequential after Batch A (shares `puspa/evidence/` with
AgentBench). Commit dcdbfe9a2d6a7a70c5a4439fb4cc9a4a287a3514.

- `puspa/evidence/bfcl_checker.py` (new, 508 lines) +
  `puspa/evidence/bfcl_tasks.py` (new, 645 lines) +
  `puspa/evidence/eval_harness.py` (wiring only: `run_bfcl_eval`
  + `__all__` entry) + `tests/test_bfcl_eval.py` (43 tests, 43
  passed). The checker is a real AST checker, not string
  matching: `ToolCall` reads from a dict, an OpenAI-style
  payload, or Python source parsed with `ast.parse` (argument
  values read from the syntax tree via `ast.literal_eval`);
  comparison is recursive and strict on function name, argument
  values AND types (bool is not int, int is not float, a digit
  string is not a number, `None` is not `""`), missing and extra
  arguments, down into nested dicts and lists with paths like
  `arguments.attendees[1]`; per turn it checks order (same set
  in different order is `wrong_order`), missing and extra calls;
  per task it scores turn accuracy and call accuracy, and a task
  only passes when every turn matches exactly (the BFCL rule).
  The task set is 8 held-out tasks written by Puspa itself (12
  turns, 16 reference calls) over a catalog of 11 real Puspa
  tools (`read_price`, `read_bill`, `prepare_payment`,
  `watch_prices`, `create_trigger`, `gmail_search`, `gmail_read`,
  `gmail_create_draft`, `calendar_create_event`, `drive_search`,
  `read_memory`), including a 3-turn multi-turn task, a user
  correction in turn 2, omitted optional arguments, an empty
  string argument (`note=""`), a digit-string customer number
  (`"512345678901"`), a float threshold (`899999.5`), and an odd
  amount (`499999`). Task ids are disjoint from dev + AgentBench.
  Measured accuracy on the held-out set: reference agent 8/8
  (100%, mean turn accuracy 1.0, mean call accuracy 1.0);
  order-reversing agent 4/8 (50%); number-to-string agent 2/8
  (25%, mean turn accuracy 0.354, mean call accuracy 0.417);
  silent agent 0/8 (0%). Honest limits: NO live model was scored
  (the reference agent replays reference calls, so 8/8 proves
  the checker + harness wiring work and the reference calls are
  consistent, not that a model would score 1.0; follow-up is a
  model client plugged in as `agent` in `make_bfcl_solver`, no
  schedule yet); tool output is simulated from inline cassettes,
  not live tools (deliberate, consistent with AgentBench);
  float comparison is exact, no epsilon tolerance yet. No BFCL
  task data was copied: all prompts, entities, values, and
  reference calls are Puspa's own writing in BFCL format; only
  the pattern was studied (gorilla, Apache 2.0, verified).

## Tier 2: 9Router (done: mechanism 1 of 3, 18 Nov 2026)

Sequential, three mechanisms, each its own iteration with its
own commit + push. 9Router (decolua/9router) is MIT, verified;
it is Node/Next.js, Puspa is Python: patterns move, not code.

### Mechanism 1: RTK tool_result compression (done)

Commit 5035c115a06ea90987744d91b7a7ba8d791b8d32.

- `puspa/router/compression.py` (new, 736 lines) +
  `puspa/chassis/tools.py` (wiring only, about 24 lines, 2
  blocks) + `tests/test_tool_result_compression.py` (53 cases
  from 26 functions, 53 passed). Lossless compression of tool
  results before they reach the model, rewritten from scratch
  from the RTK pattern. `detect_kind` auto-detects from the
  first 1KB; 10 filters per kind (`diff`, `status`, `grep`,
  `find`, `ls`, `tree`, `log`, `read`, `search`, `generic`
  smart-truncate); fail-safe returns the raw text when a filter
  fails or the result grows (`filter_error`, `no_savings`,
  `too_short`); `count_tokens` uses tiktoken `cl100k_base`
  when available, otherwise a chars/4 estimate clearly labeled
  as an estimate. Wiring: `PuspaTool.execute` compresses after
  the injection scan and before returning to nanobot/the model
  (error results are not compressed, `error_code` is kept), and
  `McpClientWrapper.call_tool` compresses string results. The
  ledger only records tool name/status, not content, so the
  evidence the Checker / Evidence Book reads is unchanged.
  Measured savings with tiktoken `cl100k_base` on realistic
  noisy fixtures: log 94.1% tokens, diff 48.2%, generic 56.4%,
  grep 40.3%, search 20.0%, status 17.5%, find 16.5%, ls 3.3%,
  read 7.3%, tree 0.2%. On real tool output (smaller/cleaner):
  `git diff -U10` 57.4% tokens, `find` 15.2%, `ls -la` 2.3%,
  `tree` 0.1%, `cat -n` 0.0%, small `git diff` 0.5%, `grep`
  without duplicates 0.2% (an early 64% figure was a fixture
  artifact from triplicating the fixture, honestly corrected).
  License verified from the RTK README 18 Nov 2026: the `rtk`
  CLI is Apache-2.0, covering the hook core and all filter
  commands; `rtk-engine` and `rtk-proxy` are BUSL-1.1 and were
  NOT used; no RTK code was looked at or copied. Honest limits:
  diff context lines are the ONE exception to the orphan-number
  rule (numbers that only appear in folded unchanged context
  lines are not kept literally, only their line count in the
  marker; every `+`/`-` line is kept whole with its numbers,
  because unchanged lines can be re-read from the file);
  small/clean output barely saves and the fail-safe returns raw
  text, which is the correct behavior, not a failure; tree saves
  only 0.2% tokens; this is tool_result only, no
  conversation-history compression or prompt caching (that is
  G20, separate work). Two real bugs were found and fixed by
  the tests (the `ls` filter dropped the link-count column,
  the `tree` filter had a global dedup that dropped `model.py`
  in different modules). No new dependency: tiktoken was already
  pinned in `requirements.txt` (0.14.0).

### Mechanism 2: smart 3-tier fallback + quota tracking (done)

Commit b8921693816ec277ed53e148048979f18289bc9b.

- `puspa/router/quota.py` (new, 368 lines) +
  `puspa/router/fallback.py` (new, 529 lines) +
  `puspa/router/router.py` (wiring only, about 30 lines) +
  `tests/test_router_fallback_quota.py` (33 tests, 33 passed).
  Smart 3-tier fallback (subscription -> cheap -> free) with
  automatic fallthrough on quota exhaustion / 429 / 5xx, plus
  per-provider quota tracking, reset countdowns, and cost
  estimates, rewritten from the 9Router pattern (MIT, verified
  from three independent sources 18 Nov 2026; no sub-project
  studied or used; no code copied). `QuotaStore` +
  `ProviderQuota` track remaining tokens, usage, reset
  countdown, and Retry-After blocking per provider, parse
  rate-limit headers (`Retry-After`,
  `X-RateLimit-Remaining`, `X-RateLimit-Reset`), and roll
  windows over automatically (5-hour / daily / weekly); the
  clock is injectable. `build_chain()` builds the chain from
  the role candidates in the router catalog, most expensive
  first via spread first/median/last, a different model per
  tier; `choose_with_quota()` routes quota-aware;
  `call_with_fallback()` executes with fallback;
  `classify_failure` maps 429 to rate_limited, quota signals
  to quota_exhausted, 5xx or no status to provider_error, and
  other 4xx to non_retryable; every hop is audited
  (`FallbackAttempt` / `FallbackHop` / `FallbackResult` with
  reason + source tier + target tier). The chain is built from
  the same `CATALOG` + role rules as `router.choose()`, and
  costs use the same Rupiah formula as `router.compute_cost`
  and `BudgetTracker` (Rp per 1,000 tokens, integer Rupiah, no
  second currency). The Rp2,000/task cap is unchanged. What
  the tests prove (all in-memory, simulated providers, fake
  clock; regression: `tests/test_router.py` +
  `tests/test_budget_tasks.py` still green, 16 passed): the
  chain order [subscription, cheap, free] with different
  models and falling prices; a real switch on 429 (tier 1 429
  -> tier 2 succeeds, hop recorded with reason and status), on
  5xx, and on quota signals; an exhausted tier is skipped
  without ever being called; 429 marks the provider blocked
  until Retry-After passes; all tiers failing raises
  `AllTiersFailed` naming each tier + model + reason, never
  silently; a deterministic 400 raises at once and the next
  tier is never tried; quota math (1000 - 500 used = 500
  remaining, countdown shrinking with the clock, full reset
  restoring 1000, three missed windows rolling forward, no
  window never resetting, `X-RateLimit-Reset` shifting the
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
  in `puspa/` today, so a live adapter cannot be proven
  without inventing a call path — the `call_fn` +
  `CallResult` signature is set here as the contract for the
  first live router caller); quota without registration is
  assumed available (limits are never guessed:
  `register()` / `seed_chain_quotas()` must be given real
  limits + windows from the provider's actual plan); costs
  come from catalog rates (public OpenRouter prices, Nov
  2026), not live provider bills, the same as the existing
  `compute_cost`.

### Mechanism 3: auto token refresh + format translation (done)

Commit a300c448d0321a993063f82cbfb1030f28b428a4.

- `puspa/router/oauth.py` (new, 560 lines) +
  `puspa/router/translate.py` (new, 781 lines) +
  `puspa/router/router.py` (wiring only, about 70 lines) +
  `tests/test_router_oauth_translate.py` (39 tests, 39 passed).
  Generic OAuth2 refresh-token grant (RFC 6749 section 6) with
  proactive refresh inside a 300-second leeway (the same
  5-minute margin nanobot uses for xAI), single-flight refresh
  per provider (lock plus re-check, two parallel callers cause
  exactly one endpoint call), clear `TokenRefreshError`
  (provider, status, error code, `reauth_required` on
  `invalid_grant`), refresh-token rotation, and in-memory
  storage only with token values never logged, printed, or
  written (redacted `__repr__`, audit events carry outcomes
  only). Request/response translation between the two formats
  the BYOK path already serves, OpenAI chat-completions and
  Anthropic Messages, with every unmapped field reported in
  `TranslationReport` instead of silently dropped (OpenAI
  `frequency_penalty` / `response_format` / `logit_bias` /
  `n` / `seed`, Anthropic `thinking` blocks and
  `cache_control`, cache-creation tokens, extra choices).
  Wiring: `register_known_oauth_providers()` (xai,
  github_copilot, openai_codex as simulated registrations),
  `get_valid_oauth_token()`,
  `translate_chat_request/response()`, all delegating with
  lazy imports. The router catalog only has API-key providers
  (OpenRouter + OpenAI-compatible families via
  `demo/byok_provider.py`); auth today is API keys in the vault
  (`byok:<provider>`), Google OAuth with manual
  `refresh_tokens()` in `puspa/auth/google_oauth.py`, and three
  nanobot OAuth providers (`xai`, `github_copilot`,
  `openai_codex`) whose login is interactive with no proactive
  refresh on Puspa's side. `get_valid_oauth_token(provider)` is
  called before an OAuth provider token is used, and the
  translate functions run before a payload is sent to a
  provider with a different format. No live provider is called
  from this code. What the tests prove (all in-memory, fake
  token endpoint, fake clock, no real credentials): proactive
  refresh (token with 120s left inside the 300s leeway
  refreshes exactly once, the new token is used, the refresh
  token rotates and is stored; expired token refreshes instead
  of being used; token with 3600s left causes 0 refreshes;
  the 299s / 301s leeway boundary; refresh without a new
  `refresh_token` keeps the old one; no `expires_in` assumes
  1 hour, the same default nanobot uses for xAI; a second call
  after refresh causes 0 extra refreshes); refresh failures
  (6 tests: `invalid_grant` raises `TokenRefreshError` with
  `reauth_required=True` and the dead token is never returned,
  HTTP 500 gives `reauth_required=False`, a 200 answer without
  `access_token` errors, an unreachable endpoint errors
  clearly, no stored token or no refresh token means login
  required, an unregistered provider errors by name);
  single-flight (2 parallel threads with a slow 0.2s endpoint
  cause exactly 1 refresh POST, both get the new token,
  exactly 1 `refreshed` audit event); no token leakage (2
  tests: `repr` of TokenSet / TokenStore / config, error
  messages, and audit events contain no fake token value);
  registry (3 tests); translation (19 tests: OpenAI to
  Anthropic and back, full two-way round-trip without lost
  fields for text, tool calls, tools, temperature, max_tokens,
  stop; two-way responses with real mappings for
  `cached_tokens` / `cache_read_input_tokens`; unmapped fields
  reported). Honest limits: token storage is in-memory (vault
  persistence under `byok:oauth:<provider>` is a follow-up,
  needs user id + DSN); the three named providers are
  simulated (xAI carries its token URL and public client id
  from nanobot's source, Copilot has no client id wired,
  Codex has no token URL wired — neither was invented); no
  interactive login flow lives here (authorization/device code
  stays in nanobot); Azure Responses and Bedrock Converse are
  not translated (outside the router catalog); Anthropic
  `max_tokens` defaults to 4096 when the OpenAI request has
  none (recorded in the report); `finish_reason=content_filter`
  maps to `stop_reason=end_turn` as the nearest neutral
  equivalent (reported); no retry/backoff on transport
  failure (one attempt, then a clear error). License: 9Router
  MIT re-confirmed by web search 18 Nov 2026; no sub-project
  studied or used. Translation field mappings are grounded in
  `nanobot/providers/anthropic_provider.py` (nanobot MIT, an
  existing dependency) and the OpenAI format Puspa's own BYOK
  factory uses, not in 9Router code.

### Test harness fix: flaky ledger test (done with mechanism 3)

Commit a2b54aff762e85e67fffeb81cc28c79efe1a1b41.

- `tests/conftest.py` + `tests/test_rls.py` (fixture moved, no
  product code changed). Diagnosis: the full suite failed
  intermittently (2 of 6 runs) on
  `test_update_delete_rejected_for_application_role` with
  `role "puspa_app" does not exist` at `SET ROLE`. The role
  was created only by a session-scoped autouse fixture in
  `tests/test_rls.py`, which runs only when that module runs;
  pytest runs files alphabetically, so `test_ledger.py` runs
  BEFORE `test_rls.py`. On a fresh DB (after a VM replacement)
  the role did not exist yet at ledger time; once `test_rls.py`
  ran, the role persisted and every later run passed. That is
  the whole pattern: it failed on the first run after each VM
  replacement and passed on every re-run. The fix moves
  `_ensure_app_role` into `tests/conftest.py` (session-scoped
  autouse, idempotent `IF NOT EXISTS`), so the role is
  guaranteed before any test file runs; it is removed from
  `test_rls.py`. Reproduced before the fix (role dropped,
  single test failed) and verified after (role dropped, the
  test plus all of `test_ledger.py`, `test_rls.py`, and
  `test_rls_memory.py` pass: 22 passed, role present
  afterwards). The stale `infra/setup_db.sql` reference in the
  fixture docstring was corrected to `infra/schema_core.sql`.

After 9Router (all 3 mechanisms done): the 4 rework items,
then the maturity phase.
