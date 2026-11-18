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

## Wave 1+2: 6 brain follow-ups + G15 (done, 18 Nov 2026)

Eight workers, seven commits (follow-up f done in two steps, one
commit). Central SOLO suite after the wave: **1,445 passed, 8 skipped,
1 warning, 9m06s** (baseline 1,321 + 124 new; the 8 skips are the 5
pre-existing Docker/Chromium skips + 3 live judge-eval tests, skipped
without `PUSPA_LIVE_JUDGE_EVAL=1`). Push order: (a), (b), (c), (d),
(e), (f+f2), G15. Postgres died twice during the wave (VM
replacements); restored each time (roles root + puspa_app, DB
puspa_test, infra/schema_core.sql; the test fixtures create the app
schemas). One ratchet fix by the coordinator: `tests/test_sigstore_verify.py`
grew to 1,101 lines (DSSE fixtures), recorded in the ratchet baseline.

### (a) Provider-layer completion (commit 449e01a0)

`puspa/router/complete.py` (new): `complete(prompt, *, role, model)`
picks the cheapest router model per role via `router.choose`, calls
OpenRouter-compatible chat completions over httpx, returns text;
`complete_detailed` adds tokens + Rupiah cost; `default_llm_complete`
factory; `resolve_llm_complete` (`None` / callable / `"default"`);
`make_call_fn` adapts to the fallback `call_fn` contract. Failures are
loud (missing key `KeyError`, HTTP/transport/malformed `CompletionError`),
never fake answers. Keys via the existing BYOK path, never printed.
Two environment fixes documented in the module: httpx env-proxy parsing
crashes on the VM's bracketed-IPv6 NO_PROXY (client takes the proxy
explicitly, `trust_env=False`); the egress MITM CA is not in certifi, so
verification uses `ssl.create_default_context()`. Wiring:
`HippoRAGRetriever` and `retrieve_with_decomposition` accept
`llm_complete="default"` (lazy, defaults stay deterministic/regex).
Tests: `tests/test_router_complete.py`, 36 passed (fake transport).
Live smoke: one call, `meta-llama/llama-3.1-8b-instruct`, "ok", 18 in /
2 out tokens, Rp0. Honest limits: `"default"` resolves lazily (missing
key surfaces on first use); `complete()` calls one model only — tier
fallback + Rp2,000/task budget apply only via `make_call_fn` +
`call_with_fallback`; live path proven by one smoke call only.

### (b) R2 judge live evaluation (commit 699c1c62)

`tests/test_hipporag_judge_live_eval.py` (new, 6 offline + 3 live
tests gated by `PUSPA_LIVE_JUDGE_EVAL=1`) + prompt fix in
`puspa/retrieval/triple_filter.py` (`build_judge_prompt` only). Finding:
the old prompt failed live two ways — 22-triple replies ran past the
1,024-token budget (truncated mid-JSON, strict parser rejected, whole
batch silently fell back to deterministic) and the model read relevance
as "mentions the product or price", dropping every fraud-report triple.
Fix, both measured: reasons capped at five words (replies now ~420-500
output tokens) + one sentence that a report/complaint/fraud involving
the seller, deal, or product DOES help for a trustworthiness question.
After the fix: chain-graph agreement 5/6 in 3 consecutive runs, fraud
passage p5 reached every time. Measured (live, temp 0.0): recall@1
0.125, @3 0.250, @10 1.000 vs deterministic 0.125 / 0.375 / 1.000 —
**the live judge does not beat the deterministic filter on this set**
(matches @10, loses @3). Full live eval: 7 calls, 4,330 in / 2,920 out
tokens, Rp6 total. The test asserts the measured floor (@3 0.250), not
a win that did not happen. Follow-up: recall@3 needs the cross-encoder
reranker (now default bge — re-measure), or a stronger judge model.

### (c) bge reranker swap (commit bf72c431)

