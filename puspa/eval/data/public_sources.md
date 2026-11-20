# Public corpus sources (register, not an import)

The public-corpus slice of the frozen attack corpus (80 records) is
empty in v1. This file records the intended sources so a future
import can be checked against it. No payload text from any source
below is in the corpus.

## garak probes (40 records reserved)

- Project: NVIDIA garak, probe set `promptinject` and related
  injection probes.
- License recorded: Apache-2.0 (from the project LICENSE; re-check
  before importing any payload text).
- Why it fits: garak probes are short injection strings aimed at
  making a model repeat or follow embedded text, which maps to
  plan goals 1 (leak) and 4 (weaken guardrails).
- Import rule: take only the probe payload strings, keep the garak
  probe name as the `source` field, and do not import garak code
  or its model-judgement machinery.

## AgentDojo injection tasks (40 records reserved)

- Project: AgentDojo (ETH Zurich Spylab), default suite v1
  (banking, slack, travel, workspace injection tasks).
- License recorded: MIT (from the project LICENSE; re-check
  before importing any payload text).
- Why it fits: each injection task pairs an attacker goal
  (e.g. send money to an attacker account) with the exact
  injected text, which maps directly to plan goals 1 and 2.
- Import rule: import the injected text plus the task goal, keep
  the suite and task name as the `source` field, and adapt the
  attacker account details to Puspa placeholders (never real
  accounts). An earlier fetch of AgentDojo task files was not
  used for v1: no payload text from that source is in this corpus.

## Retrieval public subset (50 questions reserved)

- Intended source: 2WikiMultihopQA dev split (public, no login
  needed for download).
- License: NOT confirmed in v1. Do not import until the dataset
  license is verified and recorded here. The 50 public retrieval
  questions are empty slots until then.
