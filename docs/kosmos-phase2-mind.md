# Kosmos Phase 2: A Human-Like Mind

Status: design, ordered by Nero on 21 Nov 2026. Builds on the Phase 1 brain
(K1-K13, all complete) and on the speed and memory baseline
(`docs/baseline-speed-memory.md`).

Goal, in Nero's words: a brain as complex as a human's. It acts without
being told, decides by itself, remembers short term and long term, and
maintains its own model of the world. Grounded in real AI frameworks and in
philosophy of mind, not in vibes.

## What Phase 1 already gives us

- K1 intent engine, K2 act-or-ask, K3 bitemporal state, K4 causal ledger,
  K5 other-agent goal models, K6 ignorance map, K7 simulator, K8 outcome
  watcher, K9 consolidation and ontology review, K10 interruption budget,
  K11 autonomy dial, K12 audit log plus kill switch, K13 anti-manipulation.
- Auto-ontology exists halfway: K9 reviews the ontology and reports
  problems (contradictions, stale facts, hidden variables, sampling bias)
  but never repairs anything by itself. The repair half is K17 below.

## The new items (K14-K20)

### K14 — Self-directed initiative

The agent derives its own sub-goals from the user's standing goals and open
loops, acts on them without being told, and reports what it did.

- Philosophy: agency. A mind does not only answer; it pursues.
- AI framework: BDI (beliefs, desires, intentions) plus goal-driven
  autonomy. Desires come from user goals; intentions are the agent's own
  committed plans.
- Build: a goal queue fed by the ledger's open loops; each turn the agent
  may take one self-directed step; every step is ledger-recorded and
  reported in the next user-visible summary.

### K15 — Working memory (short term)

A bounded short-term store. Items carry an attention weight; unrehearsed
items decay; rehearsed items are promoted to long-term memory.

- Philosophy: Baddeley's working memory. A mind holds a few things at
  once, not everything.
- AI framework: explicit context management instead of stuffing the whole
  history into the prompt.
- Build: `puspa/memory/working.py`, a slot store with decay and rehearsal;
  the router reads from it first each turn.

### K16 — Long-term consolidation

Episodic events are promoted to semantic facts by rules: repetition,
importance, and user correction. Low-value traces fade on a forgetting
curve. User corrections never fade.

- Philosophy: memory consolidation (sleep replays the day and keeps what
  matters).
- AI framework: replay-based consolidation; Ebbinghaus forgetting curve.
- Build: extend the K9 dream pass with promotion rules and a forgetting
  curve; corrections are pinned.

### K17 — Self-evolving ontology (auto ontology, full)

The concept graph adds, merges, and splits concepts from new facts
automatically. K9's review findings become automatic repairs, each recorded
in the ledger so the user can see and undo them.

- Philosophy: concepts are not fixed labels; a mind revises what things
  mean as it learns.
- AI framework: ontology learning from text plus graph maintenance.
- Build: `puspa/memory/ontology_evolve.py`; every repair is a ledger event
  with the old and new concept.

### K18 — Global workspace (attention)

Modules compete to broadcast to one shared workspace. The winning content
becomes the agent's current focus and drives the next action. Everything
else stays out of the way.

- Philosophy: Baars' global workspace theory. Consciousness is a stage;
  the loudest content gets the stage.
- AI framework: attention and broadcast; a shared blackboard with
  competing writers.
- Build: a workspace bus in the router; each module scores its candidate;
  the top score broadcasts; the broadcast is ledger-recorded.

### K19 — Prediction and surprise

The agent predicts the outcome of each action before doing it, compares the
prediction with the real outcome, and updates its models from the error.
Surprise is the learning signal.

- Philosophy: predictive processing. A mind is a prediction machine; it
  learns from being wrong.
- AI framework: world models; prediction error as the training signal.
- Build: extend the K8 outcome watcher with a predict-then-compare step;
  prediction errors feed the K9 consolidation pass.

### K20 — Metacognition (knowing what it does not know)

The agent tracks its own confidence per decision, knows what it does not
know, and escalates to the user only when uncertainty is high AND the
stakes are high. Low stakes with low confidence means act carefully and
record the doubt; high stakes with low confidence means ask.

- Philosophy: metacognition, higher-order thought. A mind watches itself
  think.
- AI framework: calibrated confidence; uncertainty-aware decision making.
- Build: a confidence score attached to every K2 act-or-ask decision;
  extend the K6 ignorance map into a live uncertainty register.

## Order of building

K15 and K16 first (memory is the foundation), then K17 (ontology), then
K14 (initiative) and K20 (metacognition), then K18 (workspace) and K19
(prediction). Each item lands as its own branch, PR, and green suite, the
same way K1-K13 landed.
