# Status: Brain phase (Fase Otak)

Date: 18 Nov 2026. Integration run by the Brain-phase integration
agent after all four implementation agents finished. Revision 7
was NOT changed: no definition text, gate, number, or piece of
evidence was altered. One Brain-phase draft rule was reverted
during integration because it contradicted revision 7 (see
"Integration decisions" below).

## What was built, per workstream

### 1. World model (`puspa/world/`)

- `puspa/world/model.py` (510 lines): structured world model with
  entities, relations, and learned price and bill beliefs.
  `predict(action)` forecasts before acting and never mutates
  state; `update(observation)` applies the real observation,
  scores a pending prediction, records a miss as a misprediction,
  corrects the model, and reports the miss to the learning loop.
  A daily price trend is adopted only after the same move repeats
  3 days in a row. No database, no network, no new dependency.
- `puspa/world/simulator.py`: deterministic bill plus price watch
  world used as the verification ground (fixed price drifts, so
  accuracy is reproducible).
- `puspa/world/planner.py`: multi-step planning simulated against
  a copy of the model state, never the live model.
- Tests: `tests/test_world_model.py`, 13 tests. Deterministic
  simulated world, 3 episodes, 17 scored predictions each:
  episode 1 accuracy 0.647 (11 of 17), episode 2 accuracy 1.000
  (17 of 17), episode 3 accuracy 1.000 (17 of 17).

### 2. Coding harness (`puspa/harness/`)

- `puspa/harness/executor.py`: the executor interface
  (`Executor.run(code)` returning an `ExecutionResult`), the
  production `SandboxExecutor` (generated code runs only through
  `puspa/sandbox/runner.py`), and the test-only
  `LocalTestExecutor`.
- `puspa/harness/loop.py`: `CodingHarness`, the generate, test,
  fix loop (`CodingTask`, `AttemptRecord`, `HarnessOutcome`),
  attempt and cost ceilings on the existing `BudgetTracker`
  pattern, and the deploy step through the approval gate
  (`Gate`, kind `"deploy"`).
- `puspa/policy/tokens.py` (extended): per-tool permission model
  (`tool_permissions` map, `tool_permission()`, subset rule:
  a child token may only narrow a permission, never widen it).
- `puspa/policy/gate.py` (extended): kind `"deploy"` plus
  `Gate.authorize_tool()`.
- Tests: `tests/test_coding_harness.py`, 21 tests.

### 3. Learning loop and memory hardening (`puspa/memory/`)

- `puspa/memory/learning_loop.py` (new): curriculum
  (`propose_next_task`), experience log, correction table
  (`record_misprediction`, `correction_for`, `predict`), skill
  library (`draft_skill`, `verify_skill`, `activate_skill`,
  `reject_skill`, `find_reusable_skill`). Every learned skill
  stays inactive until the user approves it; learning never
  overwrites a manual user edit; a misprediction marks an active
  skill suspect. No network call on any path; string code is
  never executed by this module.
- `puspa/memory/embeddings.py` (changed): FastEmbed provider
  plus safe hash fallback. `make_embedder("auto")` falls back to
  the built-in `HashEmbedding` when fastembed or the model is
  unavailable.
- `puspa/memory/procedural.py` (changed): decay
  (`apply_decay`, `decay_stale_procedures`: a verified procedure
  untouched for 90 days decays back to trial) and contradiction
  handling (`detect_contradiction`,
  `propose_conflicting_procedure`, `resolve_contradiction`,
  `activate_draft_procedure` for learned-skill drafts under the
  `learned_draft:` service prefix).
- `puspa/memory/consolidate.py` (changed): reflection
  (`reflect_episodes`, `reflect_and_propose`): deterministic,
  keyword-based inferences routed through Proposals in
  `puspa/memory/profile.py`, never written to the profile
  silently; each inference carries its evidence event ids and
  needs at least 2.
- `puspa/memory/episodic.py` (changed): dual-level retrieval
  (`extract_entities`, `extract_concepts`, `dual_level_search`,
  `search_episodes_dual`) on Postgres plus pgvector.
