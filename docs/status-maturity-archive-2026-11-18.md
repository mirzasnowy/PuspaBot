# Status archive: repo maturity phase, Waves 1-8 + CI lanes (18 Nov 2026)

Archived from `docs/status-maturity.md` on 18 Nov 2026, when the live
log crossed the 2,000-line hard cap of the file-size ratchet. History
is never deleted, only archived: this file is frozen and is never
appended to. The living log continues in `docs/status-maturity.md`
(Wave 9 onwards).

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

## Wave 5: G3+G27 (marketplace, done 18 Nov 2026)

Two parallel workers, disjoint files (G3 owns `manifest.json` +
`SKILL.md`, the skill/module layer; G27 owns plugin YAML manifests +
`plugin_api.json`, the plugin layer). Central SOLO suite after the
wave: **1,800 passed, 9 skipped, 1 warning, 11m15s** (baseline 1,590
+ 210 new: G3 109 + G27 101 passed + 1 skipped). Postgres stayed
online.

### G3 Module marketplace (commit 024d9a0f)

New package `puspa/marketplace/` (10 modules) + 8 test files + 1
helper (109 passed, 0.55 s `--noconftest`). `manifest.py` (strict
skill folder + manifest validation: id matches folder name +
`^[a-z0-9]+(-[a-z0-9]+)*$`, semver version, non-empty
name/description, non-empty triggerTerms, requiredCapabilities string
array; frontmatter `---` required at the top of SKILL.md; manifest
stores sha256, importedAt, license, compatibility, warnings,
skippedFiles, referencesSha256 (re-hashed, a missing reference file
fails); caps: max 30 skills, index max 4,000 bytes, max 20 staged).
`registry.py` (registry + import → install → rollback → uninstall +
audit; import takes only SKILL.md and lands DISABLED; rollback
restores the previous version from sha256 + source records (new
install row, the old version is never rewritten); uninstall removes
the install but keeps the audit; origins strictly builtin/user/library
from the manifest, the import argument only overrides). `store.py`
(own store: `create_schema()` with 3 tables
`marketplace_installs`/`marketplace_versions`/`marketplace_audit` +
`MemoryStore` + `PostgresStore`, same interface; install/rollback
records do NOT enter the ledger — no new ledger event types, still
31). `trust.py` (FLAGSHIP: trust score 0-100 from 5 signals —
Sigstore/local signature via the existing `verify_skill` = 30,
manifest completeness = 20, known license = 15, trusted source = 20,
clean install history = 15; floor `MIN_TRUST_SCORE = 60`; below the
floor install is refused without explicit user approval; no history
= 0 points, so even an unsigned skill from a trusted source only
scores 55 and is refused — the signature really decides).
`share.py` (off|ask|auto, default ask + two invariants: share never
changes the running conversation's index; a skill only reaches peers
no more numerous than where it was learned (cap `learned_from_peers`;
learned alone = cannot share)). `authoring.py` (tested authoring
standards: description ≤60 characters, native tools not shell utils
(grep→search_files, cat→read_file; code blocks excluded), platform
gating must be marked audited in frontmatter, human credit first
(author + credit line), standard section order, inline code blocks
max 10 lines, `{{config.x}}` placeholders must be declared in
frontmatter; `resolve_config` (asked at setup via asker) +
`inject_config` (injected at load)). `narrowing.py` (per-run
narrowing: deterministic triggerTerms match, or an injected
`llm_complete`; failure → fail-open whole catalog; above 50 tools the
rest wait behind one `search_tools` tool; above 128 tools the run is
refused with audit `agent.tool_limit_refused`; takes a tool list as
input, no dependency on G27's loader). `learning.py` (learn from chat
+ background review after reply: max 3 changes, 3-minute timeout, 3
consecutive failures → step discarded; deterministic by default;
optional `llm_complete`, failure → deterministic fallback, never
fakes proposals). `errors.py`. Price/license: the manifest carries
`priceCredits` + `license`, each install records both; the
entitlement check is an injected callable (default: free (price 0)
allowed, paid without entitlement refused) — real charging is G2.
Honest limits: the learning review (item 9) is not wired to the
reply loop (no clean seam from the reply loop; G24's background
review writes facts, not skill changes; call site + follow-up
documented in the `learning.py` docstring: wire
`review_after_reply` to the post-reply hook once it exists, with the
skill registry store as `apply_change`). The real Sigstore bundle
path is not tested end-to-end (trust scoring uses the existing
`verify_skill`; only the local Ed25519 signal was tested, including
rejection of a manifest changed after signing — no real bundle
fixture can be made offline). `puspa/skills/` does not exist in this
repo — the existing skill registry is
`puspa/chassis/skill_loader.py` (PuspaSkillsLoader + `verify_skill`);
the marketplace is a new package, the old loader untouched.
Entitlement is a seam (G2 owns real charging). No new deps (only
yaml, cryptography, psycopg already present). Design decision: index
entries are compact (no sha256/source/license — those stay in
store/manifest) so 30 skills fit the 4,000-byte index cap; the staged
cap 20 binds before the 30-skill cap, so filling the index fully
needs installing in parts (covered by tests). PostgresStore
smoke-tested manually (`create_schema` + round-trip in `puspa_test`,
rows cleaned); not in the automatic suite (tests designed
in-memory). 0 live LLM calls.

### G27 Plugin system (commit fb17f2ee)

