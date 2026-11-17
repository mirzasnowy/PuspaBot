# STATUS — Phase 2 Puspa MVP (Team runtime + PWA + Connectors + Scheduler)

Definition: revision 7 (approved by Nero 17 Nov 2026 ~17:42 WIB).
The 17-week timeline IGNORED on Nero's order, 21:53 WIB (dates are
only ordering, not deadlines). Local work in `build/fase-2/`;
DO NOT push to GitHub; DO NOT start Phase 3.

## Numbers (all runs SOLO, one pytest at a time)

| Suite | Result | Time |
| --- | --- | --- |
| Phase 2 (new) | **73 passed** | 17 Nov 2026 ~22:40 WIB |
| Phase 0 (re-verification) | **244 passed, 4 skipped** | 17 Nov 2026 ~22:35 WIB, solo |
| Phase 1 (re-verification) | **58 passed** | 17 Nov 2026 ~22:36 WIB, solo |

4 Phase 0 skips = live-container Docker tests (kernel rejects `setns`) —
the same environment block as Phase 0/1, not a regression.
Total new Phase 2 code: ~2,600 lines of Python + 11 HTML templates
+ 1 service worker + 10 reference cassettes.

Live PWA run evidence (uvicorn 127.0.0.1:8123, 17 Nov 2026 ~22:45 WIB):
7 screens HTTP 200 (chat 0.16s, the rest 0.01-0.02s), SSE
streaming (`data: {"type": "office"...}` + heartbeat), `POST /api/chat`
OK, `POST /api/tugas` completed (`status: completed, tahap: sajikan`),
`/sw.js` 200, `/manifest.json` OK.

## What was built (per definition scope)

### 1. Team runtime (§2.2, §2.4) — `puspa/runtime/`
- **Agent registry** (`agents.py`): max 3 agents; Puspa is mandatory
  and cannot be deleted — rejected in the API (`delete_agent`) AND in
  the database (trigger `trg_tolak_delete_puspa` on `agent_meta`; a
  direct DELETE raises an exception, tested). New agents start with no
  connectors; Puspa starts with Gmail, Drive, Calendar.
- **Handoff contract** (`handoff.py`): fixed 10-field schema,
  validated at creation; state machine proposed → accepted →
  berjalan → handed_over_at → passed/rejected; output failing the schema
  = rejected; 1 retry then report to the user; 15-second heartbeat,
  stuck if no heartbeat for 30 seconds or past the timeout (default
  45 seconds); A→B→A loops rejected by code; max 3 inter-agent handoffs
  per task.
- **Budget** (`budget.py`): max 25 steps/agent, 15/subagent;
  a subagent's budget must not exceed its agent's remainder; Rp2,000
  ceiling per task (task stops + reports when exceeded); calibration
  from the ledger (`kalibrasi_dari_ledger`: mean/median/max per
  task; honestly empty when there is no cost data yet).
- **Subagent pool** (`subagents.py`): max 4 per agent, 6 system-wide,
  the rest queue (the queue advances when one finishes); one level
  only (a subagent creating a subagent is rejected); a subagent token
  must be a subset of its agent's token (Phase 0 `is_subset`, checked
  by code); Stop kills all of the task's subagents + revokes tokens
  (`revoke_all_for_task`) — target < 5 seconds (tested: the `detik`
  result < 5 for 3 subagents).
- **Task lifecycle** (`tasks.py`): 5 stages Receive → Plan →
  Execute → Verify → Present; an ambiguous goal → max 1 question;
  a plan with outbound actions stops at approval before running;
  the executor is injected (the runner can be tested without a
  model/network); Postgres queue `SELECT FOR UPDATE SKIP LOCKED`
  (`klaim_task_berikutnya`); task-delete tombstone (C129, see
  below).

### 2. Verifier + evidence book + orphan numbers (§2.5) — `puspa/evidence/`
- **Evidence** (`evidence.py`): one record per number (evidence_id,
  klaim, value, unit, sumber_url, time_take, text_excerpt max
  200 characters, page_hash, screenshot with 30-day TTL,
  reader_id, verifier_id, vonis); deterministic layer-1 validation
  (number parsing, units, domain allowlist); stored in the `evidence`
  table.