`CrossEncoderReranker.from_flagembedding` in `puspa/retrieval/reranker.py`;
`make_reranker` default backend is now `"bge"`. Measured on this VM:
2.3 GB weights load in 23-28 s, peak RSS 2,148 MB; Indonesian query
("Berapa harga Galaxy S24?") ranks the answer 4.30 vs stuffed 0.75.
Licenses re-verified: model Apache-2.0, FlagEmbedding MIT. Pins:
`FlagEmbedding==1.4.2` + direct/transitive deps in requirements.txt;
REUSE.md + NOTICE.md updated. Tests: `tests/test_reranker_cross_encoder.py`,
15 passed in 93 s (6 ms-marco + 9 bge). Honest limits: the swap does
NOT close the Wave 1 multi-hop recall gap (fraud report still ranks
last in both languages; locked in as
`test_bge_trust_query_still_misses_the_fraud_report`); CPU scoring is
~1-1.7 s/pair; a load on a very busy VM moment can fail or trip the
OOM killer (free RAM fluctuates 0.5-3.0 GB) — failure surfaces as
`RerankerUnavailable`, ms-marco stays one explicit argument away.

### (d) Sigstore verifier wired into verify_skill (commit 01c37542)

`puspa/policy/skill_signing.py`: `verify_skill()` takes
`sigstore_identity` / `sigstore_issuer` / `sigstore_trust_root`
(kwargs overriding the manifest). Verification is an AND: local Ed25519
manifest signature (unchanged) → DSSE provenance envelope when a
transparency log is passed (unchanged) → Sigstore bundle when the
manifest declares one (new). Bundle location: a `sigstore` object in
`manifest.json` (`bundle` default `"bundle.sigstore"`, `artifact`
default `"manifest.json"`, `trust_root` default `"production"`); the
bundle attests the raw artifact bytes, and the local signature covers
the `sigstore` declaration itself. `_skill_file` rejects bundle/
artifact names escaping the skill directory. Never raises; missing or
bad bundles return False. Tests: `tests/test_skill_signing_sigstore.py`,
18 passed. Honest limits: the production-shaped flow (sign
`manifest.json` with the Sigstore CLI) cannot be tested offline;
`check_tool()` still verifies only the local signature, not the
bundle; `PuspaSkillsLoader` does not pass Sigstore parameters yet
(bundle enforcement applies automatically via the manifest).

### (e) Sigstore trust-root refresh (commit 3cbd8c27)

`puspa/policy/trust_root.py` (new): `refresh_trust_root()` (skips
network while the cache is younger than 7 days; downloads via the
sigstore package's private `TrustUpdater`; validates before installing
— non-empty, ≤1 MiB, valid JSON, parses as `TrustedRoot.from_file`;
atomic install via temp file + `os.replace`; identical download →
`unchanged`; any failure leaves the old cache untouched and records
`trust_root_refresh_failed`; missing TUF client → `unavailable`),
`check_staleness()` (stale after 30 days, records `trust_root_stale`,
never silent), `resolve_trust_root_for_verify()` (cached path for
`verify_sigstore_bundle(trust_root=...)`, `None` when no cache so
callers fall back to the bundled `"production"` root),
`run_trust_root_refresh_job()` (never raises). Wiring: `"trust_root_refresh"`
in `TRIGGER_TYPES` + `schedule_trust_root_refresh(user_id, hour=3)` +
dispatch in `run_scheduled_job` (`puspa/scheduler/scheduler.py`).
Daily scheduler trigger chosen over a startup hook (would delay boot
and fail on offline hosts). Tests: `tests/test_trust_root_refresh.py`,
25 passed (fake updater + clock, no DB/network). Honest limits:
`TrustUpdater` is a private sigstore API (re-check the import on the
next sigstore upgrade; `unavailable` is the fail-closed path); only
`trusted_root.json` is refreshed, not `signing_config.v0.2.json`; a
stale cached root is still returned (recorded, not silent); the real
TUF refresh against `tuf-repo-cdn.sigstore.dev` has not been exercised
live. Follow-up: no trigger row exists yet in the DB — call
`schedule_trust_root_refresh()` once to create the daily trigger.

### (f) DSSE bundle fixture + dispatch (commit 6b619969)

