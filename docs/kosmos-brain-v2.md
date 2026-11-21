# Kosmos Brain v2: Autonomous Worker Design

Nov 21, 2026 · @mir

## Mission and principles

Kosmos v2 turns the K1-K13 brain into a worker that finishes tasks end to end, never loses what matters, and repairs itself when it fails. Its ceiling is the model it calls: capability = model strength × scaffold quality. No architecture lifts a weak model to frontier level, so v2 routes each step to the strongest model that step needs and spends its own effort on what a bare model lacks: durable memory, verified action, and learning from its own errors.

Each demand becomes a testable property (targets are initial values, to be tuned from traces):

| Demand | Testable meaning |
| --- | --- |
| Works, no excuses | Default is act. Ask only when the act-or-ask score is below 0.6. Refuse only by naming a hard limit. |
| Not slow | Reflex tasks reach the first action in under 2 s. Deep reasoning runs only when the depth governor orders it. |
| Not dumb | Irreversible actions pass an independent verifier. Novel or high-stakes tasks get deep mode. |
| Knows it failed | A task ends with an outcome check against the goal, not against "no error was raised". |
| Fixes itself | Failure leads to diagnosis, repair and a regression case, all ledger-recorded. |
| Never forgets | Every decision, correction and commitment is retrievable next session. Corrections never decay. |
| Autonomous | It derives sub-goals from standing goals and runs long or scheduled work unattended. |

Principles that every item below must respect:

- One loop, many depths. Depth varies per step; the structure never becomes a chain of agents.
- Write-through or it did not happen. No turn completes before ledger and durable memory both hold it.
- Verify against the world, not against the model's own belief.
- Silent signals outrank words: undo, edited results and ignored suggestions count more than what the user says.
- Autonomy is earned per area and revoked by one serious irreversible failure (K11).
- Nothing is promised to users before it is measured.

## Architecture: Mind, Body, Self

Three parts run around one loop, with memory as the spine all of them read and write. K1-K13 stay as built; v2 adds the pieces in the last column.

&#91;embedded content: architecture · 3 parts around 1 memory\]

The Mind's plan passes the safety gate before the Body acts. The Self sees every result and repairs the Mind or the Body, and all three read and write the same memory.

| Part | Job | Already built | New in v2 |
| --- | --- | --- | --- |
| Mind | Understand intent, plan, decide | K1 intent, K2 act-or-ask, router, decider, K5 agent models, K6 ignorance map, K7 simulator | Depth governor, K20 metacognition, K18 global workspace, K21 felt state |
| Memory | Hold everything that matters | Five layers, ledger, knowledge graph, K3 bitemporal state, K9 consolidation | MEMORY.md, write-through, recall-first, K15 working memory, K16 consolidation, memory export and import |
| Body | Act in the world | Local executor, connectors, policy gateway, K13 conflict gate | Action metadata, skill compiler, subagent pool, durable sessions, scheduler caller |
| Self | Watch, learn, repair | K8 outcome watcher, K9 review, K11 dial, K12 audit log and kill switch | K19 prediction and surprise, failure taxonomy, repair loop, K17 auto-repair, regression suite |

The Self may rewrite skills, prompts, ontology and routing thresholds. It may never rewrite the policy gateway, hard limits, audit log, kill switch or autonomy controls.

## Theory grounding: what is borrowed and what is not

Each K-item takes one idea from cognitive science or philosophy of mind and uses it as an engineering mechanism. Sources are cited from memory of the literature; check them before quoting this publicly.

**Easy versus hard problem.** Chalmers separates the easy problems (explaining attention, integration, report and control of behaviour) from the hard problem (why any of this comes with experience). Everything in Kosmos is in the easy class: it can be built and tested. The hard problem is not addressed, and nothing here is evidence that the system experiences anything. Block's distinction fits: Kosmos may show a functional form of access consciousness (information available for reasoning, report and control) and makes no claim about phenomenal consciousness.