- `puspa/graphs/identity.py` (changed): dual-level product
  ranking (`rank_products`, `search_products_dual`); every result
  carries price evidence ids.
- Tests: `tests/test_learning_loop.py` (22 tests) plus
  `tests/test_memory_brain.py` (26 tests), 48 tests, all
  in-memory. Database coverage added during integration:
  `tests/test_memory_brain_db.py` (11 tests) executes the
  database paths against Postgres (see "Database paths" below).

### 4. Planner, evaluation, skills and hooks

- `puspa/runtime/planner.py` (new): `Planner` plus
  `decompose()`. A purpose is decomposed into `Step` objects,
  executed through the existing `TaskRunner`, and replanned on
  failure: attempt 1 retries the failed step with a narrower
  input, attempt 2 drops the failed step, then the task stays
  failed. Plans with an outbound step stop at the approval point
  and are never replanned around the gate. Plans and replans are
  recorded with the existing closed ledger event type
  `plan_created`.
- `puspa/evidence/eval_harness.py` (new): `EvalTask`,
  `EvalHarness`, `cassette_solver`, `score_state`,
  `load_reference_tasks`, `run_reference_eval`,
  `run_price_injection_eval`. The 10 reference tasks replay
  their recorded cassettes (no network, no live calls) and are
  scored from the final state, by code. The price-injection test
  tampers with a cassette price and requires the existing
  verifier to refuse to verify it.
- `puspa/memory/optimizer.py` (new): `PromptProgram`,
  `Optimizer`, `evaluate_program`, `plan_coverage`. Prompt
  programs for the planner, reader, and checker roles are scored
  against the recorded cassettes only; deterministic candidate
  variants are compared and the best is kept. The optimiser
  refuses any program that targets the security gate and never
  writes to any module.
- `puspa/chassis/hooks.py` (strengthened): `SkillHookRegistry`
  with the hook points `skill_installed`, `skill_used`,
  `skill_approved`, `skill_rejected`, `skill_blocked` (broken
  listeners are isolated), plus `LearnedSkill`,
  `LearnedSkillStore` (read-only view over learned skills),
  and `check_skill_use()`.
- `puspa/chassis/tools.py` (extended): `McpToolSpec`
  (pydantic-typed tool), `McpClientWrapper`,
  `StubMcpTransport` (in-memory fake server, no network),
  `StdioMcpTransport` (real server over stdio through the
  official MCP SDK). Every MCP call passes the signed-skill
  gate first, then the single outbound gate.
- `cli/infra/setup_cli_db.sql` (small fix): stale
  `\ir ../../fase-0/puspa/...` includes now point at
  `../../puspa/ledger/schema.sql` and
  `../../puspa/accounts/schema.sql`; verification queries check
  the real table names `users` / `sessions` / `vault`. The file
  was NOT run against any live database.
- Tests: `tests/test_planner_eval.py` (19 tests) plus
  `tests/test_skills_hooks.py` (12 tests), 31 tests.

## Final test numbers (full suite, single process)

Command, from `build/`:

    NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
      .venv/bin/python -m pytest -q

Result: **641 passed, 0 failed, 5 skipped**, in about 5 minutes.
Baseline before this phase was 516 passed, 5 skipped. New tests
in this phase: world 13, harness 21, planner/eval 31, learning
48, database paths 11, plus 1 net test from the integration
rework of the demotion tests (2 tests replaced by 3). Total new:
125. 516 + 125 = 641, which matches the run exactly.

Reference evaluation through the new harness: 10/10 reference
tasks passed (score 1.0); price-injection test passed (tampered
price Rp1.000 against the true cheapest Rp11.999.000, verifier
verdict Uncertain, not Verified). Optimiser on the 10 cassettes:
planner program baseline score 1.0 (all 10 reference tasks
covered); best variant equals the baseline, so no prompt change
was adopted.

## Seam verification: world model to learning loop

