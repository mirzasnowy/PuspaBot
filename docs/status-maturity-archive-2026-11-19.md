# Status archive: repo maturity phase, Waves 9-13 (18 Nov 2026)

Archived from `docs/status-maturity.md` on 19 Nov 2026, when the live
log passed the 1,000-line soft cap of the file-size ratchet (1,060
lines against a 1,055 baseline). Frozen: never appended to; the living
log continues in `docs/status-maturity.md` (Wave 14 onwards).
History is never deleted, only archived.

---

## Next: G25+G26 (subagent lifecycle + single-writer session)

Two parallel workers, disjoint files. G25 (hermes subagent
lifecycle): public lifecycle API wrapping the harness spawn —
serializable capability handle (fake handle → `UNKNOWN_HANDLE`),
operations `launch`/`status`/`wait`/`cancel` (cooperative — ask
for an interrupt at the next safe boundary, never claim done
until `wait`/`result` sees a terminal state), `steer` (queued text
for the next iteration boundary), `result` (immutable, idempotent,
32k cap, no hidden transcript/reasoning, stable result hash),
`reconnect` (after a process restart → `RECONNECT_UNAVAILABLE`,
never start a replacement child). Stable states: PENDING,
STARTING, RUNNING, SUCCEEDED, FAILED, INTERRUPTED,
CANCEL_REQUESTED, CANCELLED, UNKNOWN. Fail-closed: bounded
goal/context/metadata sizes, unknown or parent-expanding toolsets
rejected, per-launch working-directory override and per-tool
timeout explicitly refused until they can be supported without
weakening isolation. G26 (hermes persistence): single-writer
session authority (one writer for the ledger/session, through an
API, never direct DB access from workers) + compression lineage
(parent/child sessions across compressions) + FTS5 beside pgvector
+ DB recovery documentation. Note the G7 finding: the FTS5 home
is the SQLite sidecar (`state.db`), not Postgres tsvector.

## Wave 9: G25+G26 (subagent lifecycle + single-writer session, done 18 Nov 2026)

Two parallel workers, disjoint files. Central SOLO suite after the
wave: **2,308 passed, 9 skipped, 1 warning, 14m09s** (baseline
2,224 + 84 new: G25 37 + G26 47). Postgres stayed online (no VM
replacement this wave).

### G25 Subagent lifecycle (commit d162dcc7)

New package `puspa/subagents/` (12 modules, 1,489 lines) + 5 test
files (735 lines), 37 passed (verified by the coordinator, run
with `--noconftest` like `test_toolsets.py` — the repo conftest
needs Postgres for an autouse fixture even for in-memory tests).
`states.py` (`SubagentState` StrEnum: PENDING, STARTING, RUNNING,
SUCCEEDED, FAILED, INTERRUPTED, CANCEL_REQUESTED, CANCELLED,
UNKNOWN + `TERMINAL_STATES`). `handles.py` (versioned capability
handle: child id + process generation + random secret token,
base64-url serialized; parse failure → `UNKNOWN_HANDLE`; the token
never appears in `repr`, the registry stores only its SHA-256).
`errors.py` (stable coded errors: `UNKNOWN_HANDLE`,
`RECONNECT_UNAVAILABLE`, `TOOLSET_REJECTED`, `LIMIT_EXCEEDED`,
`UNSUPPORTED_OPTION`, `NOT_TERMINAL`). `limits.py` (goal 8,000,
context 32,768, metadata 32 keys / 4,096 chars canonical JSON,
steer 4,000 chars / 16 queued). `results.py` (`SubagentResult`
frozen: final text only — no transcript/reasoning — 32,768 cap +
`truncated` flag, stable SHA-256 of the stored text, idempotent).
`toolsets.py` (child toolset must be known, all tools registered,
and a subset of the parent toolset, else `TOOLSET_REJECTED`).
`hooks.py` (serial `subagent_stop` dispatch in registration
order, a failing hook is isolated and logged;
`collect_stop_hooks` for plugin contexts). `runner.py`
(`ChildContext` with `boundary()` / `drain_steer()` /
`record_cost()` / `should_stop()`, `ChildCancelled`,
`Runner` protocol, `ThreadRunner` default on a daemon thread,
injectable runner for deterministic tests). `host.py`
(`HostSession`: the seam to the host path that already exists —
`SubagentPool` one level only, child token a subset of the parent
token via `is_subset`, budget narrowed to at most 15 steps /
20,000 tokens / the parent rupiah ceiling, expiry no later than
the parent; cost rolls up into the parent `BudgetTracker`; finish
notifies memory through an injected callback; stop hooks fire
serially; pool entry cleaned up at finish; events go to the same
`sink(event_type, content)` shape the harness uses).
`lifecycle.py` (`SubagentLifecycle`: `launch` / `status` /
`wait` / `cancel` / `steer` / `result` / `reconnect` /
`shutdown`). `docs/subagent-lifecycle.md` documents the handle
contract, states, operations, fail-closed rules, host path, and
honest limits. Honest limits: **`delegate_task` does not exist
in this repo** (coordinator confirmed by grep) — the lifecycle
wires into `SubagentPool` + `is_subset` + `BudgetTracker` + the
harness-shaped sink instead, and a `delegate_task` body route is
a follow-up if that function ever appears; no harness-based child
yet (the child body is a plain callable; running `CodingHarness`
as a child needs a boundary hook in `CodingHarness.run` first);
the child token is not signed (needs the runtime signing key,
which the lifecycle does not hold); `subagent_stop` is not a
plugin event yet (`HOOK_EVENTS` in `puspa/plugins/context.py`
does not list it — G27 area, not touched here); memory
notification is an empty callback by default (child result →
feedback verdict mapping undefined); the child budget is carried
in the token payload but not enforced mid-run; 0 live LLM calls.