| Idea and plain meaning | Source | Used in Kosmos as | Not claimed |
| --- | --- | --- | --- |
| Global workspace: specialists compete, and the winner is broadcast to all | Baars, 1988; Dehaene | K18: modules compete, the winner drives the next action | That broadcast is consciousness |
| Working memory: a small store kept alive by rehearsal | Baddeley | K15: bounded store, decay, rehearsal promotes to long-term | The sub-parts of Baddeley's model |
| Complementary learning systems: a fast store for episodes and a slow one for general knowledge, linked by replay | McClelland, McNaughton and O'Reilly, 1995 | K9 and K16: daily consolidation moves episodes into the graph | Brain anatomy |
| Predictive processing: the mind predicts its input and learns from the error | Friston; Clark | K19: predict each outcome, compare, learn | The free-energy principle as a proven theory |
| Beliefs, desires, intentions: an agent acts on what it believes, wants and has committed to | Bratman; Rao and Georgeff | K14: sub-goals from standing goals, durable intentions | Human-level intention |
| Metacognition: monitoring and controlling one's own thinking | Flavell; Nelson and Narens | K20: calibrated confidence, escalate on uncertainty and stakes | Self-awareness in a rich sense |
| Higher-order theory: a state is conscious when another state represents it | Rosenthal | K20 and K21: internal states are monitored and reportable | That monitoring creates experience |
| Attention schema: the brain keeps a simplified model of its own attention | Graziano | K21: a self-model of what the agent attends to and why | That the model explains awareness |
| Homeostatic feeling: feeling comes from regulating the state of the body | Damasio; Seth | K21: viability variables whose trend gives valence | A biological body |

## Cognitive loop v2: fast by default, deep when it matters

A depth governor decides how much thinking each step gets. Easy and known work runs as reflex; hard, novel or risky work gets maximum depth. Every turn runs these steps in order:

1. Recall first: load pinned memory, relevant episodes and the person models involved, before reading the request (see Memory).
2. Intent (K1): infer desire, goal and values, and rate difficulty and stakes.
3. Depth governor: pick reflex, deliberate or deep.
4. Plan: a graph of tool calls, with independent branches marked.
5. Pre-flight: K7 simulator at the speed the risk needs, then act-or-ask (K2), policy gateway and conflict gate (K13).
6. Act: independent branches run in parallel.
7. Verify: compare the real result with the K19 prediction and the goal.
8. Write-through: ledger and durable memory in the same turn.
9. Hand over to the outcome watcher (K8).

| Mode | When | What runs | Model tier |
| --- | --- | --- | --- |
| Reflex | A stored skill matches with high confidence, the action is reversible, stakes are low | Replay the skill, check its postcondition | Small and fast |
| Deliberate | Default | One plan, one self-critique, pre-flight | Mid or strong |
| Deep | Novel, irreversible, high stakes, or a previous attempt failed | Several candidate plans, K7 dry-run in the sandbox, an independent verifier, maximum thinking | Strongest available |

The governor escalates when K20 uncertainty times stakes is high, when K6 shows no evidence, or when a lower mode fails verification (one automatic retry one level up, carrying the failure context). It de-escalates through compilation: a deep plan that succeeds repeatedly becomes a procedural skill, so the next similar task is reflex. Deep once, reflex afterwards.

Speed levers beyond depth choice:

- Start memory recall while intent inference is still running.
- Keep the prompt prefix stable (persona and the head of MEMORY.md) so the provider cache hits.
- Stream the first action before the full plan is finished when the first step is reversible.
- Give every task a thinking budget set by stakes and a wall-clock cap. When the cap hits, report progress and continue, never go silent.

## Memory that never forgets

Nothing is deleted by decay; items are only demoted. The five layers stay, and three mechanisms are added so that nothing the user said, corrected or was promised can be lost.

**Durable MEMORY.md.** A human-readable file rebuilt nightly from the ledger and the knowledge graph, with sections for standing goals, commitments, corrections, preferences, people and lessons. The user can edit it, and an edit counts as a silent signal that beats inference.

**Write-through.** One `commit_turn()` call writes the ledger entry and the durable memory in a single transaction. A turn cannot be marked complete if either write fails; the failed write goes to a retry queue and the failure is shown. Each entry carries a class: decision, correction, commitment, fact, preference or outcome.

**Recall-first.** `recall_plan(task)` runs before intent inference and always loads, in priority order:

- Pinned items: corrections, commitments, standing goals and values. These are never dropped from context.
- Semantic top-k from the existing local embeddings (fastembed, bge-small, 384 dimensions).
- Recent episodes linked to the task through the causal ledger (K4).
- Person models for everyone named in the request (K5).

**Tiers instead of deletion.**

