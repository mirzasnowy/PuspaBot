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
Waves 16-17 were archived to
`docs/status-maturity-archive-2026-11-19-waves-16-17.md` on 19 Nov
2026, when the live log passed the soft cap again (1,002 lines).
Waves 18-19 were archived to
`docs/status-maturity-archive-2026-11-19-waves-18-19.md` on 19 Nov
2026, when the live log passed the soft cap again (1,032 lines
with the Wave 26 entry). Waves 20-21 were archived to
`docs/status-maturity-archive-2026-11-19-waves-20-21.md` on 19 Nov
2026, when the live log approached the soft cap again (980 lines
before the Wave 28 entry).
History is never deleted, only archived.

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

## Wave 23: Kosmos K4 (Causal Ledger, DONE 19 Nov 2026)

Third Kosmos item (K1, K3 done). K4 adds hypotheses "X -> Y
under condition C" with strength, evidence, expiry, extending
the learning loop (kosmos-design.md section C point 4).
Mechanism complete, tested, pushed by the coordinator
(worker 10ea8c40, verified centrally).

What was built (home `puspa/world/causal.py`, 580 lines —
hypotheses are beliefs about cause and effect, so they live
with the world model, not in `puspa/memory/`; the learning
loop calls into it):
- `CausalHypothesis` — cause X, effect Y (canonical key + the
  real `effect_value`), condition C ("" = unconditional),
  strength 0..1, evidence links (`ledger_event` /
  `observation` ids, deduped, newest 100 kept), `created_at`
  (known time), `expires_at` (TTL), `valid_from` /
  `valid_to` (valid time), status `active` / `expired` /
  `contradicted`, supporting/contradicting counts.
- Update rule (deterministic, documented in the module
  docstring and docs): first observation proposes at 0.5
  (neutral, not proof); consistent +0.1 (cap 1.0) and
  refreshes expiry; contradicting -0.2 (floor 0.0) —
  asymmetric on purpose, one counterexample disproves more
  than one success proves; below 0.2 -> `contradicted`;
  90-day TTL with no confirmation -> `expired`. Propose is
  idempotent; contradiction never creates a hypothesis;
  expired/contradicted are terminal (re-observing starts a
  fresh hypothesis, old one kept as history). Expiry is swept
  on every read/write, so expired hypotheses are never
  returned by the read path.
- Wiring: `LearningLoop(causal_ledger=...)` is opt-in (a plain
  loop behaves exactly as before). `record_misprediction`
  contradicts the predicted outcome and proposes the observed
  one; `record_feedback` "correct" strengthens, "wrong"
  contradicts and proposes the correction — except a
  correction held as a manual-edit proposal is deliberately
  NOT proposed (tested: it cannot leak back into predictions
  through the ledger). `predict` falls back to the strongest
  active hypothesis when no correction exists, so hypotheses
  genuinely influence predictions. Read path
  `expect(cause, condition)` exists on `CausalLedger`,
  `LearningLoop`, and `WorldModel`; `TaskRunner` and
  `AgentBrain` share the loop's ledger with the model (never
  wiping one the model already has). Both accept `condition=`
  and `evidence_event_id=` for evidence linkage. NO 32nd
  ledger event type — hypotheses cite ledger events by id
  only (test asserts `len(EVENT_TYPES) == 31`).
- Persistence: table `causal_hypotheses`, idempotent additive
  migration `infra/migrate_causal_ledger.sql` + identical DDL
  in `infra/schema_world.sql`; `apply_causal_migration` /
  `save_causal_hypotheses` (upsert by id) /
  `load_causal_ledger` / `causal_hypotheses_valid_at` /
  `delete_causal_hypotheses` in `puspa/world/store.py` (K3
  precedent). One real bug found by the worker's tests and
  fixed in code: Postgres returns UUIDs dashed while
  in-memory ids are undashed hex — load now normalizes.