### G26 Single-writer session + FTS5 + lineage (commit 3ff50906)

New package `puspa/sessions/` (10 modules, 1,497 lines) + 4 test
files (576 lines), 47 passed (verified by the coordinator).
`errors.py` (`SessionError`, `SingleWriterError`,
`LiveHomeError`, `CorruptDatabaseError`, `ProfileError`,
`WriteContentionError`, `SessionNotFoundError`). `home.py`
(authoritative resolver: explicit argument → context-local
override → env `PUSPA_HOME` → default `~/.puspa`; named profiles
under `<root>/profiles/<name>/`; profile-name validation;
live-home guard requiring BOTH `allow_live_home=True` AND
`PUSPA_ALLOW_LIVE_HOME=1`). `schema.py` (`sessions`, `messages`,
`session_lineage`, `model_config`, `state_meta`, plus external-
content FTS5 `messages_fts` with 3 sync triggers; `verify_schema`,
`verify_fts`, `rebuild_fts`). `store.py` (`SessionStore`: WAL +
`busy_timeout`, every mutation in one `BEGIN IMMEDIATE`
transaction (atomic batch append), retry then
`WriteContentionError`, autocommit connections +
thread-safe RLock, BM25 `search_messages`,
atomic `create_child_session` (session + summary + lineage
edge), `check_integrity`, model config, state meta).
`gateway.py` (`SessionGateway`: process-wide registry per DB
path — a second live gateway for the same file raises
`SingleWriterError`; readers are `mode=ro` and physically cannot
write; a writable `SessionStore` refuses to open without a
`_WriterToken` that only this module mints). `assembly.py`
(`open_persistence()` / `open_persistence_reader()`; a reader
over a missing DB raises `FileNotFoundError`, never creates one
silently). `lineage.py` (`adopt_compression_result()` +
`record_compression()`; adopts only when
`stats["compressed"]` is True; the summary is the G19
`[Context summary of earlier turns]` message). `housekeeping.py`
(`maintenance()`: integrity + FTS verify + counts + optional
rebuild/VACUUM, stamps `last_maintenance`;
`recover_report()`: read-only inspection —
missing/empty/healthy/corrupt — never modifies the file).
`spawner.py` (`child_env()` always sets `PUSPA_HOME`
explicitly; `require_home_in_env()` fails a spawn that drops
it; `home_from_env()`). `docs/session-storage.md` (storage
contract, session authority, FTS5, lineage, profile isolation,
live guard, housekeeping, honest limits) and
`docs/state-db-recovery.md` (fail-closed procedure: stop writer
→ preserve evidence → `.dump`/`.recover` → restore backup →
rebuild from ledger → verify). **FTS5 G7 reused, not duplicated:**
MATCH queries use `puspa.memory.recall._fts5_query` and the
`_tokens` tokenizer; FTS5 triggers run inside the writer's own
transaction (zero extra commits, vs G7's per-row commit);
`verify_fts` closes G7's drift-detection gap (probes the index
per rowid with MATCH — a COUNT(*) on an external-content FTS5
table reads the content table, not the index, found by
experiment) + `integrity_check`, then `rebuild_fts`; the index
lives in the same persistent `state.db`. One real bug found
during the work: sqlite3's default `isolation_level=""` let an
`integrity-check` INSERT open a never-committed transaction, so
the next `BEGIN IMMEDIATE`/`VACUUM` failed — fixed with
`isolation_level=None` (autocommit) + explicit transactions.
The G19 seam is proven by test: the lineage test imports the
real `SUMMARY_MARKER` from `puspa.runtime.context_compression`
and compares it with the session marker, so a G19 marker change
fails the test instead of drifting silently (the marker is still
a duplicated constant in `lineage.py` — importing it directly
is a small follow-up). Honest limits: the single-writer
registry is process-wide, not machine-wide (two processes can
open the same profile; SQLite locking prevents corruption but
the second process is not refused at open — follow-up: file
lock at open); `get_session()` has no paging yet; `model_config`
is flat key/value without a validated schema; no backup/sync/
export of `state.db` yet (the encrypted `puspa/backup/` covers
only the Postgres ledger — the recovery doc records this as an
open backup gap); not wired to any real call site yet (nothing
calls `open_persistence()` from the turn loop) and not tested
live with a real G19 compression (fake `CompressionResult` +
real marker only); `.recover` salvage untested end-to-end with
a genuinely corrupt DB (only garbage/header-only fixtures).

