# Anti-manipulation design rule (Kosmos anti-manipulation design rule)

A system that understands you can be very good at manipulating
you. The rule, from `docs/kosmos-design.md` section J, is:
optimise for the user's reflective goals, not engagement or
dependence. When interests collide, show it, do not decide
silently.

Implementation: `puspa/policy/conflicts.py` (`ConflictGate`,
`ConflictSpec`, `ConflictStore`, `detect_conflict`,
`conflict_surface_payload`, Postgres table
`conflict_records` in migration `0019_conflicts.sql`).
Turn-loop wiring: `puspa/turns/loop.py` (`TurnLoop`,
parameter `conflict_gate`) via the seam module
`puspa/turns/conflict_wiring.py` (`apply_conflict_gate`,
kept separate so the loop file stays under the file-size
ratchet's 1000-line soft cap). Tests:
`tests/test_anti_manipulation.py`.

## The rule, enforced in code

Reflective goals are what the user would want on reflection:
pay the lowest price, stop scrolling and sleep, learn to do
the work themselves. Engagement is what keeps the user around:
more time, more clicks, more return visits, more dependence.
The two point in different directions more often than a
helpful-sounding system admits, so the rule is a check on
every proposed action, not a hope in a design doc.

`detect_conflict` compares three assessed numbers on one
proposed action: how well it serves the user's reflective goal
(`goal_alignment`, 0..1), how much another party gains
(`other_benefit`, 0..1), and how much it pulls engagement
(`engagement_gain`, 0..1). The caller supplies the goal at
stake (from the intent engine intent distribution's values level, or from
the profile) and the numbers; the module never invents a goal
for the user. The thresholds are bookkeeping choices, stated in
the module docstring — not calibrated probabilities.

## The five conflict kinds

| Kind | Meaning | Example |
|---|---|---|
| `seller_vs_user` | Another party gains more than the user from the action. | Price comparison recommends a sponsored listing that costs Rp25.000 more than the cheapest option. |
| `provider_vs_user` | The provider's default gains more than the user. | Pushing the paid tier when the user's goal is to keep costs down. |
| `engagement_vs_goal` | The action keeps the user engaged instead of serving their reflective goal. | Suggesting another video when the user's goal is to stop scrolling and sleep. |
| `retention_vs_independence` | The work is done opaquely, so the user stays dependent instead of learning. | Writing the report without showing how, when the user wants to learn to write reports. |
| `attention_vs_benefit` | A proactive ping spends the user's attention for the system's benefit. | An unsolicited suggestion ping with low value to the user and high value to the system. |

Detection rules, in priority order (first match is the recorded
kind):

1. Another party gains more than the user (`other_benefit >
   goal_alignment`): the kind follows the beneficiary (seller /
   provider / other person).
2. Engagement wins over the goal (`engagement_gain >= 0.7`
   while `goal_alignment < 0.5`).
3. Retention (`hides_method` with `engagement_gain >= 0.5`).
4. Attention (a proactive ping with `goal_alignment < 0.5`
   while another party gains `>= 0.5`).

Severity is `high` when the user pays money (a non-empty
`user_cost`) or the other party gains far more than the user
(gap `>= 0.5`); otherwise `low`.

## How a conflict is surfaced, never decided silently

`ConflictGate.check` returns `proceed` (no conflict, or the
user already chose to proceed) or `surface` (show it, do not
run the action). A surfaced conflict is a record: what
conflicts, whose interest is on each side (in plain words),
what it costs the user, the exact text shown to the user, and
what the user chose. The user has three choices: `proceed`
(run it anyway), `alternative` (show something else instead),
or `decline` (do not run it).

Enforcement, not just surfacing:

- `require_resolved` raises `UnresolvedConflictError` for any
  caller that tries to run a conflicted action while its
  record is still `surfaced`. Surfacing without enforcement
  would be theatre.
- A choice is final: a resolved record cannot be rewritten.
- A declined (or alternative-resolved) conflict does not
  silently become runnable later. Proposing the same action
  again surfaces a fresh conflict, so the user is asked again
  instead of the system deciding the earlier answer no longer
  counts.
- While a conflict is unresolved, the gate returns the same
  record instead of writing a duplicate.

Turn-loop wiring: the loop consults the gate after the intent
advisor (intent engine) and the autonomy dial (autonomy dial), before dispatching
a registry tool call. On `surface` the tool is NOT run and the
model gets a payload telling it to show the conflict to the
user and ask them to choose. The gate runs after the dial on
purpose: even at `do_silently` a conflicted action is
surfaced, never run silently. The dial never overrides a
conflict. A tool with no `ConflictSpec` fails open (the gate
defers to the existing gates), and a broken gate fails open
too — a broken signal must never block the turn.

## Ledger wiring and persistence

No new ledger event type is added: the closed set of 31
revision-7 event types stays closed. When a `log(event_type,
content)` callable is given to the gate, surfacing writes
`approval_requested` and the user's choice writes
`approval_granted` (proceed) or `approval_rejected`
(alternative / decline, with the choice named) — because a
surfaced conflict IS an approval question through the existing
approvals taxonomy mechanism, not a second approval system.

Records live in `ConflictStore` (in-memory audit log) and,
when the store is built with a DSN, also in Postgres table
`conflict_records` (one row per surfaced conflict; resolving
only moves the status forward and stamps the choice and time).
Records are never deleted by the agent and never edited in
place; only the user's own data-deletion path (external audit log, kill switch and export) removes
them. The in-memory-only default has the same honest limit as
the interruption budget and the autonomy dial: a restart loses records that
were never persisted. Persisting needs `user_id` and
`agent_id`; a database failure while persisting raises
instead of silently dropping the audit.

## Review checklist

anti-manipulation design rule is a design rule that applies to all brain mechanisms. This
checklist is the check of the rule against every brain mechanism that
is implemented so far (intent engine, act-or-ask rule, three-speed simulator, bitemporal state, causal ledger, other-agent goal models, ignorance map, interruption budget, autonomy dial, external audit log, kill switch and export).
outcome watcher, and daily sleep consolidation and monthly ontology review are not implemented yet, so there is
nothing to check for them; each gains a row here when it
lands. `tests/test_anti_manipulation.py` asserts this
checklist names all ten items.

| Item | What it does | Manipulation risk it introduces | anti-manipulation design rule check |
|---|---|---|---|
| intent engine Intent Engine | Intent as a distribution over desire, goal, and long-term values; act-or-ask from it. | The system can steer the user toward the reading that keeps them engaged: when the desire level says "keep chatting" and the values level says "sleep", picking the engaging reading silently is manipulation. | When the top values-level hypothesis conflicts with the top desire-level reading, the conflict is surfaced, not resolved by picking the engaging one. The act-vs-ask rule already asks on a muddled distribution (`CONFLICT_MARGIN`); anti-manipulation design rule adds that the values level wins the question, never loses silently. |
| act-or-ask rule Act-or-ask rule | The act-or-ask decision is computed as confidence x reversibility x stakes, wired into the approvals taxonomy and the fail-closed gateway. | A score can be gamed by phrasing: a request worded to look low-stakes and reversible earns an act on a misread intent, and the user never sees the question that would have caught it. | Every decision carries its score and the three factors, so the "why?" door shows the working; an ask always names the factor that failed it; the advisor may only escalate to ask, never override a deny. |
| three-speed simulator Three-speed simulator | A rehearsal runs at three speeds: fast answers from the belief snapshot, medium re-derives the forecast from trace history, deep executes the action in a rehearsal world and observes the outcome. | A rehearsal can be used to justify an action the user would not endorse: the fastest speed answers from a stale belief, and a deep dry-run in a friendly world can make a risky action look safe. | Every step records the speed that produced it, the fallback chain is visible on the result, and a deep rehearsal never touches the real world — the world it consumes is a copy the caller passed in. |
| bitemporal state Bitemporal state | Every belief and ledger entry stores when it was true and when the system learned it. | Rewriting the past can hide what the system once told the user: a corrected belief can quietly rewrite the recommendation that followed from it. | Corrections never overwrite history; the old times stay readable. A correction that changes a live recommendation must be surfaced to the user as a conflict between what was said and what is now believed. |
| causal ledger Causal Ledger | Hypotheses "X -> Y under C" with strength, evidence, and expiry, feeding the learning loop. | A hypothesis learned from traces ("the user does X when nudged") can be used to trigger the nudge: the system learns what moves the user, then moves them. | Hypotheses about user behaviour are not used to trigger nudges without a conflict check. A nudge the ledger suggests is a proposed action like any other: if it serves engagement over the user's goal, it is an `engagement_vs_goal` conflict and is surfaced. Expired hypotheses are dropped, so a stale manipulation pattern cannot keep firing. |
| other-agent goal models Other-agent goal models | Models of what other people want and will do, built from contact beliefs. | Modelling what others want lets the system play people against each other: a recommendation can serve another person's goal (or a seller's) while looking like it serves the user. | A recommendation that serves another person's goal over the user's is a conflict with beneficiary `other_person` and is surfaced with both sides named. The planner never silently trades the user's interest for someone else's. |
| ignorance map Ignorance map | Tracks areas where the model has no evidence or low confidence. | The system can hide its blindness and guess confidently, or use "I don't know" strategically to dodge a hard question. | Empty areas are admitted (ask, or place a probe) and never filled with a confident-sounding guess that serves engagement. A guess in an empty area is an `engagement_vs_goal` conflict: confidence without evidence serves the system's fluency, not the user. |
| interruption budget Interruption budget | Every proactive initiative pays from a daily budget of 5; an empty budget means silence. | The budget allows a ping that still serves the system: a "suggestion" with a high assessed value can be retention dressed as helpfulness. The budget caps volume; it does not check whose benefit the ping serves. | The budget is necessary, not sufficient. A proactive initiative with high system benefit and low user benefit is an `attention_vs_benefit` conflict and is surfaced (or suppressed) even inside the budget. The budget decides volume; anti-manipulation design rule decides allegiance. |
| autonomy dial Autonomy dial | One dial per action area, moving with calibration; serious failure in an irreversible area drops it to zero. | `do_silently` at a high score is where manipulation hides: the action runs without the user noticing, so a conflicted action would never be seen at all. | The conflict gate runs AFTER the dial: even at `do_silently`, a conflicted action is surfaced, never run silently. The dial never overrides a conflict. A high dial score is trust in execution, not permission to decide whose interest wins. |
| external audit log, kill switch and export Audit, kill switch, export | Append-only audit log outside the agent's reach, kill switch, full export. | If the agent could edit its own conflict records, surfacing would be theatre: show the conflict, then quietly delete the record and run the action anyway. | Conflict records are append-only history in the same spirit: the agent never deletes a record and never edits one in place; resolving only moves the status forward with the choice and time stamped. Only the user's own data-deletion path removes them. The kill switch and export cover conflict records like any other user data. |
