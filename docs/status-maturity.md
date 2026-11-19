# Status: repo maturity phase (Fase Kematangan)

Date: 18 Nov 2026. Coordinator: maturity phase (1af6c4c4), after the
deepening phase (main `68901c4a`, 1,321 passed, 5 skipped). This doc is
the living status log for the maturity phase. `docs/status-brain-deepening.md`
is frozen: the deepening phase is done, and that file sits in the
file-size ratchet baseline at 1,134 lines, so it is not appended to
anymore. Per-iteration commits: one item per commit, each pushed only
after a green SOLO full-suite run by the coordinator (workers do not
push; the coordinator runs the suite centrally and pushes, because
parallel suite runs collide on the shared `puspa_test` DB).

## Standing rules (inherited)

- One item = one iteration with its own commit + push after a SOLO
  green suite. Real code + tests + wiring per file, not empty
  scaffolds. Depth bar "SANGAT DALAM": core mechanism + flagship
  really exist, not a seam without a schedule.
- Patterns/design/architecture are taken and rewritten as Puspa code,
  NEVER copied verbatim. DuskBot PolyForm Noncommercial (study only).
  hermes-agent MIT (attribution when adapted very closely). OpenBot
  MIT, Intelligence separately licensed + enforcement server closed
  image (patterns from docs only). Deps MIT/Apache-2.0/BSD via pip;
  REUSE.md + NOTICE.md updated per iteration.
- NO Indonesian naming anywhere in the repo. No forks ever.
- LICENSE is Nero's decision: never pick it unilaterally (recorded
  pending in `docs/verification.md`). Billing model (credits vs
  subscription vs selling compute) is Nero's decision: build the
  mechanism, make the model a config, report it to the parent.
- Postgres 16.15 is fragile (VM replacements wipe packages/roles/
  DBs). Recovery: apt install postgresql-16 + postgresql-16-pgvector,
  pg_ctlcluster 16 main start, run infra/schema_core.sql; if apt is
  blocked by the broken cloudflare-warp package, dpkg --remove
  cloudflare-warp first. Check pg_lsclusters before the suite.
- Docker containers cannot run on this VM (kernel rejects setns).
  Venv build/.venv, use `.venv/bin/python -m pip`. NO_PROXY=
  localhost,127.0.0.1 for the GitHub API helper. Push via Git Data
  API (helper: `hidden_files/push.py`); git CLI has no credentials.

Older wave entries (Waves 1-8 and the CI lanes adoption) were archived
to `docs/status-maturity-archive-2026-11-18.md` on 18 Nov 2026, when this
file crossed the 2,000-line hard cap of the file-size ratchet. Waves 9-13
were archived to `docs/status-maturity-archive-2026-11-19.md` on 19 Nov
2026, when the live log passed the 1,000-line soft cap (1,060 lines
against a 1,055 baseline). Waves 14-15 were archived to
`docs/status-maturity-archive-2026-11-19-waves-14-15.md` on 19 Nov
2026, when the live log approached the soft cap again (975 lines).
History is never deleted, only archived.

## Wave 16: G31 (PWA serve modes, DONE 19 Nov 2026)

Worker 123df2a2. New files `puspa/pwa/serve_modes.py` (321
lines) + `tests/test_pwa_serve_modes.py` (366 lines, 28 tests)
+ `docs/serve-modes.md` (162 lines) +
`apps/pwa/src/state/operator.ts` (65 lines) +
`apps/pwa/src/components/OperatorPanel.tsx` (106 lines) + 3
new test files (`operator.test.ts` 141,
`OperatorPanel.test.tsx` 116, `OfficeScreen.test.tsx` 88).
Changed: `puspa/pwa/app.py` (two G31 blocks),
`puspa/hosting/pairing.py` (`+status_snapshot`),
`puspa/policy/gateway.py` (`+policy_summary`),
`docs/openapi-pwa.yaml` (+ `GET /api/operator/status` + 8
schemas), `apps/pwa/src/screens/OfficeScreen.tsx` (fetch on
mount + 5s poll), `apps/pwa/src/api/client.ts`
(`fetchOperatorStatus` + shared `getJson`),
`apps/pwa/src/api/client.test.ts` (+2 tests),
`generated-types.ts` (regenerated), REUSE.md + NOTICE.md
G31 (no source repo: Nero's product decision, not a repo
pattern). **Verified by the coordinator:** 28 Python tests +
neighbours (`test_pwa.py` 7, hosting pairing, gateway) in the
193-test wave batch; vitest **76 passed, 14 files** (G31
files: 21 tests — operator 11, OperatorPanel 7, OfficeScreen
3 — plus 2 in `client.test.ts`; the worker's "34 new" claim
was measured mid-parallel-work and does not reconcile —
verified count is 23); `npm run build` green (258 kB); ruff
check + format clean (whole tree: 539 files); mypy clean;
real `modern-web-guidance` legacy scan 0 findings on
templates/src/index/static. Contents: `resolve_serve_mode`
(env → JSON file → default `local`; an explicitly invalid
value raises `ServeModeError` and the server refuses to
start); `operator_panels_visible` (local only, fail-closed);
`GatewayDecisionLog` (bounded 50, wired as the gateway's
audit sink); `pairing_status` / `gateway_status` /
`operator_status` read from the live objects (codes and
session tokens never exposed); endpoint `GET
/api/operator/status` (login required; hosted mode returns
`operator_panels: false` + null panels). Honest limits (from
`docs/serve-modes.md`): the gateway panel is idle until a
real action flows through `gateway.decide()` (the PWA takes
no computer actions); the pairing store is in-memory and
process-local; panels poll every 5s, not push (merging
operator events into SSE is a follow-up); hosted mode only
hides the panels — end-to-end hosted verification needs the
cloud backend, not built yet. Shared-file note (whole wave):
`app.py` + `openapi-pwa.yaml` + `generated-types.ts` +
`client.ts` + REUSE.md + NOTICE.md are shared by G31/G32/G33
(parallel wave, one disk); they ride with the G31 commit
(first push) and the G32/G33 commits carry only their own
files.