## Next: G11+G12 (multi-tenant auth/relay + Sentinel)

Two parallel workers, disjoint files. G11 (DuskBot
`server/request-auth.ts` + `card-seal.ts` + `sessions.ts` +
`apps/link-service/` + `apps/cloudflare/link-relay/`): loopback
trust only when Host + Origin are loopback (a browser never gets
it); `service` mode restricts loopback without a session to an
explicit `SERVICE_ALLOW` list (method + path regex); pairing with
a short-lived code (5 minutes), 30-day session, 5 wrong tries →
10-minute address lock; per-start sealing key (HPKE) never
written to disk, each seal signed by the server's long-term
identity key (file `0600`, excluded from backups) pinned by the
app at linking; link/relay with a 7-character code (no l/o/0/1),
rate limit 30/hour per address + 2,000/hour total, AES-256-GCM
relay frames keyed only by app + server, fallback order direct →
Cloudflare tunnel → relay. Google login stays in front; this
pattern is for the server-to-server path behind it. G12 (DuskBot
`server/sentinel/` + `server/decision-log.ts` +
`server/usage-trace/`; hermes `hermes_logging.py` +
`hermes_startup_watchdog.py` + `agent/usage_*.py` +
`agent/insights.py` + `batch_runner.py`): Puspa sentinel over the
ledger + learning loop with the same 5 detectors (tool error,
stopped turn, long queue, cost, retry loop) + default thresholds
(queue 5 minutes, cost $5, retry min. 3, 7-day lookback, 30-minute
rescan) + state file; decision log as daily files with 180-day
retention; usage traces as span trees; anonymous analytics + crash
reports switchable off; the DuskBot verification rule (isolated
fixtures, never verify a mutation on live user data) as a written
rule. Note the K8 fold: K8 (independent verifier) lands here —
the sentinel's detectors are the verifier's eyes; keep the
verifier module separate from the detectors so the two can evolve
independently.

## Wave 10: G11+G12 (multi-tenant auth/relay + Sentinel, DONE 18 Nov 2026)

Coordinator handover mid-wave: the first maturity coordinator
(1af6c4c4) stopped after Wave 9; continuation coordinator
(754e424c) verified Wave 9 itself (central SOLO suite re-run:
2,308 passed, 9 skipped, 1 warning, 14m35s — same counts as the
old coordinator claimed; Postgres had been wiped by a VM
replacement and was recovered first: apt install postgresql-16 +
postgresql-16-pgvector, cluster start, infra/schema_core.sql).
Wave 9 needed no re-push: G25 (d162dcc7) + G26 (3ff50906) + docs
(9a8e5267) were already on main and the pushed tree matches disk
(35 files, 0 size mismatches).

