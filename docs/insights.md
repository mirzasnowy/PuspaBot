# Insights: hints + cost/token insights (onboarding and cost insights)

First-run onboarding in the PWA, plus a cost/token insights page fed
from the usage ledger. Two features, one idea: the user sees a nudge
exactly when they first reach a behaviour, and they can always look
up what PuspaBot actually spent.

## Hints model

A hint is a short, one-time nudge tied to a real behaviour branch --
never a long upfront tour.

| Flag | Branch (client-reported context) | Points at |
| --- | --- | --- |
| `first_modules_visit` | first visit to the Modules screen (`screen=modules`, `first_visit=true`) | Modules |
| `first_approval` | first approval request (`approval_requested=true`) | Tasks |
| `first_memory_use` | first memory use (`memory_used=true`) | Memory |
| `first_costly_task` | a task whose cost reached the Rp2.000 per-task ceiling (`task_cost_idr>=2000`) | Insights |
| `first_billing_row` | first recorded usage-ledger row (`billing_rows>=1`) | Insights |

Rules:

- Each hint is shown **once per install**. Seen flags live in
  Postgres (`onboarding_hint_seen`, primary key `(user_id, flag)`),
  written by `puspa/onboarding/hints.py` (`due_hints`,
  `mark_seen`). Marking seen is idempotent; an unknown flag is a
  400, never a silent no-op.
- Hints never contain secrets and never trigger outbound actions:
  they are plain text plus a target screen name.
- The client reports only branch facts (which screen it just
  opened, whether an approval arrived). The server decides which
  unseen hints that branch makes due. No other client data is
  stored by the hints endpoints.

## Insights engine

`puspa/billing/insights.py` reads `billing_usage_rows` through the
existing `PostgresUsageStore` and summarises the last N days
(default 7, allowed 1..90):

- Tokens in / out / cached and total.
- Cost: exact integer Rupiah total (`"Rp1.500"`), average per turn,
  and average cost per 1k tokens with **4 decimals**
  (`"Rp500.0000 / 1k tokens"`).
- Per-model and per-driver breakdowns (turns, tokens, cost, share).
- Tool usage counts, module usage, activity per day (every day in
  the window, including zero days), top 5 costly tasks, tool errors.
- The per-task cost ceiling (Rp2.000) is reported alongside, from
  the same `TASK_COST_CEILING_IDR` the scheduler policy uses.

Most functions are pure functions over `UsageRow` lists
(`insights_summary`), so they are testable without a database;
`insights_for_user` is the thin Postgres layer on top.

## API

All routes require login (`401 {"error": "belum masuk"}` when not
logged in). Validation failures are 400 with `{"error": ...}`.
Routes live in `puspa/pwa/insights_api.py` and
`puspa/pwa/onboarding_api.py` as plain handler functions plus
`register_routes(app, need_user)`; they are wired into
`puspa/pwa/app.py` (`create_app` calls both `register_routes`
after the own-model selection in PWA settings model-settings block).

### Insights

- `GET /api/insights?days=N` -> the report object described above.
  `days` defaults to 7; outside 1..90 is a 400.

### Onboarding (4-step first-run flow)

Wraps `puspa/onboarding/onboarding.py`; see that module for the
flow itself.

- `GET /api/onboarding` -> status, or
  `{"started": false, "current_step": 0}` when never started.
- `POST /api/onboarding/start` -> starts onboarding (idempotent),
  returns the status with `"started": true`.
- `POST /api/onboarding/step1` `{"name", "style", "example_message"?}`
  -> step 1. Style must be `santai`, `rapi`, or `campur`.
- `POST /api/onboarding/step2` -> step 2 (notification test).
- `POST /api/onboarding/step3` `{"connector"?}` -> step 3
  (`"nanti"` by default; `gmail`, `drive`, `calendar`, `all`).
- `POST /api/onboarding/first-task` `{"event_type", "target"}`
  -> the guided first task (`compare_prices` or `check_bill`).

### Hints

- `GET /api/onboarding/hints?screen=...&first_visit=...&approval_requested=...&memory_used=...&task_cost_idr=...&billing_rows=...`
  -> `{"hints": [...]}` (due hints only, in registry order).
- `POST /api/onboarding/hints/{flag}/seen`
  -> `{"user_id", "flag", "seen": true}`. Unknown flag: 400.

## PWA screens

- `apps/pwa/src/screens/InsightsScreen.tsx` (+ test): window
  picker (7 / 30 / 90 days), totals, per-model and per-day tables,
  tool / module / top-task lists, honest empty state, error +
  retry. State client: `apps/pwa/src/state/insights.ts`.
- `apps/pwa/src/screens/OnboardingScreen.tsx` (+ test): the
  guided 4-step conversation (name + style, notification test,
  connector, first task) with a done state. State client:
  `apps/pwa/src/state/onboarding.ts` (also carries the hint
  client: `fetchDueHints`, `markHintSeen`).
- `apps/pwa/src/api/client.ts`: the shared `getJson` / `postJson`
  helpers the two state clients use, plus the generated wire
  types (`InsightsReport`, `OnboardingStatus`, `FirstTaskResult`,
  `Hint`) from `docs/openapi-pwa.yaml`. The temporary
  `apps/pwa/src/state/http.ts` duplicate was deleted at wiring
  time (PWA serve modes (local operator vs hosted) discipline: one JSON helper).

The screens are registered in `App.tsx` / `bootstrap.ts` and
the routes are wired into `puspa/pwa/app.py`
(`docs/openapi-pwa.yaml` carries the contract, and the API
tests run against the real `create_app`). A first-run user who
never started onboarding is sent to `/onboarding` from
`main.tsx`, and a `HintBanner` renders due hints above each
screen (plus billing-context hints inside the Insights screen).

## Honest limits

- **No USD estimate.** Costs are integer Rupiah from the ledger.
  The repo has no exchange-rate source, so no dollar figure is
  produced anywhere in this feature.
- **No "~$0.00".** Exact totals use whole Rupiah labels; the only
  fractional figure is the per-1k-token rate at 4 decimals. When
  no tokens were recorded the rate is `null`, not zero.
- **Ledger-only window.** Turns never recorded (e.g. turns run
  before the ledger existed) do not appear; the report counts
  recorded turns only.
- **Step 2 is not a real push.** Web Push (VAPID) is not built
  yet, so the notification test is recorded as NOT sent
  (`notification_sent: false`). The screen says so in the open.
- **Step 4 is a measured stub.** The price-compare / bill wedges
  are not built yet, so the first task reports `verified: false`
  with a reason; no price or bill result is ever invented.
- **No hint banner yet.** The hint engine, API, and state client
  exist and are tested, but no screen renders a hint banner until
  the wiring step adds one (it needs `App.tsx` / screen changes).
- **First-visit detection is client-reported.** The PWA reports
  `first_visit=true` for a screen it opens the first time on that
  install; the server trusts that flag and only enforces
  once-per-user via the seen-flags table.