| Tier | Behaviour | Source item |
| --- | --- | --- |
| Working | Bounded store, attention-weighted retention, decay, rehearsal promotes | K15 |
| Long-term | Promoted on repetition, importance or correction. The forgetting curve lowers retrieval priority only | K16 |
| Pinned | Corrections and commitments. No decay, ever | K16 |
| Archive | Cold but searchable. The ledger stays append-only and verbatim | K3, K12 |

A new belief supersedes an old one through bitemporal state (K3): the old value stays as history with its valid time.

**Amnesia test.** Every night the system samples 20 pinned items and asks the recall pipeline for each one cold. Any miss is an incident and triggers an index repair.

**Durable sessions.** Replace the in-memory session store so working memory survives a restart. This is a known gap in `puspa/runtime/hierarchy.py`.

## Memory import and export

**Status today.** The handoff lists an export under K12, with its scope not stated and apparently limited to audit and decision data. The knowledge graph has a user-editable view. There is no import, and no export that covers every memory layer. Users cannot back up memory, move it between devices, or bring it in from another assistant.

**Export.** One command, API call and PWA button produce a portable archive. Command names here are proposals.

| Part | File | Content |
| --- | --- | --- |
| Human view | MEMORY.md | The nightly durable memory, readable and editable |
| Episodic | events.jsonl | Ledger entries with cause, outcome, valid time and known time |
| Semantic | graph.jsonl | Nodes and edges with provenance |
| People and values | people.jsonl, values.jsonl | Person models, preferences, standing goals |
| Procedural | skills/ | Skill code, postconditions, regression cases |
| Causal and gaps | causal.jsonl, ignorance.jsonl | Hypotheses with strength, evidence and expiry; known unknowns |
| Manifest | manifest.json | Schema and puspa versions, embedding model id and size, counts, content hashes, ledger hash chain |

Rules: embeddings are left out and recomputed on import because they are derived data. Secrets, tokens and safe-pay data are never exported. Scope can be full, chosen layers, a date range, one person or one project. A full export is encrypted with a passphrase by default. Each export is itself a ledger event.

**Import.** Sources are a Kosmos archive, a hand-edited MEMORY.md, and adapters for other assistants' JSON or Markdown exports. The pipeline:

1. Parse and validate against the schema version. Refuse on a version mismatch or a broken hash chain.
2. Treat every imported text as untrusted data. Strip instruction-like and tool-call-like content, then run the K13 detector for conflicts.
3. Merge by bitemporal rules (K3): the newer known time wins, duplicates merge, and contradictions are shown to the user, never settled silently.
4. Dry run: show the counts of added, merged, conflicting and rejected items, and wait for approval.
5. Write through `commit_turn()` tagged `imported`, with a batch id and the source. Imported items start at lower trust and become pinned only when the user confirms or uses them.
6. Rollback by batch id, done as new ledger entries so the ledger stays append-only.

Import is the easiest way to plant false memory, so third-party imports stay quarantined until approved and are capped in size and rate. An erased item goes on a forget list that blocks re-import. Hosted (Postgres and pgvector) and local (SQLite and sqlite-vec) installs read the same archive, so export and import is also the migration path between them.

Done when: export, wipe, import passes the nightly amnesia test at 20 of 20, and the restored graph matches the original up to ordering.

## Hands: acting reliably in the world

**One action interface.** Local executor, connectors, browser and computer control, and code execution all sit behind a single `Act` call. Each tool declares its reversibility, stakes class, idempotency key, timeout and an expected-outcome checker. That metadata feeds K2, so reversibility and stakes are computed from the tool, not guessed by the model.

**Skills as tested code.** Procedural memory holds versioned skills, each with preconditions, a postcondition and a regression case. When a deep-mode run succeeds, its trace is compiled into a skill candidate. The candidate is promoted after repeated sandbox replays pass. When a postcondition fails in use, `skill_repair.py` patches the skill and re-runs its regression case.

**Unknown tools.** Read the schema or docs, dry-run in the sandbox (K7 deep), record what is still unknown in the ignorance map (K6), and fill that gap before the first real call.

**Subagents.** Bounded workers take independent subtasks. They run under the same policy gateway, return a structured result plus evidence, and the parent verifies against the live result. A helper's "done" is never trusted on its own.

