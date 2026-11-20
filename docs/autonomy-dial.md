# Autonomy dial (Kosmos K11)

One dial per action area, not one global dial. The dial moves with
prediction calibration: it rises slowly while predictions in that
area keep coming true and falls fast when they do not. One serious
failure in an irreversible area drops that area's dial to the
lowest stage at once.

Implementation: `puspa/autonomy/dial.py` (`AutonomyDial`,
`DialDecision`, `area_for_tool`, `dial_ask_payload`). The turn
loop consults it in `puspa/turns/loop.py` (`TurnLoop`, parameter
`autonomy_dial`). The user-facing controls are P-02
(`puspa/autonomy/controls.py`, `puspa/pwa/controls_api.py`,
`puspa/pwa/stop_all.py`, `puspa/pwa/weekly_report.py`); see
"User controls (P-02)" below. Tests: `tests/test_autonomy_dial.py`,
`tests/test_autonomy_controls.py`, `tests/test_pwa_controls_api.py`.

## Areas

Seven areas, taken from the action kinds the system already
recognises (the G4 taxonomy in `puspa/policy/approvals.py` plus
the scheduling tools the turn loop dispatches), plus shopping
(P-02, plan section 12.7):

| Area | Covers | Reversible? |
|---|---|---|
| `money` | payments, billing, purchases | No, and locked (see below) |
| `messaging` | sending, sharing, or posting to another person | No — a sent message cannot be unsent |
| `files` | deleting or changing files and system state | No — a delete cannot be undone by the agent |
| `scheduling` | calendar events and reminders | Yes |
| `system` | installing skills and changing settings | Yes |
| `accounts` | credential changes | No, and capped (see below) |
| `shopping` | finding, comparing, and drafting a purchase | No — a placed order moves money (but the payment itself is a `money` action, so it still asks first) |

The tool-to-area mapping (`area_for_tool`) reads the G4 taxonomy
first: `pay` maps to `money`; `send_to_other`, `share`, and `post`
map to `messaging`; `delete` maps to `files`;
`change_credentials` maps to `accounts`; `install` maps to
`system`. No G4 kind maps to `shopping`: purchases stay
`money`-gated through the `pay` kind, so the turn loop never
consults the shopping area for a tool call; the P-02 control
layer feeds it from accepted shopping drafts instead. A tool
the taxonomy does not recognise falls back to
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
- **`money` lock (P-02, plan 12.7)** — the money area is capped
  at a score of 0.59 the same way, so it never leaves `propose`
  either: money always asks first, never autonomous, no matter
  how much evidence accumulates. The cap lives in the score
  itself, so no caller — not the turn loop, not a ceiling
  change — can promote it. `set_ceiling("money", ...)` above
  `propose` is refused as well.

## Ceilings and user controls (P-02, plan 12.7)

A ceiling is a user-set cap on one area (`AutonomyDial.set_ceiling`):
the effective stage an action may reach is the lower of the score
stage and the ceiling stage. A ceiling never raises the dial above
what the evidence score allows; it only holds it down. The PWA
(`puspa/autonomy/controls.py`, `AutonomyControls`) shows five areas
— Money, Outgoing messages, Delete, Schedule, Shopping — with three
ceiling positions each (Ask first / Balanced / Autonomous, mapping
to `propose` / `do_and_show` / `do_silently`), and sets every shown
area's ceiling to Ask first at startup. `system` and `accounts` are
not shown: accounts is already capped by the dial, and system has
no user-facing dial in this version.

A ceiling rises one step at a time and only after evidence: the
area's streak of consecutive accepted drafts (an approval the user
granted in that area, counted from ledger approval events plus the
drafts store) must reach 10, and the suggestion is visible in the
dial state with the threshold in plain words ("10 accepted drafts
in a row"). Lowering a ceiling is always allowed. Money refuses
any raise. A raise without an active suggestion is refused
(`CeilingRefusedError`, reason `needs_evidence`); a raise of a
locked area is refused with reason `locked`.

The evidence sources and their limits are documented in
`puspa/autonomy/controls.py`: a rejection whose kind cannot be
resolved breaks every area's streak (an unattributed correction
must not leave a "10 in a row" claim standing), `false_report`
breaks every streak, and a draft the user revised instead of
accepting resets the messaging streak. Draft approvals in the
PWA draft flow write no ledger event, so the drafts source exists
because without it the messaging streak would never move for the
flow users actually use.

## Stop-all and the weekly report (P-02)

- **Stop-all** (`POST /api/stop-all`, `puspa/pwa/stop_all.py`):
  the red button in the PWA header, on every screen. One press
  stops every agent the user owns through the E-11 stop package
  (`puspa.policy.stop_agent`: revoke-all, token refresh proved
  dead, live subagents interrupted) plus every live subagent task
  in the PWA pool for those agents, and records one `task_stopped`
  ledger event per agent under a single shared stop-all task id.
  It does not touch the global kill switch (operator-only, all
  users) and deletes nothing. The report carries the measured
  elapsed time (target under 5 seconds, plan 6.6) and the audit
  trail link.
- **Weekly report** (`GET /api/weekly-report`, `puspa/pwa/weekly_report.py`):
  tasks completed, tasks corrected (rejected approvals plus false
  reports), approvals, cost incurred, and links to the audit log
  (the trail plus one receipt per counted event). Time and money
  saved are null with a written reason: no savings baseline is
  recorded yet, so no savings figure is claimed.

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
  Ceilings are in-memory too: a restart returns every shown area
  to Ask first, which is the safe direction. Persisting the
  per-area scores (a Postgres table + migration, the K3/K4/K5
  pattern) is the follow-up when the dial needs to survive
  restarts.
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