New package `puspa/plugins/` (14 modules + 3 YAML catalog seeds + 3
JSON MCP seeds) + 6 test files (101 passed, 1 skipped, 6.43 s) +
chassis wiring. `context.py` (`PluginContext` `ctx`: the only surface
plugins may use — `register_tool`/`register_hook`/`register_command`/
`register_memory_provider`/`register_context_engine`/`log` + name
validation, JSON Schema parameter validation, handler arity
validation at load time). `plugin_api.json` (published API, single
source of truth: ctx methods, hook events, single-select kinds, name
patterns, allowed imports, core-importable plugin modules).
`loader.py` (discovery 3 sources user→project→pip entry point
`puspa.plugins`, fixed order + `load_plugin`/`load_plugins` +
`PluginRecord` + `run_command`; broken plugins rejected with
`PluginStructureError` naming the plugin and the problem;
`load_plugins` collects failures per source, never fails discovery).
`single_select.py` (generic single-select registry, same G24
pattern: first-wins, `PluginShadowError` anti-shadowing, explicit
selection → env `PUSPA_<KIND>_PLUGIN` → default; losers never
silently active + `install_into_provider_registry` bridge to G24's
`ProviderRegistry`, closing G24's entry-point follow-up for the
plugin path). `context_engine.py` (`ContextEngine` ABC, part of the
published API). `errors.py`. `builtin/echo.py` (general plugin proof:
tool `plugin_echo` read-only, hook `after_run`, command `echo`).
`builtin/simple_context.py` (single-select proof: tool
`remember_note` + engine `simple` — keyword overlap + stopwords +
word normalization, state shared only via `ctx.shared` — a real
mechanism, not an empty ABC). `builtin/memory_inmemory.py` (second
single-select proof: in-memory `MemoryProvider`, G24 record shape, no
Postgres). `adapters.py` (the ONLY module allowed to import nanobot:
`PluginTool` wraps a `ToolDefinition` into a nanobot `Tool`,
sync/async handlers, errors → `ToolResult.error` + `PluginHook`
dispatches to `AgentHook`, handlers isolated, `finalize_content` may
replace the answer + `attach_plugin_tools`/`build_plugin_hook`).
`boundary.py` (AST import lint: B1 core never imports builtin
plugins, B2 plugins only use the published API, B3 builtins don't
import other builtins; runs as `python -m puspa.plugins.boundary`,
exit 1 on violation). `catalog.py` (third-party plugin YAML manifests
+ admission: required fields, license allowlist MIT/Apache-2.0/BSD/
ISC, semver, https github/gitlab repo, kind, `api_version`, optional
`price_credits` owned by G3 + duplicate ids rejected +
`check_repository_alive` (HEAD then GET, never raises)). `catalog/`
seeds: 3 valid manifests (acme-notes, local-llm-context,
sqlite-memory). `mcp.py` (MCP definition format validation
server/tools/transport/dependencies + `check_readiness`/
`check_mcp_readiness` — checks modules importable + commands on
PATH, no installation/download/execution). `mcp_definitions/` seeds:
3 (local-files, http-fetch, sqlite). Modified:
`puspa/chassis/factory.py` + `puspa/chassis/__init__.py`
(`PuspaChassis` gains `plugin_dirs`/`plugins`/`plugin_failures`;
`load_chassis_plugins()` discovery (dirs + env `PUSPA_PLUGIN_DIRS` +
default user/project + entry point), failures recorded on the
chassis + ledger sink, never fail startup; `build_registry()`
attaches plugin tools (same `PuspaTool` enforcement); `assemble()`
adds `PluginHook` only when plugins exist; default behavior
unchanged — without plugins hooks stay 1, old factory tests pass).
Bug found and fixed: `load_plugin(..., single_select=registry)`
discarded an empty registry passed by the caller
(`SingleSelectRegistry` defines `__len__` → falsy); fixed to `is not
None` (caused 4 of 6 initial test failures). Tests: 101 passed, 1
skipped (seed repo liveness check needs network + fictional
`github.com/acme/*` repos, gated by `PUSPA_CATALOG_NETWORK=1`);
regression with conftest 105 passed, 1 skipped (101 plugin + 4
`test_factory_rails.py`); boundary lint CLI clean on the current
tree; probes prove the lint fails (B1/B2/B3) on newly introduced
violations (synthetic tree, never touched the real tree); ratchet +
drift 6 passed. Honest limits: two file-plugins with the same stem
from different dirs share the module name `_puspa_plugin_<stem>` in
`sys.modules` (second overwrites first) — doesn't break behavior
(each record holds its own context/handlers, duplicate ids still
rejected) but naming should be made unique per path in a follow-up.
pip entry point tested with monkeypatched
`importlib.metadata.entry_points`, not a real installed distribution
— follow-up: install a dummy distribution with a `puspa.plugins`
entry point in CI. Catalog liveness only tested fail-closed (missing
URL → False); the live seed-repo check is skipped. MCP seed only 3
definitions (not 66 like hermes); `check_readiness` only checks
presence, not matching versions. `python -m puspa.plugins.boundary`
emits a harmless stdlib `RuntimeWarning` (boundary already in
`sys.modules` when the package `__init__` imports it). Not yet: PM
plugin installation (out of G27 — PM prepares deps before use; here
only the readiness-check side), a dashboard calling `check_readiness`
(call site doesn't exist yet; the function is ready).

**Integration gap (coordinator-verified): the G3↔G27 seam is
one-sided.** G27 exposes `load_plugin` (`loader.py:339`), but G3's
marketplace code never imports or calls it (only a comment in
`narrowing.py:20` mentions the G27 loader). The marketplace
installer needs a `plugin_loader` injected callable (default lazy
import of `puspa.plugins.loader.load_plugin`) so installing a
plugin-type module actually loads it. Follow-up, not a blocker.

## Next: G2+G8 (billing + scheduler)

In progress (spawned after Wave 5): two parallel workers, disjoint
files. G2 (DuskBot billing + hermes client state machine): contract
v1 (Cart → Quote with line items, one price source, additive only) +
swappable payment provider module + fake provider + webhook with
signature verification + signed entitlements + usage ledger
(`UsageRow` + Battery view + forecast) + client state machine
(typed refusal codes, idempotency, settlement polling, effect
preview, fail-closed). **Billing model (credits vs subscription vs
selling compute) is Nero's decision: the mechanism is built with the
model as a config (default: the brief's numbers — tiers +
credits); no real money moves (no real gateway integration, no real
charges); the model question goes to the parent for Nero.** G8
(DuskBot routines + hermes cron): explicit-union schedule schema
(once/daily/interval/cron + explicit timezone) + split
due/schedules/execution/recovery/holds/receipts modules + event
receipts (idempotency key per event) + general cron job model
(prompt + skills + deliver + per-job zone + grace) + atomic saves.
Shared seam: `puspa/billing/ledger.py::task_budget_remaining(user_id,
task_id, *, cap_rp=2000, dsn=None) -> int` (G2 defines it; G8's
curriculum runner calls it via an injected callable, default lazy
import). G2 does NOT touch `puspa/scheduler/`; G8 does NOT touch
`puspa/billing/` or `puspa/pricing/`.

## Wave 6: G2+G8 (billing + scheduler, done 18 Nov 2026)

Two parallel workers, disjoint files. Central SOLO suite after the
wave: **1,955 passed, 9 skipped, 1 warning, 11m48s** (baseline 1,800
+ 155 new: G2 71 + G8 78 + 5 curriculum budget tests; 1 more from
collection differences under conftest). Postgres stayed online
through the wave itself — but see the incident below.

**Incident: VM replaced mid-wave (~09:40 WIB).** The first central
suite run was SIGTERM'd at 32%; the second run failed with 1,960 DB
errors because Postgres was gone (packages wiped, the `postgres`
user gone). The new VM ran the Hatch os-intent replay (PID 772),
which reinstalled postgresql-16 + pgvector 0.6.0; the coordinator
waited for the replay, then restored the cluster (roles root +
puspa_app, DB puspa_test, `infra/schema_core.sql`). The third suite
run went green. Lesson recorded: after a VM replacement, check
`pg_lsclusters` + package presence BEFORE the suite; the replay
reinstalls packages but not the data (roles/DBs must be recreated).
The G8 push also failed once transiently (GitHub API
`RemoteDisconnected` on the first ref fetch, nothing pushed) and
succeeded on retry.

### G2 Billing (commit 1ac8b86e)

Eight new modules + `__init__` exports + 7 test files (71 passed,
0.63 s `--noconftest`, repeated 3x stable; combined regression 116
passed in 36 s). `config.py` (single price source: tiers Free Rp0 /
Plus Rp99.000 / Pro Rp199.000 / Team Rp499.000, packs Rp9.000/20,
Rp35.000/100, Rp150.000/500, 15 free credits/month, `billing_model`
("credits"|"subscription"|"compute", default "subscription"), trial
14 days, grace 30 days, max 5 clouds, invested thresholds 3 bots /
50 messages / 3 routines, snooze 10 days). `contract.py`
(additive-only `validate_additive`, Cart → Quote with line items +
total, annual discount 2 months free (12 billed 10), dedicated from
spec: base 50.000 + 15.000/vCPU + 5.000/GB RAM + 10.000 per 20 GB
disk). `providers.py` (`PaymentProvider` interface;
`FakePaymentProvider` deterministic, checkout id from the
idempotency key, key conflict = `KeyConflictError`;
`LocalGatewayProvider` stub — all real gateway calls =
`NotImplementedError`, HMAC verification still works; product keys
from env `PUSPA_BILLING_PRODUCT_KEY_<PRODUCT>`). `webhook.py`
(`handle_webhook(payload_bytes, signature, secret)`: HMAC first,
duplicates via idempotency key return the old result, unknown events
recorded in `unknown_events` + ignored, malformed payload rejected;
plain Python function, not a live HTTP route). `entitlements.py`
(HMAC-signed per user/machine + tier + validity; trial via config;
grace 30 days; max 5 clouds/account from config; verify signature →
tier → date; status active/grace/expired/invalid). `ledger.py`
(`UsageRow` + `MemoryUsageStore` + `PostgresUsageStore`, own table
`billing_usage_rows`, idempotent `create_schema()` — NO new event
types in the core 31-type ledger; `battery_view` 7-day totals per
bot/routine, share, cap; `forecast_routines` frequent|costly;
`task_budget_remaining(user_id, task_id, *, cap_rp=2000, dsn=None)
-> int` — the exact shared-interface name/signature with G8; no DSN
→ process in-memory store, with DSN → Postgres; never negative;
`upsell_due`). `client.py` (12-state state→render table; fail-closed
on fetch failure; 9 typed refusal codes + copy + recovery + state
each; unknown codes show the server message; idempotency key per
charge, conflict = new charge with a new key; settlement polling 2 s
cap 5 min with injectable sleep/clock; timeout called timeout +
"check balance first" framing; effect preview
charge_now/scheduled/no_op/blocked; upgrade matrix reason-before-
status; resumable `StepUpFlow`; portal deep-link `?plan=<tier_id>`).
`routes.py` (`handle_billing_request(method, path, payload, deps)`:
catalog, quote, overview, invoices, license (GET verify + POST
issue), checkout, webhook, team, clouds, ops funnel/grant/review;
404 unknown path, 400 malformed payload; plain Python function).
`__init__.py` only added exports (old wedge docstring kept).
`billing.py` + `fixture.py` untouched; `puspa/pricing/` untouched.
Honest limits: the live path is untested (no LLM, no real gateway;
`LocalGatewayProvider` deliberately stub — follow-up after Nero
picks a gateway + provides credentials: implement
`create_checkout`/`get_invoice`/`refund` in the stub, then test
checkout end-to-end in a sandbox). Monthly renewal / monthly credit
reset only defined as policy; scheduling is G8's domain. Module
generation price: config records `module_generate = 5` (brief locks
5–10); the quote states the exact amount before charge. GPU
video/try-on per-second costs have no numbers yet (brief: don't sell
before measuring) — not invented. Portal base URL default
`https://portal.puspa.example` is a config placeholder (env
`PUSPA_BILLING_PORTAL_URL`). Trial tier: trial issues an entitlement
for the requested tier with trial flag + `trial_days` config; no
automatic trial→paid flow (business decision). Flaky notes from the
worker: `test_billing.py` failed once with `Invalid port: ':1]'`
(httpx + NO_PROXY bracketed IPv6 — documented env issue; with
NO_PROXY sanitized 6/6 passed) and `test_ledger_event_types.py`
errored when combined with G8's parallel suite (shared `puspa_test`
truncate collisions) but passed alone — both are
environment/parallelism artifacts, not G2 code; the central suite
(run alone) is green.

