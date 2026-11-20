# Live view + takeover (P-09, plan section 12.9)

## What it is

Two things, one screen (`/live` in the PWA):

1. **Live view** — what the bot is doing right now: status
   (`held` / `working` / `idle`), the open task, the last tool
   call, and the recent event stream. The frame is built from
   ledger events (durable, replayable) plus the PWA's in-memory
   live tail (ephemeral, marked `source: "live"`).
2. **Takeover** — a button that pauses the bot's autonomous
   work, and a release button that lets it resume.

## What it is not (honest limits)

- **No video stream of a desktop.** Puspa has no real
  computer/desktop to watch: G16 built only the execution
  backend and its contract, the Docker sandbox never ran a
  container on this VM, and there is no CDP browser driving
  anywhere. The frame's `screenshot` field stays `null` until
  a backend that can produce frames exists. The screen says so
  in plain words instead of showing a fake video.
- **No click-and-type control (V2).** V1 is watch + pause
  only. The button never drives a mouse or keyboard.
- **No fleet view.** One agent at a time, picked from the
  user's own agents. Watching several bots at once is Team
  scope, not this ticket.

## How takeover works

Pressing "take over" runs, in order:

1. **Ownership** — the agent must belong to the logged-in
   user (`puspa.evidence.proof.user_agent_ids`), else 403.
   Another user's bot can never be taken over.
2. **Interrupt live work** — the subagent lifecycle cancels
   the agent's live children, and the PWA pool stops its
   subagents (children only). Unlike P-02's stop-all, the
   agent's own tokens are NOT revoked, so release can resume
   it. The in-flight turn ends as `interrupted`; history is
   kept, so the next turn can continue.
3. **Hold row** — one row in `takeover_holds` (migration
   0026) per held agent. While it exists, the turn loop
   refuses new work: `TurnLoop(takeover_hold=store.is_held)`
   checks before every model call and every registry tool
   call, and a held turn ends `interrupted` with a takeover
   note. A second press is 409.
4. **Ledger event** — `takeover_started` is appended
   (migration 0026 extended the closed event set to 33).
   Release deletes the hold row and appends
   `takeover_released`. The ledger is the audit trail; the
   table keeps no history.

A hold check that fails fails open, like the intent advisor
and the autonomy dial: a broken signal must never block the
turn. The press itself (hold + interrupt + ledger) is what
enforces the pause; the loop gate is defense in depth.

## The frame, and the P-10 seam

`GET /api/live-view/{agent_id}` returns a `LiveFrame`:

- `status` — `held` when a hold row exists, `working` when
  an open task has an event in the last 5 minutes
  (`LIVE_WINDOW_SECONDS`), else `idle`. An open task gone
  quiet is shown as idle with its last activity time, never
  as working.
- `events` — the agent's recent ledger events, oldest first,
  with the hash chain. **P-10 replays these**: the frame is
  replayable by construction.
- `live_tail` — the PWA's in-memory events for the agent
  (newest last), marked `source: "live"`, never part of a
  replay.
- `screenshot` — always `null` today.

`FrameSource` (`puspa.pwa.live_view`) is the capture seam:
a real browser/desktop backend implements `capture_frame`
and returns a frame with a `screenshot`; until then
`LedgerFrameSource` is the only implementation.

`GET /api/live-view/{agent_id}/stream` serves one
`live_frame` SSE event plus a heartbeat, then ends — the
same snapshot idiom as the app's `/sse` endpoint. The PWA
polls the GET every 5 seconds for fresh frames.

## Where the pieces live

- `puspa/pwa/live_view.py` — the store, the takeover /
  release orchestration, the frame builder, the `FrameSource`
  seam. No FastAPI here.
- `puspa/pwa/live_view_api.py` — the four routes + the
  `/live` page wiring in `puspa/pwa/app.py`.
- `puspa/turns/loop.py` — the `takeover_hold` gate (the
  only change to the loop: a constructor param, one check
  helper, two call sites).
- `puspa/migrations/versions/0026_takeover_holds.sql` —
  the holds table + the two new ledger event types.
- `puspa/migrations/versions/0027_takeover_check_dedup.sql`
  — drops the duplicate inline `events` CHECK (`events_type_check`)
  that 0026 could not reach: 0001 created the type list twice
  (inline in CREATE TABLE, plus the named `event_jenis_check`),
  and 0026 only extended the named one, so appends of the new
  types still failed on a fresh database. 0026 was already
  applied, so it is fixed by a new migration, not edited.
- `docs/openapi-live-view.yaml` — the contract (the main
  spec references it with `$ref`; 1811 lines, under the 2000
  cap). `apps/pwa/src/api/generated-types.ts` is regenerated
  from it.
- `apps/pwa/src/screens/LiveViewScreen.tsx` — the screen
  (polling, agent picker, two-tap takeover, release, recent
  events) + `LiveViewScreen.test.tsx`.
- Tests: `tests/test_live_view.py` (store, takeover,
  release, frames), `tests/test_takeover_turn_gate.py`
  (the loop gate), `tests/test_live_view_api.py` (HTTP).

## Relation to P-02 (stop-all)

Takeover is composed from the same machinery, not a copy:
lifecycle cancel + pool stop, like stop-all — but without
the agent-level revoke-all, which is what makes takeover a
pause (resumable) instead of a stop. Stop-all still means
"stop everything, now"; takeover means "hold this one bot
while I look".
