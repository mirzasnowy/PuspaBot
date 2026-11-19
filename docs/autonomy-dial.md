# Autonomy dial (Kosmos K11)

One dial per action area, not one global dial. The dial moves with
prediction calibration: it rises slowly while predictions in that
area keep coming true and falls fast when they do not. One serious
failure in an irreversible area drops that area's dial to the
lowest stage at once.

Implementation: `puspa/autonomy/dial.py` (`AutonomyDial`,
`DialDecision`, `area_for_tool`, `dial_ask_payload`). The turn
loop consults it in `puspa/turns/loop.py` (`TurnLoop`, parameter
`autonomy_dial`). Tests: `tests/test_autonomy_dial.py`.

## Areas

Six areas, taken from the action kinds the system already
recognises (the G4 taxonomy in `puspa/policy/approvals.py` plus
the scheduling tools the turn loop dispatches):

| Area | Covers | Reversible? |
|---|---|---|
| `money` | payments, billing, purchases | No |
| `messaging` | sending, sharing, or posting to another person | No — a sent message cannot be unsent |
| `files` | deleting or changing files and system state | No — a delete cannot be undone by the agent |
| `scheduling` | calendar events and reminders | Yes |
| `system` | installing skills and changing settings | Yes |
| `accounts` | credential changes | No, and capped (see below) |

The tool-to-area mapping (`area_for_tool`) reads the G4 taxonomy
first: `pay` maps to `money`; `send_to_other`, `share`, and `post`
map to `messaging`; `delete` maps to `files`;
`change_credentials` maps to `accounts`; `install` maps to
`system`. A tool the taxonomy does not recognise falls back to
scheduling verbs (`schedule`, `remind`, `calendar`). A tool that
cannot be placed in any area is not gated by the dial — the loop
fails open and the tool runs as before. A caller may override the
mapping per tool (`TurnLoop(dial_area_for_tool={...})`).

## Stages

The dial score runs 0..1. The stage follows the score:

| Stage | Score | What happens |
|---|---|---|
| `propose` | below 0.60 | The action does not run. The user is asked first. |
| `do_and_show` | 0.60 up to 0.85 | The action runs, and its effect is shown to the user. |
| `do_silently` | 0.85 or above | The action runs without surfacing it to the user. |

## Rise and fall rules

Every area starts at 0.50 (`propose`): trust is earned, never
assumed.

- **Correct outcome** — closes 5% of the remaining gap to 1.0:
  `score += (1 - score) * 0.05`. From the 0.50 start that is 5
  consecutive correct outcomes to reach `do_and_show` (score
  0.613) and 24 to reach `do_silently` (score 0.854). The rise is
  slow on purpose: one lucky streak must not buy silent autonomy.
- **Ordinary wrong outcome** — the score drops by 0.15, floored at
  0.0. From 0.85 one wrong outcome falls back to `do_and_show`
  (0.70); two fall back to `propose` (0.55). The fall is fast on
  purpose.
- **Serious failure in an irreversible area** — the emergency drop:
  the score goes to 0.0 at once, the lowest stage, immediately,
  with no glide. Rebuilding from 0.0 takes 18 consecutive correct
  outcomes to reach `do_and_show` (score 0.603) and 37 to reach
  `do_silently` (score 0.850).
- **Serious failure in a reversible area** — the score drops by
  0.30, floored at 0.0: a hard fall, but not a reset, because the
  damage can be undone.
- **`accounts` cap** — the accounts area is capped at a score of
  0.59, so correct outcomes there are recorded but the stage never
  leaves `propose`. Credential changes always ask a human (the G4
  rule stands on top); the dial can never promote them.

## Calibration source

The dial does **not** score predictions itself and is not a second
calibration system. It aggregates outcomes that were already scored
elsewhere, per area, through the single entry point
`AutonomyDial.record_outcome(area, correct)`:

- The world model scores predictions (`WorldModel.stats`:
  `predictions` / `correct`, and the per-contact accuracy in
  `AgentGoalModel`), and the learning loop scores user feedback
  (`record_feedback` verdicts `"correct"` / `"wrong"`). Callers
  that hold a scored outcome report it here with the area it
  belongs to.
- The turn loop is the first such caller wired in: it reports each
  dispatched tool call's execution outcome (ran as expected counts
  correct, an error counts wrong) for the call's area.

`record_serious_failure(area)` is the separate entry point for
harm: it is called by whoever knows an action failed seriously
(the outcome watcher, a human report), never inferred from a plain
tool error by the dial. A plain tool error is an ordinary wrong
outcome, not a serious failure.

## Caller: the turn loop

`TurnLoop` takes an optional `autonomy_dial`. Before dispatching a
registry tool call it consults the dial for the tool's area, after
the intent advisor (K1) — an intent `ask` wins and the dial is not
consulted for that call:

- `propose` — the tool is **not** run. The model gets a dial ask
  payload (`autonomy_decision: propose`, `tool_was_run: false`,
  the reason, and the question "Shall I go ahead and run ...?")
  as the tool result, and a `dial_ask` event is recorded on the
  loop sink. The decision is kept on `TurnResult.dial_decisions`.
- `do_and_show` — the tool runs as before, a `dial_show` event is
  recorded, and the executed outcome is fed back to the dial.
- `do_silently` — the tool runs as before, a `dial_silent` event
  is recorded (the presentation layer uses the `show` flag on the
  decision to decide whether to surface the effect), and the
  executed outcome is fed back to the dial.

Intercepted tools (internal state operations) never reach the dial.
A dial failure never breaks the turn: the loop fails open and runs
the call. Without a dial the loop behaves exactly as before.

## Audit and honesty limits

- **No ledger event type.** The closed set of 31 ledger event
  types (revisi 7) has no "dial moved" type, and adding a 32nd is
  forbidden. Dial moves are audited in the dial's own per-area
  history (`AutonomyDial.history` / `summary`, capped at 100
  entries per area) — the same pattern as the K10 interruption
  budget's spend log.
- **In-memory.** A restart resets every area to the 0.50 start, so
  a restart can only make the agent more cautious, never bolder.
  Persisting the per-area scores (a Postgres table + migration,
  the K3/K4/K5 pattern) is the follow-up when the dial needs to
  survive restarts.
- **Execution success is a proxy, not prediction accuracy.** The
  turn loop feeds the dial with tool-execution outcomes (the tool
  ran, or it errored). That says the action did what was asked,
  not that the underlying prediction was right. The stronger
  feeder is the K8 outcome watcher, which scores outcomes after
  the fact; it feeds the same `record_outcome` entry point when it
  lands. Until then, treat the dial's climb as "actions in this
  area keep executing cleanly", not as "predictions in this area
  are proven right".
- **The loop does not suppress the model's narration.** At
  `do_silently` the loop records the `show: false` flag on the
  decision and in the `dial_silent` sink event; the surface that
  presents the turn to the user honours it. The model still sees
  the tool result (it needs it to continue the turn).