**DECISION QUESTION FOR NERO (billing model — ask the parent to ask
Nero): which billing model does Puspa sell?** The mechanism supports
all three; only `BillingConfig.billing_model` changes. (a) Credits
only (pay-as-you-go) — packs; per-action metering; tiers only limit
features. (b) Subscription (current default) — monthly tiers; paid
tiers grant a monthly credit allowance (Plus 60, Pro 150, Team 400)
+ cloud machines; packs top up. (c) Sell compute — customers pay for
cloud machines (dedicated pricing) + pay AI costs directly to the
model provider with their own API key, like DuskBot; Puspa doesn't
sell AI tokens. Follow-ups: which local payment gateway (only
implementable after a choice + credentials), and whether the 14-day
trial auto-converts to paid or stops.

### G8 Mature scheduler (commit 8d0e9877)

Eight new modules + curriculum refactor + `__init__` exports + 8
test files + 5 budget tests in `test_curriculum_runner.py` (78
passed `--noconftest`, 2.18 s; combined with conftest 121 passed in
36.82 s; old regression 43 passed). `schedules.py` (explicit union
once/daily/interval/cron, all with an explicit timezone validated at
create/update, readers use `.get("timezone", "UTC")` so old records
work without migration; `validate_schedule`, `next_occurrence`
incl. a 5-field cron parser + `zoneinfo`, `merge_schedule` (type
change = whole schedule replaced, old timezone kept), separate
`RoutineScheduleInput` for user input validation). `due.py`
(not_yet|due|late|missed; late runs still run up to 12 h, then
missed and never run silently; `sort_due_first`, most-late first).
`execution.py` (compose an execution prompt for a fresh agent
without history: job prompt + injected skills + delivery target +
run id + due status). `recovery.py` (failed requeue ONCE,
`MAX_REQUEUE = 1`; second failure terminal, recorded as a failure
fact, owner notified; no unbounded retry). `holds.py` (`HoldStore`
hold until time/clear, per scope; `Pacer` minimum interval between
checks per scope; quiet hours `HH:MM-HH:MM` supporting past
midnight, per timezone). `receipts.py` (per-event idempotency
receipts: `InMemoryReceiptStore` + `PostgresReceiptStore`, table
`event_receipts`, atomic claim `INSERT ... ON CONFLICT DO NOTHING`;
`process_once` — duplicates don't run the handler). `claim.py`
(OpenBot-style claims: `work_items` table, `SELECT ... FOR UPDATE
SKIP LOCKED`, lease on the DB clock (`now()`), attempt cap (failure
= pending until cap, then `failed` terminal), advisory lock
`pg_try_advisory_xact_lock` (only one replica scans; others get
`[]`), plus an `InMemoryWorkQueue` fallback with the same surface).
`cron.py` (general cron scheduler: 4 formats in one model — relative
delay `30m`/`2h`/`1d`, interval `every 2h`, cron expression, ISO
timestamp; job carries prompt + skills + `deliver_to` + optional
model/provider/script + top-level timezone validated at create/
update, `.get()` in readers; fresh-agent execution without history;
state + `next_run` updated; missed runs reported, not executed;
quota hold; incidents; separate delivery queue (`flush_deliveries`);
atomic storage (temp + fsync + rename); one model tool
`cronjob_manage` (create, list, update, pause, resume, run, remove)
as a Python function, not an HTTP route). Modified: `curriculum.py`
(thin refactor: the runner takes a `budget_check` callable, new
status `budget_hold` that doesn't consume the daily quota; the
default is a lazy import of
`puspa.billing.ledger.task_budget_remaining` + fail-soft if the
module is absent; NO billing import at top level; 5 tasks/day +
Rp2.000/task caps intact as policy above the general scheduler),
`__init__.py` (new public exports), `test_curriculum_runner.py` (5
new budget tests). `scheduler.py` NOT touched at all
(schedule/bill due/price drop/manual triggers, dream,
trust_root_refresh unchanged). Three bugs found by tests and fixed:
(1) `claim_work_items` couldn't reclaim expired-lease items
(`claimed` status missing from WHERE) — fixed in Postgres and
in-memory; (2) `cronjob_manage(create)` didn't pass `now` to delay
parsing (`30m` computed from the real clock, not the test clock) —
fixed; (3) `flush_deliveries` kept delivered deliveries — fixed
(delivered dropped from the queue). Honest limits: 0 live LLM calls
(all cron execution uses an injected `llm_complete` stub); the live
path (`complete` via `default_llm_complete`, lazy in
`cron._resolve_llm_complete`) is untested — follow-up with a key.
`schedules.py` was truncated during writing (line 196, incomplete
`except Value`) — fixed, the whole file verified passing. Regression
failed 2x not from G8's code but from G2's parallel billing suite on
the same `puspa_test` DB (conftest truncate collisions;
`ForeignKeyViolation` + deadlock seen); after G2's process finished,
full green regression. Lesson for the coordinator: don't run two
DB-using workers together (already known — the parallel workers' DB
tests collide; G8's own claim/receipts tests use their own tables
`work_items`/`event_receipts`, not truncated by conftest, so they're
safe). Seam verified by the coordinator: the curriculum's default
budget check resolves to G2's real function (no spend = full
Rp2.000 remaining); no top-level billing import in `curriculum`.