### G11 Hosting auth/relay/pairing (commit 25cc465a)

New package `puspa/hosting/` (11 modules, 1,575 lines) + 4 test
files (968 lines), 100 passed (verified by the coordinator, with
conftest AND with `--noconftest`), ruff check + format clean,
mypy clean (11 source files). `loopback.py` (Host + Origin both
loopback; browsers never get trust — `Sec-Fetch-Mode`/UA heuristic,
fail-closed); `service_allow.py` (explicit method + path-regex
allowlist, default 3 read-only endpoints); `gateway.py`
(`authorize_request`: Google session → pairing session →
loopback (`single_user` outright, `service` only via allowlist) →
deny; Google validator injected, a raising validator means "no
session", never a crash); `pairing.py` (6-digit code, 5-min TTL,
hash-only storage, 30-day session, 5 wrong → 10-min address
lock); `identity.py` (Ed25519 long-term key, file `0600`,
over-open file refused fail-closed, fingerprint = SHA-256 of raw
pubkey); `seal.py` (HPKE `DHKEM(X25519)` + `HKDF-SHA256` +
`AES-256-GCM` — available in `cryptography==50.0.2`, no fallback
needed; per-start sealing key memory-only, each seal signed by
the identity key over `sealing_pubkey ‖ ciphertext`, 64 KiB cap);
`link.py` (7-char code, alphabet without l/o/0/1, 10-min TTL,
32-byte relay key held only by app + server, rate limit 30/hour
per address + 2,000/hour global); `relay.py` (AES-256-GCM frames,
in-memory FIFO hub, never opens a frame); `fallback.py` (direct →
tunnel → relay, attempts recorded). `docs/hosting-auth.md`
documents all six mechanisms. Coordinator fixes during
verification (worker code, not worker report): the pairing test
was flaky two ways — the issued code can be "000000" (1 in a
million; the test used it as the canonical wrong code) and the
test claimed a code survives a 10-minute lockout while its own
TTL is 5 minutes (the lockout never burns a code, but the TTL
still runs — the test now proves non-consumption via the stored
hash and expects expiry after the lock); `clock: object` failed
mypy in `pairing.py`/`link.py` (now `Callable[[], float]`);
`__init__.py` missed 6 error names in `__all__` (now all 46
exports listed and sorted); `loopback.py` SIM102/SIM103; 9 ruff
auto-fixes. Honest limits: no HTTP route yet
(`authorize_request` is a pure function; natural call site
`puspa/pwa/app.py`); stores are in-memory (pairing/link/relay
state dies on restart — follow-up: Postgres store with the same
interface); no live Cloudflare tunnel / relay network yet
(`connect_with_fallback` uses injected probes); no new ledger
event types for pairing/link audit (needs a ledger-owner type);
browser detection is a heuristic (real trust rides on loopback
interface + session/allowlist, not headers). Note: the G11
REUSE.md/NOTICE.md attribution lines are on disk but were NOT
in this commit — the G12 worker edited the same files
concurrently, so they ride with the G12 commit instead.

### G12 Sentinel + decision log (commit 08e85fa5)

