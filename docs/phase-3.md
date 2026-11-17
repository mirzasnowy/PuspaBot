# Phase 3 — Dual wedge + Moat (Puspa MVP, revision 7)

Definition: revision 7 (approved by Nero 17 Nov 2026 ~17:42 WIB).
The 17-week timeline IGNORED on Nero's order, 21:53 WIB (dates are
only ordering, not deadlines). Local work in `build/fase-3/`;
DO NOT push to GitHub; DO NOT start Phase 4.

## What was built

- **Wedge 1 — compare prices** (`puspa/pricing/`): 3 readers
  (Tokopedia, Shopee, Blibli) + 1 verifier; matching via the
  identity graph; 9-category scope locked; fashion rejected with a
  clear message; detail check 1 credit, 15 free/month.
- **Identity and price graph** (`puspa/graphs/identity.py`): canonical
  products + listing links (`automatic`/`user_confirmed`/
  `user_rejected`) + price time series (rejected without `evidence_id`).
- **Catalog seed** (`puspa/pricing/catalog.py`): 14 seed rows
  (13 unique products, 9 categories) + 10 default test items.
- **Watchlist + sampler** (`puspa/pricing/watchlist.py`): read budget
  max 20/day/user; price-drop alerts.
- **Shared evidence cache** (`puspa/pricing/cache.py`): 30-minute TTL;
  the verifier still rereads independently (never uses the cache).
- **Wedge 2 — bill autopilot** (`puspa/billing/`): PLN, PDAM,
  airtime, data packages (NO BPJS); procedures from the service flow
  graph; due-date reminders; prepare-payment safe mode (draft only).
- **Service flow graph** (`puspa/graphs/service.py`): trial →
  verified → broken → fix (3 tests) + rollback cycle; daily canary;
  cross-agent reuse; stored subagent patterns.
- **Reliability score** (`puspa/moat/reliability.py`): per source
  (marketplace x category) + per procedure; < 80% → max
  Uncertain; evidence-panel labels.
- **False reports** (`puspa/moat/report.py`): recorded in the ledger
  (`false_report`) + lowers the source score + triggers a reread.
- **Gate 8 moat metrics** (`puspa/moat/metrics.py`): automatic-match
  coverage, 4 verified + reused procedures, token ratio, reliability
  of all sources; active-workflow metrics (Phase 4) reported as
  `pending`, not claimed.
- **Workflow library** (`puspa/moat/library.py`): Phase 4 foundation
  (successful workflows + block requests).
- **Terms-of-service check** (`puspa/pricing/tos.py`): check results +
  live-read gate (see status-phase-3.md — the biggest finding of this phase).

## Running the tests

All runs SOLO (one pytest at a time — TRUNCATE collisions on the
shared `puspa_test` DB). For all outbound HTTP use
`NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1`.

```sh
cd build/fase-3
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ../fase-0/.venv/bin/python -m pytest tests -q
```

Latest numbers: Phase 3 **62 passed** (with a Chromium browser via
`PUSPA_CHROMIUM_EXECUTABLE`; 61 passed + 1 skipped without the browser
env); Phase 0 248 passed + 4 skipped; Phase 1 58 passed; Phase 2
73 passed (all solo runs).

## Honest notes (short version; detail in status-phase-3.md)

- LIVE reads of all three marketplaces HELD by the ToS gate: the terms
  of service of all three forbid automated reading without permission.
  The price wedge is fully tested in fixture mode; live waits on
  Nero's decision (decision 15).
- Live bills wait on Nero's customer numbers (decision 5); canary +
  tests use a local test site.
- Playwright: package installed; Chromium browser available via
  Chrome for Testing 159 + the `PUSPA_CHROMIUM_EXECUTABLE` env
  (Playwright CDN download blocked by the egress proxy — see
  status-phase-3.md). Without the env, the reader fails visibly
  (`browser_gagal`).
- Puspa orchestration = nanobot's AgentLoop + Puspa's own task
  lifecycle. LangGraph is NOT used and is not claimed anywhere.