- **Verifier** (`verifier.py`): independent (only accepts claims,
  rereads via its own callable); three layers (deterministic,
  reread, model only for language decisions — optional callable,
  default compares normalized names); verdicts Verified /
  Uncertain / Cannot be determined. Injected-wrong-price test:
  3 of 3 caught (not Verified).
- **Orphan-number renderer** (`renderer.py`): `{label:evidence_id}`
  markers filled by code from the evidence book; orphan numbers in
  a draft are held → fixed 1x → if still orphaned, dropped +
  the statement "tidak bisa memastikan angkanya". Measured 3 of 3
  orphan numbers held/dropped. Important correction during the build:
  orphan detection must run at DRAFT level (not render output), so a
  number already rendered from evidence is not wrongly flagged orphan.
- **Reliability score** (`evidence.py`): % Verified of the last 50
  readings; below 80% → all results from that source are max
  Uncertain; badge summary ("5 verified, 1 uncertain").
- **Projections** (`projections.py`): chat thread, Office (live status
  + cost), audit, evidence, cost — all pure functions over the ledger
  event list; rebuildable at any time.

### 3. Quarantined reader in the runtime (§2.6)
- Read connectors (`gmail_read`, `drive_read`) pass raw content
  through the Phase 0 `QuarantinedReader` (domain allowlist, free text
  marked `[DATA: ...]`, max 200 characters). Email reply drafts:
  the reader writes the draft text as data, the executor calls the
  draft tool, sending still goes through the gate + approval (enforced
  in `GoogleConnector.gmail_send`: `disetujui=False` → rejected).

### 4. Tiered model router (§2.4) — `puspa/router/router.py`
- Built ON TOP of the Phase 0 generic factory
  (`demo/byok_provider.py`, `build_byok_provider`) — not a rewrite.
  Selection layers: planner/presenter = cheapest that passes the
  10 reference tasks; reader/searcher = cheapest; verifier = a
  different family from the creator; vision = cheapest that can read
  labels (if none → `None`, the agent asks the user to type the item
  name). Catalog rates are configuration (Rp/1,000 tokens, public
  OpenRouter prices Nov 2026, rounded) — not guaranteed numbers;
  recalibrate from the ledger.

### 5. 7-screen PWA (§2.1, §2.9, §2.10) — `puspa/pwa/app.py` + `apps/pwa/`
- FastAPI + Jinja + HTMX + service worker (decision 1). Live
  updates via SSE (`/sse`, `StreamingResponse`, no WebSocket).
  Screens: Chat (group chat, 6 message types via projection, folded
  subagents, @mentions, work strip, live EventSource), Tasks
  (queue + Plan + approval queue), Modules (2 built-in +
  custom-module panel — the modules themselves are phases 3-4),
  Connectors (connect/disconnect per agent + scope), Memory (profile
  + Proposals + export/delete via Phase 1), Settings (Rp0 spending
  limit, Indonesian language, notifications + test send,
  account+devices, data consent, autonomy record), Office (team cards
  from the ledger — NOT animation; evidence badges; Take over/Stop/
  Retry controls; live handoff log; strip widget in chat). Exactly
  7 screens (enforced by test).
- Google login (decision 16, Phase 1 foundation): `/auth/google`
  uses Phase 1 `build_auth_url`; the callback records that Phase 1
  `complete_google_login` handles the code exchange.
  HONEST LIMIT: the httpOnly session cookie is not yet set in the
  callback (the callback returns a JSON note, it does not complete
  the login); full end-to-end login waits on client_id/secret + a
  final callback — recorded as a follow-up, not claimed done.
  Test seam: `create_app(test_user_id=...)`.

### 6. Connectors (§2.8) — `puspa/connectors/`
- `GoogleConnector` (httpx, injected client; tests use
  `MockTransport`, no network): Gmail search/read/create draft/send
  (send only after approval), Drive search/read, Calendar read
  events/create event (create only after approval). Tokens from the
  Phase 1 vault (`load_tokens`); 401 → "reconnect".