## Wave 16: G32 (WebUI lite as a real mode, DONE 19 Nov 2026)

Worker 32a96b94. New files `puspa/pwa/lite.py` (277 lines) +
`apps/pwa/templates/trial.html` (39 lines) +
`tests/test_webui_lite.py` (453 lines, 37 tests) +
`docs/webui-lite.md` (157 lines). Changed: `puspa/pwa/app.py`
(G32 block: `GET /trial`, `POST /trial/start`, `POST
/trial/chat`), 7 templates fixed against the real app (base
nav `/tugas` etc. → real routes; chat `p.dari`/`teks` →
`p.sender`/`text`; connectors `agent`/`agen_id`/`aksi` →
`agents`/`agent_id`/`action`; memory fact dict → `key: value
(status)`; office `office.agent`/`task_terakhir`/`sub_agent`/
`handoff`/`event_type` → `agents`/`last_task`/`subagents`/
`handoffs`/`type`; tasks `tujuan`/`setuju` →
`purpose`/`approve`), `evidence_panel.html` renamed to
`panel_evidence.html` (the route renders the new name; the
old file was deleted from GitHub in a follow-up delete commit
after the G32 push), `docs/GLOSSARY.md` (2 lines), REUSE.md +
NOTICE.md G32 (no source repo: Nero's product decision).
**Verified by the coordinator:** 37 Python tests re-run with
conftest; ruff check + format clean; mypy clean; real
`modern-web-guidance` legacy scan 0 findings on the owned
web files (the old hand-rolled scanner was replaced by the
real tool per Nero 19 Nov 2026; the one ERROR in
`dist/assets/*.js` is React DOM's own legacy-IE `attachEvent`
fallback inside the Vite bundle, not Puspa code — `dist/` is
gitignored build output, fix would be a React bump, triaged
in `docs/webui-lite.md`). Contents: trial = G2 HMAC
entitlement (Pro, `trial_days` 14), httpOnly cookie,
verified against the server secret on every request,
fail-closed with no secret; start reuses an active/grace
trial and refuses when an expired token is still presented
(one trial per user as far as the cookie proves); the trial
page is Jinja-only even when `dist` exists. Honest limits
(from `docs/webui-lite.md`): no server-side trial ledger
(deleting the cookie can start a new trial); the approval
queue is never populated (nothing writes
`app.state.approval`); htmx comes from the unpkg CDN (only
the trial page works with plain HTML, no JS); trial chat is
not a live LLM (demo `/api/chat` storage, message stored and
shown back).

## Wave 16: G33 (own-model selection in PWA Settings, DONE 19 Nov 2026)

Worker 1c4f8692. New files `puspa/pwa/model_settings.py`
(432 lines) + `tests/test_pwa_model_settings.py` (305 lines,
30 tests) + `tests/test_pwa_model_settings_api.py` (89 lines,
6 tests) + `apps/pwa/src/state/modelSettings.ts` (94 lines)
+ 3 new test files (`modelSettings.test.ts` 76/7 tests,
`modelSettings` API test 86/5, `SettingsScreen.test.tsx`
86/4) + `docs/model-selection.md` (96 lines). Changed:
`puspa/pwa/app.py` (G33 block: `GET`/`POST
/api/model-settings`, `POST`/`DELETE
/api/model-settings/byok-key`), `puspa/router/complete.py`
(+ optional `provider` arg on `complete_messages` /
`complete_detailed` / `complete`, backward-compatible),
`apps/pwa/src/screens/SettingsScreen.tsx` (Model card),
`apps/pwa/src/api/client.ts` (get/save/delete BYOK),
`openapi-pwa.yaml` + `generated-types.ts`, REUSE.md +
NOTICE.md G33 (no source repo: Nero's product decision).
**Verified by the coordinator:** 36 Python tests + router
complete tests in the 193-test wave batch; vitest **76
passed, 14 files** (G33 files: 16 tests); `npm run build`
green; ruff check + format clean; mypy clean; real legacy
scan 0 findings. Contents: three sources (hosted default /
BYOK vault-only / local Ollama default
`http://localhost:11434/v1`); `model_settings` table (one
row per user, no key column); `save_selection` refuses any
payload carrying a key field; `validate_base_url` (http(s)
only, host required, no userinfo); `save_byok_key` /
`has_byok_key` / `delete_byok_key` through the existing
`puspa.accounts.vault` (`byok:<provider>`);
`resolve_completion_kwargs` → router kwargs, fake-transport
proven (local → `127.0.0.1:11434` with `Bearer ollama` +
`provider` ollama; BYOK OpenAI → `api.openai.com` with the
vault key; hosted → default OpenRouter, unchanged);
`complete_with_selection` + `llm_complete_for_user`
(resolves the selection on every call). Honest limits (from
`docs/model-selection.md`): a local model needs real
hardware (BYOK is the realistic path for most users); Puspa
does not pre-check that the local server runs or the model
is pulled (fails at first completion as `CompletionError`);
BYOK providers outside the known-endpoint map need their
base URL typed in; selection is per user, not per role/task;
no live BYOK/Ollama call proven (fake transport + dummy keys
only). Wave 16 central SOLO suite: **2,980 passed, 9 skipped, 1
warning, 18m43s, EXIT 0** (2,879 Wave 15 + 101 new Wave 16 tests,
exact; run SOLO 19 Nov 2026 by the continuation coordinator, log
`hidden_files/suite-wave16.log`). Postgres had been wiped by a VM
replacement, so the run was preceded by a full restore from
scratch (apt reinstall postgresql-16 + postgresql-16-pgvector,
`pg_ctlcluster 16 main start`, `infra/schema_core.sql`).

## New items: G29 + G30 (approved by Nero 18 Nov 2026, queued after Wave 14)

Nero approved two more maturity items (18 Nov 2026 ~22:08
WIB). They run as the next wave AFTER Wave 14 (G4+G5+G6)
finishes, then the remaining G/K list continues as before
(G28, G16, G18, G17, Kosmos K1, K3, K4, K5, K6, K10, K11,
K12, K13). Each item gets its own commit + push, same standard
(real code + green SOLO tests + wiring, rewritten as Puspa
code, never verbatim).

G29 — Code graph extraction, Graphify pattern
(Graphify-Labs/graphify, Apache-2.0). Parse code structure
into a graph (entities: functions, classes, files, SQL tables,
config; edges: imports and calls), built from static analysis
+ LLM triple extraction. Scope: extend
`puspa/retrieval/knowledge_graph.py` with a code parser
(Python AST first: functions, classes, imports, calls) that
inserts entities/edges into the existing KG; wire
JsonTripleExtractor to a real completion function via the
`router/complete.py` seam so LLM extraction finally goes live
(not just in tests); queries like "which functions call X"
must be answerable from the graph without re-reading files.
Out of scope: PDF/document ingestion (the document topic is
dead), verbatim Graphify code. Tests: extraction from a sample
Python file, multi-hop queries, incremental updates.

G30 — Skill discovery, vercel-labs find-skills pattern
(vercel-labs/skills, MIT; skills/find-skills/SKILL.md +
src/find.ts + registry skills.sh). Search via the skills.sh
API (GET https://skills.sh/api/search?q=...), check the
install leaderboard first, fallback drops one query word when
exact results are under 10 then merges (max 25), quality
rubric (prefer 1K+ installs, official sources trusted, repos
under 100 stars suspect), show choices to the user, install
via the G3 marketplace path (sha256 + license allowlist
MIT/Apache/BSD + import-disabled-default), install REQUIRES
user approval first (G4). Scope: Python client for skills.sh
search + ranking in `puspa/marketplace/` (or
`puspa/skills/discovery.py`), plus one built-in find-skills
skill (SKILL.md) active when the user asks "how to do X",
connected to the marketplace install path and the G24 curator;
catalog cached locally, never fully dependent on skills.sh at
runtime. Out of scope: porting the Node CLI, verbatim copy,
auto-install without approval. Tests: search parsing +
fallback merge, ranking rubric, hash/license gate on the
install path, approval gate.

## New items: G31 + G32 + G33 (modes decision locked by Nero 18 Nov 2026, queued after Wave 15)

Nero locked the midpoint on Puspa modes (discussion 18 Nov 2026
~23:00 WIB; decision doc `docs/modes-decision.md`). The settled
middle ground: ONE web codebase (PWA `apps/pwa`), TWO serve
modes (local for the operator, hosted for the end user). Never
two separate frontends. TUI is DEFERRED — no ticket (the nanobot
chassis already has a TUI underneath as an emergency path;
Puspa's own TUI would be a third frontend for the smallest
audience; revisit only if users ask). Three new tickets, as the
next wave AFTER Wave 15 (G29+G30) finishes, then the remaining
G/K list continues as before (G28, G16, G18, G17, Kosmos K1, K3,
K4, K5, K6, K10, K11, K12, K13). Each item gets its own commit
+ push, same standard (real code + green SOLO tests + wiring,
rewritten as Puspa code, never verbatim).

G31 — PWA serve modes (local operator vs hosted). Serve-mode
config; operator panels in the PWA: pairing status, gateway
status, live logs (extend the Office screen, which already has
live SSE projections, or panels in Settings). Same codebase,
both modes. Tests: mode config, panels render from live state.
Already built: PWA (G1, 7 screens + login); operator panels NOT
built yet.

G32 — WebUI lite as a real mode. Verify all 9 Jinja templates
in `apps/pwa/templates/` render and work with no React build;
trial entry page (one chat page + upgrade button); no-install
trial flow against trial entitlements (G2). Tests: render
without `dist`, trial flow. Already there: 9 templates exist as
a fallback, NOT yet verified as a real lite mode. Trial and
subscription are entitlements, not a UI — the lite mode is the
lightest zero-install way to reach the trial, not a second
product.

G33 — Own-model selection in PWA Settings. Let the user pick
the model provider: BYOK key (stored in the vault) vs local
model base URL (Ollama default `localhost:11434`) vs hosted
default. Wire the choice through to the router's `llm_complete`
path (the router already accepts any OpenAI-compatible base
URL). Tests: selection persists, router uses the selected base
URL/key. Honest limit from the decision: a local model needs
real hardware; for most users BYOK is the realistic path, local
is for users with a GPU or patience for CPU. Already verified:
CLI exists (build/cli, 31 tests, live); BYOK demoed live via
OpenRouter.

Note: the PWA is public (commit be76d3e3), holds no secrets;
the hosted backend it talks to is private (puspa-cloud, not
built yet) — hosted serving is cloud work, not in this repo.

## Wave 17: G28 (onboarding hints + cost/token insights page, DONE 19 Nov 2026)

G28 closes the last gap of the original 28: first-run onboarding and
cost/token visibility. Built by two workers, verified centrally by the
coordinator (full suite SOLO, vitest, build, ruff, mypy all re-run by
the coordinator, not taken from worker claims).

What was built (implementation worker 9916a221, 20 new files, 3,031 lines):
- `puspa/onboarding/hints.py` — 5 contextual hints (first visit, first
  task, approval waiting, memory unused, cost check), due-hint
  selection from branch facts, Postgres seen-flags table
  `onboarding_hint_seen` (one row per user per hint, server-enforced).
- `puspa/billing/insights.py` — pure `insights_summary` over the G2
  usage ledger plus a thin Postgres layer: exact IDR labels, 4-decimal
  per-1k-token rate, null rate when zero tokens, per-model / per-driver
  / per-tool / per-module breakdowns, top costly tasks, per-day
  activity. No USD estimates anywhere.
- `puspa/pwa/insights_api.py` + `onboarding_api.py` — `GET /api/insights`
  (7/30/90-day windows) and the 4-step onboarding flow
  (name+style, notification test, connector choice, guided first task)
  plus hints read/seen endpoints, all behind `need_user` (401 when
  not logged in).
- PWA: `InsightsScreen` (exact Rupiah tables, honest zeros when no
  turns recorded) + `OnboardingScreen` (4 steps, saved server-side
  per step) + state clients + OpenAPI contract + generated types.

Wiring (worker f2e473ce, verified by the coordinator file by file):
- `puspa/pwa/app.py`: SCREENS grows 7 -> 9 (`insights`,
  `onboarding`); both APIs registered after the G33 block.
  The module docstring records honestly that the revisi-7 7-screen
  cap no longer holds after the maturity additions — the nav grows
  to 9 because Nero's maturity order requires the G28 insights page
  and first-run onboarding.
- `bootstrap.ts` ScreenName/SCREENS, `App.tsx` renders both screens
  plus a shell-level `HintBanner`, `main.tsx` first-run redirect
  (GET /api/onboarding; not started -> /onboarding; fail-open on
  error/401, login never blocked).
- `HintBanner` (new component + 5 vitest tests): at most the first
  due hint, POSTs seen on dismiss, polite live region with an
  accessible name, focusable dismiss button that never steals focus
  (modern_web_guidance `persistent-toast-notifications` +
  `accessibility` guides). Also rendered inside InsightsScreen with
  real billing numbers; the two sites can only surface different
  hints. `first_visit` is client-reported via localStorage; the
  server seen-flags table is the once-per-user enforcement.
- Jinja fallback templates `insights.html` + `onboarding.html`;
  `base.html` nav gains both links. OpenAPI spec gains the G28
  endpoints + schemas (spec 1097 -> 1707 lines, re-baselined in the
  ratchet test with a reason); generated types regenerated with the
  real tool (`npm run generate`, openapi-typescript), not hand-edited.
- Single JSON helper: `getJson`/`postJson` now live in
  `api/client.ts`; `state/http.ts` deleted, its 3 tests moved into
  `api/client.test.ts` (coverage kept, not dropped). The 2 API test
  files now build the app with `create_app(test_user_id=...,
  dsn=test_dsn)`; `tests/test_pwa.py` asserts 9 screens plus a new
  `test_no_tenth_screen_exists`.

Two bugs found by the coordinator's own verification and fixed
before the push (the implementation worker's own suite run was
green; the coordinator re-ran and caught these):
1. Real code bug in `insights.py`: the per-day window was off by
   one (built days from `now - 7d` timestamp instead of the last 7
   calendar days ending today). Fixed to day-aligned
   `first_day = now.date() - (days - 1)`.
2. Test fixture bug in `test_billing_insights.py`: a 4th row
   inherited the cheap model/module and polluted totals. Fixed the
   fixture, corrected the share expectation (2000/3600 -> 2000/3500)
   and added a full `module_usage` list assertion.

Suite (coordinator, SOLO, 19 Nov 2026): 3,027 passed, 9 skipped,
1 failed in 19m57s — the single failure was the file-size ratchet
on this doc (1,060 lines vs the 1,055 baseline, +5 from the Wave 16
suite note). Fixed the honest way per the doc's own rule: Waves
9-13 archived to `docs/status-maturity-archive-2026-11-19.md`
(~570 lines, under the soft cap, no baseline entry needed); the
live log drops back under 1,000 lines and its baseline entry is
removed. Ratchet test re-run after the archive: all 3 tests pass.
Reconciliation: 2,980 (Wave 16) + 44 (G28 implementation) + 2
(route-existence tests) + 1 (`test_no_tenth_screen_exists`) = 3,027.
Vitest (coordinator re-run): 19 files, 103 passed. `npm run build`:
green (274 kB JS). ruff check + format: clean. mypy on the changed
Python files: clean.

Honest limits (shown in the open, not hidden):
- Step 2 notification test records NOT sent: Web Push is not built
  yet; the screen says so.
- Step 4 first task is a measured stub (the price-compare / bill
  wedges are not built yet); its result is reported as unverified,
  and the screen says so.
- No real usage has flowed through the new insights API yet beyond
  tests; the first real numbers appear after real turns are recorded.
- `dist/` was rebuilt by `npm run build` (fresh bundle contains the
  new screens) and is gitignored, not pushed.

Remaining: G16 (computer use, principles first) -> G18 (i18n) ->
G17 (skill narrowing + search_tools) -> Kosmos K1, K3, K4, K5, K6,
K10, K11, K12, K13. Then uji + 15 gates + 10 reference tasks
(waiting on Nero's decisions).

## Wave 18: G16 (computer use / per-bot sandbox, principles first, DONE 19 Nov 2026)

G16 is BIG and deferred until after the MVP: no computer use /
live browser automation was built. What was built is the
principles plus real, tested seams (worker 1579b6a4, verified
centrally by the coordinator).

What was built (10 new files, `puspa/computer/`, all under 1000
lines; tests 857 lines; docs 143 lines):
- `backends.py` — ExecutionBackend ABC + CommandResult +
  name→variant registry (no silent fallback on unknown names);
  `local.py` — local backend (subprocess, scrubbed env,
  scope-enforced cwd, credential-looking env keys refused
  outright; test/dev only, NOT a security boundary);
  `docker.py` — docker backend wrapping the hardened
  puspa.sandbox (`--network none`, read-only, `--cap-drop ALL`);
  `scope.py` — SandboxScope reusing the harness write-path
  profile rules; `sync.py` — FileSync ABC (LocalFileSync /
  NoFileSync); `config.py` — ComputerConfig (dict/env, default
  local, unknown backend refused); `errors.py` —
  BackendError / BackendUnavailable / BrowserUnavailable /
  BrowserSessionBusy / UnknownToken / TokenExpired / TokenRevoked.
- `broker.py` — CredentialBroker ABC + BrokerToken + in-memory
  TEST-ONLY broker: credential never in env/log/repr/error
  (fingerprints only), child gets only PUSPA_BROKER_TOKEN,
  tokens die on expiry / revoke / context-exit / close() /
  process exit (atexit). Reuses the G22 pool conceptually,
  does not duplicate it. No real broker service (post-MVP
  follow-up).
- `browser.py` — INTERFACE ONLY: BrowserSession (per-task),
  profile_dir_for (one profile per agent, agent id validated
  so a crafted id cannot escape the profiles base),
  TakeoverRequest (sign-in / captcha / 2FA — no password field
  anywhere by design), SessionRegistry (one live session per
  agent, orphan reaping by heartbeat), BrowserLauncher ABC,
  UnavailableBrowserLauncher (raises BrowserUnavailable naming
  the Docker-host + human-takeover-UI dependency). No CDP
  driving, no Chromium, no live browser.
- Harness wiring (3 modified files): `BackendExecutor` in
  puspa/harness/executor.py (production_safe only for docker,
  test_only for local) + `backend_executor()` factory; loop.py
  production gate accepts SandboxExecutor or docker-backed
  BackendExecutor, main attempt loop catches BackendUnavailable
  exactly like SandboxUnavailable (fail-closed →
  sandbox_unavailable, no host fallback).
- `docs/computer-use.md` — the 4 principles, backend table,
  conformance rules, broker rules, browser-session contract,
  honest limits, post-MVP follow-up schedule (Docker host/VPS
  first → real broker service → human-takeover UI → launcher
  + CDP).

Coordinator fix (pre-existing gap found by the worker, fixed by
the coordinator before the push): the reproduce-first starter
probe (loop.py) did not catch executor exceptions, so a dead
backend during that probe raised out of harness.run instead of
stopping fail-closed — already true for
SandboxExecutor/SandboxUnavailable before G16. Wrapped in the
same (SandboxUnavailable, BackendUnavailable) catch as the main
attempt loop, plus a test
(test_docker_backend_unavailable_in_reproduce_probe_stops_loop:
docker-backed executor + reproduce_first task →
sandbox_unavailable, generator never called, no host probe file).

Suite (coordinator, SOLO, 19 Nov 2026): 3,094 passed, 23 skipped,
1 warning, 19m24s, EXIT 0 (log hidden_files/suite-wave18-g16.log).
Reconciliation: 3,028 (Wave 17 post-ratchet-fix baseline) + 66
(G16 module tests incl. the coordinator's probe test) = 3,094;
9 + 14 (Docker conformance skips) = 23. Module tests
(coordinator re-run): 66 passed, 14 skipped. Harness/sandbox
regression files (coordinator re-run): 59 passed, 4 skipped
(the worker's "124 passed, 18 skipped" was the combined total
of both runs: 65+59 passed, 14+4 skipped, before the
coordinator's +1 probe test). Ruff check + format: clean. Mypy
on the changed files: clean.

Honest limits: docker backend NEVER live-tested on this host
(check_available() False, every run raises BackendUnavailable —
nothing faked); broker is interface + in-memory test broker
only; browser is contract + registry only. First live check
needs a Docker host/VPS, after the MVP.

Remaining: G18 (i18n) -> G17 (skill narrowing + search_tools) ->
Kosmos K1, K3, K4, K5, K6, K10, K11, K12, K13. Then uji + 15 gates
+ 10 reference tasks (waiting on Nero's decisions).

## Wave 19: G18 (i18n — Indonesian server-lines first, DONE 19 Nov 2026)

G18 is medium and deferred: full i18n is global-expansion work.
What was built now is the boilerplate the report ordered —
Indonesian `server-lines` from the start, with the locale
structure ready for more languages later (worker 49bd61c0,
verified centrally by the coordinator). No whole-UI translation,
no 17 locales.

What was built (new package `puspa/i18n/`, all files under 1000
lines; tests 362 lines; docs 137 lines):
- `server_lines.py` — `render(key, locale="id", **vars)` with
  named placeholders only. Fail-closed: unknown key raises
  UnknownMessageKey, missing variable raises MissingVariable
  naming the variable, unknown locale falls back to Indonesian
  (the default) and records the fallback in fallback_records()
  — never crashes, never silently serves English. There is no
  English catalog at all, so there is nothing English to fall
  back to. The default catalog loads at import, so a broken
  `id.json` fails startup, not the first user.
- `locales.py` — JSON locale loader + validation: every key in
  the default locale must exist in every other locale, no
  orphan keys; missing default locale raises. With only `id`
  present the check passes trivially and guards future
  additions. No source-hash check (DuskBot's 8 MB file) —
  documented as the honest minimum for one locale.
- `locales/id.json` — 36 keys: 21 `error.*` + 15 `hint.*`
  (5 hints x title/body/action_label).
- `docs/i18n.md` — locale model, server-lines rule, how to add
  a language later (deferred, 5 steps), wired/unwired lists,
  honest limits.

What was wired (audit first, then wired; call sites import
`render as render_server_line`):
- PWA API — the 401 `{"error": "belum masuk"}` at 17 sites
  (app.py x15, insights_api.py x1, onboarding_api.py x1), the
  503 Google-login-not-configured error, and the 404
  `{"error": "permintaan tidak dikenal"}` on
  `POST /api/persetujuan/{request_id}`. Wire responses are
  byte-identical to before.
- Onboarding hints (`puspa/onboarding/hints.py`) — all 5 hints
  now render Indonesian copy from server-lines (15 call sites);
  this was the one place the server wrote English to users.
  Old copy ("Modules add abilities", "Puspa asks before it
  acts", ...) is gone from `puspa/*.py` (0 occurrences left).
  The costly-task body takes the ceiling as a named variable
  (`{ceiling}` -> `Rp2.000` from `TASK_COST_CEILING_IDR`).
- Approval gate (`puspa/policy/gate.py`) — all 21 Indonesian
  GateDenied messages now come from server-lines (21 call
  sites), byte-identical. One real fix along the way: the old
  string repeated a word ("tidak ada penegak batas belanja
  **belanja**"); the catalog carries the corrected sentence,
  covered by a negative regression test in
  tests/test_i18n_wiring.py. No existing test asserted the old
  typo'd string, so nothing else changed.

Deliberately left unwired (documented in `docs/i18n.md`):
internal logs and English developer messages
(`puspa/accounts/`, `puspa/auth/`, `puspa/billing/`, ...) and
the `str(exc)` 400 responses that surface them; `approvals.py`
`decide()` reasons and the gate's "action content changed
after approval" message (English developer text, asserted
verbatim by tests/test_approvals_taxonomy.py); the trial error
query param (an error code, not a sentence); the PWA client
(TypeScript) and Jinja templates (server sentences only in
this pass).

Suite (coordinator, SOLO, 19 Nov 2026): 3,116 passed, 23 skipped,
1 warning, 18m50s, EXIT 0 (log hidden_files/suite-wave19-g18.log).
Reconciliation: 3,094 (Wave 18) + 22 (G18 module tests) = 3,116;
skipped unchanged at 23. (A first attempt was SIGTERM'd at 61%
by a VM replacement that also wiped Postgres; the run above is
the clean re-run after recovery: replay reinstalled
postgresql-16 16.15 + pgvector 0.6.0, cluster restarted, role
root recreated, infra/schema_core.sql re-run, RLS re-verified.)
Module tests (coordinator re-run):
22 passed. Neighbor modules (coordinator re-run): 166 passed
(onboarding hints, PWA insights/onboarding APIs, test_pwa,
gate, approvals taxonomy, WebUI lite, model settings API,
ratchet). Ruff check + format: clean. Mypy on the changed
files: clean.

Honest limits: one locale only; nothing in the request path
selects a locale yet (every call site relies on the `"id"`
default). No pluralisation / gender / date-number rules; no
source-hash check (cannot catch a stale translation until a
second locale exists). `str(exc)` 400 responses still surface
module error text verbatim. No full i18n / no 17 locales, per
the G18 "ditunda" scope — other locales are deferred to global
expansion.

Remaining: G17 (skill narrowing + search_tools) -> Kosmos K1,
K3, K4, K5, K6, K10, K11, K12, K13. Then uji + 15 gates + 10
reference tasks (waiting on Nero's decisions).

## Wave 20: G17 (skill narrowing + search_tools, DONE 19 Nov 2026)

G17 is the LAST of the original 28 gaps: per-run narrowing and
`search_tools`. Built on top of the existing G23 registry (worker
20103db5, verified centrally by the coordinator).

What was built (new module `puspa/tools/narrowing.py`, 644 lines;
tests 572 lines; docs 236 lines):
- `prepare_run_tools(message, catalog, skills, granted, selector,
  config, audit_sink)`: refusal check first (catalog + granted
  > 128 -> audit + raise), then skill selection, then the ~10
  offered cap (tools ranked by message<->name/description token
  overlap, ties by name), granted tools always kept, overflow
  past 50 behind `search_tools`.
- Default selector `KeywordSelector`: deterministic trigger-term
  (2 pts) + description-token (1 pt) overlap, no LLM call, no
  cost. The selector is a pluggable `ToolSelector` protocol
  (`select(message, skills)`); an LLM-backed selector is NOT
  built — the seam (implement the protocol with the router's
  `llm_complete` callable, fail to `None` so it falls back to
  keywords then fail-open) is documented in
  `docs/skill-narrowing.md`.
- Real `search_tools` tool: registered via
  `register_search_tools()` (idempotent) and by
  `register_builtin_tools`, so discovery picks it up. The
  handler searches the run's hidden tools when bound via a
  `ContextVar` (`hidden_tools_context`, same pattern as the
  token binding in `puspa/chassis/context.py`), otherwise the
  registry it was registered on. Results ranked by keyword
  overlap, capped (default 10, max 50), each carrying name +
  description + parameters. Pure function `search_tools(query,
  tools)` is directly testable.
- Refusal audit goes through the G4/G5/G6 ledger-shape sink
  `sink(event_type, content)` — no new audit path.

Wiring:
- `puspa/turns/config.py`: `TurnConfig` gains
  `max_direct_tools=50`, `max_tools=128`,
  `max_offered_tools=10`, `search_result_limit=10` with
  validation (mirrors narrowing defaults as literals so config
  never imports the tools package).
- `puspa/turns/loop.py`: `TurnLoop(tool_catalog=...,
  narrowing_skills=..., narrowing_selector=...)`; `run_turn`
  narrows on the last user message when a catalog is wired and
  no explicit `tools=` passed (explicit tools win), binds
  hidden tools for the whole turn, and returns a failed
  `TurnResult` naming the limit before any model call when
  refused. `run_turn`'s body moved verbatim into
  `_run_turn_inner`; `run_subagent_turn` inherits narrowing via
  delegation.
- One test expectation updated honestly: `test_discover_imports_
  tool_modules` expected exactly 2 discovered tools; discovery
  now legitimately registers `search_tools`, so the assertion
  is 3 + `search_tools` present.

Coordinator decision — refusal audit vs the ledger's closed 31
(the worker flagged this for a decision): NO ledger change.
The worker's concern was that `agent.tool_limit_refused` is
not one of the 31 revisi-7 event types, so a raw Ledger sink
(best-effort `suppress(Exception)` in `make_ledger_sink`)
swallows it. Verified by the coordinator: that is exactly how
EVERY turn-loop event behaves — `turn_failed`,
`turn_completed`, `narrowing_selected` and the rest are also
outside the 31, kept in the turn sink (in-memory list by
default), and swallowed by a ledger sink the same way. The
refusal is recorded twice in the turn sink (the refusal event
itself, then the loop's `turn_failed` carrying the message),
exactly like any other turn failure. Singling the refusal out
for a 32nd type — or shoehorning it into `task_failed` — would
be inconsistent with the whole turn-loop vocabulary, and the
G6 precedent is explicit: the closed set stays untouched.
The same gap exists in the G3 marketplace narrowing (same
event name, same sink shape); it is a shared known limit,
documented in `docs/skill-narrowing.md`, not a G17 regression.

Suite (coordinator, SOLO, 19 Nov 2026): 3,149 passed, 23 skipped,
1 warning, 20m48s, EXIT 0 (log hidden_files/suite-wave20-g17.log).
Reconciliation: 3,116 (Wave 19) + 33 (G17 module tests) = 3,149;
skipped unchanged at 23. Module tests (coordinator re-run): 33 passed
(test_narrowing.py 24 + test_narrowing_turn.py 9, incl. a real
run where the fake model calls `search_tools` mid-turn and
reaches a hidden tool). Worker's exact command
(test_narrowing*.py + test_tools*.py): 48 passed. Neighbor
regression (coordinator re-run): 191 passed (registry,
approval, check, footprint, result-compression, turn loop,
turn intercepts, marketplace narrowing — the worker's "239"
was the combined total of both runs: 48 + 191). Ruff check +
format: clean. Mypy on the changed files: clean.

Two bugs found by the worker's own tests and fixed in code
(not papered over): narrowed-away tools were never added to
`hidden` when the cap didn't trigger, and the `search_tools`
handler always searched the default registry instead of the
registry it was registered on.

Honest limits: a wrong-but-confident narrowing hides tools
(the keyword selector has no synonyms, no stemming, no
understanding; `search_tools` is the recovery path and only
helps if the model calls it). Narrowing runs once per turn on
the last user message; a mid-turn topic change does not
re-narrow. With no skills registered, narrowing is a no-op
fail-open (overflow/refusal rules still apply). 50/128 are
OpenBot's starting points, not numbers measured on Puspa's
models. Intercepted tools (`todo`, `memory`, `session_search`,
`delegate_task`) are not registry tools and are not counted
toward the limits.

All 28 gaps G1-G28 are now DONE. Remaining (order locked by Nero
19 Nov 2026 ~05:41 WIB): Kosmos K1, K3, K4, K5, K6, K10, K11, K12,
K13, then B features (B6 group chat, B9 tasks, B10 meetings, B11
patungan, B12 joint shopping, B13 drafts), then B15 culture LAST
(the BUDAYA GO! 2026 batik demo order from ~05:37 WIB was
cancelled by Nero before any work started). Then uji + 15 gates
+ 10 reference tasks (waiting on Nero's decisions).

## Wave 21: Kosmos K1 (Intent Engine, DONE 19 Nov 2026)

First Kosmos item after all 28 G gaps. K1 builds the intent
SIGNAL (kosmos-design.md section B); the act-vs-ask RULE itself
is K2, already folded into G4/G5 — K1 adds what the rule needs
from intent, and the ask always goes through the existing G4
approval mechanism. No second approval system was built
(worker 5b70678c, verified centrally by the coordinator).

What was built (new package `puspa/intent/`, all files under
1000 lines; tests 786 lines; docs 222 lines):
- `distribution.py` — `IntentDistribution` with the three
  levels (desire, goal, values), each a scored set of
  `IntentHypothesis` values normalized to sum to 1 per level.
  Empty levels are allowed and recorded as empty, never
  invented. Unnormalized direct construction raises
  ValueError. Conflict = top-two weights within 0.15.
- `inference.py` — default deterministic inferrer: keyword /
  topic coverage against the user's known goals/values from
  the profile (only intent-bearing keys: `goal*` -> goal,
  `value*` / `principle*` -> values, `want*` / `need*` /
  `desire*` / `preference*` -> desire; identity facts like
  name/address ignored) plus world beliefs (unpaid bills,
  known prices -> desire; repeated habits, schedules -> goal).
  A small hand-written signal lexicon is a fallback only at
  the desire level when nothing grounded scored, and concept
  expansion is how one message reads at all three levels.
  `IntentInferrer` is the pluggable interface;
  `build_llm_prompt` documents the LLM seam contract (JSON,
  weights sum to 1, empty level = `[]`) — the LLM inferrer
  itself is NOT built, per spec.
- `act_ask.py` — the intent signal feeding act-vs-ask.
  Decision order: G4 policy first when wired in and the kind
  is recognized (deny/ask/escalate wins; allow lets intent
  run) -> fully empty distribution fails open to act -> desire
  unread asks -> conflicting asks -> top weight < 0.6 asks ->
  irreversible/high-stakes asks -> otherwise act. Plus
  `clarifying_question` (one calm question naming what was
  read).
- `turn_wiring.py` — `IntentTurnAdvisor` (profile + world +
  `ActionSpec` per tool: reversible/stakes) and the ask
  payload (`tool_was_run: false` + question).

Wiring into the turn loop (`puspa/turns/loop.py`): optional
`intent_advisor` on `TurnLoop`. Consulted before a registry
tool call (never for intercepted tools); on `ask` the tool is
NOT run and the model gets the ask payload; on `act` the call
runs as before. Verdicts land on `TurnResult.intent_decisions`
plus an `intent_ask` sink event. A broken advisor fails open
to running the call. Without an advisor behavior is unchanged.
Also a short K1 paragraph in `docs/turn-loop.md`.

Two bugs found by the worker's own tests and fixed in code:
`_world_state` returned `{}` for a `WorldModel` (its `state`
property yields a dict, not a callable), and the conflict check
was unreachable after the confidence check (normalized weights
make a conflicting top necessarily < 0.575 < 0.6), so conflict
is now checked before low confidence, with the math documented
in the module docstring.

Suite (coordinator, SOLO, 19 Nov 2026): 3,200 passed, 23 skipped,
1 warning, 21m32s, EXIT 0 (log hidden_files/suite-wave21-k1.log).
Reconciliation: 3,149 (Wave 20) + 51 (K1 module tests) = 3,200;
skipped unchanged at 23. Module tests (coordinator re-run): 51 passed
(test_intent.py + test_intent_turn_wiring.py, incl. the run
test proving the distribution changes the decision: same
message "buy the laptop" with one recorded preference -> act
and the tool runs; with two equally-matching preferences ->
ask and the tool never runs; same for world state, "pay the
bill" acts with one unpaid bill, asks with two). Regression
(coordinator re-run): 72 passed (test_turn_loop.py +
test_approvals_taxonomy.py). Ruff check + format: clean. Mypy
on the changed files: clean.

Honest limits: keyword overlap, not understanding
(paraphrases with no shared words read nothing; "save the
file" false-matches a "save money" goal); no morphology
("prices" != "price", "frugality" != "frugal"); thresholds
0.6/0.15 are chosen, not calibrated; no learning from
corrections yet; the advisor reads only the last user message,
not turn history; profile freshness is the caller's job (or
use `context_provider`); LLM inferrer is a documented seam
only, no live model call exists.

Remaining: Kosmos K3, K4, K5, K6, K10, K11, K12, K13, then B
features (B6, B9, B10, B11, B12, B13), then B15 culture LAST.
Then uji + 15 gates + 10 reference tasks (waiting on Nero's
decisions).

## Wave 22: Kosmos K3 (Bitemporal state, DONE 19 Nov 2026)

Second Kosmos item (K1 done, Wave 21). K3 adds valid time vs
known time on beliefs and ledger entries (kosmos-design.md
section C point 3). Mechanism complete, tested, pushed by the
coordinator (worker d3ba4e51, verified centrally).

What was built:
- `puspa/world/bitemporal.py` (451 lines, pure Python, no DB,
  no network) — `BeliefVersion` (frozen; key/value/version,
  valid_from/valid_to, known_from/known_to, supersedes) and
  `BitemporalBeliefStore` with `record()` / `correct()` /
  `current()` / `history()` / `known_as_of()` / `valid_as_of()`
  / `as_of()` plus `to_dict` / `from_dict` with history
  validation. Intervals are half-open `[from, to)`; exactly
  one open version per key at any moment. Naive datetimes and
  out-of-order known times are rejected (fail-closed).
  Corrections NEVER update a row in place: a correction appends
  a new version and always closes the old version's `known_to`;
  it closes the old `valid_to` only when the caller passes
  `end_previous_valid_at` (the world really changed). A late
  correction about the past leaves the old valid interval as
  believed. New beliefs default valid time to known time.
  As-of rule: `valid_as_of` returns the latest version whose
  valid interval covers the asked time (a late correction
  overrides the past it covers; a world-change correction does
  not cover the old interval, so the old fact still reads true
  then).
- Ledger — `Ledger.append_event(..., valid_at=...)` in
  `puspa/ledger/ledger.py`: `valid_at` defaults to
  `created_at`, lives in its own column, and is NOT a hash
  input — `compute_event_hash` signature and inputs are
  byte-identical to before, pinned by a regression test on a
  fixed hash vector (`98b22fbe...`). New
  `events_by_valid_time()` orders by when events were true,
  not when recorded (with `COALESCE(valid_at, created_at)`
  fallback for unmigrated DBs). Append tolerates a
  pre-migration DB (retries without the column instead of
  failing the write). One design point: an event gets a single
  valid instant, not a pair — an event is a point in valid
  time; beliefs are intervals. No 32nd event type added (the
  31 revisi-7 types stay closed, G6 precedent).
- Postgres — new table `belief_versions` (one row per version,
  CHECKs on both intervals, RLS like `world_beliefs`) in
  `infra/schema_world.sql`, plus `events.valid_at` in
  `puspa/ledger/schema.sql`. New idempotent migration
  `infra/migrate_bitemporal.sql` (71 lines): creates the table,
  adds `valid_at`, backfills `valid_at = created_at` honestly.
  `puspa/world/store.py` gains `apply_bitemporal_migration()`,
  `save_belief_versions()` (immutable re-save inserts 0 rows),
  `load_belief_history()`, `beliefs_known_at()` /
  `beliefs_valid_at()` SQL as-of queries,
  `delete_belief_history()` (tests only). The `world_beliefs`
  JSON snapshot is untouched — the version table is purely
  additive.
- `docs/bitemporal-state.md` (282 lines): the two time pairs,
  the two correction cases, as-of queries, the ledger hash
  decision and its consequence (`verify_chain` does not
  authenticate `valid_at`; the `puspa_app` UPDATE ban is the
  protection), migration instructions, honest limits.

One real bug caught by the worker's own tests and fixed in
code: the first `valid_as_of` only consulted the current
version, so after a world-change correction the old fact's
interval became unreadable (2 tests failed). The correct rule,
now implemented in both the in-memory store and the SQL query
(`DISTINCT ON ... ORDER BY version DESC`): the latest version
whose valid interval covers the asked time wins. The docs were
corrected to match.

Suite (coordinator, SOLO, 19 Nov 2026): 3,222 passed, 23 skipped,
1 warning, 19m59s, EXIT 0 (log hidden_files/suite-wave22-k3.log).
Reconciliation: 3,200 (Wave 21) + 22 (K3 module tests) = 3,222;
skipped unchanged at 23. NOTE: the first suite attempt was SIGTERM'd
at 64% (no failures visible) by a VM replacement that wiped
Postgres; the replay reinstalled postgresql-16 16.15 + pgvector
0.6.0, the coordinator restarted the cluster, recreated role root,
re-ran infra/schema_core.sql, re-verified RLS, and re-ran the suite
to green. Module tests (coordinator re-run): 22 passed
(test_bitemporal.py — correction appends without deleting
history; known-time query returns the old belief; valid-time
query returns the fact true then; hash pin; chain verifies
after migration + backfill; migration run twice is idempotent,
simulating a pre-K3 DB by dropping the column/table first).
Neighbors (coordinator re-run): 105 passed
(test_world_*.py + test_ledger*.py + test_bitemporal.py) and
55 passed (test_sentinel.py + test_sentinel_logs_traces.py +
test_sentinel_trajectory_insights.py). Ruff check + format:
clean. Mypy on the changed files: clean.

Honest limits: `valid_at` / default valid times are claims,
not measurements; nothing checks them against the world. The
ledger hash does not cover `valid_at` — tampering with it alone
is invisible to `verify_chain`. The world model itself is not
yet wired to record versions automatically (`WorldModel`
beliefs still update in place); callers needing history record
versions explicitly. That wiring is a FOLLOW-UP, not claimed
here — recorded for a later wave. No delete API for versions
(append-only by design); no cross-key in-memory queries (use
the Postgres as-of queries); model-day clock vs wall-clock UTC
is not reconciled; out-of-order known-time ingestion is
rejected rather than spliced in.

Remaining: Kosmos K4, K5, K6, K10, K11, K12, K13, then B
features (B6, B9, B10, B11, B12, B13), then B15 culture LAST.
Then uji + 15 gates + 10 reference tasks (waiting on Nero's
decisions).