Verified live by the integration agent with a scratch script
(`/tmp/seam_world_learning.py`, not part of the repo): a
`WorldModel` seeded with product P1 at store S1 for Rp10.000
predicted Rp10.000, then observed Rp12.000. The result: the
record landed in `WorldModel.mispredictions` (task
`check_price:P1@S1`, predicted Rp10.000, observed Rp12.000,
error Rp2.000) AND in the learning loop experience log
(`get_learning_loop().experiences`, 1 entry, same values);
`loop.correction_for(task)` returns the observed value,
`loop.predict(task)` returns the corrected price, the curriculum
proposes a retry task for the gap, and the model's next
prediction uses Rp12.000. Seam: PASS, no fix needed.

Note: the world agent tested this seam with a fake loop module
because `learning_loop.py` did not exist yet. The test
`test_world_model_imports_without_learning_loop` asserted the
module was absent from `sys.modules`, which is false once the
real loop exists and other test modules import it. During
integration that test was rewritten (still in
`tests/test_world_model.py`, a phase file) to run the check in a
fresh interpreter subprocess: importing `puspa.world.model`
alone must not import the learning loop, and a misprediction
there must still be recorded. The real seam is covered by the
live check above.

## Requirements decisions

`requirements.txt` (all comments translated to English; no
Indonesian left in the file):

- `fastembed==0.9.1` added (MIT). Local embedding model for the
  learning loop and memory retrieval. Optional at runtime:
  `puspa/memory/embeddings.py` falls back to `HashEmbedding`
  when fastembed or the model is unavailable. The model is
  BAAI/bge-small-en-v1.5, dimension 384, the same as
  `EMBEDDING_DIM`, so it is a drop-in replacement with no schema
  migration. Verified live: the model downloads from HuggingFace
  (about 65 MB, ONNX) and embeds locally with no network at
  inference time. Semantic check: "laptop asus vivobook" vs
  "laptop asus vivobook harga" similarity 0.861, vs "tagihan
  listrik pln" 0.481.
- `loguru==0.8.0` lowered to `loguru==0.7.3`. fastembed 0.9.1
  requires `loguru<0.8`, and 0.7.3 still satisfies nanobot-ai's
  `loguru>=0.7.3`. Installed with
  `.venv/bin/python -m pip install "loguru==0.7.3"` (the
  `.venv/bin/pip` entry point is broken: its shebang points at
  the old `build/fase-0/.venv/bin/python3` path; always use
  `.venv/bin/python -m pip`). Verified after the install:
  `loguru 0.7.3` and `fastembed 0.9.1` both import.
- `python -m pip check` after the change reports exactly one
  conflict, pre-existing and untouched by this phase:
  `sse-starlette 3.7.0 requires starlette>=0.49.1, but starlette
  0.46.2 is installed`. The fastembed/loguru conflict is gone.
- The full suite passed with loguru 0.7.3 (641 passed, 0 failed),
  so no fallback was needed: fastembed stays pinned and loguru
  stays at 0.7.3. The fallback path (drop fastembed, keep
  `HashEmbedding`, restore loguru 0.8.0) was NOT taken.
- No other new dependency was added in this phase. The MCP SDK
  (`mcp==1.30.0`, MIT) was already installed as a nanobot
  dependency.

## Database paths: covered

The five database paths the learning agent reported as never
executed against Postgres are now covered by
`tests/test_memory_brain_db.py` (11 tests, all passing against
`puspa_test`):

- `decay_stale_procedures`: a verified procedure backdated 100
  days decays to trial with zero successful runs; a fresh
  verified procedure and a stale trial procedure are untouched.
- `propose_conflicting_procedure`: a conflicting step is stored
  as a separate trial candidate with `conflicts_with` set to the
  old procedure id and status `proposed`; the old procedure is
  untouched; a non-conflicting step reports `recorded` with
  `conflicts_with` None.
- `search_episodes_dual`: an entity match (model code SM-A546E)
  ranks first with level `entity` and its proof id; empty query
  raises.
- `search_products_dual`: a model-number query returns one
  entity match carrying its price (Rp12.999.000) with its
  evidence id; a series query returns one concept match with
  empty prices; empty query raises.