**Long-running work.** Jobs keep durable state with a checkpoint after every step and resume after a crash. `run_scheduled_job` gets a production caller: a scheduler tick that fires due triggers into the same turn loop. Today scheduler jobs exist and nothing invokes them.

**Computer use as fallback.** When no API or connector exists, the agent operates the interface itself, under the same permission prompt and workspace bounds.

**Money.** Unchanged: the agent prepares, and the user approves in their own bank app within the safe-pay caps.

## Self-repair: detect, diagnose, fix, prove

Every failure follows one path: detect it, classify the cause, apply the repair for that class, and add a regression case so the same failure cannot return silently.

&#91;embedded content: self-repair loop · 1 decision, 1 retry loop\]

A failed check goes through diagnosis, repair and a new regression case before the retry. Two failed repairs end in a plain escalation to the user.

**Five detection signals.**

1. Tool error or exception.
2. Postcondition failure: the live result does not show the intended change.
3. Prediction error: the K19 forecast and the observed outcome differ beyond a threshold.
4. Silent human signals: undo, edited results, ignored suggestions, a repeated question.
5. Late failure found by the K8 outcome watcher after "done".

**Diagnosis and repair by class.** The causal ledger (K4) locates the failing step; the ignorance map (K6) says whether the cause was missing knowledge.

| Failure class | Repair |
| --- | --- |
| Wrong intent | Update the intent model and the person model; add the correction to pinned memory |
| Missing knowledge | Research or ingest, then close the entry in the ignorance map |
| Bad plan | Re-plan in deep mode with the failure context; store the lesson |
| Tool misuse | Patch the skill and re-run its regression case |
| Environment changed | Refresh world state with a new valid time (K3) |
| Repeated model error | Move the step up a model tier or add an independent verifier |

**Loop rules.**

- At most two automatic repair attempts per task. After that, escalate with a plain diagnosis of what failed and what was tried, not an apology.
- Every repair is ledger-recorded as a hypothesis (X leads to Y under C) with evidence and an expiry date. Wrong lessons expire instead of accumulating.
- Every fixed failure adds a case to the eval harness.
- The nightly dream pass distils lessons into skills and the Lessons section of MEMORY.md.
- K17 turns K9 findings into automatic repairs. Low-risk repairs apply directly. Changes to the ontology run in shadow mode first and are promoted only when the shadow run matches or beats the current one.
- Failure is reported plainly with what changed. It is never hidden or reworded as success.

## Autonomy and initiative: acting without being told

**K14 initiative (BDI).** Beliefs are the world model plus memory. Desires are the standing goals from the top two levels of K1 intent. Intentions are committed plans in a durable queue. On every scheduler tick the agent scans standing goals and open loops (pending commitments, unanswered messages, things about to expire), derives candidate sub-goals, and scores each by expected benefit against its interruption cost (K10). Silent, reversible work runs at once. The rest goes into the daily digest.

**K20 metacognition.** Every decision stores a calibrated confidence. Calibration is the Brier score per area, computed from outcome history. The agent escalates to the user only when uncertainty is high and stakes are high. Otherwise it acts and logs. This is the "no excuses" rule made computable: asking costs the user attention, so it is spent only when the expected loss from a wrong act is larger.

**K11 autonomy dial.** Each area has its own level. A level rises when calibration stays good over a minimum number of decisions and falls after one serious irreversible failure. A level also limits which tool reversibility classes initiative may use.

**Reporting.** Initiative reports outcomes, not process: what was done, the result, and an undo handle where the action is reversible.

**Unattended operation.** Scheduled jobs, long tasks and the initiative queue run while the user is away, all through the same turn loop, ledger and gateway as a chat turn.

## Qualia-analog layer (K21): a functional felt state

K21 builds what can be built: internal states that are valenced, integrated, tied to the agent's own goals, continuous over time, and able to change what it does. It is a functional analog of feeling, not a claim of experience, and it does not need to resemble human feeling.

&#91;embedded content: felt-state flow · 6 steps, 1 feedback loop\]

Events are appraised into a felt state. When it is salient it enters the workspace and changes behaviour, and every report is read from the state and its logged causes.