- Per-agent grants (`grants.py`): Puspa starts with all three,
  new agents start empty; grant/revoke from the PWA; minimal scope per
  connector.
- KNOWN BLOCK: publishing the Google OAuth app to
  "In production" needs Nero's browser (Testing status = refresh
  tokens expire after 7 days). Code complete + tested under Testing
  status; publishing = Nero's pending step (decision 19).

### 7. Scheduler + host (§2.14) — `puspa/scheduler/`, `puspa/push/`
- The `triggers` table + one scheduler process (`tick`): triggers
  recorded in the ledger (`trigger_fired`) with an idempotency key
  (`trigger_runs` — one trigger never runs two tasks, tested); missed
  triggers run once on wake + are reported (`trigger_missed`, tested);
  scheduler heartbeat to the Office (dead > 2 minutes → `sehat()`
  False, tested).
- Built-in jobs wired up: Phase 1 daily backup (C126 —
  `schedule_backup_daily` registers the `backup_daily` trigger;
  `jalankan_job_terjadwal` calls Phase 1 `create_backup`) and
  idle-time consolidation from Phase 1 (`consolidate_idle`).
  LIMIT: the next schedule shifts +1 day (daily schedule);
  a full cron parser is a Phase 4 improvement.
- Web Push VAPID (`puspa/push/push.py`): self-generated P-256 keys,
  self-made ES256 JWT (RFC 8292) with `cryptography`, aes128gcm
  payload encryption (RFC 8291) — round-trip tested
  (encrypt → decrypt = original content); sending via httpx
  (MockTransport, 201); empty push (wake only) supported.
  iOS warning (only works if the PWA is installed to the home screen)
  is in Settings + onboarding tests sending.
