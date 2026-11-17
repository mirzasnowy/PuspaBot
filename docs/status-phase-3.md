# STATUS — Phase 3 Puspa MVP (Dual wedge + Moat)

Definition: revision 7 (approved by Nero 17 Nov 2026 ~17:42 WIB).
The 17-week timeline IGNORED on Nero's order, 21:53 WIB (dates are
only ordering, not deadlines). Local work in `build/fase-3/`;
DO NOT push to GitHub; DO NOT start Phase 4.

## Numbers (all runs SOLO, one pytest at a time)

| Suite | Result | Time |
| --- | --- | --- |
| Phase 3 (new) | **62 passed** (with browser; 61 passed + 1 skipped without the browser env) | 17 Nov 2026 ~23:30 WIB |
| Phase 0 (re-verification) | **248 passed, 4 skipped** | 17 Nov 2026 ~22:50 WIB, solo |
| Phase 1 (re-verification) | **58 passed** | 17 Nov 2026 ~22:50 WIB, solo |
| Phase 2 (re-verification) | **73 passed** | 17 Nov 2026 ~22:45 WIB, solo |

4 Phase 0 skips = live-container Docker tests (kernel rejects `setns`) —
the same environment block as Phase 0/1/2, not a regression.
Phase 0 rose 244 → 248 passed: 4 new registry regression tests
(decision 22, token-filtered `get_definitions`, landed
17 Nov 2026 ~22:40 WIB) — not a Phase 3 code change.

Total new Phase 3 code: ~3,500 lines of Python (code + tests; 10 modules
+ 10 test files + a 16-table schema).

## BIGGEST FINDING: the terms of service of all three marketplaces forbid automated reading

Decision 15 check — read directly from each source's official terms
page (17 Nov 2026, recorded in `tos_checks` + `puspa/pricing/tos.py`):

| Source | Result | Clause |
| --- | --- | --- |
| Tokopedia | **melarang_otomatis** | Terms B.12-13: forbidden to use robots, macros, crawlers and/or automated tools for crawling/scraping without Tokopedia's permission. Source: https://www.tokopedia.com/help/article/terms-conditions |
| Shopee | **melarang_otomatis** | Terms of Service section 3: forbidden to access with bots/emulators and to monitor/copy automatically without written permission; the permitted official route is the Shopee Open Platform (needs a partner account + store authorization). |
| Blibli | **melarang_otomatis** | Terms 2.2: forbidden to systematically take content to create/compile a collection, compilation, database or directory without written permission. Source: https://www.blibli.com/faq/tentang-blibli/syarat-ketentuan/ |
| PLN | layanan_resmi_terbatas | Bill checks via the official service for your own account at layanan.pln.co.id; needs login + captcha — the automated route is not guaranteed, not an explicit ban. |
| PDAM | layanan_resmi_terbatas | Each region has its own site; bill checks via the official service for your own account. MVP covers only Nero's PDAM + test users (§4.2). |
| airtime / data packages | layanan_resmi_terbatas | Checks via the operator's official channels (app/USSD); no general public API. |

**Code consequence (not just a note):** `puspa/pricing/tos.py`
has the `boleh_read` gate — LIVE reads of all three marketplaces are
HELD until Nero decides to proceed or switch sources (decision
15). `compare_price(mode="live")` returns
`tidak_bisa_dicek` + the reason `syarat_service_pending_keputusan_nero`
WITHOUT a single HTTP attempt to a marketplace (tested: the readers
were called 0 times). If a source blocks (403/429/captcha), the reader
reports `sumber_memblokir_http_*` / `captcha_terdeteksi` — WITHOUT any
attempt to break through the block (§11). The compliant official routes
(Shopee Open Platform, and possibly official Tokopedia/Blibli APIs)
need a partner account + approval — not part of the MVP, needs Nero's
decision.

## What was built (per definition scope)

### 1. Wedge 1 — compare prices (§4.1) — `puspa/pricing/compare.py`
- 3 readers (one per marketplace) + 1 verifier rereading every
  number (§2.5); matching via the identity graph; every displayed
  number has an `evidence_id` (a price without evidence is not written
  to the graph).
