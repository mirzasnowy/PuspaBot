# Status archive: repo maturity phase, Waves 16-17 (19 Nov 2026)

Archived from `docs/status-maturity.md` on 19 Nov 2026, when the live
log passed the 1,000-line soft cap of the file-size ratchet (1,002
lines). Frozen: never appended to; the living log continues in
`docs/status-maturity.md` (Wave 18 onwards).
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

