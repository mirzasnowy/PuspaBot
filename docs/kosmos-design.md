# Kosmos — Brain Design for Puspa

Status: accepted as the brain design document (Nero, 18 Nov 2026). Source: design note presented by Nero, 18 Nov 2026. This is a design document, not a promise: the riskiest parts (fractal resolution, self-growing ontology) are research-level and are NOT promised to users. Product name stays Puspa; Kosmos is the internal name for the brain design.

## The core idea

Two worlds separated by a wall. Inside is the World Engine, a machine that models reality and tests itself continuously. Outside is Presence, a surface that is almost without interface. The user only ever meets Presence, and the wall has one audit door. The system is called Kosmos.

## A. Presence (what the user feels)

- Any input: half-finished speech, a photo, a long silence, a habit. No command format.
- Output is a world already changed, plus 3-4 sensory signals (silence, pulse, texture change, warmth).
- Universal undo: every action can be undone with one gesture.
- A layered "why?" door: brief first, deep when asked.
- Kosmos rarely speaks, and when it does, one sentence is enough.

## B. Intent Engine

- Intent is read as a distribution over three levels: the current desire, the goal in progress, and long-term values. "I'm so tired" is interpreted at all three at once.
- Act or ask is decided from confidence x reversibility x stakes. If reversible and low-stakes, act directly. If irreversible and high-stakes, stop and ask one calm question.

## C. World Engine (what makes it "general")

1. Seven universal primitives: Entity, Relation, Process, Resource, Agent (people, organisations, machines with their own goals), Constraint, Time. Any domain is a combination of them, and the ontology can grow when a pattern does not fit.
2. Fractal resolution: detail only where relevant to the user, coarse priors from the LLM elsewhere. Zoom in when needed. This is what makes a "general world model" computationally sensible.
3. Bitemporal state: what is true, and when the system learned it.
4. Causal Ledger: hypotheses "X -> Y under condition C" with strength, evidence, and expiry.
5. Models of other agents: what other people want and will do. Reality is full of agents, not dead objects.
6. Three-speed counterfactual simulator: fast (LLM intuition), medium (dynamics learned from traces), deep (dry-run and sandbox connected to real tools).
7. Ignorance map: knowing where it is blind.

## D. Planner and actuators

- Universal actuators through adapters: API, browser, GUI control, code, devices, even asking another human for help. If an adapter does not exist, Kosmos writes it itself and tests it in the sandbox.
- Effects grouped: reversible, expensive-but-reversible, and irreversible. Flow is draft -> shadow -> commit, with two-phase confirmation for irreversible effects.
- When it does not know, it places a small probe that is safe if it fails.
- Procedures that succeed are compiled into reusable skills.

## E. Verification (the heart of credibility)

- Before acting, Kosmos writes down its prediction and its confidence.
- The verifier is independent of the model that did the work, and judges from world signals: message sent, balance changed, reply arrived.
- The outcome watcher keeps monitoring after "done", because many failures only appear later.
- Internal prediction market: models and hypotheses bet, their weights following track record.

## F. Learning (three clocks)

- Seconds to minutes: updates from action results.
- Daily ("sleep"): consolidate traces, discard stale hypotheses, retrain dynamics.
- Monthly: restructure the ontology, look for hidden variables, audit bias.
- The most honest signal is silent correction: undo, edited results, or ignored suggestions. That is more honest than the user's words.

## G. Memory

Five layers: episodic, semantic, procedural, person models, and values/preferences. There is deliberate forgetting, and the user can delete anything including its derivatives.

## H. Proactivity

Default is silence. Kosmos "pays" for the user's attention from an interruption budget and only moves on its own when the benefit exceeds the cost of the interruption. Good proactivity is mostly silent reversible action, not notifications.

## I. Autonomy and trust

- Autonomy dial per area, not global, moving automatically with prediction calibration.
- Stages: propose -> do and show -> do silently.
- One serious failure in an irreversible area drops the dial immediately.

## J. Security, privacy, values

