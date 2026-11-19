# Status archive: maturity Waves 18-19 (19 Nov 2026)

Archived from `docs/status-maturity.md` on 19 Nov 2026, when the
live log passed the soft cap again (1,032 lines with the Wave 26
entry). Frozen, never appended to; history is never deleted, only
archived. The living log continues in `docs/status-maturity.md`
(Wave 20 onwards).

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