## Next: G9+G10 (decider + layered connectors)

In progress (spawned after Wave 6): two parallel workers, disjoint
files. G9 (DuskBot decider): one small module with a strict contract
— 3 question forms (Choice/Score/YesNo), typed answers, pluggable
backend, 1,500 ms default timeout, failure = value not exception,
explicit `DECIDER_JOBS` list with a seam per job, key-check, decider
log with a state hash; a layer BELOW planner + world model, not a
replacement. G10 (DuskBot 4-layer connectors): native 1-click
(Indonesian wedges: Tokopedia/Shopee/Blibli + PLN/PDAM/pulsa) +
aggregator (broker + advertised-tool allowlist) + generic MCP
(registry + OAuth pending sign-in, constant-time state comparison) +
token apps (paste token → live check → sealed store → MCP server;
refresh 10 min before expiry, check every 5 min) + per-module
account choice + advertised-tool allowlist. **Wedge live execution
is gated by a config flag, default OFF, pending Nero's ToS decision
(decision 15 — live price execution held); the worker builds
definitions + contracts + fake/test providers, NOT live scraping.**

## Wave 7: G9+G10 (decider + layered connectors, done 18 Nov 2026)

Two parallel workers, disjoint files. Central SOLO suite after the
wave: **2,022 passed, 9 skipped, 1 warning, 12m18s** (baseline 1,955
+ 67 new: G9 34 + G10 33). Postgres stayed online (no VM replacement
this wave; the restored cluster from Wave 6 held).