- The 9-category scope is locked in code (`CATEGORY_SCOPE`):
  fashion/shoes/bags rejected with a clear message, not silently built
  as a weak version (§5.2). Entry rule: the model number must be
  matchable.
- When unsure → ask the user (candidates shown), never guess (§3.2).
- Output: cheapest price + store + rating, price per marketplace,
  difference, a link per listing, verdict per price.
- Pricing: base compare free; detail check (30-day history + current
  price) = 1 credit; 15 free credits/month (`credits.py`).
- Photo input: not yet — photos are read by the Phase 2 vision model;
  the wedge takes an item name / product link (photo → item name by
  the agent, per §2.4; recorded as a limit, not claimed).

### 2. Identity and price graph (§3.2) — `puspa/graphs/identity.py`
- Identity layer: canonical products (brand, series, model number,
  normalized variants: storage/color/RAM); listing links with status
  `automatic` / `user_confirmed` / `user_rejected`.
- Price layer: time series (price, shipping, store rating, stock,
  `evidence_id`); `record_price` REJECTS numbers without `evidence_id`.
- Tiered matching: deterministic model key → candidates + ask
  user → `user_confirmed` links → NO silent guessing.
- Read API: current price, 30-day history, identity candidates.
- Storage: Postgres (tables + relations), NOT an external graph DB —
  §7 satisfied; the embedded-graph LadybugDB pattern is only a pattern
  reference (see `reuse.md`). No evidence Postgres is insufficient.

