# Kosmos Brain — Implementation Handoff

Purpose: hand this document to a strong AI and let it improve the brain. It
states what the brain is, what is actually built (with file paths), what is
not built yet, and the order to build it in. Everything claimed here is
verifiable in the repo; nothing is a promise to users.

## 1. The product in one paragraph

PuspaBot is the product, `puspa-ai` was the old repo name, the repo is now
`mirzasnowy/PuspaBot`, the package is `puspa` (`pip install puspa`,
`npm install -g puspa`). It is a personal AI agent the user runs
themselves: Python core, TypeScript PWA surface, local-first data. Kosmos
is the internal name for the brain design. The brain is one agent loop,
not a chain of agents: one loop, direct tool calls, decide by default.

## 2. How a turn works (the loop)

1. Input arrives (chat, voice, scheduled trigger) at the turn loop
   (`puspa/turns/loop.py`, `puspa/turns/turn_api.py`).
2. The intent engine reads intent as a three-level distribution: current
   desire, ongoing goal, long-term values
   (`puspa/intent/distribution.py`, `puspa/intent/inference.py`).
3. The router picks the path (`puspa/router/router.py`); the decider
   chooses the action (`puspa/decider/decider.py`).
4. The act-or-ask rule decides act vs ask from
   score = confidence x reversibility x stakes (threshold 0.6)
   (`puspa/intent/act_ask.py`). The policy gateway enforces it
   fail-closed (`puspa/policy/gateway.py`); the conflict gate refuses
   conflicted actions until the user chooses
   (`puspa/policy/conflicts.py`).
5. Tools run through the local executor (`puspa/localexec/`, permission
   prompt, workspace-bounded subprocess) or connectors
   (`puspa/connectors/`).
6. Every step is written to the causal ledger (`puspa/ledger/`) and to
   memory (`puspa/memory/`). The outcome watcher keeps monitoring after
   "done" (`puspa/sentinel/outcome_watcher.py`).

## 3. What is built: Kosmos K1-K13 (all complete, all merged)

| Item | What it is | Where |
|---|---|---|
| K1 intent engine | Intent as a 3-level distribution | `puspa/intent/` |
| K2 act-or-ask | Computed confidence x reversibility x stakes | `puspa/intent/act_ask.py` |
| K3 bitemporal state | Valid time vs known time on beliefs and ledger entries | `puspa/world/bitemporal.py` |
| K4 causal ledger | Hypotheses X -> Y under C, with strength, evidence, expiry | `puspa/world/causal.py`, `puspa/ledger/` |
| K5 other-agent goal models | Models of what other people want and will do | `puspa/world/agents.py` |
| K6 ignorance map | Records where the model has no evidence | `puspa/world/ignorance.py` |
| K7 three-speed simulator | Fast (LLM intuition) / medium (dynamics from traces) / deep (dry-run in sandbox), fallback deep -> medium -> fast | `puspa/world/speeds.py`, `puspa/world/simulator.py` |
| K8 outcome watcher | Late failures recorded and fed to the learning loop; harness wiring | `puspa/sentinel/outcome_watcher.py`, `puspa/evidence/eval_harness.py` |
| K9 consolidation + ontology review | Daily consolidation, monthly-gated ontology review (flags contradictions, stale facts, hidden variables, bias; findings only, never corrects) | `puspa/memory/ontology_review.py`, `puspa/memory/consolidate.py`, `puspa/memory/dream.py` |
| K10 interruption budget | Proactivity pays from 5 units/day; silent actions free | `puspa/proactivity/budget.py`, `puspa/turns/budget.py` |
| K11 autonomy dial | Per-area dial, moves with prediction calibration; one serious irreversible failure drops it | `puspa/autonomy/dial.py`, `puspa/autonomy/controls.py` |
| K12 audit log + kill switch + export | Append-only audit log outside the agent's reach, kill switch, export | `puspa/ledger/`, `puspa/ledger/kill_switch.py`, `puspa/sentinel/decision_log.py` |
| K13 anti-manipulation | Deterministic detector over 5 conflict kinds; conflicts surfaced, never decided silently | `puspa/policy/conflicts.py` (991 lines), `docs/anti-manipulation.md` |

Full design rationale: `docs/kosmos-design.md` (sections A-L, acceptance
criteria per item). That document also names what was explicitly NOT taken
literally: sensory Presence signals (poetry, not a spec), the prediction
market literally (implemented as track-record weights), fractal resolution
and self-growing ontology as promises (research-level, unproven at scale).

## 4. Memory system (5 layers)

- Working: the current session (`puspa/sessions/`, `puspa/turns/`).
- Episodic: the ledger, append-only events with time, cause, outcome.
- Semantic: the knowledge graph (`puspa/retrieval/knowledge_graph.py`,
  `puspa/graphs/`), user-visible and user-editable graph view.
- Procedural: skills and modules (`puspa/memory/procedural.py`,
  `puspa/memory/skill_repair.py`).
- Person models + values/preferences: `puspa/memory/profile.py`,
  `puspa/persona/`.
- Supporting pieces: `puspa/memory/episodic.py`, `puspa/memory/recall.py`,
  `puspa/memory/embeddings.py` (fastembed, BAAI/bge-small-en-v1.5,
  384-dim, ONNX), `puspa/memory/journal.py`,
  `puspa/memory/learning_loop.py`, `puspa/memory/learning_graph.py`.
- Storage: hosted = Postgres + pgvector (103 tables); local install =
  SQLite file + sqlite-vec in-process, vectors as fallback blobs + numpy
  cosine. Embeddings are local (fastembed), no torch.