### G9 Decider (commit a1281ee8)

New package `puspa/decider/` (7 modules, 1,245 lines) +
`tests/test_decider.py` (521 lines, 34 passed in 0.38 s
`--noconftest`; also 34 passed in the normal conftest lane).
`questions.py` (3 forms `ChoiceQuestion`/`ScoreQuestion`/
`YesNoQuestion` + same-typed answers, validation at construction,
`build_prompt()` asking for JSON only). `backends.py`
(`RuleBackend` deterministic keyword overlap, confidence =
winner-over-runner-up margin; `LLMBackend` via the router's
`complete()`, reader role, strict JSON parsing; `UserEngineBackend`
alternative chooser, 8,000 ms budget, fast fail `no_user_engine`
when no engine). `decider.py` (`Decider`: sequential fallback
chain, default timeout 1,500 ms (daemon thread + join budget, plus
an injectable clock check), key-check before each call, NEVER throws
to the turn — all failures become `{ok: False, reason}` + a
per-backend `attempts` list). `jobs.py` (`DECIDER_JOBS =
("moduleRouting", "inboxTriage")`, each job has its own seam
building the question + interpreting the answer; `moduleRouting`
picks 1 module from a message + candidate modules; `inboxTriage`:
actions `act_now`/`act_later`/`ignore` + priority from sender/
action). `keycheck.py` (`check_backend()` separate, fast fail
`missing_api_key`/`no_user_engine`, never throws). `log.py`
(`DecisionLog` + `state_hash` SHA-256 over canonical JSON `{job,
question, backend, value/reason}`; stable question ids (sha256, not
Python's per-process-randomized `hash()`), so the same decision
always hashes the same and can be replayed). Regression:
`test_file_size_ratchet` 3 passed; `test_router_complete` +
`test_marketplace_narrowing` + `test_decomposer_llm` 71 passed. Live
numbers (2 calls, cheapest reader model
`meta-llama/llama-3.1-8b-instruct`): call 1 (1,500 ms budget) — the
LLM timed out at 1,513 ms, the chain fell back to the rule backend
and answered correctly (`bills`, confidence 1.0); timeout + fallback
proven live, not just in stubs. Call 2 (30 s budget, deliberately
loosened to measure) — the LLM answered correctly in 1,604 ms
decision latency (1,741 ms wall). Tokens and Rp NOT measured:
`LLMBackend` only returns text, not `CompletionResult` — follow-up:
pass token/cost from `complete_detailed` through the backend.
Honest limits: NO real wiring — deliberate, not negligence. No
clean seam exists in the areas they're allowed to touch: module
routing today is a workflow trigger in `puspa/modules/manifest.py`
(not a message dispatcher), and the narrowing path is in
`puspa/marketplace/narrowing.py` (another worker's area, forbidden).
Correct call sites documented in `__init__.py`: (a) `narrow_tools`
should ask the decider a `ScoreQuestion` over skill ids (replacing
`_llm_select` + `_match_scores`), needs the coordinator to assign
to the marketplace owner; (b) a future message-to-module dispatcher
uses the `moduleRouting` job; (c) live inbox triage waits for an
inbox polling loop (the Gmail connector is in G10's area). Live
latency ~1.6 s > the 1,500 ms budget in this sandbox (egress proxy +
OpenRouter). With the default budget, the LLM practically always
loses to the rule backend here. Undecided: raise the LLM-specific
budget, or accept rule fallback as normal behavior. The worker
didn't change the 1,500 ms contract unilaterally.
`UserEngineBackend` built + tested with an injected callable, but no
engine seam exists in the repo (`engine_complete` not found
anywhere) — live wiring is a follow-up. `DecisionLog` is in-memory
only; persistence needs a new ledger event type (forbidden in this
item) — follow-up. One design fix during work: rule backend
confidence was initially a keyword-list fraction (triage `act_now`
only 2/11 = 0.18 for a clearly urgent message) — replaced with
winner-over-runner-up margin, and 3 failing tests were fixed on the
implementation side, not by loosening expectations.

### G10 Layered connectors (commit a29ce0c3)

Seven new modules (1,593 lines) + `__init__.py` modified + 6 test
files (715 lines, 33 passed in 0.74 s `--noconftest`; with conftest
68 passed together with `test_connectors.py`, `test_plugin_mcp.py`,
`test_vault.py`, 19.71 s). `allowlist.py` (FLAGSHIP: `ToolAllowlist`
+ parse env `CONNECTOR_ALLOWED_TOOLS`/`CONNECTOR_SERVICE_SLUGS`
with a 4,096-byte cap (`MAX_ENV_BYTES`; over-long values rejected,
not silently truncated); `advertise()` filters tools by name +
service; `assert_no_leak()` proves no tool outside the allowlist
leaks to the engine). `native.py` (`ConnectorDefinition`/`Operation`:
request/response contracts + `requires_approval` + `live` +
`CuratedDirectory` with explicit registration, duplicates rejected,
slug/operation validation). `accounts.py` (account sync:
`ConnectorAccountStore`, one account used by many bots/modules, no
secrets, only `credentials_ref` + per-module account choice
`ModuleAccountSelection`: explicit resolution order = module choice
→ explicit default → user's first account → None; module choice
beats default; another user's account / wrong connector rejected;
unlink removes choices pointing at that account). `wedges.py` (7
wedge definitions: Tokopedia/Shopee/Blibli `check_price`; PLN/PDAM
`check_bill`+`pay_bill`; pulsa/data `check_bill`+`top_up` +
`FakeMarketplaceProvider`/`FakeBillProvider` deterministic example
data, `example.id` URLs, `fake-...` receipts; pay/top-up require
approval + `LiveExecutionGate`/`execute_live`). `aggregator.py`
(`Broker` ABC interface + `FakeBroker` (github/notion/slack,
deterministic) + `ProjectSession` (allowlist from env, `call_tool`
rejects unadvertised tools); the real broker doesn't exist — no
broker choice + credentials; clearly documented as a follow-up).
`mcp_registry.py` (uses the G27 `puspa/plugins/mcp.py` MCP
definition format, no second format; OAuth: one pending sign-in per
server, state compared with `hmac.compare_digest`, listener called
exactly once then removed, pending expires after 10 minutes;
`engine_view()` contains only id/name/tools (no `server.env`,
tokens, or Authorization header); `authorization_header()` for
transport only). `token_apps.py` (`validate_token_live`: one GET
validation call; 2xx valid, 401/403 invalid, network failure =
`TokenValidationError`, not fake success → `attach_token_app`
(live check first, then seal; invalid tokens store nothing) → seal
via `VaultSealStore` (existing vault envelope encryption) or
`InMemorySealStore` (tests only) → `token_app_to_mcp_definition`
(G27 format, no token); refresh 10 min before expiry
(`REFRESH_BEFORE_SECONDS=600`), checked every 5 min
(`CHECK_INTERVAL_SECONDS=300`); `needs_refresh`/`due_for_check`/
`refresh_token_app` defined, scheduling is the scheduler's domain
(untouched)). `__init__.py` modified: English docstring + main-name
exports. `google.py`/`grants.py` untouched. **ToS flag (most
important): `live_execution_enabled` defaults OFF (env
`PUSPA_CONNECTORS_LIVE_EXECUTION`, `1`/`true` = ON). Fake providers
run without the flag and without network. `execute_live()` refuses
when the flag is OFF (`LiveExecutionDisabledError`, mentions
decision 15 still open), and STILL refuses when the flag is ON
(`LiveExecutionError`: "no live client ... yet") because no live
marketplace/biller client exists at all. No scraping or live calls
to marketplaces/billers anywhere.** Live measurement: one validation
call to `https://api.github.com/user` with a fake token (via egress
proxy): HTTP 401 in 1.37 s — the live-check path proven working, the
fake token rejected as it should be. No real credentials, no cost
(Rp 0, 0 LLM tokens — no LLM calls at all). `VaultSealStore`
round-trip verified against the real `puspa_test` DB with a real
user. The probe user is no longer in the DB (likely deleted by
TRUNCATE from the parallel G9 worker's conftest; checked: no probe
rows remain). Honest limits: ToS decision 15 still open (Nero's):
no live wedge execution until Nero decides; the flag +
`execute_live()` are the single refusal point. The real aggregator
broker doesn't exist (interface + fake only). Real token refresh
doesn't exist: `refresh_token_app` calls an injected refresher;
per-provider OAuth refresh implementation is a follow-up; the
5-minute check scheduling is the scheduler's domain (untouched).
`ConnectorAccountStore` is in-memory: no persistence table yet
(follow-up: a table like `agent_connectors`); account-selection
logic is complete and tested without a DB.
`token_app_to_mcp_definition` uses the placeholder command
`puspa-token-app`: the transport running the token-app server
doesn't exist yet (follow-up when there's an MCP runner). Minor
note: the first vault probe failed because vault `user_id` must be a
UUID + FK to the `users` table — not a bug, existing vault behavior;
the second round-trip with a real user succeeded.

## Next: G22+G23 (credential pool + tool registry)

In progress (spawned after Wave 7): two parallel workers, disjoint
files. G22 (hermes credential pool): unified BYOK provider resolver
(one shared runtime resolver for PWA, gateway, cron, harness) +
per-(provider, model) credential pool with rotation cooldown (the
area of Nero's own PR #146450 — cooldown scoped to the requested
model, not global) + per-profile secret scope (never raw
`os.getenv`) + redaction module per secret type + OAuth (PKCE,
device flow, anon auth). G23 (hermes tool registry): dependency-free
registry + automatic discovery (`register()` at import time) +
registration/exposure separation via toolsets + `check_fn` per
module (key/permission/availability) with a per-tenant TTL cache +
dangerous-command approval (yolo mode, approval floor, per-OS
variants) + footprint ladder. Shared seam: G23's `check_fn`
credential check defaults to a lazy import of
`puspa.credentials.scope.check_credential(profile_id, provider,
model=None) -> bool` (exact name, defined in both briefs; G22
defines it, G23 consumes it via an injected callable).

## Wave 8: G22+G23 (credential pool + tool registry, done 18 Nov 2026)

Two parallel workers, disjoint files. Central SOLO suite after the
wave (run on the final tree, including the CI lint fixes below):
**2,224 passed, 9 skipped, 1 warning, 13m39s** (baseline 2,022
+ 202 new: G22 83 + G23 119). Postgres stayed online (no VM
replacement this wave).

### G22 Credential pool + secret scope (commit 6d6f4fc6)

New package `puspa/credentials/` (6 modules, 1,866 lines) + 5 test
files (1,012 lines), 83 passed (verified by the coordinator).
`resolver.py` (unified BYOK resolver: `ProviderRegistry` with
shadowing rejected, `PROVIDER_REGISTRY` with 13 providers from the
router catalog + OpenAI/Anthropic/Gemini/Groq/DeepSeek/Mistral/
Together/xAI/github_copilot/openai_codex/openai_compatible/Ollama;
`ResolvedCredential` repr shows only a SHA-256 fingerprint, never
the key; `resolve()` + `resolve_for_complete()`; default key source
is the existing BYOK path, no duplicated lookup). `pool.py`
(per-(provider, model) `CredentialPool`: cooldown keyed by
(provider, model, key fingerprint) — the area of Nero's PR
#146450, proven by test (same key, model A cooldown does NOT block
model B); `acquire()` skips cooldown keys; `mark_rate_limited()`
honours Retry-After, default 60 s; `mark_auth_failed()` 600 s;
`AuxiliaryClient` shares the pool, rotates on rate limit/auth
failure). `scope.py` (`CredentialScope`: per-(profile, provider)
source callable; `vault_source()` / `pool_source()` /
`scoped_env_source()`; `get()` never falls back to another profile;
`check_credential(profile_id, provider, model=None) -> bool` with
the exact agreed name/signature — the seam G23 consumes).
`redact.py` (8 secret types: API keys/tokens, command segments,
DB URLs, headers, Indonesian phone numbers, registered vault
values, home paths, partial-key tails; marker `«redacted»` matching
`puspa/memory/trust.py`). `auth.py` (PKCE verified against the RFC
7636 vector, device flow with injectable sleep, refresh, anon auth
with no token). No new deps, no new ledger event types. Honest
limits: `complete.py` does NOT use the resolver yet
(`resolve_for_complete()` is ready, the call-site swap is a
coordinator follow-up, then one small live call to prove it);
OAuth token storage is still in-memory (same as
`puspa/router/oauth.py`); 0 live LLM calls.

### G23 Tool registry + toolset + check_fn (commit 7d5b0d31)

New package `puspa/tools/` (7 modules, 1,641 lines) + 5 test files
(1,121 lines), 119 passed (verified by the coordinator).
`registry.py` (dependency-free: `register()` import-time + decorator,
`discover()` package scan, schema collection, sync + async dispatch
`handle_function_call()` / `handle_function_call_async()`, JSON
error wrapping, multimodal envelope; string JSON from a handler
passes through as-is, dict merges into the envelope).
`toolsets.py` (registration/exposure split: `_PUSPA_CORE_TOOLS`
only `plugin_echo`, presets `default` + `coding` (core +
`run_code`), per-tenant `register_custom()`, exposure honours
`ToolAllowlist.is_advertised` + availability check).
`check.py` (`AvailabilityChecker`: per-(profile, provider, model)
TTL cache, default 300 s, injectable clock; stale cache = re-check,
not fail; raising check = fail-soft; credential seam calls G22's
`check_credential` directly, verified live by the coordinator
(unknown profile → `credential_missing`)). `approval.py`
(`classify_command()` with separate POSIX/Windows patterns,
`ApprovalGate` with callback, yolo mode, approval floor — DANGEROUS
can never be yolo — `ensure_allowed()` raises `ApprovalDenied`).
`footprint.py` (`recommend_layer()` → cli/skill/plugin/core_tool as
a decision function, not a doc). `builtin.py` (thin adapter:
`run_code` from chassis `SandboxTool`, `plugin_echo` from the
builtin echo plugin). No new deps. One bug found and fixed by the
worker: the same empty-registry falsiness as G27 (`registry or
_DEFAULT_REGISTRY` silently swapped a given empty registry;
fixed to `is not None` in registry/toolsets/builtin + 1 regression
test). Honest limits: approval is NOT wired (no clean seam yet —
the production harness runs no shell command; call site
documented in `approval.py`, wire it when the first shell seam
lands); `run_code` success in the sandbox is untested here
(Docker unavailable — only the fail-closed path was proven live);
the `coding` toolset holds only 1 extra tool so far.

**Coordinator fix inside the G23 commit:** `builtin.py`
originally imported the builtin plugin module directly (`from
puspa.plugins.builtin import echo`), which the G27 boundary lint
(rule B1) forbids — core must load builtin plugins by module path
through the loader. Rewritten to
`load_plugin("puspa.plugins.builtin.echo")` and the record's
public `.tools` accessor (no more reading the private `_tools`
dict). The G23 tests still pass unchanged (119).

## CI lint + typecheck lanes (Nero's order, done 18 Nov 2026)

Direct order from Nero (18 Nov ~17:19 WIB): add lint + typecheck +
big-repo CI practice to `.github/workflows/ci.yml`, pin ruff and
mypy first, then add the lanes. One iteration, commit e20e40d8
(359 files — the adoption touches almost every Python file).

- **Pins:** ruff 0.17.5, mypy 2.4.1, types-PyYAML 6.0.12.20261113
  in new `requirements-dev.txt` (dev tools only, never imported by
  Puspa code; separate file so the lint lanes stay fast and never
  install torch). `requirements.txt` points at it.
- **Config:** new `pyproject.toml` — ruff (line-length 100,
  target py312, explicit select + documented ignores) and mypy
  (non-strict, `explicit_package_bases`, `warn_unused_ignores`,
  overrides for `nanobot.*` / `FlagEmbedding` / runtime-injected
  top-level demo names). The ruff select is explicit because
  ruff's implicit default grows between versions (proven here:
  bare `ruff check` on 0.17.5 already enables I/UP/S/BLE/SIM).
  Not enabled, with reasons in the config: BLE (blind-except —
  deliberate fail-soft in fallback paths), TRY (raises inside try
  blocks are the error-translation pattern), S (bandit —
  silent except-pass guards + exec in the sandbox), E501 (width
  is the formatter's job). Ignored members of selected families:
  E402/PLC0415 (lazy imports are deliberate), PLR2004 (749 magic
  values — naming constants is a refactor), PLR0911/0912/0913/
  0915/0917 (complexity caps are design metrics), B905 (zip
  truncation is relied on), PLW0603 (module-level state).
- **Workflow:** `changes` job (detect-changes, hermes style —
  diff against the PR base or the previous commit, fallback to
  "everything changed") + `lint` (`ruff check`), `format` (`ruff
  format --check`), `typecheck` (`mypy puspa tests demo`) lanes;
  `all-checks-pass` stays the only required check and tolerates
  skipped lanes. The `checks` lane was also fixed: it ran `pytest`
  and `import yaml` without installing either (latent G15 bug —
  it would have failed on its first real GitHub run).
  `docs/verification.md` documents the lanes + the local commands.
- **Ruff:** 869 errors → 0 (worker: 605 auto-fix, 178 unsafe-fix,
  ~100 manual, no new `# noqa`). **Mypy:** 101 errors in 42 files
  → 0 (worker: no new `# type: ignore`, 2 stale ones removed;
  real fixes, including `_recent_run_statuses` return type int →
  list[str] and `next_trigger_at` → `datetime | None`).
  **Format:** 348 files reformatted, line-length 100.
- **Ratchet re-baselines** (recorded with reasons in the test):
  `agent_scan.py` 1052→1070 (+18, format), `test_sigstore_verify.py`
  1101→1124 (+23, format), `model.py` 1460→1605 (+145, mypy belief
  restructuring + format — read by the coordinator, clean, no
  duplication), new entries `test_router_oauth_translate.py` 1123
  and this doc 1084 (append-only logs / test matrix).
- **Boundary incident (found by the central suite, fixed by the
  coordinator):** the ruff worker's SIM117 fix in
  `puspa/plugins/boundary.py` broke B2 detection (the check ended
  up nested inside the B3 branch, dead), and G23's `builtin.py`
  violated B1. First suite run: 5 failed, all
  `test_plugin_boundary.py`. Fixed: B2 restored as its own branch
  in `lint_tree`, `builtin.py` loads echo via the loader (above).
  Second suite run: **2,224 passed, 0 failed.**
- **Honest limits:** CI cannot run end-to-end on this VM (no
  Docker, no GitHub runner) — first real run on GitHub; mypy is
  non-strict (most of the codebase is unannotated); the typecheck
  lane installs the full requirements (torch ~2.5 GB, slow first
  run, like the test lane); pre-existing Indonesian comments
  remain across the repo (only the 3 new ones from the mypy worker
  were translated — the English-only rule is enforced on new
  code, a repo-wide comment sweep is a separate job).
