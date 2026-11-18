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

## Wave 3: G19+G20 (context compression + prompt caching, done 18 Nov 2026)

Two parallel workers, disjoint files, one shared interface (message
list shape + compressor return shape + prompt surface shape, defined
in the briefs). Central SOLO suite after the wave: **1,491 passed, 8
skipped, 1 warning, 9m27s** (baseline 1,445 + 46 new: G19 24 + G20
22). Postgres stayed online through the wave (no restore needed).
Coordinator verified the G19→G20 integration itself (compressed
messages feed the builder: 427 → 291 tokens, stable prefix preserved
byte-identical).

### G19 Context compression (commit 4b7821d5)

`puspa/runtime/context_compression.py` (533 lines) +
`tests/test_context_compression.py` (24 passed, 0.90 s, in-memory).
Home is `puspa/runtime/` (not `puspa/memory/`): the compressor works
on the live turn message list at the runtime layer, reusing
`count_tokens` (tiktoken `cl100k_base`, chars/4 fallback) from
`puspa/router/compression.py`. `should_compress` → `"gateway"` ≥85%,
`"preflight"` ≥50%, `None` below 50%. `ContextCompressor.compress`
returns `CompressionResult` (`messages` new list, input never
mutated; `lineage_id` uuid4 hex fresh per compression; `stats` with
tokens before/after, ratio, threshold hit, protected/summarized
counts). Standard order: flush first → summarize middle turns →
`protect_last_n` (default 20, halved at gateway, min 4) kept whole →
tool call/result pairs never split (messages grouped into units first;
boundaries fall only between units) → fresh lineage. System messages
always kept whole. Summary is one `assistant` message prefixed
`[Context summary of earlier turns]` (role `assistant`, not `system`,
so it never clashes with G20's system prompt). Summarizer is an
injected callable; default is the cheapest reader model via
`puspa.router.complete.complete` (`default_llm_complete(role="reader")`),
with a deterministic extractive fallback that never raises (recorded
in stats). The summary prompt orders every number/date/amount/name/
identifier preserved exactly (orphan-number rule). Flush is an injected
callback run before summarization, failure never blocks compression;
the real default (when `user_id` is given) calls
`puspa.memory.consolidate.consolidate_idle` (lazy import, no Postgres
needed at import). `prepare_turn_messages` is the call site: check
thresholds, compress when hit, return `(messages, None)` otherwise.
Measured (tiktoken, real tokens): a 61-message history 4,634 tokens →
preflight 1,583 (ratio 0.34; 20 protected, 40 summarized) → gateway
813 (10 protected). Honest limits: **not wired to the live turn loop**
— the live loop is nanobot's `AgentLoop` (it builds its own transcript
from session history; `PuspaHook.before_run` only receives a deep-copy
snapshot, so the hook cannot rewrite the live message list); the
correct call site is `prepare_turn_messages` for a future Puspa-owned
turn wrapper (or an upstream nanobot hook that allows pre-request
message rewriting). The flush default is idle consolidation (episodic
older than 30 days), NOT a turn-scoped flush — turn-scoped flush
(write this turn's facts to episodic before summarizing) does not
exist as a callable yet (follow-up; the callback is ready to take it).
No live LLM call was made (no key): token/Rp/latency of the live path
still need one smoke test. Variants not built (micro-compaction,
native/relay/prepared, image compression, trajectory compression).
Note: the worker's regression numbers for
`test_tool_result_compression.py` (85 passed + 3 failed + 1 error,
nanobot import) were an artifact of its `--noconftest` run; the
standard run is 53 passed and the central suite is green.

### G20 Prompt assembly + caching (commit fb67381a)

`puspa/runtime/prompt_assembly.py` (547 lines) +
`puspa/runtime/prompt_caching.py` (172 lines) +
`tests/test_prompt_surface.py` (22 passed, 0.30 s, in-memory) +
`tests/snapshots/prompt_surface.json` (69 lines). Home is
`puspa/runtime/` (not `puspa/router/`): the router is the model-call
path; prompt assembly is session state. `PromptBuilder.build(messages,
*, stable, context, volatile) -> PromptSurface` (`system_prompt`,
`messages`, `cache_breakpoint`, `snapshot_hash`), exactly the shared
interface. Layers accept `str | Iterable[str] | None`, normalized
deterministically, joined with `"\n\n---\n\n"` (same as nanobot) in
fixed order stable → context → volatile. `cache_breakpoint` is the
character offset where the stable layer ends (snapshot fixture: 216 =
`len(stable)`); the stable layer is byte-identical across turns and
volatile/context changes never move the breakpoint. Freezing: the
first `build()` freezes the stable layer; a later `build()` with a
different stable layer still renders the frozen text and records the
attempt as a `DeferredChange(kind="replace_stable")`.
`snapshot_hash` is SHA-256 over canonical JSON of
`{system_prompt, messages, tools}`. Tools are frozen at construction
(`with_tools()` returns a new builder, for the next session). The
builder NEVER compresses: input messages pass through unchanged (new
list, same dicts), with light validation only (valid role, `content`
present, tool messages require `tool_call_id`). `PromptSession` holds
one session's state (stable layer as tagged parts `base`/`skill:<name>`,
`model`, queue); `request_change(...)` → `"deferred"` unless
`apply_now=True` → `"applied"` immediately; `next_session()` drains
the queue FIFO into a new session while the old session keeps its
frozen layer. `PromptSurface.to_prompt_text()` flattens the surface to
one string for the `complete(prompt)` path. `prompt_caching.py` holds
per-provider breakpoint markers (`anthropic_system_blocks`,
`openai_request`, `anthropic_request`, `stable_prefix`), split out
because markers are a provider concern, not an assembly concern.
Real wiring delivered: the builder feeds both router formats without
editing `translate.py`/`complete.py` — `openai_request()` →
`translate_request` (openai→anthropic, system whole, tools included,
`report.dropped == []`); `anthropic_request()` converts history via
`translate_request` (tool messages → `tool_result` blocks,
`tool_calls` → `tool_use` blocks; hard failure if anything drops) +
system as a block with `cache_control: {"type": "ephemeral"}` on the
stable block; translate back anthropic→openai returns the system text
byte-identical. One real bug found and fixed during that wiring: the
first `anthropic_request` passed raw tool messages (invalid role
`tool` in Anthropic), so the back-translation dropped them — history
is now always converted first. Snapshot test fails when prompt/tools
change (proven) and passes after regeneration
(`PROMPT_SURFACE_REGEN=1 pytest
tests/test_prompt_surface.py::test_snapshot_matches_committed_file
--noconftest`; regen is idempotent — second run diffs empty). Honest
limits: **not wired to the live turn loop** — the system prompt today
is assembled entirely inside the nanobot dependency
(`ContextBuilder.build_system_prompt` / `build_transcript`;
`AgentLoop.__init__` hardcodes `self.context = ContextBuilder(...)`
with no injection parameter; `assemble()` in `puspa/chassis/factory.py`
can only inject `tool_registry`/`hooks`/`hook_factories`; the
`before_model_request` hook cannot change the transcript). Follow-up:
ask nanobot for a seam (accept `context_builder` in `AgentLoop`) or
build a Puspa-owned transcript builder; after that, measure real
cache-hit rate + cost per turn. The live path is untested (no
token/Rp/latency measured with a real provider). G19 was not in the
tree while G20 worked — interface compatibility was verified by the
coordinator after both landed (see above).