- Local-first and encrypted, so the user's world model belongs to the user.
- Everything from the outside world (email, web, other people's messages) is treated as untrusted data, not commands.
- Hard limits the agent cannot change: money above a threshold, illegal things, harming third parties.
- Append-only audit log outside the agent's reach, plus kill switch and export.
- Anti-manipulation: a system that understands you can be very good at manipulating you. It optimises for the user's reflective goals, not engagement or dependence. When interests collide, show it, do not decide silently.

## K. How it fails, and the remedies

- A wrong guess collapses the illusion. Remedy: reversible actions, honest calibration, fast repair without defensiveness.
- Over-trust in its own model is countered with the prediction market and internal red-teaming.
- The user changing is handled through hypothesis expiry and drift detection.
- Excessive dependence is handled with an "independence dial" that keeps the user's skills alive.

## L. Building it realistically

"God" cannot be built at once. The order:
1. Presence, undo, and verifier on a few narrow but cross-domain actuators (communication, schedule, documents).
2. Ledger and pre-registered predictions.
3. Autonomy dial.
4. Deep simulator.
5. Self-growing ontology.

The moat starts forming from outcome data in steps 2 and 3.

Most at risk: fractal resolution and self-growing ontology. Both are still research-level and unproven at large scale, so do not promise them to people as something certain to work.

## Assessment (Luna, 18 Nov 2026)

Kosmos is not a new product. It is a sharper formulation of Puspa's brain. Mapping to what exists:

| Kosmos part | Puspa today |
|---|---|
| World Engine | puspa/world (model + beliefs + simulator), exists, narrow (tasks, schedule, capability, contacts, habits) |
| Presence | PWA, exists as a shell |
| Verifier | eval harness + learning loop, exists, needs independence + outcome watcher |
| Learning, fast clock | learning loop, exists |
| Learning, daily clock | puspa/memory/consolidate.py, exists in embryo |
| Actuator adapters written by the system | coding harness (write-test-fix in sandbox), exists in embryo |
| Memory, 3 of 5 layers | 3-layer memory + profile, exists |
| Security posture | fail-closed, ledger, policy, exists |

### Taken into the work list (K items)

- K1. Intent Engine: intent as a distribution over three levels (current desire, ongoing goal, long-term values). NEW.
- K2. Act-or-ask rule: confidence x reversibility x stakes, computed, not just a taxonomy. Extends approvals (G4) and gateway (G5).
- K3. Bitemporal state: valid time vs known time on beliefs and ledger entries. NEW.
- K4. Causal Ledger: hypotheses "X -> Y under C" with strength, evidence, expiry. Extends the learning loop.
- K5. Other-agent goal models: extend contact beliefs into models of what other people want and will do. NEW.
- K6. Ignorance map: track where the model is blind. NEW.
- K7. Three-speed simulator: structure the existing simulator into fast / medium / deep. Extends the world model.
- K8. Outcome watcher: keep monitoring after "done". Extends eval harness + learning loop.
- K9. Daily sleep consolidation + monthly ontology review. Extends consolidate.py.
- K10. Interruption budget for proactivity. NEW, small.
- K11. Autonomy dial per area, moving with prediction calibration; one serious irreversible failure drops it immediately. NEW.
- K12. Audit log outside the agent's reach + kill switch + export. Hardening.
- K13. Anti-manipulation as a design rule: optimise for reflective goals, surface conflicts, never decide silently. Design rule, applies to all K items.

### Acceptance criteria (added 19 Nov 2026)

These criteria were written by Luna on 19 Nov 2026 from this design note and the build standard of the G items (SOLO-green tests, real wiring, docs updated). The original design note defined no completion criteria.

**K1 — Intent Engine**
1. Intent is stored as a three-level distribution (current desire, ongoing goal, long-term values), not a single label.
2. Tests prove one input ("I'm so tired") maps to all three levels with distinct scores.
3. The distribution is consumed by a real caller (turn loop or decider), not a standalone module.
4. Suite green, docs updated.

**K2 — Act-or-ask rule**
1. The act-or-ask decision is computed from confidence x reversibility x stakes, not just a taxonomy.
2. Reversible, low-stakes actions run directly; irreversible, high-stakes actions stop and ask one calm question.
3. Wired into approvals (G4) and gateway (G5).
4. Tests cover both ends of the spectrum plus boundary cases.
5. Suite green, docs updated.

**K3 — Bitemporal state**
1. Every belief and ledger entry stores two times: when true (valid time) and when the system learned it (known time).
2. Tests prove correcting the past does not overwrite history (old times stay readable).
3. Schema migration and queries using both times run on Postgres.
4. Suite green, docs updated.

**K4 — Causal Ledger**
1. Hypotheses stored as X -> Y under condition C, plus strength, evidence, and expiry.
2. Expired hypotheses are automatically dropped or weakened.
3. Hypotheses are used by the learning loop (they influence predictions or decisions), not just stored.
4. Tests cover formation, strengthening, and expiry.
5. Suite green, docs updated.

**K5 — Other-agent goal models**
1. Each model stores another person's goals and predicted behaviour, built from existing contact beliefs.
2. Tests prove predictions are recorded and scored right or wrong once the outcome is known.
3. Models are used by a real caller (e.g. the planner accounts for other people's reactions).
4. Suite green, docs updated.

**K6 — Ignorance map**
1. The map records areas or topics where the model has no evidence or low confidence.
2. Tests prove an empty area is detected and disappears once evidence arrives.
3. The map is used by a real caller (e.g. the system asks or places a probe in an empty area instead of guessing).
4. Suite green, docs updated.

**K7 — Three-speed simulator**
1. The simulator is structured in three speeds: fast (LLM intuition), medium (dynamics learned from traces), deep (dry-run and sandbox with real tools).
2. Each speed is callable and its results are recorded.
3. Wired into the existing world model.
4. Tests cover all three plus fallback when one speed fails.
5. Suite green, docs updated.

**K8 — Outcome watcher**
1. The system keeps monitoring results after a task is marked done.
2. Late-appearing failures are recorded and fed into the learning loop.
3. Wired into the eval harness and sentinel (G12).
4. Tests cover a late failure being detected.
5. Suite green, docs updated.

**K9 — Daily sleep consolidation + monthly ontology review**
1. Daily consolidation runs (traces merged, stale hypotheses dropped, dynamics retrained).
2. Monthly ontology review runs (restructuring, hidden-variable search, bias audit).
3. Both are scheduled and recorded in the ledger.
4. Tests cover one end-to-end consolidation cycle.
5. Suite green, docs updated.

**K10 — Interruption budget**
1. Every proactive initiative "pays" from a limited daily interruption budget.
2. An empty budget means silence: no new notifications.
3. Tests prove the budget suppresses spam while still allowing high-value actions.
4. Wired into the existing scheduler or turn loop.
5. Suite green, docs updated.

Implemented 19 Nov 2026 (`puspa/proactivity/budget.py`,
`docs/interruption-budget.md`): 5 units/day, per-kind cost
and minimum value, a scarcity rule reserving the last 2 units
for value >= 0.85, silent actions free. Wired into
`scheduler.tick` / `run_trigger` / `run_missed` (proactive
trigger types `price_drop`, `due_date_bill` only) and into
`GatewayRunner.notify`. In-memory: a restart resets the day's
spend (documented limit). Denials are audited in the budget's
spend log, not the ledger — no 32nd event type.

**K11 — Autonomy dial per area**
1. The dial is per area (not global) and moves with prediction calibration.
2. One serious failure in an irreversible area immediately drops that area's dial.
3. The stages propose -> do and show -> do silently follow the dial.
4. Tests prove an immediate drop after a simulated failure.
5. Suite green, docs updated.

**K12 — Audit outside agent reach + kill switch + export**
1. The append-only audit log is outside the agent's reach (the agent cannot modify or delete it).
2. The kill switch stops the agent and is recorded in the log.
3. Export of all user data works (including memory derivatives).
4. Tests prove an agent attempt to modify the log fails (fail-closed).
5. Suite green, docs updated.

**K13 — Anti-manipulation design rule**
1. The rule is written in docs and enforced in code (not just intent): optimise for the user's reflective goals, not engagement.
2. Conflicts of interest are shown to the user, never decided silently.
3. A test or check proves one conflict scenario is surfaced to the user.
4. The rule is checked against every other K item (review checklist).

### Explicitly NOT taken literally

- Presence sensory signals (silence, pulse, texture, warmth): poetry, not a buildable spec for a phone PWA. The buildable core is kept: minimal interface, universal undo, layered "why?", rarely speak, one sentence.
- Internal prediction market, literally: too heavy. Implemented as weights from track records.
- Fractal resolution and self-growing ontology as promises: research-level, unproven at scale. Built incrementally (step 5 of the build order), never promised to users.

### Placement in the work queue

K items fold into the maturity phase after Tier 2 and the rework of 4 shallow spots. Items overlapping the G list fold into those G items (K2 into G4/G5, K8 into G12, K9 into G7/G24, K7 into the world-model work); the rest are new items after the G list. No new phase is created for Kosmos.
