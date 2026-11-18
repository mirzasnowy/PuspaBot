# Puspa PWA (G1)

Date: 18 Nov 2026. Status: full PWA built (7 live screens); the
central Python suite still has to run (coordinator runs it SOLO —
workers never run pytest, parallel runs collide on the shared
`puspa_test` DB).

The PWA is the product home (revision 7, locked decision). Before
G1 it was a shell: 11 files (10 Jinja templates + `sw.js`),
"coming soon" screens, no state management, no API client. G1
replaces the shell with a real TypeScript app and keeps the Python
server as the API + auth layer.

## Stack decision

- **React 19.3.1 + Vite 6.4.6 + TypeScript 5.9.3.** Chosen
  because the comparison repos use it (OpenBot: React 19 + Vite;
  hermes-agent: React + Vite dashboard) and because Nero decided
  TypeScript for the web surface while Python stays the agent/brain
  core. `npm ping` to the registry works from this VM, so the
  full stack installed cleanly — no vanilla-TS fallback was
  needed.
- **Vitest 3.2.7 + jsdom + @testing-library/react** for tests.
- **openapi-typescript 7.13.0** for client generation (see below).
- No TanStack Router/Query (OpenBot uses them): plain anchors +
  server bootstrap per screen keep navigation server-driven and
  make every screen work on first paint without a client router.
  Adding a client router is a follow-up, not a gap that blocks
  use.

## API contract first

`docs/openapi-pwa.yaml` is the source of truth for every PWA
route: the 7 screen pages, login + Google OAuth, SSE, the
evidence panel, manifest + service worker, and all `/api/*`
endpoints (chat, agents, tasks, approvals, connectors, push,
onboarding time). Form posts stay form-encoded
(`application/x-www-form-urlencoded`) exactly as the FastAPI app
expects; JSON bodies are used only where the server reads
`request.json()` (push subscription, onboarding time).

Client generation is real, not claimed:

- `npm run generate` runs openapi-typescript over the spec and
  writes `apps/pwa/src/api/generated-types.ts` (1,299 lines) —
  generated, never edited by hand.
- `apps/pwa/src/api/client.ts` is a thin hand-written fetch
  wrapper typed against those generated types (form posts, JSON
  posts, `ApiError` with the server's error message, SSE frame
  parser, Rupiah formatter). The split is deliberate: generated
  types, hand-written transport.

## Architecture

```
apps/pwa/
  index.html            Vite entry; <!--PUSPA_BOOTSTRAP--> marker
  package.json          scripts: dev / build / test / generate
  vite.config.ts        build to dist/; dev proxy /api + /sse to :8000
  vitest.config.ts      jsdom, src/**/*.test.* + tests/**/*.test.*
  src/
    main.tsx            mounts App + Nav, reads the bootstrap
    App.tsx             screen switch (7 screens + login, no 8th)
    styles.css          the old base.html styles, extended
    api/
      generated-types.ts  generated from docs/openapi-pwa.yaml
      client.ts           typed fetch wrapper (see above)
      bootstrap.ts        window.__PUSPA_BOOTSTRAP__ + screen list
    state/              one store file per screen (DuskBot pattern):
      chat.ts           messages, optimistic echo, SSE live strip
      tasks.ts          session tasks + stop state
      approvals.ts      one-tap decisions + content-hash binding
      memory.ts         profile-fact normalizer (tolerant of rows)
      connectors.ts     agents, grant toggles, channel checklist
      office.ts         office projection (keyed -> sorted list)
      modules.ts        built-in modules + skills note
    components/
      Nav.tsx           7-link header, active screen highlighted
      ApprovalCard.tsx  one-tap card (full content on the card)
      WorkStrip.tsx     "who is working" strip from live SSE
    screens/            Chat, Tasks, Modules, Connectors, Memory,
                        Settings, Office, Login
  static/
    sw.js               puspa-v2: shell + /assets cache-first,
                        screens/API always network, push handler
    icon-192.png        generated PWA icon (teal, white P)
  tests/
    build-output.test.ts  dist/index.html exists, loads the bundle
  dist/                 build output (served by FastAPI; see below)
```

State is split per screen (DuskBot `src/state/` pattern), never
one giant store. Stores are framework-free pure functions, so
they are unit-tested without React; screens hold the state with
`useState` and call the typed client.

### Server wiring (`puspa/pwa/app.py`, minimal change)

- `DIST_DIR = apps/pwa/dist`; `/assets` is mounted from
  `dist/assets` when it exists (versioned Vite assets).
- `spa_or_template(screen, request, context)`: when
  `dist/index.html` exists, it serves it with the screen's real
  data injected as `window.__PUSPA_BOOTSTRAP__` JSON (schema
  `Bootstrap` in the OpenAPI spec; `<`, `>`, `&` escaped so the
  JSON can never break out of its `<script>` tag). When `dist`
  has not been built, it falls back to the old Jinja template —
  so a fresh checkout without `npm run build` still serves the
  old screens and the existing Python tests still pass.
- `/masuk` now serves the SPA login screen (the old code rendered
  `masuk.html`, a template that never existed — the file is
  `login.html`; the fallback now renders that name).
- No API route and no auth behaviour was changed. The service
  worker and manifest routes are untouched.

## The 7 screens (all live, none "coming soon")

Menu follows revision 7 §2.1 exactly — 7 screens, no 8th.