- Bitemporality: the valid interval applies to the claim
  itself (`valid_to` set on contradiction/expiry;
  `causal_hypotheses_valid_at` answers "which claims held at
  T"). It does NOT apply to strength — the score is a running
  summary updated in place, so no as-of strength query;
  documented as such.
- `docs/causal-ledger.md` (204 lines): the record, the update
  rule, the lifecycle, the read path, honest limits.

Three test failures during development, all fixed and re-run
green (2 test bugs: strength-cap arithmetic, expiry
arithmetic; 1 real determinism wart: same-instant sort ties
fell back to random UUID order — sort key now
`(-strength, created_at, effect, condition)`).

Suite (coordinator, SOLO, 19 Nov 2026): first run 3,259 passed,
23 skipped, 1 warning, 1 FAILED, 21m13s, EXIT 1 (log
hidden_files/suite-wave23-k4.log) — the single failure was the
file-size ratchet: `puspa/world/model.py` grew to 1,626 lines,
past its 1,605 baseline, from the K4 `expect` + ledger slot the
worker added. The coordinator did NOT re-baseline new-logic
growth (the ratchet only sanctions mechanical re-baselines);
instead the file was split on the clean seam its own baseline
comment anticipates: the six belief dataclasses + price-key
helper + three constants moved to new `puspa/world/beliefs.py`
(238 lines), and the causal read path to the
`CausalExpectationMixin` in `puspa/world/causal.py`
(`WorldModel.expect` unchanged as API). model.py shrank
1,626 -> 1,397; baseline re-recorded down to 1,397 with the
reason in the test file. The affected set re-ran green after
the split (161 passed, same as before). Full central suite
re-run after the fix (coordinator, SOLO, 19 Nov 2026):
3,260 passed, 23 skipped, 1 warning, 21m11s, EXIT 0 (log
hidden_files/suite-wave23-k4.log). Reconciliation: 3,222 (Wave 22)
+ 38 (K4 module tests) = 3,260; skipped unchanged at 23.
Module tests (coordinator re-run): 38 passed
(test_causal_ledger.py 17 + test_causal_learning_wiring.py 16
+ test_causal_store.py 5, Postgres live). Affected existing
tests run together with the new ones (coordinator re-run):
161 passed (incl. test_learning_loop, test_feedback,
test_loop_persistence, test_loop_persistence_db,
test_world_model, test_world_beliefs, test_world_store,
test_bitemporal, test_world_brain_wiring,
test_failed_steps_teach, test_turn_loop). Ruff check + format:
clean. Mypy on the changed files: clean.

Honest limits: strength is counting with fixed steps, not a
probability — 0.8 must never be presented as "80% likely".
One observation proposes a hypothesis; a single coincidence
becomes a claim at 0.5 (mitigated by the neutral start and
fast contradiction, not cured). Conditions are exact strings,
no condition language; the loop passes "" unless the caller
names one, so most loop-formed hypotheses are unconditional.
`75000` vs `"75000"` are different effects; evidence caps at
100 links. The ledger only observes correlation, never proves
causation. THE PLANNER DOES NOT CONSULT HYPOTHESES YET — the
read path exists on the world model, but `planner.py` still
plans from price/bill/schedule/capability beliefs only;
wiring causal expectations into plan steps is a FOLLOW-UP,
recorded for a later wave (verified by the coordinator: no
"causal" / "expect(" in puspa/world/planner.py).

Remaining: Kosmos K5, K6, K10, K11, K12, K13, then B features
(B6, B9, B10, B11, B12, B13), then B15 culture LAST. Then uji
+ 15 gates + 10 reference tasks (waiting on Nero's decisions).


## Wave 24: G34 (Sandbox deepening from NVIDIA/OpenShell, DONE 19 Nov 2026)

New item from Nero (19 Nov ~08:24 WIB), after K4. The worker
studied NVIDIA/OpenShell first (shallow clone to /tmp, commit
`5c0d71641fbc9d62a4ee2f33de235f6662ae196c`, 19 Nov 2026:
README, RFC 0001 gateway/supervisor architecture, RFC 0004
typed resource requirements, `proto/sandbox.proto`,
`proto/sandbox_protocol.proto`, `proto/openshell.proto`,
`sftp.rs`, `pty.rs`, sandbox docs, supervisor-network) and
rewrote the mechanisms as Puspa's own Python code — no line
copied (worker d91c5051, verified centrally by the coordinator;
the clone is still at /tmp/openshell, commit verified by the
coordinator).

What was built (7 new modules, all in `puspa/sandbox/`):
- `policy.py` (309 lines) — `NetworkPolicy` deny-by-default +
  explicit `host:port` endpoints (a host must already be on the
  session allowlist; a policy only narrows it, never widens
  it), `FilesystemPolicy` (read-only/read-write +
  `include_workdir`), `SandboxPolicy` (version + root refusal,
  validated at admission).
- `quota.py` (273 lines) — `ResourceQuota` (CPU/memory/disk/
  pids; `gpu_count > 0` is refused, never silently downgraded
  to CPU), `docker_flags()`, `QuotaRegistry` (per-user
  overrides, live-session cap per user, reserve/release),
  `measure_disk_usage` (symlinks not followed).
- `session.py` (374 lines) — `SandboxSession` +
  `SessionManager` (create/get/list/kill, `reap_expired`
  lifetime + idle, fake-able clock; admission order: validate
  policy -> reserve quota -> build backend; any failure
  releases the reservation with no session left).
- `filesystem.py` (309 lines) — `SandboxFilesystem`
  (read/write/list/stat/delete, upload/download),
  `LocalSandboxFilesystem` (real, bound to the scope +
  disk quota checked before write), `DockerSandboxFilesystem`
  (uniform refusal: no persistent filesystem),
  `filesystem_for_backend`.
- `exec.py` (355 lines) — `ExecRequest` (argv *or* code, no
  shell string), `run_exec`, `exec_stream` (chunked
  stdout/stderr/exit, 64 KiB chunks; live via Popen on local,
  chunked-after-completion on Docker).
- `terminal.py` (245 lines) — `TerminalSession`, a real PTY on
  the local backend (`send_input` / `read_output` / `resize`
  TIOCSWINSZ + SIGWINCH / `close` kills the process group);
  `start_terminal` honestly refuses Docker.
- `browser.py` (197 lines) — `SandboxBrowserSession` +
  `SandboxBrowserManager` (one live session per sandbox
  session, profile dir inside the workdir, CDP endpoint only
  planned; `launch` refuses until a container host exists).

Changed:
- `puspa/sandbox/runner.py` (272 -> 320 lines) —
  `Sandbox(quota=..., policy=...)`, `docker_flags()` derived
  from quota + policy (defaults unchanged: 256 MB, 0.5 CPU,
  64 processes, `--network none`, non-root 65534). A policy
  that admits hosts is refused in `run()` *before* Docker is
  probed (it needs a filtered egress proxy that does not exist
  yet; never opened silently).
- `puspa/sandbox/__init__.py` (13 -> 81 lines) — exports all
  new APIs.
- `docs/sandbox-deepening.md` (169 lines) — the OpenShell
  mechanisms taken + mapping table to Puspa code + honest
  limits.
- `REUSE.md` (+44 lines, now 773) and `NOTICE.md` (+35 lines,
  now 760) — OpenShell Apache-2.0 attribution, study-only, no
  code copied.

One trap fixed by the worker: importing `puspa.computer` in
the sandbox `__init__` caused a circular import through
`puspa.harness` — all computer/harness imports in the new
sandbox modules are now lazy (inside functions) or
`TYPE_CHECKING`. Both import orders (`computer` first and
`sandbox` first) were verified working by the coordinator.

Suite (coordinator, SOLO, 19 Nov 2026): 3,319 passed, 23 skipped,
1 warning, 19m30s, EXIT 0 (log hidden_files/suite-wave24-g34.log).
Reconciliation: 3,260 (Wave 23) + 59 (G34 module tests) = 3,319;
skipped unchanged at 23. NOTE: the first suite attempt was SIGTERM'd
at 96% (no failures visible) by a VM replacement; the recovery took
three VM replacements in about an hour (the replay did not reinstall
Postgres within 15 minutes, a manual apt install failed on missing
package lists and was killed by the next replacement, then the replay
installed postgresql-16 16.15 + pgvector 0.6.0 itself). The coordinator
restarted the cluster, recreated role root, re-ran
infra/schema_core.sql, re-verified RLS, and re-ran the suite to green.
Module tests (coordinator re-run): 136 passed, 18 skipped
(test_sandbox_g34*.py + test_sandbox.py + test_computer*.py +
test_file_size_ratchet.py — 59 new G34 tests, all passed; the
18 skips are the live-Docker tests that cannot run on this VM).
Four failures during development, all fixed and re-run green
(3 test bugs; 1 real code bug: `list_dir` returned paths
relative to the listed directory, not the root — fixed in code,
not in the test). Ruff check + format: clean. Mypy: clean.
File-size ratchet: clean (all new files under 1000 lines).

Honest limits: still no live container on this VM (the kernel
rejects `setns`): `check_available()` is False, Docker
admission fails closed (tests prove the quota is released and
no session is left), Docker flags are only *built*, never
*executed*. The first live check waits for a Docker host /
VPS. No filtered egress proxy yet: a policy that admits hosts
is validated and stored, but exec under it is refused. No GPU;
sessions are in-memory (lost on restart); no live browser
(no Chromium is run anywhere). `LocalBackend` still executes
on the host (not a container); G34 only binds it to the
scope temp-dir jail + refuses credential-smelling env in
sessions, exec, and terminals.

Remaining: Kosmos K5, K6, K10, K11, K12, K13, then B features
(B6, B9, B10, B11, B12, B13), then B15 culture LAST. Then uji
+ 15 gates + 10 reference tasks (waiting on Nero's decisions).

## Wave 25 — Kosmos K5: other-agent goal models (DONE 19 Nov 2026)

K5 models what other people want and will do, one model per
contact (`puspa/world/agents.py`, new, 1,047 lines): the goals
that contact is pursuing (label, strength 0..1, supporting /
contradicting counts, evidence, status active / stale /
dropped) plus predictions about what the contact will do next
(reply, channel, next action, agreement), each a scored claim
with confidence and evidence. Update rule deterministic, no
LLM: a new goal starts at 0.5, each support +0.1 (cap 1.0),
each contradiction -0.2 (floor 0.0), below 0.2 it is dropped
and kept as history. Confidence = strength (or reply rate) x
evidence factor `min(1, observations / 3)` x staleness factor
(1.0 within 30 days, 0.75 within 60, 0.5 within 90, 0.25
after); a goal untouched for 90 days decays (strength halved,
status stale). K4 is used as context: `predict_agent` also
returns the active causal expectations for
`contact:<id>`, attached as evidence on the reply prediction.
K1 is used in shape only (labelled scored claims with
evidence, like `IntentHypothesis`), not its normalised
distribution: goal strengths are independent (they do not sum
to 1) and there is no desire / goal / values split — the
system cannot see another person's values, only what they
asked for and agreed to. An LLM-backed inferrer is only a
seam (`GoalInferrer` protocol); the built one is
`KeywordGoalInferrer`.

Wiring: `WorldModel` owns the per-contact models next to
`_contact_beliefs` (via `AgentModelMixin`, see the ratchet
fix below), `ingest_task_result` feeds the goal model from
the same interaction that feeds the contact belief, and
`WorldModel.predict_agent(contact_id, proposed)` is the read
path — fail-open (unknown contact = known False, empty
predictions). The planner is a real caller: new goal
`message_contact`, feasible only when a reply is likely and,
when an ask is given, the agreement confidence is at least
0.5; otherwise infeasible with reason `agreement_unlikely`.
The turn loop can call the same method on the session model.
Privacy (hard rule, kosmos-design.md section J):
`check_user_involved` raises `ValueError` before anything is
recorded when `user_involved` is False, `participants` lacks
`"user"`, or `source` is `third_party_thread`; the refusal is
recorded in `agent_refusals` (runtime only) and the goal model
is untouched. A goal model never causes an action on its own.
Persistence: new table `agent_goal_models` (one row per
user / agent / contact, upsert), idempotent migration
`infra/migrate_agent_goal_models.sql` + the same DDL in
`infra/schema_world.sql` (checked identical); the snapshot in
`world_beliefs.state` also carries `agent_models`. No 32nd
ledger event type: the 31 stay closed, goal models cite
ledger events by id only.

Files (worker 362c005c, not yet pushed): created
`puspa/world/agents.py` (997 lines at build time),
`tests/test_agent_models.py` (484),
`tests/test_agent_store.py` (248),
`docs/other-agent-models.md` (214),
`infra/migrate_agent_goal_models.sql` (56); changed
`puspa/world/model.py`, `puspa/world/planner.py` (+63: the
`message_contact` goal), `puspa/world/store.py` (+158:
migration + save / load / delete + snapshot agent models),
`puspa/world/__init__.py`, `infra/schema_world.sql` (table
added, 150 lines total).

Verification (coordinator, re-ran herself): module tests
`tests/test_agent_models.py tests/test_agent_store.py`:
34 passed (29 in-memory + 5 Postgres). Neighbour regression
(world model / contact-habit / world store / world beliefs /
brain wiring / causal ledger / causal store / causal learning
wiring / intent / intent turn wiring / ledger event types,
combined with the K5 files): 194 passed. Ruff check +
format: clean. Mypy: clean. Read in full: the mixin
(`agents.py` lines 871-997), the planner goal, the doc, the
migration. Two test failures during the worker's run, both
fixed in the tests, not the code (one expected `Store.observe`
to return a label when it returns a model; one expected reply
rate 1.0 when a decline without a reply makes the true rate
3/6 = 0.5, fixed by marking the decline as a reply).

Ratchet fix (coordinator, same pattern as K4): the worker's
code grew `puspa/world/model.py` 1,397 -> 1,443, past its
1,397 baseline. Not re-baselined: the ingest hook moved to
`AgentModelMixin.feed_task_result` (compacted: a key loop
instead of spelling the 13 interaction fields out, same
keys and values), the per-contact state to
`AgentModelMixin.__init__` (runs via `super().__init__()` in
`WorldModel.__init__`; `CausalExpectationMixin` defines no
`__init__`, so the chain reaches it), and the `Prediction`
dataclass moved to `puspa/world/beliefs.py` (a forecast is a
belief about a future outcome; `dataclass` import left with
it). No logic change, only the seam. model.py shrank
1,443 -> 1,395; baseline re-recorded DOWN to 1,395.
`puspa/world/agents.py` crossed 1000 in the same move
(997 -> 1,047): recorded as a NEW baseline entry at 1,047
with its reason (K5 module: goal-model types + in-memory
store + keyword inferrer + mixin in one place) and its split
candidate (the inferrer, or the store, into its own module).
`puspa/world/beliefs.py` grew 238 -> 250 (docstring +
Prediction), still under 1000. Re-verified after the fix:
ratchet 3 passed, mypy clean, the 194-test set still passed.

Suite (coordinator, SOLO, log
hidden_files/suite-wave25-k5.log): 3,353 passed, 23 skipped,
1 warning, 21m05s, EXIT 0.
Reconciliation: 3,319 (Wave 24) + 34 (K5 module tests) = 3,353.

Honest limits: confidence is bookkeeping, not a calibrated
probability (0.5 / +0.1 / -0.2 are chosen, not measured);
accuracy is tracked per model (`accuracy`, `stats`) so it can
be checked against outcomes. The keyword inferrer is a weak
heuristic; unknown text infers nothing. The model has no
theory of mind: it cannot tell polite agreement from real
commitment, and a goal that stopped being mentioned looks
the same as a goal that was achieved. `message_contact` in
the planner is a rehearsal, not a message being sent.
`puspa/world/agents.py` is now over 1000 lines with a
recorded baseline: the next wave that touches it must split
it first (inferrer or store seam). No LICENSE or billing
model decision was made.

Remaining: Kosmos K6, K10, K11, K12, K13, then B features
(B6, B9, B10, B11, B12, B13), then B15 culture LAST. Then uji
+ 15 gates + 10 reference tasks (waiting on Nero's decisions).

## Wave 26 — Kosmos K6: ignorance map (DONE 19 Nov 2026)

K6 records where the world model is blind: areas (topics) it
has encountered but cannot answer, either because it has no
evidence at all or because the evidence it holds is too thin
to trust. New module `puspa/world/ignorance.py` (482 lines),
deterministic, no LLM, no persistence (in-memory, derived
from belief state — the reason is documented in the module
and the docs).

An area is one belief domain plus one topic: `price`
(product@store), `bill` (bill id), `schedule` (task kind),
`capability` (task kind), `contact` (contact id), `habit`
(task kind), plus `topic` (a free topic with no belief
behind it, e.g. a question the system hit and could not
answer from the world model). The unknown rule is the same
test the forecasts use, plus a confidence floor
`UNKNOWN_CONFIDENCE = 0.5` (= `DEFAULT_CONFIDENCE`, the
fresh-belief default): no belief for the topic, or a belief
with no usable value, means `no_evidence`; a belief with a
usable value but confidence below the floor means
`low_confidence`. The usable-value test mirrors each
`WorldModel._forecast_*` "known" test (verified for price:
`last_price is None` is exactly the forecast's unknown
test), so the map and the forecast never disagree about
whether the model can answer. One observation clears a
`no_evidence` area; only scored misses (-0.2 each in
`_apply_observation`) can push a belief below the floor;
correct predictions (+0.05) heal it back. A habit needs a
repeated pattern: 1 task does not clear a habit area, 3
repeated tasks do.

The map is derived, not stored, in two halves: the
`low_confidence` half is computed from the live belief
dicts on every read (a belief the model stopped trusting
shows up even when nobody registered it); the `no_evidence`
half is registered — a caller that hits a topic with no
answer records it with `register_unknown`, and every read
re-checks registered areas against the current beliefs,
dropping the ones whose evidence has arrived. A belief with
no usable value yet is "not yet formed", not a gap: the
derived half never lists `no_evidence`, so the map does not
flood with every young habit belief. Free topics have no
belief to re-derive from, so they are dropped on restart and
must be re-registered by whoever hit them; only the
`"topic"` domain resolves by declaration
(`resolve_unknown`), a belief domain leaves the map only
when evidence arrives.

The read path is `IgnoranceMixin` (same seam as
`CausalExpectationMixin` and `AgentModelMixin`):
`ignorance_map()` / `is_unknown()` / `register_unknown()` /
`resolve_unknown()` / `probe_for()`, plus `area_key` /
`parse_area_key` / `probe_action_for` (the smallest world
action that would supply the evidence; a free topic has
none, so it returns None). `WorldModel` inherits it, so the
read path stays `WorldModel.ignorance_map`.

The real caller is the planner, goal
`find_cheapest_price`: when no store has a known price the
plan does not guess a store — it is infeasible with the new
reason `"needs_probe"`, the missing price areas are
registered in the model's ignorance map, and the plan
carries one probe step (`check_price` at the first store)
plus the `"probe"` and `"unknown_areas"` fields, so the
caller can go look once and re-plan with evidence. After
the probe observation lands, re-planning is feasible. The
old reason `"no_known_price"` is gone (no test asserted the
old string — verified by grep). Registering the gap is
metadata about the model's own blindness, not world state:
the beliefs and `model.state` are unchanged by planning
(the registered half is not part of `state()`).

Files (worker e3b9a4df, not yet pushed): created
`puspa/world/ignorance.py` (482 lines),
`tests/test_ignorance.py` (282 lines, now 16 tests with the
coordinator's pinning test below),
`docs/ignorance-map.md` (143 lines); changed
`puspa/world/model.py` (net 0 lines: +1 import
`IgnoranceMixin`, class line extended, one 3-line comment
compressed to 2 — still 1,395, baseline untouched),
`puspa/world/planner.py` (217 -> 246: the `needs_probe`
branch + docstring), `puspa/world/__init__.py` (156 lines:
exports `IgnoranceArea` + docs bullet, K5 pattern).
`puspa/world/agents.py` untouched. No new table, no new
migration, no 32nd ledger event type.

Verification (coordinator, re-ran herself): module tests
`tests/test_ignorance.py`: 16 passed (15 worker + 1
coordinator addition: a test pinning
`ignorance.HABIT_MIN_OBSERVATIONS` to
`model.HABIT_MIN_OBSERVATIONS` — the value is duplicated on
purpose because importing the model would be circular, and
the test is the guard that catches drift). Neighbour
regression (ignorance / world model / world beliefs /
contact-habit / agents / agent models / brain wiring /
planner eval / file-size ratchet / ledger event types):
141 passed. Ruff check + format: clean. Mypy: clean.
Read in full: `ignorance.py` (both halves), the planner
branch, and the model diff vs main (3 lines changed).
Two development rounds, both fixed (1 real code bug: the
derived scan listed every young unpatterned habit belief as
noise — fixed in code to derived `low_confidence` only;
1 float-dust test expectation, fixed in the test; 1 ruff
B007, fixed in code).

Suite (coordinator, SOLO, log
hidden_files/suite-wave26-k6.log): 3,369 passed, 23 skipped,
1 warning, 19m56s, EXIT 0. The first run was SIGTERM'd at ~6%
by a VM replacement (uptime 0 min, Postgres wiped); the
recovery (replay reinstalled postgresql-16 16.15 + pgvector,
cluster restarted, role root recreated, schema_core.sql
re-run, RLS re-verified) took minutes and the re-run above
is the recorded result. The lone "E" seen at ~4% before the
kill was a casualty of the shutdown, not a real failure:
the full re-run has zero failures.
Reconciliation: 3,353 (Wave 25) + 16 (K6 module tests) = 3,369.

Honest limits: the 0.5 floor is a bookkeeping choice, not a
true measure of uncertainty: one observation clears an
area, so the map cannot tell a lucky guess from a solid
fact. The map covers only gaps the system met (registered)
plus beliefs it stopped trusting (derived
`low_confidence`); unknown unknowns are not on the map. A
`low_confidence` area can flicker at the floor because of
float dust (it takes 5 correct predictions, not 4, to heal
from 0.3). The planner probes only the first store; no probe
rotation or probe budget yet. Free topics are lost on
restart and must be re-registered. The duplicated
`HABIT_MIN_OBSERVATIONS` constant (model.py vs
ignorance.py) is now pinned by a test. No LICENSE or billing
model decision was made.

Remaining: Kosmos K10, K11, K12, K13, then B features
(B6, B9, B10, B11, B12, B13), then B15 culture LAST. Then uji
+ 15 gates + 10 reference tasks (waiting on Nero's decisions).
## Wave 27 — Kosmos K10: interruption budget (DONE 19 Nov 2026)

K10 gives proactivity a daily interruption budget: default
silence, and the system "pays" for the user's attention from
the budget, moving on its own only when the benefit exceeds
the cost of interrupting. New package
`puspa/proactivity/` (`budget.py`, 464 lines), deterministic,
no LLM, in-memory by deliberate choice (see the honest
limits).

One day carries `DEFAULT_DAILY_BUDGET = 5` units of
attention (the why-5 reasoning is in
`docs/interruption-budget.md`). Every proactive initiative
pays a cost by kind and must clear the kind's minimum value
(0..1) to justify interrupting at all: `urgent_alert` 1
(min 0.50), `bill_due` 1 (0.60), `price_drop` 2 (0.70),
`reminder` 1 (0.60), `digest` 1 (0.50), `suggestion` 2
(0.80), `silent_action` 0 (0.00). The decision checks run
in order: value below the kind's minimum → denied, however
full the budget is; silent action (cost 0) → allowed, it
never interrupts so it never pays and is never gated;
budget empty → denied (silence, no new notification);
remaining below the cost → denied; budget scarce
(remaining at or below `LOW_REMAINING_THRESHOLD = 2`) and
value below the high-value bar (`HIGH_VALUE_THRESHOLD =
0.85`) → denied, so the last units are reserved for what
matters most; otherwise allowed and the cost is deducted.
The kind set is closed: a kind not listed is rejected, so a
caller cannot invent a cheap kind to dodge the budget.
Every attempt is recorded in the spend log either way
(`spend_log()`, with kind / label / value / cost / allowed /
reason), and `summary()` reports one day in numbers. A
denied attempt never deducts (`spent()` counts allowed
records only). Spend-log days older than `MAX_KEPT_DAYS =
30` are dropped; a reset day reads as a full budget again.

Two wirings into paths that are really used, not new ones:
(1) the scheduler (`puspa/scheduler/scheduler.py`): `tick`
/ `run_trigger` / `run_missed` take an optional `budget`;
only the system-initiated pings pay — `price_drop` (value
0.70, saves money but rarely urgent, so it is suppressed
first when the budget thins) and `due_date_bill` (0.85,
time-critical, also clears the scarcity bar). A denied
trigger is simply not run: no run row is written and
`pemicu_berikutnya` is NOT moved — the trigger stays due
and is evaluated again on the next round (or tomorrow, when
the budget resets). User-created schedules, manual
triggers, and the silent system jobs (backup,
consolidation, dream, trust-root refresh, sentinel scan)
never pay: the user asked for the first two, and the rest
never interrupt anyone. (2) the channel gateway
(`puspa/channels/run.py`): `GatewayRunner` owns one
`InterruptionBudget` per profile, shared by every
notification the runner sends; `notify(..., initiative=...)`
pays from the budget first — denied means nothing is sent
and it returns None. Without `initiative` the send is
ungated: this path is for proactive pings only, and replies
to the user never go through `notify`. The denial is
recorded in `budget.spend_log()`, not in the ledger: there
is no ledger event type for "held back", and the 31 revisi-7
event types stay closed.

Files (worker 1a0e2d46, not yet pushed): created
`puspa/proactivity/budget.py` (464 lines),
`puspa/proactivity/__init__.py` (45 lines),
`tests/test_interruption_budget.py` (482 lines, 23 tests),
`docs/interruption-budget.md` (165 lines); changed
`puspa/scheduler/scheduler.py` (357 -> 409),
`puspa/channels/run.py` (231 -> 260),
`docs/kosmos-design.md` (+13 lines: implementation note
under the K10 criteria). `puspa/world/model.py` and
`puspa/world/agents.py` untouched.

Verification (coordinator, re-ran herself): module tests
`tests/test_interruption_budget.py`: 23 passed. Neighbour
regression (scheduler push / cron / claim / due / execution
/ holds / link / receipts / recovery / schedules,
channels gateway, ledger event types, file-size ratchet):
139 passed (162 with the module file). Ruff check +
format: clean. Mypy: clean. Read in full: `try_spend`
(the decision order), `spent()` (denied records never
deduct), the scheduler wiring, the gateway wiring.
Coordinator fix before push: the worker's new paragraph in
the `run_trigger` docstring was written in Indonesian; it
is now English, matching the worker's brief. Pre-existing
note: `puspa/scheduler/scheduler.py` itself still carries
Indonesian identifiers and docstrings from before K10
(`pemicu_berikutnya`, `pemicu_terpicu`, ... — 24 matches on
main); the K10 addition follows the file's existing
language except for the translated paragraph. A full
English rename of the scheduler module is a separate item,
not part of K10.

Suite (coordinator, SOLO, log
hidden_files/suite-wave27-k10.log): 3,392 passed, 23 skipped,
1 warning, 21m40s, EXIT 0.
Reconciliation: 3,369 (Wave 26) + 23 (K10 module tests) = 3,392.

Honest limits: the budget is in-memory, so a restart resets
that day's spend — a restart can let through a ping that
should have been held. Postgres persistence (table +
migration, K3/K4/K5 pattern) is a documented follow-up, not
done here. The scheduler's benefit values are still fixed
per type (bill 0.85, price drop 0.70) because the scheduler
has no per-event benefit signal yet — it cannot tell a
small discount from a big one; per-event scoring from the
world model is a follow-up. User-created cron routines and
replies to the user are deliberately excluded (solicited,
not interruptions). No ledger row exists for denials — if
one is ever needed, it takes a revisi-7 decision to add an
event type, since the 31 are closed. No LICENSE or billing
model decision was made.

Remaining: Kosmos K11, K12, K13, then B features
(B6, B9, B10, B11, B12, B13), then B15 culture LAST. Then uji
+ 15 gates + 10 reference tasks (waiting on Nero's decisions).
## Wave 28 — Kosmos K11: per-area autonomy dial (DONE 19 Nov 2026)

K11 is the per-area autonomy dial: trust per action area,
moving with prediction calibration, with an emergency drop
that is instant. New package `puspa/autonomy/` (`dial.py`,
543 lines), deterministic, no LLM, in-memory by deliberate
choice (see the honest limits). First item built under the
2-worker mode: worker 1 in worktree `build-w1` on
`puspa_test_w1`; the coordinator verified, merged, and runs
the suite in `build/` on `puspa_test`.

Areas: `money`, `messaging`, `files` (irreversible);
`scheduling`, `system` (reversible); `accounts`
(irreversible + capped at 0.59, so it never rises above
`propose`). Every area starts at 0.50 (`propose`): trust is
earned, never assumed. A correct outcome closes 5% of the
remaining gap to 1.0 (slow rise: 5 correct in a row reach
`do_and_show` at 0.613; 24 reach `do_silently` at 0.854).
An ordinary wrong outcome drops the score by 0.15, floored
at 0.0 (fast fall on purpose: 1 wrong from 0.85 lands at
0.70; 2 wrong land at 0.55, back to `propose`). A serious
failure is the emergency drop: in an irreversible area the
score falls to 0.0 at once (rebuild: 18 correct to
`do_and_show`, 37 to `do_silently`); in a reversible area
it falls by 0.30. A serious failure is an action that
failed harmfully, or an irreversible action that went
wrong — not a plain tool error (that is an ordinary wrong
outcome). `reset_area` is an operator action (tests, or a
human resetting trust after a review); the agent itself
never calls it. Each area keeps an audit history (cap 100
records): the dial's audit lives there, not in the ledger,
by design (no ledger event type for dial moves, keeping
the 31 revisi-7 types closed, same pattern as K10).

Calibration uses signals that already exist: the dial does
not score predictions itself, it only aggregates outcomes
already scored per area through `record_outcome` (the
world-model `WorldModel.stats` scores, the K5 per-contact
accuracy, and the learning-loop verdicts are its sources;
the turn loop is the first feeder, from tool-execution
results). The wiring chosen is the turn loop (same advisor
pattern as the K1 intent advisor), not approvals G4: one
clear gating path, and the dial feeds back from execution
results. `TurnLoop` takes an optional `autonomy_dial` (+
`dial_area_for_tool` overrides); the dial is consulted in
`_run_tool_calls` AFTER the intent advisor, so an intent
`ask` wins. At `propose` the tool does NOT run: the turn
records a `dial_ask` event and returns a
`dial_ask_payload` (`tool_was_run: false`). At `do_and_show`
the tool runs and the turn records `dial_show`; at
`do_silently` it runs and records `dial_silent` (the model
still sees the tool result, so it can continue the turn;
the presenting surface must honour the `show: false` flag
— the loop itself does not silence the model's narration).
Every executed outcome feeds the dial (`record_outcome`,
correct = ran as expected, wrong = error), wrapped so a
dial failure never breaks the turn; a broken dial fails
open (the call runs as if no dial were wired in). Verdicts
land on `TurnResult.dial_decisions`.

Files (worker 3afbe2f2 in build-w1, merged by the
coordinator): created `puspa/autonomy/dial.py` (543 lines),
`puspa/autonomy/__init__.py` (45 lines),
`tests/test_autonomy_dial.py` (584 lines, 36 tests),
`docs/autonomy-dial.md` (148 lines); changed
`puspa/turns/loop.py` (859 -> 969, still under 1000).
`puspa/world/model.py` (1,395) and `puspa/world/agents.py`
untouched. No new ledger event type (31 stay closed).

Verification (coordinator, re-ran herself in build-w1 on
`puspa_test_w1`): module tests
`tests/test_autonomy_dial.py`: 36 passed, including the
immediate-drop test (climb to `do_silently` with 24
correct, `record_serious_failure`, next turn blocks
`gmail.send` with an empty registry and payload
`tool_was_run: false`). Neighbour regression (autonomy
dial / intent turn wiring / approvals taxonomy / turn loop
/ turn intercepts): 148 passed. Ruff check + format:
clean. Mypy: clean. Read in full: the dial rules
(`record_outcome`, `record_serious_failure`, `_stage_for`)
and the loop wiring (`_run_tool_calls`,
`_consult_autonomy_dial`, fail-open).

Worktree suite (worker, build-w1, `puspa_test_w1`):
3,427 passed, 23 skipped, 1 failed, 36m21s, EXIT=1 —
total 3,428 tests, exactly 3,392 (Wave 27) + 36 K11. The
single failure was `tests/test_backup.py::
test_create_and_backup_items` asserting the manifest
database equals the hardcoded `"puspa_test"`, while the
backup manifest writes the real DSN database
(`puspa_test_w1`). Pre-existing environment mismatch, not
a K11 effect (the worker touched no backup code). Fixed
by the coordinator in `tests/test_backup.py` (rides with
this wave): the manifest assertion now reads the dbname
from the test DSN, and the restore database is derived
per-DSN (`<dbname>_restore`) instead of the shared
`puspa_test_restore`, so two worktree suites can no longer
race on it. Verified in build-w1: `tests/test_backup.py`
4 passed (including the restore test against
`puspa_test_w1_restore`).

Suite (coordinator, SOLO in build/ on `puspa_test`): 3,444
passed, 23 skipped, 1 warning, 21m52s, EXIT 0. The first
K11-only attempt was SIGTERM'd by a VM replacement at 54%
(no failures visible); the verification re-ran as one
combined run after the K12 merge, log
hidden_files/suite-wave28-k12.log.
Reconciliation: 3,392 (Wave 27) + 36 (K11 module) + 16 (K12
module) = 3,444.

Honest limits: the dial is in-memory — a restart returns
every area to 0.50 (the safe direction: only more
cautious, never bolder). Postgres persistence is a
follow-up. The calibration signal today is tool-execution
results (ran / errored), a proxy, not proof the prediction
was right; a stronger feeder (K8 outcome watcher) is
planned to use the same `record_outcome` entry point. At
`do_silently` the loop records `show: false` and
`dial_silent`, but the model still sees the tool result;
the presenting surface must honour the flag. No LICENSE or
billing model decision was made.

Remaining: Kosmos K12, K13, then B features
(B6, B9, B10, B11, B12, B13), then B15 culture LAST. Then uji
+ 15 gates + 10 reference tasks (waiting on Nero's decisions).

