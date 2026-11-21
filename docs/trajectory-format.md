# Trajectory format (self-observability (Sentinel and decision log))

How PuspaBot records trajectories — the hermes-agent
`trajectory-format.md` pattern, rewritten for PuspaBot. A
trajectory is the honest record of one run of one task: what
the agent was asked, which tools it called, what came back,
and whether it passed.

## JSON (stored)

One trajectory per line in `trajectories.jsonl` (the batch
runner's checkpoint file — see `docs/sentinel.md`):

```json
{
  "task_id": "eval-compare_price_laptop",
  "steps": [
    {"role": "human", "content": "Compare prices.", "tool_calls": []},
    {"role": "assistant", "content": "", "tool_calls": [{"tool": "read_price"}]},
    {"role": "tool", "content": "{\"value\": 100}", "tool_calls": []},
    {"role": "assistant", "content": "Task passed (score 1.0).", "tool_calls": []}
  ],
  "final_state": {"tools_used": ["read_price"], "cheapest_price": 100},
  "passed": true,
  "score": 1.0,
  "usage": {"tools_used": ["read_price"]},
  "started_at": "2026-11-18T12:00:00+00:00",
  "ended_at": "2026-11-18T12:00:01+00:00"
}
```

Roles: `human` (the prompt), `assistant` (an agent turn,
which may carry `tool_calls`), `tool` (a tool result).
`tool_calls` items are `{"tool": "<name>", ...}` — extra keys
(arguments, ids) are kept as the runner recorded them.
`final_state` is the harness's final state (or the agent's,
for `record_prompt_run`); `usage` is free-form run metadata
(tool lists, token counts when the agent reports them).

Redaction: before a trajectory is written, step content and
tool-call arguments pass through `puspa.memory.trust.redact_secrets`
(secrets become `«redacted»`). The raw trajectory is never
stored by the runner.

## ShareGPT (export)

`Trajectory.to_sharegpt()` converts the same steps to the
ShareGPT conversation format hermes-agent documents for
tool-calling training data:

```json
[
  {"from": "human", "value": "Compare prices."},
  {"from": "gpt", "value": "", "function_call": [{"tool": "read_price"}]},
  {"from": "function", "value": "{\"value\": 100}"},
  {"from": "gpt", "value": "Task passed (score 1.0)."}
]
```

Mapping: `human` -> `human`, `assistant` -> `gpt` (with its
`tool_calls` as `function_call` when present), `tool` ->
`function`. No field is added and none is dropped: the export
is a relabelling of the stored steps, so a training run can
always be traced back to the stored trajectory by `task_id`.

## What a trajectory is not

A harness trajectory records a cassette replay (deterministic,
no live calls) — it proves the harness path ran, not that a
live agent would do the same. A live-agent trajectory records
whatever the injected agent returned; the runner never invents
steps, scores, or tool calls. When the source is unknown, the
trajectory says so by carrying the harness version (via the
task) or the agent result as given — it never claims a live run
it did not make.