- KNOWN BLOCK: an always-on host needs the Singapore Contabo VPS
  (~$6.99/month + Nero's decision); the 256 MB natbox trial is
  NOT used (too small). Scheduler + push are built complete and
  tested locally; the production host = Nero's pending step.

### 8. Cassette tests (§2.11) — `puspa/cassette/`, `cassettes/`
- Recorded from ledger `tool_completed` events; `CassettePlayer`
  replaces the tool (no network; an exhausted cassette → an honest
  error, not an invention); `suntik_price_wrong` (a new cassette, the
  original unchanged); deleting a cassette follows task deletion
  (file + DB row).
- 10 reference cassettes created (`cassettes/*.json`, generator
  `cassettes/create_reference_cassettes.py`, test data): replay 10/10
  numbers identical (gate 12 target: at least 9 of 10).

### 9. Repo layout alignment (§7)
- DECISION: gradual (not a full migration at once). Reason:
  the Phase 0/1 packages are a stable base (244 + 58 tests green) and
  moving files would break imports + the three suites' conftest.
- What Phase 2 did: new code written INSIDE the target
  structure — `apps/pwa/` (PWA templates + static),
  `apps/api` represented by the FastAPI endpoints in `puspa/pwa/app.py`
  (one FastAPI deploy, decision 1), `packages/*` mapped to
  `puspa/*` subpackages (see the mapping below), `infra/` holds
  `schema_runtime.sql`. `puspa/` stays one namespace
  (`extend_path`) across the three phase directories.
- Old → new mapping (migration roadmap when the repo is unified
  into mirzasnowy/puspa-ai):

  | Old (phase 0/1 build) | New (§7) |
  | --- | --- |
  | `puspa/ledger/` | `packages/ledger/` (ledger + evidence) |
  | `puspa/policy/` | `packages/policy/` (tokens + gate) |
  | `puspa/accounts/`, `puspa/auth/` | `packages/policy/` (accounts) + `apps/api` (login) |
  | `puspa/quarantine/`, `puspa/sandbox/` | `packages/agent/` (quarantine + sandbox) |
  | `puspa/chassis/` | `packages/agent/` (nanobot integration) |
  | `puspa/memory/`, `puspa/persona/`, `puspa/onboarding/` | `packages/agent/` (memory + persona) |
  | `puspa/backup/`, `puspa/consent/` | `packages/ledger/` (backup) + `apps/pwa` (consent) |
  | `puspa/runtime/`, `puspa/evidence/`, `puspa/router/` (Phase 2) | `packages/agent/` + `packages/ledger/` + `packages/agent/` |
  | `puspa/connectors/`, `puspa/scheduler/`, `puspa/push/`, `puspa/cassette/` (Phase 2) | `packages/agent/` (connectors) + `packages/workflow/` (scheduler) + `packages/agent/` (cassettes) |
  | `puspa/pwa/`, `apps/pwa/` (Phase 2) | `apps/pwa/` |
  | `demo/byok_provider.py` (Phase 0) | `packages/agent/` (BYOK router) |

### 10. Decisions (3 Phase 0 questions + Phase 1 conflicts)
- **(a) Puspa agent registration: MANDATORY.** One Puspa agent is
  created automatically when a user is created / on first login
  (`ensure_puspa_agent`, idempotent). Reason: the Phase 0 owned
  reader fail-closed rejects events from unregistered agents, so
  without a registered Puspa every task event would be rejected.
- **(b) The `events` table: NO RLS needed.** Isolation via the
  agent registry + `require_owner` in the owned reader is enough;
  `events` has no `user_id` column and adding one would change the
  append-only schema + hash chain. Phase 2 tables with a `user_id`
  HAVE been given RLS (policies using
  `current_setting('app.current_user_id')`).
- **(c) `puspa_app`: YES, given LOGIN + password.** Only when env
  `PUSPA_APP_PASSWORD` is set (without the env, still NOLOGIN as in
  Phase 0; no default password). Implemented in
  `puspa/runtime.create_schema`.
- **C57 (slices in the ledger): DECIDED — stays an audit table.**
  The closed 31-type event set is NOT extended in Phase 2
  (adding a type needs revision 8 + a Phase 0 schema CHECK migration).
  Slice grants stay recorded in the Phase 1 audit table
  `slice_grants` (with RLS, auditable). Nero's decision in revision 8
  is needed if a `slice_granted` type is wanted.
- **C129 (account-deletion tombstone): DONE.** Deleting a task =
  append `task_stopped` with content `{"deleted": true}` (no
  UPDATE/DELETE on `events`; hash chain stays intact) +
  cassettes/blobs deleted with it (`tombstone_task`, tested).
- **C126 (daily backup): DONE (wired up).** The `backup_daily`
  trigger is registered via the scheduler; execution calls Phase 1
  `create_backup`. The real backup schedule runs when the scheduler
  runs on the host (VPS — Nero's step).
- **C122 (5-minute onboarding): LIMIT RECORDED.** The
  `/api/onboarding/waktu` endpoint records onboarding time from the
  PWA (for gate 15 measurement). Human time can still only be
  measured in real user testing (8 of 10 within 5 minutes).

## Gates touched
- **Gate 2 (reference tasks): PARTIAL.** 10 reference cassettes done +
  replay 10/10 identical; live runs of the 10 reference tasks (with
  model + wedge) wait on Phase 3 (wedge) + test users.
- **Gate 9 (performance): PARTIAL.** Local PWA load 0.01-0.16s
  (target < 3s on 4G — not yet measured on a 4 GB phone); subagent
  spawn in the pool is instant (container < 3s cannot be measured —
  Docker does not run on this VM, the Phase 0 environment block).
- **Gate 10 (cost): PARTIAL.** Rp2,000 ceiling enforced +
  calibration from the ledger done; Rp500 thousand/month burn not yet
  measured (needs a test period + host).
- **Gate 11 (agents + subagents): PARTIAL (measured).** Creating a
  2nd agent from the PWA (tested); the 3-reader + 1-verifier pattern
  in the pool (limits + permission classes tested); the verifier
  catches wrong prices 3/3; stuck handoffs detected (30s heartbeat,
  45s limit) — reporting to the user within 2 minutes met via 1 retry
  (100s max total); Puspa cannot be deleted (API + DB trigger,
  tested); out-of-token tokens rejected 3/3; circular handoffs
  rejected; Stop < 5 seconds (tested). Not yet: tests with a live
  model + the price-compare wedge (Phase 3).
- **Gate 12 (backbone + evidence): PARTIAL (measured).**
  Orphan numbers held 3/3; tampered hash chain detected 3/3;
  10 cassettes replayed 10/10 identical (target 9/10). Not yet: forced
  kill test 3/3 (0 duplicate outbound actions) — needs a host + real
  processes, recorded as a follow-up.
- **Gate 15 (operations + onboarding): PARTIAL.** Scheduler +
  heartbeat + missed triggers done; onboarding-time measurement
  endpoint done; 0 expired tokens over 4 weeks + 0 missed reminders
  due to a dead host NOT yet measured (needs a test period + VPS +
  OAuth publishing).
- **Gate 7 (bill autopilot): NOT YET.** 100% reminders delivered
  needs an always-on host (VPS — Nero's step).

## Needs Nero (human steps, not code blockers)
1. **Singapore Contabo VPS** (~$6.99/month) for the always-on host
   + scheduler + reminders (decision 2). Without this, gate
   7 cannot pass.
2. **Publish the Google OAuth app** to "In production" (Nero's
   browser; decision 19). Without this, refresh tokens expire after 7 days.
3. **Decision 5** (PLN/PDAM/phone customer numbers for canary +
   tests) — needed since Phase 2/3; not yet given.
4. **End-to-end Google login**: client_id + client_secret +
   final callback in the PWA (after item 2) — the Phase 1 foundation
   code already exists.
5. **Decisions 1, 4, 8, 13, 14, 15**: the definition's recommendations
   were already followed in the build (FastAPI+Jinja+HTMX+SW; cheap BYO
   router; 25 steps; Rp2,000 ceiling; verifier from a different
   family; terms of service checked before Phase 3) — only Nero's
   confirmation is left, if he disagrees.

## Large-repo patterns adapted (detail: `reuse.md`)
- **LiteLLM** (MIT): tiered model router → `puspa/router/`.
- **agno / AgentOS** (Apache-2.0): durable execution, approvals,
  scheduling, observability → runtime + ledger + scheduler.
- **OpenHands** (MIT): sandbox hardening → quarantine + allowlist
  (reused from Phase 0, enforced in the Phase 2 connectors).
- **opencode** (MIT): agent UX (chat/task/approval) → PWA.
- **Home Assistant** (Apache-2.0): connectors + scheduled automation →
  Google connectors + scheduler.
- **Supabase** (Apache-2.0): cloud patterns (RLS + auth + realtime)
  → Phase 2 RLS + SSE instead of realtime.
- No code copied verbatim; no new dependencies except FastAPI (MIT); no forks.

## What was NOT built (deliberately, per the definition)
- Langflow-style visual builder → workflow/module builder UX = Phase 4.
- Voice (Pipecat/LiveKit) → the Voice module is NOT in the definition;
  recorded post-MVP in `reuse.md`.
- Playwright read-only browser (C67/C68): not yet built — pages read
  via reader + HTTP; Playwright follows in Phase 3
  (the wedge needs to read listings).
- Warm sandbox pool (C18): not yet — container spawn cannot be tested
  on this VM; cut item no. 3 if late (§9).
- Full cron parser: trigger schedules shift +1 day; full cron is
  Phase 4.
- Prayer times/qibla, WhatsApp/Telegram, fashion, full QRIS:
  still not built (section 6).

## Environment incidents / notes during Phase 2
- FastAPI was not in the Phase 0 venv yet: installed `fastapi==0.163.1`
  (MIT). Pip briefly downgraded starlette to 0.46.2 (conflicting with
  sse-starlette 3.7.0, which needs >=0.49.1) — avoided by using
  Starlette's built-in `StreamingResponse` for SSE instead of
  sse-starlette. The Phase 0 suite stayed green afterwards (244 passed).
- `pkill -f uvicorn` while stopping the evidence-run server briefly
  killed its own shell (the pattern matched its own command line).
  Lesson: do not `pkill -f` with a pattern present in your own command line.
- Postgres stayed alive throughout Phase 2 (no total-loss incident
  like Phase 1).