1. **Chat** — history seeded from the bootstrap; send via
   `POST /api/chat` with an optimistic local echo reconciled
   against the server reply; live work strip from `/sse`
   (office projection + heartbeat).
2. **Tasks** — create via `POST /api/tugas` (outcome: stage +
   question shown as the plan); stop via
   `POST /api/tugas/{id}/hentikan`; approval queue below.
3. **Modules** — the 2 built-ins (Price Compare, Bill Autopilot)
   with a preview panel that opens inside this screen; custom
   modules are created through chat (revision 7 §2.12–2.13).
4. **Connectors** — grant/revoke Gmail, Drive, Calendar per agent
   (`POST /api/konektor/{name}`); create/delete agents
   (max 3, Puspa cannot be deleted — server enforces it).
5. **Memory** — profile facts + Proposals from the bootstrap
   (tolerant normalizer: full fact objects or plain strings).
6. **Settings** — spending limit (Rp0 default), language, real
   Web Push enable flow (`Notification.requestPermission` +
   service-worker subscription + `POST /api/push/langganan`),
   account/devices note, data consent text, Autonomy Record (T0).
7. **Office** — agent cards from the office projection (status,
   last task, running cost vs the Rp2,000/task cap, sub-agents
   with permission class), live-updated by SSE; handoff log;
   per-agent stop button.

### hermes-agent page checklist (folded in, no new screens)

Channels → Connectors screen (Gmail/Drive/Calendar live via
grants; Telegram/WhatsApp labelled "not available", G14).
Cron → Tasks screen note (scheduler runs server-side; no read
endpoint yet). Skills → Modules screen note (skills ship inside
modules; registry is G23). Sessions + Analytics → Office screen
(live sessions and cost from the SSE projection). Config/Logs/
Models/Plugins and the rest have no PWA surface in the MVP and
are not faked as pages.

## Build / run / test

```bash
cd apps/pwa
npm install          # first time (registry reachable from the VM)
npm run generate     # regenerate types from docs/openapi-pwa.yaml
npm run build        # tsc -b && vite build  -> dist/
npm test             # vitest run (37 tests)
npm run dev          # Vite on :5173, /api + /sse proxied to :8000
```

Serve the built app with the FastAPI app (`puspa.pwa.app.run`,
port 8000) after `npm run build`. A fresh checkout must run
`npm install && npm run build` first — `node_modules/` and
`dist/` are build outputs, not pushed sources. The `.gitignore`
does not yet list them; the coordinator should add
`apps/pwa/node_modules/` and `apps/pwa/dist/` when pushing (G1
did not touch `.gitignore`: outside the G1 file scope).

## Verification (this worker)

- `npm run build`: green (tsc + Vite, 49 modules, `dist/` 249 kB
  JS gzip 77.5 kB).
- `npm test`: **37 passed, 0 failed** (8 files: client API with
  mocked fetch, chat/approvals/memory/office/tasks stores,
  ApprovalCard one-tap behaviour, build output).
- Python smoke (standalone TestClient script, NOT pytest):
  all 7 screens return 200 with the SPA + bootstrap + bundle
  asset; chat post then page contains the message; SSE streams;
  `/masuk`, `/manifest.json`, `/sw.js` (puspa-v2) OK;
  unauthenticated `/chat` still redirects to `/masuk`.
  Jinja fallback verified separately (dist moved aside): all
  screens 200, chat round-trip works.
- The central Python suite was NOT run by this worker (task
  order: the coordinator runs it SOLO). `tests/test_pwa.py`
  expectations were preserved by design: screens still return
  200 with "Puspa", the chat page still contains a posted
  message (now via the bootstrap JSON), SSE/manifest/sw routes
  unchanged, no API route changed.

## Honest limits (not yet live / not yet verified)

- **No read endpoints for lists.** The SPA hydrates from the
  server bootstrap on each navigation; there is no
  `GET /api/tasks`, `GET /api/approvals`, `GET /api/konektor`
  (grants), or memory-management endpoint, so: connector grant
  state is session-local (resets on reload), Tasks shows only
  tasks created in the session, and memory confirm/correct/delete
  and export run through chat until those endpoints land.
  Follow-up: add the read endpoints, then switch the screens to
  refresh from them.
- **Login callback is not final.** `/auth/google/callback`
  still returns the Fase 1 note (full login lives in
  `puspa.auth.complete_google_login`); the SPA login screen links
  to `/auth/google` and the server answers 503 when Google is
  not configured. No live Google login was verified in this
  task (no client credentials in scope).
- **No live browser check.** The SPA was verified by build +
  unit tests + server smoke, not by opening it in a real
  browser; a live render + click-through (chat send, one-tap
  approval, push enable) is still open.
- **Evidence buttons need structured numbers.** Messages are
  plain text in the current API, so chat messages do not yet
  carry per-number Bukti buttons; the evidence panel route
  (`/bukti/{id}`) is unchanged and documented in the spec.
- **Push needs a VAPID key.** The Settings flow is real but
  reports honestly when `VITE_VAPID_PUBLIC_KEY` is unset; no
  push round-trip was verified live.
- **Photo upload (price check, phase 3) is not in the chat
  composer yet** — text only, as in the current API.
- **The central Python suite result is pending** (coordinator
  run); G1's Python change is small and covered by the smoke
  checks above, but the suite number is not claimed here.
