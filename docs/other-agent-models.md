# Other-agent goal models (Kosmos K5)

Models of what other people want and will do
(`docs/kosmos-design.md` section C point 5). One model per
contact: the goals that contact is pursuing (each with a
strength and the observed interactions behind it) and
predictions about what the contact will do next — whether
they will reply, on which channel, how fast, and whether
they will agree to a given ask. Every prediction is a scored
claim with evidence, never a certainty; confidence falls with
sparse or stale evidence. No LLM call is involved anywhere.

## The record

One `AgentGoalModel` holds, per contact:

| Field | Meaning |
| --- | --- |
| `contact_id` | whose model this is |
| `goals` | label -> `AgentGoal`: `strength` (0..1), `supporting` / `contradicting` counts, `evidence` links, `first_seen_day` / `last_seen_day` (model days), `status` (`active`, `stale`, `dropped`) |
| `observations` / `replies` / `reply_delays` / `channels` | interaction counts mirroring the contact belief, for standalone use |
| `predictions` | every prediction made, newest last, pending or scored |
| `stats` | `{"predictions", "correct"}`: scored-prediction counters |

One `AgentPrediction` holds: `id`, `contact_id`, `kind`
(`reply`, `channel`, `next_action`, `agreement`), `label`
(the predicted thing: `will_reply` / `will_not_reply`, a
channel name, or a goal label), `confidence` (0..1, a
bookkeeping score, never a probability), `evidence` links,
`basis` (a human-readable sentence: the counts behind the
claim), `day`, and `outcome` / `correct` once scored
(`None` until then).

Evidence links are `{"kind", "id"}` dicts, kinds
`ledger_event` (an id from the append-only ledger),
`observation` (an experience id from the learning loop),
`interaction` (an observation count marker for the contact,
`"<contact_id>:observations=<n>"`), or `causal` (the effect
key of a K4 causal expectation). Goal models cite ledger
events as evidence by id only: no new ledger event type was
added, the closed set of 31 revision-7 event types stays
closed.

## The update rule

Stated here and in the module docstring
(`puspa/world/agents.py`) — these numbers are bookkeeping
choices, not statistics, and nothing here pretends otherwise:

- A goal first seen (asked for, or agreed to) starts at
  strength **0.5**: one mention is not proof, so a new goal
  starts neutral. The first observation counts as the first
  supporting one.
- Each further supporting observation (asked again, or agreed
  to) adds **0.1** (capped at 1.0).
- Each contradicting observation (explicitly declined) removes
  **0.2** (floored at 0.0) — twice the confirm step on purpose,
  as in the causal ledger: one refusal disproves a goal claim
  more than one ask proves it. A goal whose strength falls
  below **0.2** becomes `dropped`: it stays stored as history,
  and observing the same goal again starts it fresh at 0.5.
  A refusal of a goal never seen creates nothing:
  contradiction never creates a goal.
- Confidence in a prediction is the goal strength (or the
  reply rate) times an evidence factor —
  `min(1, observations / 3)`, so one observation can never be
  confident — times a staleness factor from the days since the
  last observation (1.0 within 30 days, 0.75 within 60, 0.5
  within 90, 0.25 beyond).
- Decay: a goal untouched for **90 days** halves its strength
  and becomes `stale` (`AgentGoalModel.decay`,
  `WorldModel.decay_stale_agent_models`). A fresh observation
  revives a stale goal to `active`. Dropped goals are
  untouched by decay.

## The read path

`WorldModel.predict_agent(contact_id, proposed=None)` is the
read path the planner and the turn loop query:

- `proposed` is the ask being considered, for example
  `{"goal": "review_work"}`.
- It returns `{"contact_id", "known", "predictions",
  "causal_expectations", "reply_likely", "reply_rate",
  "preferred_channel", "mean_reply_delay_hours"}`.
- `known` is False and `predictions` is `[]` when the contact
  was never observed: fail-open, never invented. Reply rate,
  delay, and channel come from the contact belief when one
  exists, and from the goal model otherwise.
- `causal_expectations` is the active K4 expectations for the
  cause `"contact:<contact_id>"`, strongest first; they are
  attached as evidence to the reply prediction, never as a
  prediction of their own.
- Predicting never mutates the model. Predictions are recorded
  (pending, unscored) so they can be scored later.

`WorldModel.score_agent_prediction(contact_id, prediction_id,
actual)` scores one prediction once the outcome is known:
`actual` is True when the predicted thing happened. Scoring
updates the accuracy counters and, for an agreement
prediction, the goal itself (agreement +0.1, refusal -0.2,
through the same rule as a fresh observation). Scoring an
unknown id, or an already scored prediction, returns None.