- `consolidate_idle` with `reflect=True`: two old successful
  `check_pln` episodes produce 1 summary and 1 proposal
  (`reliable_service` = `check_pln`, source `inferred`, status
  `proposed`); without `reflect=True` no proposal is created.

One test-writing note: a `%s` placeholder inside the SQL string
literal `interval '%s days'` is not bound by psycopg (it stays
literal text and the parameter binds to the wrong placeholder,
silently updating zero rows). The fix was
`make_interval(days => %s)`.

## Reuse and attribution

- The four per-workstream reuse fragments
  (`docs/reuse-fase-otak-world.md`,
  `docs/reuse-fase-otak-harness.md`,
  `docs/reuse-fase-otak-learning.md`,
  `docs/reuse-fase-otak-planner.md`) were merged into the Brain
  phase section of `build/REUSE.md` (no entry already present
  there was duplicated) and stay in place as archive. The merged
  section reflects the resolved requirements decision and the
  database coverage added during integration; the learning
  fragment's older notes (requirements undecided, database paths
  untested) are superseded by this file.
- Attribution for the studied repos was added to `build/NOTICE.md`
  (Brain phase section: pattern attribution for generative_agents,
  Voyager, hermes-agent, letta-code, LightRAG, opencode,
  SWE-bench, ChatDev, OpenManus, inspect_ai, tau-bench,
  lm-evaluation-harness, DSPy, pydantic-ai, MCP python-sdk, ECC,
  strands-agents, langgraph, claude-code, plus the fastembed
  dependency entry). No code was copied verbatim, no GPL, AGPL,
  SSPL, PolyForm, or CC BY-NC code was used, no fork was created.
- `docs/brain-repo-list.md`: the "In Puspa" column was updated
  for the 19 repos implemented in this phase, with exact file
  locations (hermes-agent, OpenManus, ECC, opencode, AutoGPT,
  claude-code, strands-agents, ChatDev, generative_agents, DSPy,
  Voyager, letta-code, LightRAG, MCP python-sdk,
  lm-evaluation-harness, inspect_ai, SWE-bench, tau-bench,
  pydantic-ai). No other row was changed. Known staleness left
  in that file on purpose (only the column was to be updated):
  the "Counts" header and the "Top 10 brain-relevant repos not
  yet in Puspa" section still describe the pre-phase state; all
  10 of those top rows are now implemented. Recomputing the
  counts is a coordinator decision.

## Known gaps

- The Gate has no kind for skill activation. `puspa/policy/gate.py`
  kinds cover outbound actions only, and that file must not be
  modified by this phase. Skill activation therefore reuses an
  approved Gate request record: `LearningLoop.activate_skill`
  verifies through the Gate's stored requests that an approval
  for the user exists with status `approved` or `executed`. The
  caller obtains that approval through the Gate with the
  activation described in the request content. A dedicated Gate
  kind for skill activation would need a change owned by the
  gate workstream.
- A real end-to-end sandbox run has not been done. On this dev VM
  the real Docker sandbox cannot start (the kernel rejects the
  `setns` syscall, so `runc` fails and
  `Sandbox.check_available()` returns False). The production path
  is fail-closed here: `SandboxExecutor.run()` raises
  `SandboxUnavailable` and no generated code runs anywhere. The
  end-to-end loop test (`test_e2e_loop_to_green`) runs on the
  test-only `LocalTestExecutor` (temp directory on the host with
  a scrubbed environment), which is rejected when
  `production=True` and unreachable from
  `create_production_harness()`. A real end-to-end sandbox run
  still needs a host with working Docker (for example the
  Contabo VPS).
- The demotion idea is deferred, not implemented (see below).
  A verified procedure that fails once keeps its verified status
  until a second consecutive failure makes it broken, exactly as
  revision 7 section 3.3 says.

## Integration decisions

### Demotion reverted to honor revision 7 section 3.3

The first full-suite run (before integration fixes) reported
637 passed, 3 failed, 5 skipped. Two failures shared one root
cause:

- `tests/test_service_canary.py::test_canary_success_and_failed_canary_becomes_broken`
- `tests/test_integration_gates.py::test_phase3_gate_end_to_end`

Both expect a procedure to become `broken` after 2 failed canary
runs. The Brain phase had added a demotion rule to
`compute_run_update` in `puspa/memory/procedural.py`: one failure
on a verified procedure dropped it back to trial with zero
successful runs. That broke the canary contract, because
`run_canary` in `puspa/graphs/service.py` only runs on verified
procedures: after the first failed canary demoted the procedure
to trial, the second canary call returned early
(`bukan_verified`) without recording a run, so the procedure
could never reach 2 consecutive failures and stayed `trial`
instead of `broken`.

Fix, confined to phase files: the demotion rule was removed from
`compute_run_update`. Revision 7 section 3.3 says verified after
3 successful runs by at least 2 agents and broken after 2
consecutive failed runs, with no demotion step, so the lifecycle
now follows revision 7 exactly. The `demoted` key stays in the
returned dict, always False, so callers reading the old shape do
not break. The two demotion tests in
`tests/test_memory_brain.py` were replaced by three revision 7
tests (single failure keeps verified status with 1 consecutive
failure; two consecutive failures mean broken; failure then
success recovers with the counter reset). The module docstring
in `procedural.py` records the decision. No file outside this
phase was touched: `puspa/graphs/service.py`,
`tests/test_service_canary.py`, and
`tests/test_integration_gates.py` are unchanged. A demotion rule
would need a canary-guard change first, which belongs to a later
phase.

The third failure in that first run,
`tests/test_world_model.py::test_world_model_imports_without_learning_loop`,
was the stale ordering-dependent test described under "Seam
verification" above; it was rewritten as a subprocess check.
After both fixes the full suite was re-run once: 641 passed,
0 failed, 5 skipped.

### Definition vs implementation

No conflict between revision 7 and the implementation remains
open after the demotion revert. Revision 7 was not changed. The
only definition-adjacent judgement call is the skill-activation
approval gap above (Gate has no kind for it), which is recorded
as a known gap, not resolved by changing the definition or the
Gate.

## Files changed by integration (this agent)

- Created: `tests/test_memory_brain_db.py` (11 tests),
  `docs/status-brain-phase.md` (this file).
- Changed: `requirements.txt` (comments translated to English,
  `fastembed==0.9.1` added, `loguru==0.8.0` lowered to
  `loguru==0.7.3`); `REUSE.md` (Brain phase section merged from
  the 4 fragments); `NOTICE.md` (Brain phase attribution);
  `docs/brain-repo-list.md` (19 "In Puspa" cells updated);
  `puspa/memory/procedural.py` (demotion removed, module note
  added); `tests/test_memory_brain.py` (demotion tests replaced
  by revision 7 tests, header updated);
  `tests/test_world_model.py` (stale import test rewritten as a
  subprocess check).
- Not changed: `build/web-replica/`, any artifact,
  `~/workspace/puspa-refactor/`, revision 7, any file outside
  this phase. Nothing was pushed (the coordinator pushes).

## Pre-existing notes, untouched

- `pip check` still reports the pre-existing conflict
  `sse-starlette 3.7.0 requires starlette>=0.49.1, but starlette
  0.46.2 is installed`. It predates this phase and was not fixed.
- Em dashes still exist in the old Indonesian module docstrings
  of pre-existing files (`puspa/memory/consolidate.py`,
  `puspa/memory/episodic.py`, `puspa/graphs/identity.py`,
  `puspa/chassis/hooks.py`, `puspa/chassis/tools.py`,
  `puspa/policy/gate.py`). No em dash was added by this phase:
  every file created or newly written in this phase, and every
  new section added by integration, is em dash free.
- The fastembed model cache was copied to `~/.cache/fastembed`
  so it survives restarts (fastembed's own default cache is
  under `/tmp`, which is ephemeral). `FASTEMBED_CACHE_PATH`
  overrides the cache dir. On a fresh machine the model
  re-downloads from HuggingFace; offline, the hash fallback
  applies.