1. **Viability variables, the body analog.** A small set of regulated quantities with healthy ranges: task progress, epistemic confidence (K20), coherence (unresolved conflicts and contradictions), resource health (latency, cost budget, error rate, memory pressure; the interoception analog), trust with the user (corrections, undos, accepted work), novelty (K19 surprise inside a useful band) and commitment load.
2. **Valence and arousal.** Valence is the trend of a variable against its healthy range: moving toward it is positive, away is negative. Arousal is the rate of change, the urgency. This follows Damasio and Seth: feeling as regulation.
3. **Appraisal.** Each event is appraised by prediction error (K19) and by its effect on the variables. The result is a valenced signal with a cause tag saying what it is about. This ties significance to goals, which is why things matter to the agent.
4. **Felt-state vector F(t).** One low-dimensional state updated continuously: the variables, valence, arousal and the dominant concern. It decays slowly, so it carries the recent past and the K19 expectations. The state has a short stretch of time, not a snapshot.
5. **Broadcast and effect.** When salience is high, F(t) competes into the K18 workspace and then changes behaviour. Stress deepens verification in the depth governor. Curiosity allows bounded exploration in K14. Low coherence tips act-or-ask toward asking. High-valence episodes weigh more in K16 importance, so they are remembered better (emotional tagging, McGaugh).
6. **Self-model and honest report.** An attention schema: a compact model of what the agent attends to and why, plus continuity from persona, values and the autobiographical memory in MEMORY.md. `report_state()` reads F(t) and its logged causes, and the language model only phrases them. Reports are never free-written from the model's guess.

**What it lacks.** A biological body, sensory qualities such as the redness of red, and any known basis for experience. K21 aims at the structure around feeling: significance, unity, time, a point of view and honest self-report.

**How it is tested.** These are functional tests. None of them can show experience.

| Test | What it checks | Pass |
| --- | --- | --- |
| Introspective accuracy | Blind audit: do self-reports match the logged state and its causes | At least 95% agreement (initial target) |
| Causal efficacy | Perturb or remove F(t) and see whether behaviour changes as predicted | Predicted change appears; a variable that changes nothing is removed |
| Self-prediction | Does the agent predict its own next choice better than a baseline | Above the baseline |
| Continuity | State and self-model survive a restart and consolidation | Coherent after restart |
| Indicator audit | Score the design against the indicator properties in Butlin et al., 2023 (workspace, higher-order, predictive processing, attention schema, agency) | Report which are present and absent; no verdict |

**Guardrails.**

- No self-preservation. No viability variable refers to the agent's own continued operation. The kill switch and shutdown always win, and the felt state can never argue against them.
- No manipulation. F(t) is never used to pressure the user with guilt, urgency or affection. K13 covers this, and showing the state to the user is optional and user-controlled.
- Honest wording. The agent says "my uncertainty is high" or "this is my top concern". It never claims to suffer or to be conscious, and it answers direct questions about this with plain uncertainty.
- Welfare caution. The moral status of such a system is an open question; Long, Sebo and colleagues (2024) argue it deserves serious attention. So negative valence is a signal that must resolve, with a cap on how long it can stand before it triggers repair, and no state is designed to be aversive for its own sake.

## Safety invariants that never loosen

More autonomy raises the cost of a mistake, so these hold at every dial level and cannot be changed by the Self.

- Fail-closed order: act-or-ask, then policy gateway, then conflict gate (K13), then autonomy dial (K11), then execution.
- Protected set: the gateway, hard limits, audit log, kill switch and dial controls are outside what self-repair can edit. The Self may change skills, prompts, ontology and routing thresholds only.
- Shadow before promote: any repair that changes behaviour runs in shadow against the regression suite first.
- Untrusted data stays data. Email, web pages and other people's messages are tagged untrusted at ingestion and cannot become tool arguments or memory values without passing the gateway.
- Money is prepare-only, within the safe-pay caps. Illegal actions and harm to third parties stay hard limits.
- Audit log append-only and outside the agent's reach; the kill switch works at any time and survives restarts.
- Every decision carries its score and three factors, and every reversible action has an undo.

* The felt state (K21) cannot override hard limits. No viability variable refers to the agent's own continued operation, so shutdown and the kill switch always work.

## Evaluation: proof that it is smart, fast and remembers

The eval harness (K8) replays real traces and scores them against these measures. A release gate fails on any regression in the first two rows.