New package `puspa/sentinel/` (11 modules, 2,535 lines) + 3 test
files (1,219 lines), 55 passed (verified by the coordinator),
ruff check + format clean, mypy clean (11 source files).
`detectors.py` (5 pure detectors over ledger events: tool_error
— a tool failing 3+ times in the window; turn_stop —
`task_stopped`, plus stalled turns with no terminal event and no
activity for 24h; queue_wait — `trigger_fired` to `task_created`
over 5 min; cost — one task over $5 in the window, `cost_rp`
via `idr_per_usd` 16000; retry_loop — 3+ consecutive failed
calls of one tool in a task; stable issue keys; deleted
tombstones excluded); `thresholds.py` (the five DuskBot defaults
plus `stalled_hours` 24, `tool_error_min` 3, `idr_per_usd`,
decision retention 180 days); `state.py` (atomic JSON state,
open/acknowledged/resolved, prune: open past the window, resolved
past twice the window, acknowledged never pruned); `scanner.py`
(rescan gate 30 min, auto-resolve of open issues whose condition
cleared — acknowledged never auto-resolved, scheduler entry
`run_sentinel_scan`, never raises); `verifier.py` (K8: separate
module, re-reads fresh events, re-runs the single detector for
the issue's scope; confirmed/stale/duplicate/unverifiable,
fail-closed, never writes); `decision_log.py` (daily JSONL files,
SHA-256 entry hash, 180-day prune); `traces.py` (span tree from
ledger events, span ids reuse event ids); `analytics.py`
(snake_case counters only, crash reports without the exception
message, no network sender at all); `trajectory.py` (batch runner
over prompt datasets, checkpoint + resume, redact-before-save via
G7 `redact_secrets`, ShareGPT format); `insights.py` (session
insights from `state.db` read-only: tokens via G19
`count_tokens`, cost via router catalog or recorded `cost_rp`,
4-decimal sub-cent labels, nothing invented when data is
absent). Wiring: `Ledger.recent_events` (read-only window query)
+ `sentinel_scan` trigger type + `schedule_sentinel_scan` (daily
03:00) + dispatch in the scheduler. Docs: `docs/sentinel.md`,
`docs/trajectory-format.md`, Sentinel self-checks section in
`docs/verification.md`. REUSE.md + NOTICE.md in this commit also
carry the G11 attribution sections (they were on disk but not in
the G11 commit). Coordinator fixes during verification (worker
code, not worker report): the stall detector never recorded a
task's first event as its last activity, so a task with a single
event could never be flagged stalled (real bug — now max-tracking
with explicit None init); trajectory JSONL wrote with default
`ensure_ascii=True`, so the `«redacted»` marker landed as
`\u00ab` escapes (now `ensure_ascii=False`, matching the rest of
the module); the prune test's second-half expectation was wrong
(8 days also ages the recent open issue past the 7-day window —
test corrected, code was right per its docstring). First run: 3
failed, 52 passed; after fixes: 55 passed. Honest limits: the
verifier re-reads the cached read within one scan (the detection
read IS the fresh data for that scan; a live ledger re-read
across scans is the follow-up); the scanner's 30-minute rescan
interval lives in state — `schedule_sentinel_scan` is a daily
03:00 trigger, so a 30-minute cadence needs a G8 cron job
invoking `run_sentinel_scan`, not this trigger; insights read
`state.db` read-only but the G26 session store is not yet wired
to any real call site (same follow-up as G26); no live sentinel
run against the real ledger yet (first live scan is a follow-up
once the scheduler runs on a host). Wave 10 central SOLO suite:
first run 2,461 passed, 2 failed — both ratchet tests, tripped by
G1's `package-lock.json` (3,201 lines) and `generated-types.ts`
(1,299 lines) landing mid-run; fixed by excluding generated files
from the ratchet (lockfiles, `generated-*`, `dist/`,
`node_modules/` — commit c499e487). Re-run: **2,463 passed, 9
skipped, 1 warning, 14m24s, EXIT 0** — Wave 10 green.

## Wave 11: G1 (full PWA, TypeScript, DONE 18 Nov 2026)

### G1 Full PWA (commit be76d3e3)

