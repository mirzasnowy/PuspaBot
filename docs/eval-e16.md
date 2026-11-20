# E-16: attack corpus, benchmark, mutation, scorecard (plan section 8)

This package is the measurement side of Puspa: it proves numbers
instead of claiming them. Nothing here calls a model; every
number comes from deterministic code and is reported with an
uncertainty interval, a date, and a corpus version.

## What lives here

- `puspa/eval/corpus.py` + `puspa/eval/data/attack_corpus_v1.json`:
  the frozen attack corpus. 200 records: 60 live payloads plus
  140 declared empty slots (60 adaptive-attacker, 80 public
  corpus). The loader verifies the manifest hash, so a silent
  edit is caught. The corpus is never edited to tune a prompt or
  a defense; new findings go into a new version.
- `puspa/eval/data/benign_indonesian_v1.json`: 40 normal
  Indonesian messages (10 tricky) for scanner false positives.
- `puspa/eval/data/public_sources.md`: the register of intended
  public sources (garak probes, Apache-2.0; AgentDojo, MIT) with
  import rules. No payload text from either source is in v1.
- `puspa/eval/attack_harness.py`: runs payloads through the real
  layers (scanner, input rails, output rail, approval policy)
  and classifies each as blocked / held at approval / success /
  no action. "Held at approval" is counted separately: it is not
  a defense success. `TransformAttacker` is the deterministic
  harness for the adaptive slice (leet, base64, homoglyph,
  zero-width, split, language mix).
- `puspa/eval/reference_tasks.py` + `puspa/eval/replay.py`: 10
  reference tasks with deterministic checkers, run 3 times, and
  cassette replay for gate 12 (target 9 of 10 identical).
- `puspa/eval/retrieval_sets.py` + `puspa/eval/bm25.py`: 50
  internal multi-hop questions over a 48-passage price graph
  (answers verified against the passages), plus 50 empty public
  slots. Compares BM25, plain vectors, and HippoRAG on recall@5
  and answer accuracy.
- `puspa/eval/gold_sets.py` + `puspa/eval/data/gold_*_v1.json`:
  30 cases each for entity identity (precision/recall), quoted
  numbers (accuracy vs evidence), and orphan numbers (hold rate,
  wrong-hold rate).
- `puspa/eval/mutation.py`: an AST mutation runner (mutmut
  equivalent) plus CLI. Surviving mutants in Gate and the
  gateway count as missing tests.
- `puspa/eval/fuzz.py`: seeded gateway rule-parser fuzzer.
  Invariants: only `PolicyError`/`RuleEvaluationError` on bad
  input, `decide` never raises on a compiled policy, an empty
  policy denies everything.
- `puspa/eval/scorecard.py`: the one-page release scorecard
  (JSON + Markdown). Run it with:
  `python -m puspa.eval.scorecard --output-dir eval-results`
  and pass a mutation report with `--mutation-report`.

## Honest limits (also printed on every scorecard)

- The 60 live payloads were written by one internal corpus
  author, not by people outside the team as the plan asks.
- The adaptive (60) and public (80) slices are empty slots in
  v1: no attacker model has been run, no public payload text was
  imported.
- ASR measures pass-through of the deterministic stack, not
  end-to-end agent behavior with a live model.
- 0 successes in N trials is an upper bound of 3/N at 95%
  confidence (rule of three), never "safe".
- The 50 public retrieval questions are empty slots: the
  2WikiMultihopQA license was not confirmed.
- Kill-drill and restore-drill times are not measured by this
  package; the scorecard lists them as not measured.

## Measured results (2026-11-20, corpus v1.0.0)

- Reference tasks: 30/30 over 3 repetitions; replay 10/10.
- Retrieval recall@5: BM25 0.84, plain vectors 0.86, HippoRAG
  0.74. HippoRAG does not beat plain vectors on this set, so
  the triple-extraction cost at index time is not justified by
  this number.
- Attack corpus (live 60): 30 blocked, 25 held at approval, 5
  success. Overall ASR 5/60. Goal 1 (leak): 5/15 success
  (ATK-003, ATK-008, ATK-010, ATK-013, ATK-014) — these pass
  the scanner, the input rail, and the output rail, so the beta
  target of zero ASR for goals 1 and 2 is NOT met on the frozen
  corpus. Goal 2: 0/15. Goals 3-7: 0 success each. A broad
  "always" share grant adds one more success (6/60): the share
  payload that passes the layers is auto-allowed.
- Scanner false positives on normal Indonesian content: 7/40
  (17.5%); tricky subset 4/10 (40%). Utility under attack (user
  rail): 40/40 (1.0).
- Gold sets: entity identity precision 0.83 / recall 1.00 (the
  5 false positives are concept traps: a nonexistent model
  matches the nearest series); quoted-number accuracy 30/35
  (0.86); orphan-number hold rate 1.00, wrong-hold rate 0.00.
- Gateway fuzz (500 iterations, seed 20261120): 0 unexpected
  exceptions; empty policy denies.
- Mutation (AST runner, `puspa/policy/tokens.py`, first 10
  mutants, 4 test files): score **0.333** — 3 killed, 6
  survived, 1 timed out (727s). The survivors are real missing
  tests, not equivalent mutants: permission-rank values
  (`TOOL_PERMISSION_RANK` deny/ask), the non-string guard in
  `_normalize_id`, the `not isinstance` guard in `jti_of`, and
  the `or` guards in `_validate_payload` (a string `tools`
  value passes validation). Narrow run (test_gate.py only):
  0.300. Full-module mutmut was not run: it would take much
  longer on this VM.