## Next: G7+G24 (memory)

In progress (spawned after Wave 3): two parallel workers, disjoint
files. G7 (DuskBot pattern): versioned facts with validity dates +
history/restore, DREAM cross-chat, trust anti-injection, journal +
undo, hybrid recall (FTS5 BM25 + local embedding + name + time, RRF
k=60, cosine 0.6 / 0.5 with a matching word, max 14 facts), memory
file size limits. G24 (hermes pattern): memory provider plug-in ABC
(pgvector stays one provider), background review fork per turn,
inactivity skill curator (active→stale→archived, archive as max,
pins immune), learning graph. FTS5 is owned by G7; G24 consumes the
recall interface without building FTS5 itself.

## Wave 4: G7+G24 (memory, done 18 Nov 2026)

Two parallel workers, disjoint files, one shared interface (fact
record shape + `record_fact` as an injected callable for the
background review). Central SOLO suite after the wave: **1,590 passed,
8 skipped, 1 warning, 9m52s** (baseline 1,491 + 99 new: G7 47 + G24
52). Postgres stayed online. Note: G7 reported 2 failures in
`tests/test_scheduler_link.py` in isolation (`WorkflowError: alur
tidak ditemukan`), proven pre-existing by reverting its scheduler
edits — in the full central suite those tests pass (test-order
dependency: they need a workflow created by another test file). One
combined run also showed 3 teardown fixture errors + one
`DeadlockDetected`; the same run re-run went green 108/108 (flaky,
not G7's code).

### G7 Memory tightening (commit 12bcd9d1)

Seven new modules + seven test files (47 passed, 0.82 s
`--noconftest`; also green with full conftest). `puspa/memory/facts.py`
(versioned facts: `PostgresFactStore` + `MemoryFactStore`, same
interface; record/supersede/expire/restore/history/at; record shape
`{id, user_id, key, content, status, valid_from, valid_until,
superseded_by, source, created_at, updated_at}`, `content` mapped to
the `value` column; `source="inferred"` stays `proposed`, never
silently active — the Rev 7 rule; statuses `active`/`superseded`/
`expired`, old `proposed`/`rejected`/`replaced` still readable in
history; `profile.py` still says `replaced` — unifying the vocabulary
is a separate small PR). `puspa/memory/trust.py` (16 instruction
regex patterns, external vs own origin/tool lists, `redact_secrets`
— the first redaction helper in the repo, `mark_blocked`,
`check_text` → allow/quarantine/blocked; wired into `record_fact` —
blocked/quarantined facts never become permanent, they go to the
journal with `[blocked: ...]` / `[quarantined: ...]` markers for user
review). `puspa/memory/journal.py` (append-only + `undo_last` — undo
expires instead of deleting; undo of a change restores the `before`
snapshot; `list_quarantined` review queue; optional JSONL
persistence). `puspa/memory/recall.py` (4-signal hybrid + RRF k=60,
cosine 0.6 / 0.5 with a keyword hit, max 14 facts; owns FTS5). FTS5
home: SQLite sidecar, not Postgres tsvector (`ts_rank` is not BM25;
the sidecar is a derived index, rebuildable via `FtsIndex.rebuild`;
default in-memory, optional file path for a persistent `state.db`).
`puspa/memory/dream.py` (DREAM cross-chat + `run_dream_job` scheduler
entry; LLM only via `resolve_llm_complete`; no LLM → status `no_llm`,
never faked; learned results recorded as `inferred` proposals;
expired kills matching active facts). `puspa/memory/write_cycle.py`
(`record_now` / `background_pass` idle ≥120 s or every 6 turns via
`consolidate.reflect_episodes` → proposals / `nightly_tidy` 03:00
expire + consolidate + DREAM). `puspa/memory/limits.py` (200 lines /
24,000 bytes, 256 KB per file — Puspa has no on-disk narrative memory
files (grep-verified), so the limits live as functions enforced in the
only narrative writer, `consolidate._extractive_summary`; the DREAM
40,000-byte budget is enforced in `dream.py`). Modified:
`puspa/memory/schema.sql` (`profile_facts`: status + `superseded`/
`expired`, `superseded_by` + `updated_at`, idempotent migration DO
block), `puspa/memory/consolidate.py` (one wiring:
`_extractive_summary` → `truncate_narrative`),
`puspa/scheduler/scheduler.py` (dream trigger + `schedule_dream` + one
dispatch branch only — verified live: trigger daily 03:00, next
2026-11-19 03:00 UTC, dispatch returns `job: dream, status: too_few,
applied: {learned: 0, expired: 0}` for an empty user, no LLM, no key).
Honest limits: the live DREAM path is untested (no key; needs one
`run_dream_job` with `llm_complete="default"` + BYOK key, recording
token/Rp/latency); `record_now`/`background_pass` are not wired to a
chat handler/idle detector (call sites documented in the
`write_cycle.py` docstring); the `replaced` vs `superseded`
vocabulary unification is a separate small PR; the persistent FTS5
sidecar is not activated (default in-memory, rebuilt per recall —
activate the file sidecar + `index_fact` sync on `record_fact` if
recall goes on the hot path).

### G24 Memory provider + background review + curator (commit c0a4e40c)

Four new modules + four test files (52 passed, 1.88 s
`--noconftest`, including 2 DB tests). `puspa/memory/provider.py`
(`MemoryProvider` ABC over the shared record shape;
`DefaultPgVectorProvider` routes by `record["layer"]` — episodic via
`record_episode`/`search_episodes`, profile via `add_fact` (live
facts only; `superseded`/`expired` rejected with `ValueError`),
procedural via `record_successful_task`; explicit registry,
first-registration wins, duplicate name → `ProviderShadowError`;
selection: argument → env `PUSPA_MEMORY_PROVIDER` → `pgvector`;
per-provider `config_schema` dict + `validate_config` fills defaults,
rejects unknown/missing-required/wrong-type keys).
`puspa/memory/background_review.py` (`review_turn(snapshot,
record_fact=..., recall_facts=..., sync=...)`; snapshot deep-copied,
daemon-thread fork, writes only via the injected `record_fact`, never
touches the main conversation/cache; deterministic extraction
(`extract_facts` + `is_memorable` filter); internal dedupe + via the
`recall_facts` seam; 120 s timeout enforced by `ReviewHandle.wait()`
→ `"timeout"`; `sync=True` inline for tests).
`puspa/memory/curator.py` (manual trigger, not a daemon; deterministic
one-step-per-run state machine active→stale (90 days inactive, same as
the procedural decay window) →archived (180 days); pins immune;
archived is terminal for the curator (only `record_use` revives);
archive = max, never delete (tested: all files remain); full snapshot
backup of the skills root BEFORE state changes; opt-in LLM
consolidation via an injected `consolidate` callable (result recorded
in the report, never auto-applied); `.usage.json` telemetry
(`use_count`, `view_count`, `patch_count`, `last_activity_at`,
`state`, `pinned`)). `puspa/memory/learning_graph.py` (skill +
memory-chunk nodes first-class; edges `declared` + `lexical_overlap`
(light stemmed declared-token vs chunk-concept overlap, default
threshold 0.3) + `related`; `related_skills`/`related_chunks` ranked
by weight; declarations read from `manifest.json`
(`tools`/`domains`) or `SKILL.md` frontmatter; in-memory + JSON
serialization). Modified: `puspa/memory/__init__.py` only (exports
`get_memory_provider`/`select_provider` — the file also carries G7's
exports; it rides in the G24 commit). Honest limits and findings:
G7's `record_fact` contract differed from the brief's assumption —
it is `record_fact(user_id, key, content, source=...)`, not
`(user_id, record)`; G24 wrote a `_default_record_fact` adapter (key
= `fact:<sha256[:16]>` of the content, so identical clauses share a
key and supersede cleanly; source passed through, `inferred` →
proposal, never active); the default path was tested live against
G7's Postgres store; trust-gate rejection (`stored: False`) is
handled as an error, not counted. **Recall seam mismatch: G24's seam
is `recall_facts(query) -> list`, but G7 provides
`hybrid_recall(query, facts, ...)` needing a facts list supplied —
the call-site adapter (`lambda q: hybrid_recall(q,
store.list_user(...))`) still needs writing at wiring time
(follow-up).** Wiring: the provider is a seam only
(`get_memory_provider` exported; old call sites still call layer
functions directly — migrating call sites to the provider is a
follow-up). Background review has no LLM summary yet (follow-up via
`complete()`); the curator has no LLM consolidation wired + no
Postgres mirror; the graph has no Postgres table; graph stemming is a
light suffix stemmer, not a real NLP stemmer. Simplifications
documented in code: provider discovery = registry dict + env var (no
`plugin.yaml`/`config_schema.py`/pip entry-point scan — follow-up if
third-party providers need self-registration); curator telemetry =
`.usage.json` sidecars, not a Postgres table (must work without a DB,
travels when a skill is copied; Postgres mirror is a follow-up);
graph = in-memory + JSON, not a table (derived view rebuilt from
skills + memory, consumed by the dashboard as JSON).
Note: both workers edited `puspa/memory/__init__.py`; the live file
carries both sets of exports and resolves (verified: `import
puspa.memory` exposes `facts`, `provider`, `get_memory_provider`).

## Next: G3+G27 (marketplace)

In progress (spawned after Wave 4): two parallel workers, disjoint
files. G3 (DuskBot + OpenBot + hermes authoring): marketplace registry
+ skill folder/manifest validation + import (disabled by default) +
install/rollback + trust scoring + share modes (off|ask|auto) +
authoring standards test + per-run narrowing + `search_tools`
overflow + price/license layer in the manifest (entitlement check is
an injected callable; real charging is G2). G27 (hermes plugin
system): published plugin API (`plugin_api.json` + `ctx`) + loader +
discovery (3 sources) + plugin types (general + single-select) +
boundary lint (test-enforced) + plugin catalog (YAML manifests +
admission rules + CI test) + optional MCP definitions (seed set).
Split: G3 owns `manifest.json` + `SKILL.md` (skill/module layer);
G27 owns plugin YAML manifests + `plugin_api.json` (plugin layer).
