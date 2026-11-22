# Missions with checklist (V2-42, issue #219)

A mission is one multi-step goal the user gives the agent, worked
through in the background with a checklist the user can follow in
the PWA (Tasks screen, "Misi" section). Mechanism studied from
nouverse/nouride missions, cron step-asks, and cron webhooks
(FSL-1.1-ALv2, verified 22 Nov 2026) and rewritten as Puspa
Python, never verbatim; the TypeScript code itself was not taken
(the FSL forbids competing use, and Puspa's hosted side is
competing use).

## Shape

- **Store** (`puspa/missions/missions.py`, table `missions`,
  migration `0045`): id, owner, title, goal, status
  (`active` / `waiting` / `done` / `failed` / `stopped`), the
  checklist as JSONB (one entry per step: title, status
  `pending` / `active` / `done` / `failed` / `skipped`, result,
  and an optional step-ask `question` with `options` and the
  user's `answer`), and a random webhook token.
- **Runner** (`puspa/missions/runner.py`): `advance_mission`
  runs the next pending step through the same turn loop a chat
  turn uses — one step per call. Each step outcome is recorded
  in the hash-chained ledger, reusing the closed event types
  (`task_created` / `task_completed` / `task_failed` /
  `task_stopped` for the mission and its steps, no new type).
- **Step-ask**: a step carrying a `question` pauses the mission
  in `waiting` and records `approval_requested` with the
  question and its options. The user answers with
  `answer_step_ask` (or the PWA): `go: true` sends the mission
  back to work with the answer in the step's prompt
  (`approval_granted`); `go: false` skips the step and stops
  the mission (`approval_rejected`). A step that needs a
  decision never guesses.
- **Scheduler** (`puspa/scheduler/mission_triggers.py`, trigger
  type `mission` in `puspa/scheduler/scheduler.py`): `schedule_mission`
  (a schedule dict or a short string, the same shapes a routine
  takes). Each fire advances one checklist step through the tick's
  turn loop. A mission that reaches a terminal state deactivates
  its trigger (one mission per trigger). With no turn loop wired
  in, the step is recorded as `not_run` and the trigger stays due,
  so the next tick retries it instead of silently dropping the step.
- **Webhook** (`puspa/missions/webhooks.py`): `POST
  /api/missions/webhook/<token>` (no login; the token is the
  credential) records the payload in the ledger
  (`evidence_recorded`) and queues the mission's next step as
  an immediate one-shot trigger, so the serve loop runs it in
  the background. Rotating the token invalidates the old one
  at once.

## PWA

- API (`puspa/pwa/missions_api.py`, contracts
  `docs/openapi-missions.yaml` and
  `docs/openapi-missions-webhook.yaml`): create, list, get, run (queue
  the next step now), answer, cancel, schedule, rotate the
  webhook token, and the webhook wake. The PWA never runs a
  turn inline: run and wake only queue a trigger.
- Client (`apps/pwa/src/api/missions.ts`, hand-written types
  because `npm run generate` is broken at this base) plus the
  `MissionsSection` component on the Tasks screen: create form
  (goal + one step per line), checklist with statuses and step
  results, the open question with option buttons and a free
  answer, run and cancel buttons.

## Honest limits

- One step per tick (or per run): a mission with many steps
  needs that many fires, it does not fan out into parallel
  subagents.
- A step-ask question is declared when the mission is created
  (on the step), not invented by the model mid-step: the model
  cannot yet raise a new question from inside a step.
- The webhook wake queues the step; it does not run it inline,
  so a mission woken while the serve loop is down waits until
  the loop is back.