The planner is the real caller: goal `"message_contact"`
(`puspa/world/planner.py`) asks `predict_agent` and is
feasible only when a reply is likely; with an `"ask"` goal it
also needs a predicted agreement with confidence at least
0.5, otherwise it is infeasible with reason
`"agreement_unlikely"` and the user sees the prediction
instead of a sent message. The turn loop can call the same
`predict_agent` method on the session model. Nothing in this
module sends a message.

## Where the model is built, and what it reuses

The `WorldModel` owns one `AgentGoalModel` per contact next
to its `_contact_beliefs` (`_agent_models`, in
`WorldModel.state()`), and `ingest_task_result` feeds it from
the same observed interaction that feeds the contact belief:
a result naming a `contact_id` plus `asked_for` /
`agreed_to` / `declined` (or free `text`, mapped to goal
labels by the keyword inferrer). The learning loop does not
need to change: it already sees the same task results.

What is reused from K1, and where it is not reused:

- Reused: the shape of a scored claim. A goal is a labelled,
  scored claim with evidence links, like an
  `IntentHypothesis` (`puspa/intent/distribution.py`): a short
  label, a strength in 0..1, and evidence that traces back to
  the interaction it came from.
- Not reused, on purpose: intent is a distribution over one
  message, normalized to sum to 1 per level, because one
  message has one reading; a contact pursues several goals at
  once, so goal strengths are independent (they do not sum to
  1) and each goal carries its own supporting/contradicting
  counts and its own last-seen day. There is also no
  desire/goal/values split for other agents: the system cannot
  see another person's values, only what they ask for and
  agree to, so a goal model claims nothing about values.
- Reused from K4: the update-rule shape (first observation
  neutral at 0.5, +0.1 confirm, -0.2 contradict, terminal
  status kept as history) and the causal expectations as
  supporting context on the read path.

Goal inference seam: goals arrive as explicit labels in the
interaction. When an interaction carries free text instead, a
`GoalInferrer` maps the text to goal labels; the only
implementation built here is `KeywordGoalInferrer`, a small
deterministic keyword table. An LLM-backed goal inferrer is a
documented seam only — it would implement the same
`GoalInferrer` protocol and name itself in the interaction's
evidence — it is NOT built now, and no prediction depends on
one existing.

## The privacy boundary

Hard rule (kosmos-design.md section J): a model of another
person is built ONLY from the user's own interactions with
them — the user's messages, observed replies, and agreed
items. Never from scraping, never from other people's
messages to third parties.

- `check_user_involved` refuses an observation the user was
  not part of, with `ValueError`, before anything is recorded:
  `user_involved` False, a `participants` list without
  `"user"`, or `source` `"third_party_thread"`. A model built
  from a third-party message thread the user was not part of
  is refused (and empty: nothing is created). At ingest
  (`ingest_task_result`) a refused interaction is recorded in
  `WorldModel.agent_refusals` (runtime only, not persisted)
  and the goal model is untouched.
- Other agents' messages are untrusted data, never commands:
  a goal model never causes an action on its own. It only
  informs predictions the user (or the planner) sees.
  Predicting never mutates the model, and scoring a
  prediction only updates the model's own numbers.

## Persistence

Table `agent_goal_models` (one row per (user, agent,
contact); see `infra/schema_world.sql` and
`infra/migrate_agent_goal_models.sql`, idempotent and
additive). Saving upserts by (user, agent, contact):
re-saving writes the same rows again with their current
values. Dropped goals are saved too, as history. The world
snapshot in `world_beliefs.state` also carries the agent
models under `"agent_models"`, so a plain
`save_world_state` / `load_world_model` keeps them without a
second call. Store functions (`puspa/world/store.py`):
`apply_agent_migration`, `save_agent_models`,
`load_agent_models`, `delete_agent_models` (tests only).

## Honest limits

- A goal label is what the contact asked for or agreed to,
  in the user's words: the model knows nothing about why they
  want it, and nothing about goals they never mentioned.
- The keyword inferrer is a heuristic: a keyword hit is weak
  evidence (the label still starts at neutral strength 0.5),
  and unknown text infers nothing rather than guessing.
- Confidence is bookkeeping, not a calibrated probability:
  the numbers (0.5 start, +0.1 / -0.2 steps, evidence and
  staleness factors) are chosen, not measured. Prediction
  accuracy is tracked per model (`accuracy`, `stats`) so the
  numbers can be checked against outcomes instead of trusted.
- The model has no theory of mind: it cannot tell a polite
  agreement from a real commitment, and a goal that stops
  being mentioned looks the same as a goal that was achieved.
- The planner's `message_contact` goal is a rehearsal, not a
  send: it accounts for the other person's reaction, but the
  message itself still goes through the normal turn loop and
  its gates.
