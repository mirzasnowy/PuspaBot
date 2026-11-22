# Local Admin Dashboard

The operator surface for a self-hosted instance: instance
configuration, sessions, usage statistics, and plugin/skill
management. The PWA is the user home; this is the screen for the
person who runs the machine.

- Page: `GET /admin` (Jinja template `apps/pwa/templates/admin.html`)
- API: `GET` / `POST /api/admin/*` (code: `puspa/pwa/admin_api.py`,
  instance name store: `puspa/pwa/admin_settings.py`)
- Contract: `docs/openapi-pwa.yaml` (paths `/admin`,
  `/api/admin/*`; schemas `Admin*`)
- Tests: `tests/test_pwa_admin_dashboard.py` (18 tests)

## Local only

Every admin route requires a logged-in operator session, and in
`hosted` serve mode the whole surface answers 403: the operator
surface of a hosted deployment lives in the private cloud
backend, not in this repo (see `docs/serve-modes.md`). Nothing
here touches hosted billing, multi-tenant features, teams, or
payments. The page says so on the screen, and the API error
says the same.

## What it can do

### Instance configuration — `GET` / `POST /api/admin/config`

- **Instance name** (editable): the display name shown on the
  dashboard. Stored in Postgres (`instance_settings` table,
  migration `0039_instance_settings`); default `"Puspa"`. A name
  must be a non-empty string of at most 80 characters.
- **Serve mode** (read-only): shown with a note that changing it
  needs `PUSPA_SERVE_MODE` (or the mode file) plus a server
  restart. There is deliberately no button for it: a mode change
  is a deployment decision, not a dashboard toggle.
- **Model selection** (editable): the same choice the Settings
  screen offers (hosted / BYOK / local), validated and stored by
  `puspa/pwa/model_settings.py`. Keys are never accepted here:
  a payload carrying `api_key`, `key`, `secret`, `token`, or
  `password` is refused, exactly as the Settings endpoint refuses
  it. BYOK keys live in the vault only.
- **Telemetry switch** (editable): the same toggle the Settings
  screen offers, stored by `puspa/telemetry/settings.py`. The
  response shows the stored toggle, the `PUSPA_TELEMETRY`
  environment override (if any), and the effective switch.
- **Embedding snapshot** (read-only): the startup embedding
  health already carried by the operator status.

The POST accepts any of `instance_name`, `model`,
`telemetry_enabled`; at least one is required, and an unknown
key is a 400, never silently ignored. It returns the saved
configuration in the GET shape.

### Sessions — `GET /api/admin/sessions`

- The operator's login sessions: device label, created and
  last-seen times, revoked flag. Tokens are never exposed: only
  their hashes are stored, and this listing does not even carry
  those.
- The live pairing-store snapshot (outstanding codes, pairing
  sessions, locked addresses) — the same snapshot
  `GET /api/operator/status` serves.
- `POST /api/admin/sessions/{id}/revoke` revokes one session by
  its row id (new in `puspa/accounts/ports.py`:
  `revoke_session_by_id`). Idempotent. An unknown id and
  another user's id both answer 404, so one user's ids cannot
  probe another's sessions.
- `POST /api/admin/sessions/revoke-all` revokes every session of
  the operator, including the browser making the call: the
  operator signs themselves out everywhere.

Only the signed-in operator's sessions are listed. A local
instance has one operator; this is not a multi-user admin panel.

### Usage statistics — `GET /api/admin/usage`

Counts from data the instance already keeps — no new telemetry
is collected for this endpoint:

- Ledger (`events` table): total events, events by type, events
  in the last 24 hours / 7 days.
- Account tables: user count, active (non-revoked) sessions.
- Hub skill list: total / installed / builtin / library skills.
- In-memory chat store: messages held this run only (it resets
  on restart, so the number is labelled as such on the page).

### Skills — `GET /api/admin/skills`, enable / disable

The same list the hub serves: shipped builtins plus registry
skills (`puspa.marketplace.hub.list_hub_skills`, via
`puspa/pwa/hub_api.py`). Enable and disable go through the
existing skill registry — `MarketplaceRegistry.install` /
`uninstall` — not a new plugin system. The operator's POST is
the explicit approval, mirroring the hub install (so a skill
below the trust floor can still be enabled deliberately by the
operator, and the decision is written to the registry audit
trail).

Builtin skills ship with Puspa and are always on: toggling one
answers 409 with that reason, never a pretend-success. An
unknown skill id answers 404.

## What it cannot do (honest limits)

- **No separate admin password.** The guard is the existing
  local operator auth: the logged-in operator's session cookie.
  Anyone logged in as the operator on this machine can open
  `/admin`. The page states this plainly instead of claiming a
  stronger gate. Do not run a local instance as a shared
  multi-user machine and expect this page to separate operators.
- **No serve-mode change from the page.** See above.
- **No builtin-skill toggle.** Builtins are part of the shipped
  product; removing one means removing its folder, not a
  dashboard switch.
- **No other users' sessions.** Only the operator's own.
- **No hosted anything.** Billing, entitlements, teams, rooms
  sharing, and the hosted operator panel live in the private
  cloud backend.
- **No new telemetry.** The usage numbers count what the
  instance already stores. Turning the telemetry switch on
  still only sends what `puspa.telemetry` sends (anonymous
  aggregates to the configured collector, off by default).

## Page rendering

`/admin` is Jinja-only on purpose, like `/trial`: it renders
`apps/pwa/templates/admin.html` with server-rendered data from
the same readers the API serves, so the numbers on the page and
the numbers from the API cannot disagree. It needs no React
build and no new SPA route, and the PWA redesign (pending
Nero's references) is untouched: the page uses the existing
template language (`base.html`, `.kartu`, `.pesan-sub`).

Each section is read on its own: a failing reader leaves an
honest error line in that section instead of taking the whole
page down. The nav header links to `/admin` from every Jinja
page.