Two steps, one commit. `tests/test_sigstore_verify.py`: real DSSE
fixture from sigstore-python public test assets
(`a.dsse.staging-rekor-v2`, byte-identical via git blob SHAs + sha256
asserts) + 11 DSSE tests. Finding: the premise was wrong —
`verify_sigstore_bundle` did NOT handle DSSE bundles (`verify_artifact`
raises "Missing bundle message signature" for every DSSE bundle).
`puspa/policy/sigstore_verify.py` now dispatches on the parsed bundle:
`_dsse_envelope` present → `Verifier.verify_dsse(bundle, policy)`
(detection via `getattr`, so a future sigstore version dropping the
private attribute fails closed). Then artifact binding (decided
semantic for skill verification — envelope-only would let any DSSE
bundle from the right identity "verify" for any artifact):
`_check_dsse_binding` requires an in-toto statement and accepts only
if sha256 of the artifact bytes equals the sha256 digest of any listed
subject; anything else fails closed, never raises. Fixture find: the
matching artifact is sigstore-conformance's `a.txt` (109 bytes, sha256
`a0cfc712…`), a different file from sigstore-python's companion `a.txt`
(104 bytes) despite the same name — that collision is why the fixture
first looked unbindable. Tests: 50 passed, 0 xfail (24 DSSE-related).
Honest limits: tampered-envelope-signature rejection surfaces as "not
enough sources of verified time" (the RFC3161 timestamp is bound to the
envelope signature); binding is digest-only, not name-bound; dispatch
keys off the private `Bundle._dsse_envelope` attribute (re-check on
sigstore upgrade). DSSE bundles are verifiable at the
`verify_sigstore_bundle` level and flow through `verify_skill` via (d).

### G15 CI + verification discipline (commit 56b668e0)

`.github/workflows/ci.yml` (124 lines): one workflow, hermes
orchestrator pattern in miniature — `test` lane (full suite SOLO
against a `pgvector/pgvector:pg16` Postgres service with
`infra/schema_core.sql`, `all-checks-pass` aggregate gate; cancel in
progress on PR branches, never on `main`) + `checks` lane (ratchet +
drift tests with `--noconftest`, no DB). `docs/verification.md` (124
lines): the written verification discipline (SOLO rule, isolated
fixtures, never verify mutations against live user app/data, the
four-part "tested" claim, reproduce-first, docs-drift and ratchet
rules, CI, license pending). `tests/test_file_size_ratchet.py`: no
file over 2,000 lines; files over 1,000 lines may only shrink against
the recorded baseline; no new file over 1,000 lines without a record.
`tests/test_docs_drift.py`: the 31 ledger event types identical in
`puspa/ledger/ledger.py`, both CHECK lists in `puspa/ledger/schema.sql`,
and the count line in `docs/contracts.md`. Tests: 6 passed, including
negative validation (a probe 1,500-line file failed the ratchet;
tampered contracts/schema copies were detected). NO LICENSE file
created (Nero's decision; recorded pending in `docs/verification.md`).
No ruff/mypy installed or pinned → no lint step invented (follow-up:
pin a linter/typechecker, then add the step). The CI workflow cannot be
executed end-to-end on this VM (no Docker, no GitHub runner here);
correct by construction, first real run on GitHub (watch: torch
~2.5 GB install makes the first `test`-lane run slow).

## Next: G19+G20 (context compression + prompt caching)

In progress (spawned after Wave 1+2): two parallel workers, disjoint
files, one shared interface (message list shape + compressor return
shape, defined in the briefs). G19: two thresholds (preflight 50%
before an API call, gateway 85% between turns), standard order (flush
memory first → summarize middle turns → protect last N → never split
tool call/result pairs → new lineage per compression), telemetry,
summarizer via the injected `complete()` callable (live LLM) with a
deterministic stub in tests. G20: three-layer prompt assembly (stable →
context → volatile), stable layer frozen per session, cache breakpoint
markers, snapshot test for prompt surface + tool list, prompt-state
changes deferred to next session (compression is the only allowed
mid-session mutation).
