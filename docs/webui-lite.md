# WebUI Lite (G32)

The lite mode is the no-install way to reach Puspa: the same PWA
served as server-rendered Jinja templates instead of the React
build. It exists for one job — the trial entry (see
`docs/modes-decision.md`, decision locked by Nero 18 Nov 2026):
one chat page plus an upgrade button, zero install friction.
It is not a second product and not a second frontend.

## How it serves

`puspa/pwa/app.py` (`spa_or_template`) serves the built SPA shell
from `apps/pwa/dist/index.html` when the build exists, and falls
back to the Jinja template of the same screen when it does not.
The lite mode is that fallback, verified as a real mode: all 9
templates render and work with `dist` absent, and the trial page
(`GET /trial`) is Jinja-only on purpose — it renders the template
even when `dist` exists, because the trial entry must never depend
on a frontend build.

Templates (`apps/pwa/templates/`):

| Template | Screen |
| --- | --- |
| `base.html` | Shared shell + navigation (all 7 screens + Trial) |
| `chat.html` | Chat |
| `tasks.html` | Tasks + approval queue |
| `modules.html` | Modules |
| `connectors.html` | Connectors |
| `memory.html` | Memory / profile facts |
| `settings.html` | Settings |
| `office.html` | Office (live team status from ledger projections) |
| `panel_evidence.html` | Evidence panel (`/bukti/{evidence_id}`) |
| `trial.html` | Trial entry (chat + upgrade button) |

## What G32 fixed in the templates

The 9 templates existed since G1 as a fallback but had never
been exercised, and several were broken against the real app:

- `base.html`: navigation linked to routes that do not exist
  (`/tugas`, `/modul`, `/konektor`, `/memori`, `/pengaturan`,
  `/kantor`); the active-screen checks used singular names
  (`task`, `module`, `connector`) that never match the screen
  name the app sets. Both now use the real routes and names
  (`/tasks`, `/modules`, `/connectors`, `/memory`,
  `/settings`, `/office`), plus a Trial link.
- `chat.html`: rendered `{{ p.dari }}` (no such message field —
  always empty) instead of `{{ p.sender }}`; the live strip
  polled dead `/kantor` instead of `/office`; the form posted
  field `teks` while `/api/chat` expects `text`.
- `connectors.html`: looped over `agent` (context key is
  `agents`); the grant/revoke forms posted `agen_id` / `aksi`
  while `/api/konektor/{konektor}` expects `agent_id` / `action`.
- `panel_evidence.html`: the route renders `panel_evidence.html`
  but the file was named `evidence_panel.html`, so the panel
  always failed with TemplateNotFound. The file was renamed to
  match the route. Its fields used keys the evidence content
  never has (`klaim`, `sumber_url`, `time_take`,
  `text_excerpt`, `vonis`); it now uses the real keys from the
  evidence projection (`claim`, `value`, `verdict`) and shows
  `source_url`, `retrieved_at`, `excerpt` only when present.
- `memory.html`: rendered each profile fact as a raw dict
  (`{{ f }}`); it now shows `key: value (status)`.
- `office.html`: read `office.agent`, `task_terakhir`,
  `sub_agent`, `handoff`, `event_type`; the office projection
  returns `agents` with `last_task` / `subagents`, and the app
  passes `handoffs` of events with a `type` field.
- `tasks.html`: the new-task form posted `tujuan` while
  `/api/tugas` expects `purpose`; the approval forms posted
  `setuju` while `/api/persetujuan/{id}` expects `approve`.

`login.html`, `modules.html`, and `settings.html` needed no
change (their variables already match the app).

## Trial flow

A trial is an entitlement, not a flag. The trial entitlement is
the G2 billing entitlement (`puspa/billing/entitlements.py`):
HMAC-signed, Pro tier, `trial_days` (14) from `BillingConfig`,
carried in the httpOnly `puspa_trial` cookie and verified
against the server secret on every request
(`puspa/pwa/lite.py`).

Routes (wired in the G32 block of `puspa/pwa/app.py`):

- `GET /trial` — logged-in user only (otherwise redirect to
  `/masuk`). Active or grace trial: chat page (message history
  + plain-HTML chat form + trial banner with days left +
  upgrade button). Expired trial: upgrade invitation only.
  No trial: "start free trial" form + upgrade button.
- `POST /trial/start` — mints a trial entitlement (Pro,
  `trial_days`) and sets the cookie, then redirects to `/trial`.
  If an active/grace trial is already presented, it reuses it
  instead of minting a second one. If an expired trial token
  is still presented, it refuses (`trial_already_used`) — one
  trial per user. With no entitlement secret configured it
  redirects with `trial_unavailable`.
- `POST /trial/chat` — plain-HTML chat post (no htmx, no JS):
  appends the message with the same shape as `/api/chat` and
  redirects back to `/trial`. Refuses when the trial is not
  active/grace.

The upgrade button points at the billing portal base URL
(`BillingConfig.portal_base_url`).

Configuration: `PUSPA_ENTITLEMENT_SECRET` (the HMAC secret that
signs billing entitlements; it never leaves the server). With
no secret configured, trials cannot start and every trial check
reports "none" — the lite mode never mints or trusts an
entitlement without a secret.

## Honest limits

- **No server-side trial ledger.** The G2 billing package keeps
  no record of issued entitlements (the license endpoint is
  stateless), so one trial per user is enforced only as far as
  the presented cookie proves it: a user who still presents a
  validly signed trial token (active, grace, or expired)
  cannot mint a second trial, but a user who deletes the
  cookie can start over. A server-side trial-grant ledger is
  future work, not this ticket.
- **The approval queue is never populated by the app.**
  `tasks.html` renders the queue correctly (field names
  verified against `ApprovalRequest`), but nothing in
  `app.py` writes to `app.state.approval`, so the page always
  shows the empty state until the task/approval flow wires
  approvals into it. That wiring belongs to the task flow,
  not to G32.
- **htmx comes from a CDN.** The 7 screens' forms use htmx
  loaded from `unpkg.com` (G1's choice); they need network on
  first load. Only the trial page works with plain HTML and no
  JavaScript at all.
- **No live model call is proven in the suite.** Trial chat
  stores the message and shows it back; the agent's reply path
  is the existing `/api/chat` demo storage, not a live LLM
  completion.
- **Trial is per browser cookie, not per account server-side**
  (see the ledger limit above). A paid (non-trial) entitlement
  that verifies is also accepted on the trial page — a paying
  user landing there is entitled to chat, and the page labels
  the tier instead of "Trial".

## Legacy scan (modern_web_guidance)

`legacy_scan.py` on the owned web files (`templates/`,
`index.html`, `static/`, `tests/`) passes: 0 errors,
0 warnings. (The scan covers `.html` / `.css` / `.js` /
`.mjs` only; the React `.tsx` sources are covered by `tsc` +
`vitest` instead.) The full-tree scan reports one ERROR in
`dist/assets/*.js` (`attachEvent`) — that is React DOM's own
legacy-IE fallback inside the Vite-built bundle, not Puspa
code (no `attachEvent` anywhere in `src/`); `dist/` is
gitignored build output regenerated by `npm run build`, so
the fix, if ever needed, is a React version bump, not a
source edit. The remaining findings sit in `node_modules/`
(third-party tooling, not delivered).
