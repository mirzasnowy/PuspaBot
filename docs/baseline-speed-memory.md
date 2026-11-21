# PuspaBot Baseline: Speed, Decisiveness, and a Memory That Never Forgets

Status: baseline, ordered by Nero on 21 Nov 2026. Every later feature builds
on this. Nothing below is optional.

## 1. Speed is the baseline

- One loop, direct tools. Routine tasks run in a single agent loop with
  direct tool calls. No multi-agent handoff chains for work one agent can
  do. Every handoff loses detail and adds waiting.
- Decide by default. The agent decides and acts. It asks only when the
  stakes are high and the action is hard to undo (K2 act-or-ask rule).
  Asking is the exception, never the default.
- Act first, report after. No plan narration, no excuses. A report states
  what is done and what remains, counted from real items, never guessed.
- Parallelize independent calls. Independent tool calls run in parallel;
  dependent ones stay ordered.
- Small checks, not full runs. Module plus neighbor tests per change; the
  full suite runs only at merge checkpoints.

## 2. Never forget: the memory system

Layers, each with one job:

- Working memory: the current session. What the user just said, what is
  in flight right now.
- Episodic memory: the ledger. Every action and decision as an append-only
  event with time, cause, and outcome. Nothing is edited. Corrections are
  new events; deletes are tombstones.
- Semantic memory: the knowledge graph. Facts, people, preferences, and
  their relations. User-visible and user-editable (B18).
- Procedural memory: skills and modules. How things are done, learned
  from use.
- Durable memory: a human-readable MEMORY.md plus a narrative journal,
  rebuilt from the lower layers every night (dream and consolidation, K9).

Rules:

- Write-through. Every decision, correction, and commitment is written to
  the ledger AND to durable memory in the same turn it happens. Never
  batched, never "later".
- Recall-first. Every task starts by loading the relevant durable memory.
  The agent never starts from zero on a known user, project, or decision.
- Corrections win. A user correction is recorded as a new ledger event and
  updates the graph and durable memory immediately. The old claim is marked
  superseded, never silently overwritten.
- Verifiable. Any claim about the past must trace to a ledger event or a
  memory file line.

## 3. Verification: end state, not steps

- Every task states its end state in checkable terms before starting (for
  example: "the private repo contains zero files that also exist in
  public, except the named list").
- Done is verified against the end state by reading the actual result
  (API, files, logs), never by trusting a "done" report from a helper.
- If the approach produces the wrong end state, stop and change the
  approach. Do not continue the steps.

## 4. Mapping to what exists

- Built: K2 act-or-ask, K4 causal ledger, K9 consolidation and dream, K8
  outcome watcher, B18 graph view.
- Gaps to build as part of this baseline: durable MEMORY.md plus narrative
  journal (COW-05), write-through enforcement in the agent loop,
  recall-first loading at task start.