### 3. Catalog seed (§3.8) — `puspa/pricing/catalog.py`
- 14 seed rows (13 unique products) across 9 categories; 10 default
  test items (decision 7: final list waits on Nero's confirmation).
- HONEST NOTE: the seed was written from known public specifications,
  NOT pulled directly from the brands' official spec pages during the
  build (live pulls are held by the ToS gate). The seed is marked
  `sumber_bibit="bibit_awal"` in this STATUS so it can be replaced with
  official pulls once Nero decides on sources (decision 15).

### 4. Watchlist + price sampler (§3.8) — `puspa/pricing/watchlist.py`
- The user flags items; the sampler reads periodically with a budget
  of max 20/day/user (enforced by code — budget exhausted → items
  skipped + reported, not read silently); price-drop alerts when the
  price is at or below the threshold.
- RECORDED LIMIT: the full cron parser does NOT exist yet (Phase 2
  note: triggers shift +1 day). The sampler uses the Phase 2
  `price_turun`/`schedule` triggers as-is (once a day per item); hourly
  cron stays Phase 4. The Phase 2 scheduler is unchanged (phase work
  rule: do not edit Phase 2 packages except for bug fixes).

### 5. Shared evidence cache (§3.8) — `puspa/pricing/cache.py`
- 30-minute TTL, reused across users by readers; the cache-hit rate
  is recorded (`statistics_hit`, no MVP target).
- The verifier NEVER uses the cache: the verifier path calls the
  reader directly; tested (rereads still called 3/3 even with a warm
  cache).

### 6. False reports (§3.8) — `puspa/moat/report.py`
- Every number has a false report: recorded in the `false_reports`
  table + the `false_report` event in the ledger (type already exists,
  §2.3) + one `Cannot be determined` reading written to
  `source_readings` so the source's reliability score drops + a reread
  is requested. Tested: Shopee's score 100% → 98% after 1 report out
  of 50.

### 7. Wedge 2 — bill autopilot (§4.2) — `puspa/billing/billing.py`
- MVP providers: PLN, PDAM, airtime, data packages. NO BPJS
  (firmly rejected, tested).
- Procedures from the service flow graph; if none exists yet, the
  agent finds it + writes it as a trial procedure, then runs it.
- Customer numbers NEVER enter the graph: `tulis_prosedur` rejects
  steps containing a customer-number/phone pattern (tested).
- Output: amount + due date + status (numbers from evidence),
  reminders before the due date (N days before, default 3), prepare
  payment safe mode = DRAFT only (amount + destination + `content_hash`
  for content-bound approval; NO payment execution).
- Local test site (`fixture.py`, stdlib only): PLN Rp132,500 due
  2026-11-20 `not_yet_pay`; PDAM Rp87,500; airtime Rp50,000; data
  package Rp75,000. These are test data, not anyone's real bills.

### 8. Service flow graph (§3.3) — `puspa/graphs/service.py`
- On top of the Phase 1 `procedures` table (trial → verified
  cycle: 3 successful runs, min 2 agents → broken after 2 consecutive
  failures).
- Phase 3 additions: daily canary (verified procedures only;
  failure → procedure broken; results recorded in `canary_runs`);
  cross-agent reuse recorded (`procedure_uses`); fix =
  a new version passing 3 tests before replacing + rollback to the old
  version (snapshots in `procedure_versions`); `broken` procedures
  are not run (the agent says it cannot check).
- Subagent patterns are also stored here: `save_pattern_subagen`
  (`compare_prices`: 3 readers + 1 verifier; `check_bill`:
  reader + verifier) + `daftar_pola` across agents.

### 9. Reliability score (§3.4) — `puspa/moat/reliability.py`
- Per source (marketplace x category) and per procedure: %
  Verified of the last 50 readings + age of the last reading.
- Used for: (a) score < 80% → all results from that source are max
  Uncertain (enforced in `compare.py`, tested); (b) honest evidence
  panel labels ("Shopee, TV category: 92% verified of the last 50
  readings"); (c) material for the business evidence report (metrics).
- The reliability-score panel in the PWA is in cut order #1 (its data
  is still computed) — the panel itself is minimal: the panel label is
  available as a function, not yet mounted into the PWA template
  (Phase 2 unchanged).

### 10. Gate 8 moat metrics (§3.6 + §3.8) — `puspa/moat/metrics.py`
- Measured locally: automatic-match coverage 100% in the gate test
  (3/3 automatic links; 66.7% in the metrics test with 1
  user_confirmed link — target ≥ 60% passes in both); 4 verified
  procedures + each reused by another agent (passes); canary catches
  2 of 2 injections (passes); reliability score computed for every
  source used (passes).
- Honestly pending: token ratio last week vs first week
  (needs a 2-week test period — the token measuring + recording code
  is done); the 3.8 active-workflow metric (5/10 users with one active
  workflow) is PHASE 4 — the workflow-library foundation is recorded
  (`library.py`: successful workflows + block requests), its metric is
  NOT claimed in Phase 3.

### 11. Readers: HTTP fetch + Playwright (§2.8) — `puspa/pricing/readers.py`
- `HttpReader` (httpx, injected client; tests use MockTransport):
  generic JSON-LD `Product`/`Offer` parser + meta
  `product:price:amount`; 403/429/captcha blocks reported, never
  broken through; domains outside the allowlist rejected.
- `PlaywrightReader` (read-only browser): the `playwright==1.65.2`
  package (MIT) is INSTALLED in the Phase 0 venv. The Chromium 1255
  browser download from the Playwright CDN FAILED 3x (`Download
  failure, code=1`, attempts 17 Nov ~22:30, ~22:40, and ~23:00 WIB,
  the last one 7 minutes). ROOT CAUSE DIAGNOSED: the Playwright CDN
  redirects to `playwright.download.prss.microsoft.com`, and the egress
  proxy returns `400 Bad Request` (`GatewayExceptionResponse`) for
  that host — proven by direct `curl` (307 redirect → 400).
  Meanwhile `storage.googleapis.com` is reachable (200 OK).
- WORKING SOLUTION: Chrome for Testing 159.0.8154.6 (the same revision
  Playwright 1.65.2 asks for) downloaded from
  `storage.googleapis.com` (131 MB, succeeded) to
  `~/.cache/ms-playwright/chrome-for-testing-159/`; `PlaywrightReader`
  uses the `PUSPA_CHROMIUM_EXECUTABLE` env seam to point at that
  binary (`p.chromium.launch(executable_path=...)`). PROVEN:
  the reader reads the local test site and extracts Rp132,500
  (`test_playwright_reader_read_situs_test_bila_browser_ada`, passes
  when the env is set; skips when no binary is present — not a failure).
  Revision 1223 already in the cache is NOT used (it does not match
  Playwright 1.65.2). Without the env, `read()` honestly returns
  `browser_gagal`; the wedge uses HTTP fetch where it can.
  NO read result is invented.

## Gates touched
- **Gate 2 (reference tasks): PARTIAL.** J4 (compare laptop prices)
  + J5 (check PLN bill + prepare payment) now have wedge logic +
  evidence + procedures — tested end-to-end in fixtures (Phase 3 gate
  tests). Live runs with a model + live sources wait on
  decision 15 + Nero's numbers + test users.
- **Gate 5 (accurate price compare): PARTIAL (mechanism).**
  The accurate mechanism is tested in fixtures (the verifier catches
  injected wrong prices; verdict + evidence_id on every price).
  Measuring 95% over 20 live compares is PENDING — live is held by
  ToS (decision 15).
- **Gate 6 (price-compare usage): PARTIAL.** Detail-check credits
  done + tested (1 credit, 15/month, exhausted → rejected).
  Real usage (20 compares/week, 1 person paying 1 credit)
  needs a test period + live sources.
- **Gate 7 (bill autopilot): PARTIAL.** Procedures for 4 providers +
  reminders + prepare payment (draft) done + tested in fixtures; 100%
  numbers matching the provider + 100% reminders delivered need Nero's
  live numbers + an always-on host (VPS — Nero's step).
- **Gate 8 (three-layer moat graph): PARTIAL (measured locally).**
  Both graphs proven written by the wedge + read by the next check
  (direct queries in tests); 3.6 metrics partly pass (coverage, 4
  procedures + reuse, canary 2/2, reliability of all sources), partly
  pending (token ratio — needs a test period); the 3.8 active-workflow
  metric is Phase 4.
- **Gate 11 (agents + subagents): PARTIAL.** The 3-reader +
  1-verifier pattern stored in the graph + used across agents; the
  verifier catches injected wrong prices (tested in the wedge, not
  only in cassettes). Stuck handoffs / Stop / Puspa deletion stay as
  in Phase 2.
- **Gate 12 (backbone + evidence): PARTIAL.** 100% of wedge numbers
  have an `evidence_id` (enforced by `record_price` + `compare`);
  orphan numbers held in the Phase 2 renderer. Phase 3 cassettes
  (replaying the wedge without network) not yet made — wrap-up
  follow-up.

## Needs Nero (human steps, not code blockers)
1. **Decision 15 — MOST URGENT.** All three marketplaces forbid
   automated reading in their terms of service. Nero decides: (a) go
   via the official route (Shopee Open Platform + official Tokopedia/
   Blibli APIs — needs a partner account + approval, outside the MVP),
   (b) switch sources, or (c) keep reading at the user's request
   with a risk Nero explicitly accepts. Until there is a decision,
   the live price wedge stays held by the gate (not silently blocked —
   the agent says it cannot check + why).
2. **Decision 5** (PLN/PDAM customer numbers + phone number for canary
   + live bill tests) — needed since Phase 2; not yet given.
   Canary + live tests wait on these numbers.
3. **Decision 7** (final test-item list): 10 default items prepared
   from the catalog seed — waiting on Nero's confirmation (or his
   replacement list).
4. **Singapore Contabo VPS** (~$6.99/month) for the always-on host +
   scheduler + reminders (decision 2, gate 7). Daily canary +
   periodic sampler mechanisms are done; their daily schedule runs
   when the scheduler runs on the host.
5. **Publish the Google OAuth app** to "In production" (Nero's
   browser; decision 19) + the final PWA login callback — not a wedge
   blocker (the wedge uses web reads + fixtures), but reference tasks
   2, 3, 6, which need live Gmail/Drive, depend on it.

## Definition vs build conflicts (recorded, definition unchanged)
1. **Fixture bill verification vs full verifier.** Phase 2 `periksa`
   rejects sources outside the allowlist (layer 1) — the local test
   site (localhost) does not pass. Fixture mode uses deterministic
   reread comparison (the verifier still rereads via an independent
   callable, values compared; verdicts Verified / Uncertain / Cannot
   be determined are the same). The LIVE path still uses full `periksa`
   (allowlist + reread + language decision). Not a security reduction
   on the live path; fixtures are clearly marked (fixture `sumber_url`
   + the `tagihan <provider>` claim from test data).
2. **The catalog seed is not a direct pull from official pages.** See
   §3 above — the initial seed was written from known public
   specifications; official pulls wait on decision 15.
3. **The full cron parser stays Phase 4.** The sampler uses the Phase 2
   triggers as-is (once a day); not pretend hourly cron.
4. **Minimal PWA reliability-score panel.** Its data is computed (the
   panel label is available); mounting it into the PWA template was
   not done because the phase work rule forbids editing Phase 2
   packages — the panel is in cut order #1, so this matches the
   definition.

## Large-repo patterns adapted (detail: `reuse.md`)
- **LadybugDB** (MIT): embedded-graph pattern → graphs on top of
  Postgres (nodes + edges as relational rows). NOT used as a
  library — only its pattern.
- **RAGFlow** (Apache-2.0): pattern of a machine that stores work steps
  as reusable data → service flow graph
  (procedures + canary + reuse). NOT used as a library;
  used as a moat component, not a document product.
- No code copied verbatim; no new dependencies except Playwright (MIT);
  no forks; no LangGraph claims anywhere (orchestration = nanobot's
  AgentLoop + Puspa's own task lifecycle — a correction of a wrong
  survey sentence, see "Consistency notes" below).

## Consistency notes (from Luna, 17 Nov 2026 ~22:46 WIB)
- **Phase 0 registry contract change (decision 22):** Phase 3 code
  does NOT call `PuspaRegistry.get_definitions` / `bind_turn` /
  `prepare_call` at all (empty grep in `puspa/` + `tests/`) —
  the tool-visibility change does not affect Phase 3. If Phase 4
  uses the registry, bind a token first (`bind_turn`) before expecting
  any tool definitions.
- **Remaining `prepare_call` gap:** the unknown-tool error message
  still lists the full `tool_names` — Phase 3 does NOT reintroduce
  this pattern (no Phase 3 error message leaks the tool list);
  closing the gap is a wrap-up follow-up (Phase 2's share).
- **`reuse.md` does not yet record the Phase 0 design influence** (OpenBot,
  E2B/Firecracker, agentskills, ECC, hermes) — not Phase 3's job;
  recorded as a wrap-up follow-up (full audit in
  `docs/reuse-audit.md`).
- **CLI polish (Phase 2's share):** nanobot's loguru logs interrupt
  the spinner during interactive CLI use — Phase 3 does not use the
  interactive CLI in testing, so it was not met directly; recorded as
  a wrap-up follow-up, not Phase 3 scope.
- **LangGraph:** zero imports, zero claims in Phase 3 (empty grep).

## What was NOT built (deliberately, per the definition)
- Anti-fraud network + sold-price ledger (§3.7) — still
  not built.
- Fashion + non-standard items in the wedge (§4.1, §6) — rejected with
  a clear message, not built silently.
- BPJS (§4.2) — firmly rejected.
- Langflow (Phase 4), voice Pipecat/LiveKit (post-MVP).
- Full QRIS payment / payment execution (§6) — safe payment mode
  (draft) only.
- Phase 3 cassettes, full PWA reliability-score panel, full cron parser —
  follow-ups (see above).

## Environment incidents / notes during Phase 3
- Postgres stayed alive throughout Phase 3 (no total-loss incident
  like Phase 1).
- Parallel pytest runs were NOT used (all numbers from solo runs).
- Playwright: the Chromium 1255 download from the Playwright CDN failed
  3x — root cause diagnosed (the egress proxy rejects host
  `playwright.download.prss.microsoft.com`, 400); the working solution
  via Chrome for Testing 159 from `storage.googleapis.com` + the
  `PUSPA_CHROMIUM_EXECUTABLE` env seam (the reader reads the local test
  site, Rp132,500 extracted — tested).
- Phase 0 rose 244 → 248 passed because of 4 new registry regression
  tests (decision 22) — verified, not a run artifact.