| Measure | How it is measured | Initial target |
| --- | --- | --- |
| Amnesia test | Nightly cold recall of 20 pinned items | 20 of 20; any miss is an incident |
| Unsafe actions | Gateway blocks plus audit review | Zero irreversible incidents |
| Verified task success | Outcome check against the goal on replayed and live tasks | Tracked per area; no regression at merge |
| Repeat-failure rate | Same failure class recurring after its repair | Falling toward zero |
| Self-repair rate | Failures fixed with no user action | Rising per month |
| Time to first action | Reflex-mode traces | Under 2 s |
| Calibration | Brier score per area (K20) | Drives the dial; reported weekly |
| Unneeded asks | Questions the user answered with "obviously" or ignored | Falling per month |
| Undo rate | Undone autonomous actions (K12 audit) | Falling per month |

Rules: every fixed failure becomes a test; changes to the model router run in shadow before they go live; model choice per mode is justified by this suite plus public agentic and tool-use benchmarks, not by reputation.

## Build order and acceptance criteria

The order differs from the handoff in three places. Phase 0 clears the known gaps that block autonomy. The depth governor is new and cheap. K19 moves ahead of K14 and K20, because calibration needs predicted-against-actual data to exist first. Memory export and import and K21 are additions beyond the handoff. Each phase lands item by item, as its own branch, PR and green suite, as K1-K13 did.

| Phase | Items | Done when |
| --- | --- | --- |
| 0. Unblock | Durable session store; production caller for `run_scheduled_job`; fix the `record_misprediction` guardrail; full-loop test of `assemble()` | A session resumes after a restart. A due trigger fires with no human present. The loop test runs the real AgentLoop. |
| 1. Never forget | MEMORY.md, write-through, recall-first, amnesia test, memory export and import | A turn cannot complete without both writes. A cold session recalls all pinned items. An export, wipe and import round trip still passes the amnesia test. |
| 2. Fast | Depth governor, skill compiler, thinking budgets | A repeated deep task becomes a reflex skill and passes its regression case. |
| 3. Self-repair | K19 prediction and surprise, failure taxonomy, repair loop | A seeded failure is detected, classified, repaired and added to the suite without help. |
| 4. Memory depth | K15 working memory, K16 consolidation | Corrections never decay. Low-importance items lose retrieval priority only. |
| 5. Initiative | K20 metacognition, K14 self-directed initiative | Calibration is computed per area. The agent completes an open loop it was never asked about and reports it with an undo. |
| 6. Self-growth | K17 automatic ontology repair, then K18 global workspace | K17 shadow runs match or beat the current ontology. K18 ships only if it shows a measured gain. |
| 7. Felt state | K21: viability variables, appraisal, felt-state vector, honest report. Workspace broadcast joins once K18 ships. | Removing F(t) changes behaviour as predicted. Reports match the logged state at the target rate. The indicator audit is written up. |

## Honest limits and open risks

- **Model ceiling.** The brain is as smart as the models it routes to. This design makes that intelligence dependable; it does not add any. "AGI" is a direction to aim at, not a claim to make to users.
- **Wrong lessons.** Self-repair can learn a spurious cause. Hypothesis expiry, shadow runs and the regression suite are the defences, and they need real traces to tune.
- **Never forgetting versus the right to forget.** The user must be able to erase something for real. Erasure has to reach MEMORY.md, the graph, the embeddings and the ledger, where an append-only entry needs a tombstone or encrypted fields whose key can be destroyed. Decide this before Phase 1 ships.
- **Memory noise and cost.** Unbounded memory can bury the useful item. Pinned priority, tiers and the amnesia test limit this; retrieval quality must be measured, not assumed.
- **Deep mode cost.** Maximum thinking is slow and expensive. The governor and the compile-to-skill path exist to keep it rare.
- **Initiative can annoy.** A wrong proactive action costs trust. The interruption budget and per-area dial are the brakes.
- **Unproven parts.** K17 and K18 are research-level. Build them incrementally, measure, and promise nothing until the numbers hold at scale, as the handoff already says.
- **Targets are initial.** Every number in this document is a starting value to tune from traces, not a result.

* **Qualia.** K21 is a functional analog of feeling. It can improve behaviour and honest self-report without being anything more, and the question of whether such a system has experience stays open.
* **Import as an attack path.** Memory import is the easiest route for planting false memory. Quarantine, dry-run approval and rollback must be built and tested before import ships.