React 19 + Vite app in `apps/pwa/` (51 files pushed), 7 live
screens + login, OpenAPI contract + generated client. `docs/
openapi-pwa.yaml` (contract for every PWA route: 7 screens, /sse,
/bukti, all /api/*); `src/api/generated-types.ts` (1,299 lines,
openapi-typescript 7.13.0 output, never edited by hand);
`src/api/client.ts` (thin typed fetch wrapper: chat, tasks, stop,
one-tap approval, agents, connectors, push, onboarding, SSE
parse); `src/state/` (one store per screen: chat, tasks,
approvals, memory, connectors, office, modules); `src/components/`
(ApprovalCard locks after deciding and shows the content hash the
decision binds to, Nav, WorkStrip); 7 live screens. Server wiring
in `puspa/pwa/app.py`: serves the built SPA from `apps/pwa/dist`
with a server bootstrap (`window.__PUSPA_BOOTSTRAP__`, JSON
escaped against script breakout) and falls back to the Jinja
templates when `dist` has not been built; no API route or auth
behaviour changed. Also fixes the login fallback, which rendered
`masuk.html` — a template that never existed (the real one is
`login.html`). Docs: `docs/pwa.md` (architecture, hermes
checklist mapping, honest limits). New npm deps, all MIT/
Apache-2.0: react, react-dom, vite, typescript, vitest, jsdom,
@testing-library, openapi-typescript. `node_modules/` and `dist/`
are build outputs, excluded from the push (and from the ratchet);
a fresh checkout runs `npm install && npm run build` in
`apps/pwa/` first. `.gitignore` gains `node_modules/` + `dist/`
(coordinator fix — the worker did not add them). Verified by the
coordinator: `npm run build` green (tsc + vite, 1.86s), 37 vitest
tests pass (8 files), Python PWA tests 7 pass (`tests/test_pwa.py`,
app.py wiring intact). Honest limits (from `docs/pwa.md`): no read
endpoints for lists — the SPA hydrates from the server bootstrap
on each navigation, so connector grant state is session-local
(resets on reload), Tasks shows only tasks created in the
session, and memory confirm/correct/delete/export run through
chat until those endpoints land (follow-up: add the read
endpoints, then switch the screens to refresh from them); login
callback not final (`/auth/google/callback` still returns the
Fase 1 note; no live Google login verified — no client
credentials in scope); no live browser check (build + unit tests
+ server smoke only; a live render + click-through is still
open); chat messages do not yet carry per-number Bukti buttons
(evidence panel route unchanged); push needs a VAPID key (no
push round-trip verified live); no photo upload in the chat
composer yet (text only). Wave 11 central SOLO suite: **2,463
passed, 9 skipped, 1 warning, 15m01s, EXIT 0** — Wave 11 green
(same count as Wave 10: G1 added no new Python tests; the 7 PWA
tests were already in the 2,463).

## Wave 12: G13 (multi-tenant teams / hosting, DONE 18 Nov 2026)

### G13 Multi-tenant teams (commit ec86981f)

New package `puspa/teams/` (9 modules, 2,419 lines) + 6 test
files (1,455 lines), 112 passed (verified by the coordinator),
ruff check + format clean, mypy clean (9 source files).
`teams.py` (create/delete, owner-only role changes, last-owner
guard, idempotent re-add, self-leave, cascade delete);
`invitations.py` (hash-only tokens, 7-day TTL, single use,
idempotent accept, revoke; acceptance runs as the table owner,
like team creation); `visibility.py` (`can_see_thread` pure
function over a ThreadRef + `BotAccessGate` grants/requests,
decisions final, requests table is the audit trail);
`entitlements.py` (Ed25519-signed `v1.<payload>.<signature>`
tokens, canonical JSON, tiers from G2 + `admin`, `HomeGate.decide`
with plain deny reasons; Ed25519 — not G2's HMAC — because the
home must not hold the server secret); `moves.py`
(pending/in_progress/done/failed/cancelled, freeze derived from
move records, failed stays frozen and resumable, one move per bot
at a time); `deploy.py` (offline Dockerfile/compose parse + rule
validation); `schema.py` (applies `infra/schema_teams.sql`).
Schema: seven tables + RLS (teams, team_members, invitations,
bot_access_grants, bot_access_requests, entitlements,
workspace_moves). Deploy: `Dockerfile` (multi-stage, s6-overlay
PID 1, dashboard on 127.0.0.1 default, UID/GID remap, API server
off unless keyed) + `docker-compose.yml` (gateway + dashboard on
host 127.0.0.1 only, shared home volume). Docs: `docs/teams.md`
(model, RLS rules, deploy + live-validation steps). Coordinator
fixes during verification (worker code, not worker report): the
RLS policies subqueried `team_members` inside its own policies —
Postgres refuses that ("infinite recursion detected in policy") —
replaced with SECURITY DEFINER helpers `is_team_member` /
`is_team_owner` (locked search_path, schema-qualified tables),
used by every policy; `resume_move`/`begin_move` shared one
transition target (`in_progress`), so a pending move could be
"resumed" and a failed move "begun" — each method now enforces
its own source status; the non-owner UPDATE test expected
`InsufficientPrivilege`, but RLS on an invisible row returns 0
affected rows without raising (same rule as `tests/test_rls.py`)
— test now asserts rowcount 0 + unchanged name, and the unused
`pg_errors` import left behind was removed. First run: 6 failed,
106 passed; after fixes: 112 passed. Honest limits (from
`docs/teams.md` + the Dockerfile/compose headers): the Docker
files were NOT built or run here (the VM kernel rejects setns) —
parse + rule validation only; first live check must happen on a
VPS (`docker build` + `docker compose up` + real login). The move
transport (copying bot data between workspaces) is an injected
callable at the call site, not built here. No new ledger event
types for access decisions (core ledger stays at 31 types); the
gateway/teams HTTP wiring is a follow-up; two containers sharing
one home volume are not refused at open (G26 single-writer is
process-wide — the file-lock follow-up). Wave 12 central SOLO
suite: **2,575 passed, 9 skipped, 1 warning, 16m51s, EXIT 0** —
Wave 12 green (+112 from G13).

## Wave 13: G14 (Telegram/WhatsApp channels, DONE 18 Nov 2026)

### G14 Channels (commit 181a545e)

New package `puspa/channels/` (16 modules, 1,986 lines) + 4
test files (914 lines), 61 passed (verified by the coordinator),
ruff check + format clean, mypy clean (16 source files).
`run.py` (GatewayRunner facade, process-wide single-writer
registry, lazy adapters, webhook + long-poll paths, offset
restore/persist, drop-backlog option); `telegram.py` (raw Bot API
over pinned `httpx==0.28.1`, no new dependency —
python-telegram-bot is LGPL; `getUpdates` with offset as query
params, webhook secret verify, `sendMessage` bounded retry +
backoff); `whatsapp.py` (raw Cloud API over pinned httpx, webhook
handshake + HMAC signature); `authz.py` (allowlist + DM pairing:
6-digit codes, hash-only, 5-min TTL, 5 wrong → 10-min lock);
`inbound.py` (bot-loop guard, pairing command, dedup, silent drop
of unauthorised chats with a ledger record, agent then reply via
origin adapter); `ledger.py` (own `channel_deliveries` table, not
the core ledger — no new core event type); `delivery.py`,
`agent.py` (TaskRunner seam: one message = one Task with a
`respond` step), `notify.py` (bot name only, never message
content), `heartbeat.py`, `config.py` (tokens from env only,
never hardcoded), `schema.py`. Schema:
`infra/schema_channels.sql` (one table). Docs: `docs/channels.md`
+ `docs/adding-a-platform.md` (guide for a third adapter).
Coordinator fixes during verification (worker code, not worker
report): the Postgres ledger `record()` RETURNED only
id/created_at/updated_at but built the record from
`row["status"]` etc. — KeyError on every Postgres write (now
`RETURNING *`); `poll_once` sent the `getUpdates` offset in the
JSON body, but the conventional call (and the test) puts
offset/timeout in the query string — `_post` now takes query
params (plus a `ruff format` wrap on the longer signature). First
run: 5 failed, 56 passed; after fixes: 61 passed. Honest limits
(from `docs/channels.md`): NO live bot verified (no real token,
no real message sent; all HTTP in tests goes through
`httpx.MockTransport`). Groups are DM-only in this iteration
(group messages dropped). The production agent executor is NOT
wired yet — `TaskRunnerAgent` with no executor raises
`ChannelError` fail-closed (follow-up, named in `agent.py`:
wire the executor that calls the model via `puspa.chassis`).
Pairing bindings are in-memory (durable home is a follow-up with
its own migration). Channel bindings/session keys are not yet
wired to the PWA; the gateway is not yet connected to a live
scheduler run. Wave 13 central SOLO suite: **2,695 passed, 9
skipped, 1 warning, 17m43s, EXIT 0** (2,575 Wave 12 + 61 G14 +
59 G21, exact). Incident on the way there: the first suite run
was killed by a VM replacement (~21:00 WIB, SIGTERM, empty
log), and the re-run came back 18 failed + 13 errors — all
environmental, not code: the fresh VM's `NO_PROXY` carries
bracketed-IPv6 entries (`[::1]`, `[fd8b:...]`) and httpx raises
`InvalidURL: Invalid port: ':1]'` on plain `httpx.Client()`
construction (channels adapters, reranker fixture,
billing/integration paths). The failing subset re-run with
`NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1`
went 47/47 green, and the full suite with the same sanitised
variables went fully green. Rule from now on: run the central
suite (any pytest that builds an httpx client) with sanitised
NO_PROXY. Postgres was recovered the same way as before: the
system os-intent replay reinstalled postgresql-16 + pgvector on
its own, cluster started, `schema_core.sql` re-run (roles +
puspa_test + RLS on). A second VM replacement hit ~22:00 WIB
(cluster down on arrival, DB/roles wiped again); same
recovery, cluster started + `schema_core.sql` re-run.

## Wave 13: G21 (interruptible turn loop + layered fallback + budget, DONE 18 Nov 2026)

New package `puspa/turns/` (14 modules, 2,947 lines) +
`puspa/router/chains.py` (214 lines, new) + small router
changes + 4 test files (1,081 lines) + `docs/turn-loop.md`
(321 lines) + G21 sections in REUSE.md + NOTICE.md
(hermes-agent MIT pattern attribution, no code copied).
**59 tests passed** (verified by the coordinator), ruff check +
format clean, mypy clean (18 source files). Contents:
`loop.py` (TurnLoop: prepare -> overflow -> interruptible model
call -> tool calls -> checkpoint/recovery; statuses
completed/interrupted/budget_exceeded/failed, always with a
clear message; `make_task_executor` adapts it to a TaskRunner
step executor; subagent turns run under a narrowed budget);
`interrupt.py` (background daemon thread + result queue, main
thread polls in 50ms slices for result/interrupt/timeout; on
interrupt the API thread is abandoned and its reply discarded,
no partial response in history; the HTTP request itself cannot
be cancelled, only abandoned — named in the module docstring);
`messages.py` (one internal format for chat completions /
Responses / Anthropic Messages, converters both ways with
dropped/notes reports; alternation enforced in code with
deterministic merge repairs); `turn_api.py` (chain execution
through the existing `call_with_fallback`, live OpenRouter
adapter `make_openrouter_model_call`); `turn_api_error.py`
(refresh-first auth wrapper over the router's existing
TokenRefresher); `chains.py` (config-driven main + auxiliary
chains — vision, compression, title — each independent, built
on the 9Router 3-tier fallback, never duplicated; at most 3
refs per chain); `budget.py` (per-agent 500, per-subagent 50
narrowed by the parent's remaining iterations);
`intercepts.py` (todo/memory/session_search/delegate_task before
the registry, real stores: in-memory todo store,
`puspa.memory.facts` + hybrid recall, SessionGateway search,
SubagentPool spawn / injected delegate); `turn_overflow.py` +
`turn_truncate.py` (caps, system block + protected tail never
touched); `turn_prepare.py`, `turn_recover.py`, `config.py`,
`todos.py`. Router changes: `complete.py` (`ModelReply` gains
tool_calls + finish_reason; `complete_messages` message-list
entry point); `fallback.py` (auth failures — 401, 403 with an
auth signal — retryable, but only after a credential refresh
first; 403 with a quota signal stays quota exhaustion);
`__init__.py` re-exports. No new dependency. No nanobot lines
touched; `puspa/runtime/tasks.py` unchanged.
Coordinator fixes during verification (worker code, not worker
report): `validate_alternation` flagged assistant -> tool ->
assistant as "two assistant messages in a row" (it never
updated its previous-role tracking for tool messages, though
its own docstring says a tool run counts as part of the
assistant turn — now sets previous_role to "tool");
`InternalMessage.text` was "" for tool messages (their content
lives only in the tool_result block — `.text` now returns the
tool-result content, and `char_count` sums per block so the tool
content is not double-counted). First run: 2 failed, 57 passed;
after fixes: 59 passed. Honest limits (from
`docs/turn-loop.md`): the engine + `make_task_executor`
adapter are built and tested, but NO call site uses them yet —
TaskRunner still runs its old executor, so the swap-in at the
PWA / channels / scheduler construction sites is a follow-up
(the old inline path is still live; this is not a second
parallel loop in production, the new loop is simply unused
until wired). The todo store is in-memory (no durable todo
store yet). The overflow compressor is a seam (without a
compressor, overflow truncates). `session_search` /
`delegate_task` need a gateway / pool injected, else they
answer with a clear "not wired" error. The production model
executor via the chassis is still the follow-up named in
`puspa/channels/agent.py`; until it lands the default model
adapter is the router's live OpenRouter caller. Turn events
(`turn_started`, `model_called`, ...) are advisory, not part of
the 31 closed core ledger event types — a production Ledger
sink drops them silently (same as `world_prediction`); test
observation via `event_memory()`. Wave 13 central
SOLO suite: **2,695 passed, 9 skipped, 1 warning, 17m43s, EXIT
0** (2,575 Wave 12 + 61 G14 + 59 G21, exact).