## 5. Decision and safety stack

- Act-or-ask (K2) -> policy gateway (fail-closed, CEL-style rules) ->
  conflict gate (K13) -> autonomy dial (K11) -> execution.
- Money: the agent never moves money silently. It prepares; the user
  approves in their own bank app, within caps (safe-pay).
- Outside data (email, web, other people's messages) is untrusted data,
  never commands. Hard limits the agent cannot change: money above a
  threshold, illegal things, harming third parties.
- Every decision carries its score and its three factors; every action
  is ledger-recorded and undoable where reversible.

## 6. Learning stack (three clocks)

- Seconds-minutes: action results update the learning loop.
- Daily ("sleep"): consolidation merges traces, drops stale hypotheses,
  retrains dynamics (`puspa/memory/dream.py`,
  `puspa/memory/consolidate.py`).
- Monthly: ontology review restructures concepts, searches hidden
  variables, audits bias (`puspa/memory/ontology_review.py`).
- Honest signal: silent correction (undo, edited results, ignored
  suggestions) counts more than the user's words.

## 7. What is NOT built yet (in build order)

### Baseline gaps (from `docs/baseline-speed-memory.md`)

1. Durable MEMORY.md + narrative journal (COW-05): a human-readable
   memory file rebuilt nightly from the lower layers.
2. Write-through enforcement: every decision, correction, and
   commitment written to the ledger AND durable memory in the same turn.
3. Recall-first loading: every task starts by loading the relevant
   durable memory; the agent never starts from zero on a known user.

### Kosmos Phase 2 (from `docs/kosmos-phase2-mind.md`)

4. K15 working memory: bounded short-term store, attention-weighted
   retention, decay, rehearsal promotes to long-term.
5. K16 long-term consolidation: promotion rules (repetition,
   importance, correction), forgetting curve, corrections never fade.
6. K17 self-evolving ontology: the graph adds, merges, and splits
   concepts automatically; K9 findings become automatic ledger-recorded
   repairs. (K9 today only flags; this is the repair half.)
7. K14 self-directed initiative: the agent derives sub-goals from
   standing goals and open loops, acts without being told, reports what
   it did. (BDI: beliefs, desires, intentions.)
8. K20 metacognition: calibrated confidence per decision; escalate only
   when uncertainty is high AND stakes are high. Extends K6 + K2.
9. K18 global workspace: modules compete to broadcast to one shared
   workspace (Baars); the winner drives the next action.
10. K19 prediction and surprise: predict each action's outcome, compare
    with reality, learn from the error (predictive processing, Friston).
    Extends K8.

Build order: 1-3, then 4-6, then 7-8, then 9-10. Each item lands as its
own branch, PR, and green suite, the way K1-K13 landed.

### Known brain-adjacent gaps (from the partial-done audit)

- Durable session store missing: only InMemorySessionStore exists;
  sessions are lost on restart (`puspa/runtime/hierarchy.py:253,256`).
- Chassis factory full-loop end-to-end test deferred to phase 2:
  `assemble()` tested only with a stubbed AgentLoop.
- `run_scheduled_job` has no production caller: scheduler jobs exist,
  nothing invokes due triggers.
- The learning loop's `record_misprediction` guardrail is fixed:
  its lazy import of the evidence renderer resolves through the
  public `puspa.policy` allowlist (the removed private sandbox
  is gone from the chain), and the watcher tests run the real
  guardrail end to end instead of stubbing it, so a
  misprediction reaches the loop on every path
  (`tests/test_misprediction_guardrail.py`).
- The world model re-derives dynamics from traces on read, so the
  consolidation pass IS the retraining (documented, not a separate
  training step).

## 8. How to improve it (where the headroom is)

- The biggest lever is memory that never forgets: write-through +
  recall-first + durable MEMORY.md (items 1-3). A brain that forgets
  cannot be trusted, however smart its reasoning is.
- Second is initiative (K14) plus metacognition (K20): the difference
  between an assistant that answers and a mind that acts.
- Third is the world model: prediction + surprise (K19) turns the
  outcome watcher from a checker into a learner.
- Do not promise fractal resolution or a self-growing ontology to users
  until they are proven at scale; build them incrementally (K17, K19)
  and measure.
- Ground every improvement in the philosophy-of-mind sources named in
  Phase 2 (Baars, Baddeley, Friston, BDI, metacognition) and in the AI
  frameworks named there; keep the one-line plain explanation of each
  concept next to its use, the way this document does.

## 9. Rules for working in this repo

- All naming, docs, and commits in English. Commit subjects:
  `type(scope): short plain description`, 3-line body (why, what,
  tests), NO ticket codes anywhere.
- One short-lived branch per item, PR + green suite + squash-merge,
  branch deleted after merge. Max 2 branches alive.
- Per change run only module + neighbor tests; the full suite runs at
  merge checkpoints. Never claim green without running it.
- "Done" means 100% full, no remainder. Anything partial is "not done"
  plus what remains, counted from real items. Never write "done, but..."
- Verify against the live result (API, files, logs), never trust a
  "done" report from a helper.
- Related docs: `docs/kosmos-design.md` (Phase 1 rationale),
  `docs/baseline-speed-memory.md` (speed + memory baseline),
  `docs/kosmos-phase2-mind.md` (Phase 2 items),
  `docs/split-public-private.md` (what belongs in this public repo vs
  the private `puspa-cloud` repo).
