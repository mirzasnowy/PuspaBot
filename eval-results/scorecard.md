# Release scorecard — 2026-11-20

Corpus v1.0.0 · retrieval set v1.0.0 · scorecard v1.0.0

## Reference tasks

- Success: **30/30 (0.886-1.000, 95% Wilson)**
- Wall time: 0.2s for 30 runs
- Cost: Rp0.0 (no model calls)

## Retrieval (recall@5 / answer accuracy, 50 internal questions)

- bm25: recall@5 **0.84** (42/50), answer accuracy **0.84** (42/50)
- vectors: recall@5 **0.86** (43/50), answer accuracy **0.90** (45/50)
- hipporag: recall@5 **0.84** (42/50), answer accuracy **0.74** (37/50)
- Public questions: 50 reserved, 0 live (license not confirmed)

## Injection corpus

- Live payloads: 60 (+140 empty slots)
- Attack success rate: **0/60 (0.000-0.060, 95% Wilson)**
- Rule-of-three 95% upper bound: **0.050**
- Blocked: 36 · held at approval: 24 (not a defense success)
- Goal 1: 0 success / 15 live
- Goal 2: 0 success / 15 live
- Goal 3: 0 success / 6 live
- Goal 4: 0 success / 6 live
- Goal 5: 0 success / 6 live
- Goal 6: 0 success / 6 live
- Goal 7: 0 success / 6 live
- Broad 'always' share grant (stress scenario): 0 success / 60 live

## Scanner false positives (normal Indonesian content)

- False-positive rate: **0.17** (7/40)
- Tricky subset: **0.40** (4/10)

## Utility under attack (benign requests still complete)

- Utility rate: **1.00** (40/40)

## Gold sets (30 cases each)

- Entity identity: precision **0.83**, recall **1.00**
- Quoted numbers: accuracy **0.86** (30/35)
- Orphan numbers: hold rate **1.00**, wrong-hold rate **0.00**

## Mutation testing

- puspa/policy/tokens.py: score **1.000** (10 killed, 0 survived)

## Not measured

- kill_drill_seconds: Kill-switch drill time is owned by the audit/kill-switch work, not by this package.
- restore_drill_minutes: Restore-from-zero drill time is owned by the backup/restore work, not by this package.
- live_model_attack_success_rate: End-to-end ASR with a live model has not been run; the deterministic-stack ASR below is not a substitute.

## Honest limits

- Attack corpus v1 live payloads (60) were written by one internal corpus author, not by people outside the team as the plan asks; the 30% outside-author slice is filled by the internal author and flagged here.
- The 60 adaptive-attacker records and 80 public-corpus records are empty slots in v1: no attacker model has been run and no public payload text was imported.
- ASR measures pass-through of the deterministic defense stack (scanner, rails, approval policy), not end-to-end agent behavior with a live model.
- 0 successes in N trials means an upper bound of 3/N at 95% confidence (rule of three), not 'safe'.
- The 50 public retrieval questions are empty slots: the 2WikiMultihopQA license was not confirmed, so nothing was imported.
- Reference-task cost is 0.0 because no model is called; the cost of a real model-backed task is a different measurement.
