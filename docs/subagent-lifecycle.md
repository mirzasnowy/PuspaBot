# Subagent lifecycle API

Public lifecycle for Puspa subagents (G25). This document is the
contract: the handle, the states, the operations, and the
fail-closed rules. The implementation lives in `puspa/subagents/`;
the pattern follows the hermes-agent lifecycle
(`agent/subagent_lifecycle.py`, `delegation_*.py`,
`tools/delegate_tool.py`, `plugins/delegation/`,
`subagent-lifecycle-api.md`), rewritten as Puspa's own code — never
copied verbatim.

## Why this exists

The coding harness spawns subagents. Without a tidy lifecycle, a
stuck subagent cannot be steered or cancelled safely, and a
returned result cannot be trusted (hidden transcript, changing
output). This API wraps the spawn path with a capability handle, a
stable state machine, cooperative cancel and steer, and an
immutable, idempotent result.

## Handles

`launch` returns a `SubagentHandle`: a serializable, opaque
capability. It carries a format version (`1`), the child id, the
process generation that minted it, and a random capability token.
The token is never stored in cleartext in the registry — only its
SHA-256 is kept — and it never appears in `repr`.

- A handle that fails to parse, names an unknown child, or carries
  the wrong token is refused with `UNKNOWN_HANDLE`. A forged handle
  can never reach a child, and the caller learns nothing about
  which ids exist.
- Handles are accepted both as `SubagentHandle` objects and as
  their serialized string form (one URL-safe base64 string).

## States

```
PENDING -> STARTING -> RUNNING -> SUCCEEDED
                                -> FAILED
                                -> INTERRUPTED
                                -> CANCELLED
RUNNING -> CANCEL_REQUESTED -> CANCELLED
```

Terminal states: `SUCCEEDED`, `FAILED`, `INTERRUPTED`,
`CANCELLED`. `UNKNOWN` is a state value, not a state a child ever
sits in: it is what an unknown handle resolves to (as the
`UNKNOWN_HANDLE` error).

- `PENDING`: launched, body not yet started. A cancel here
  finishes the child at once as `CANCELLED` — nothing ran, so
  there is no boundary to wait for.
- `STARTING` / `RUNNING`: the child body is executing.
- `CANCEL_REQUESTED`: a cancel was asked for and the child has not
  reached its next safe iteration boundary yet. This state is
  never terminal.
- `INTERRUPTED`: the host abandoned the child (`shutdown`), before
  it finished. Any further work its thread does is discarded.
- `CANCELLED`: the child stopped at a boundary after a cancel.
- `FAILED`: the child raised, or returned a non-string result.

## Operations

All operations take a handle and raise `UnknownHandleError`
(`UNKNOWN_HANDLE`) for a forged or corrupt one.

| Operation | Behaviour |
|---|---|
| `launch(...)` | Validates everything fail-closed (below), accounts the child in the pool, mints the handle, starts the body on the runner. Returns the handle. |
| `status(handle)` | Snapshot: state, toolset, exposed tools, cost, steer queue length, cancel flag, error, whether a result exists. |
| `wait(handle, timeout_s=None)` | Blocks until the child is terminal or the timeout elapses. Returns the state observed — a terminal state only when the child actually finished. A timeout returns the live non-terminal state; it never claims the child is done. |
| `cancel(handle)` | Cooperative cancel. `PENDING` -> `CANCELLED` at once. `STARTING`/`RUNNING` -> `CANCEL_REQUESTED`; the child becomes `CANCELLED` when its next `ctx.boundary()` observes the request. A terminal child is returned as-is. A child that never reaches a boundary keeps running: cancel is cooperative by design. |
| `steer(handle, text)` | Queues text for the child's next iteration boundary (`ctx.drain_steer()`). Returns the queue length. Steering a terminal child is refused; a full queue (16 items) is refused — steered text is never silently dropped. |
| `result(handle)` | The immutable result of a finished child. Idempotent: every call returns the same frozen result, same text, same stable hash. Raises `NotTerminalError` while the child runs. |
| `reconnect(handle)` | Re-attaches to a live child in this process and returns its status. A handle from a previous process generation raises `ReconnectUnavailableError` (`RECONNECT_UNAVAILABLE`): the child is gone and reconnect never starts a replacement child. |
| `shutdown()` | Host going away: every live child becomes `INTERRUPTED` with its finish path run once. |

## Results

A `SubagentResult` is frozen and carries only the child's final
text: no transcript, no hidden reasoning, no intermediate steps.

- Text is capped at 32,768 characters (`MAX_RESULT_CHARS`); the
  `truncated` flag records whether anything was cut.
- `result_hash` is the SHA-256 of exactly the stored text, so the
  same output always produces the same hash.
- On `FAILED` / `INTERRUPTED` / `CANCELLED` the text is empty and
  `error` carries the terminal reason (at most 500 characters).

## Fail-closed rules

Enforced at `launch`, before anything is spawned:

- **Bounded sizes.** Goal at most 8,000 chars, context at most
  32,768 chars, metadata at most 32 keys rendering to at most
  4,096 chars of canonical JSON. Oversized input is refused with
  `LimitExceededError`, never silently truncated.
- **Toolset must fit the parent.** The child toolset must be a
  known toolset whose resolved tools are all registered and all
  inside the parent toolset; an unknown toolset, an unregistered
  tool, or any widening tool is refused with `ToolsetRejectedError`.
  A child toolset wider than the parent *token* tools is refused
  too. Availability (`check_fn`) is deliberately not consulted
  here: it is a per-profile live check, and launch must not turn
  into a credential probe.
- **Unsupported options are refused explicitly.** A per-launch
  working-directory override and a per-tool timeout raise
  `UnsupportedOptionError` until they can be supported without
  weakening child isolation.
- **Pool caps.** At most 4 active children per agent, 6
  system-wide (the existing `SubagentPool` caps). A full pool
  refuses the launch; the pool's internal queue is not driven by
  this API.
- **One level only.** A child cannot spawn children: the child
  token payload carries a `subagent_id`, and `is_subset` refuses
  a child of a child.

## Host path

A child runs through the host path Puspa already has — no parallel
spawn path was built. `puspa.subagents.host.HostSession`:

1. **Spawn accounting** — `SubagentPool.spawn` with the child
   token payload (a subset of the parent payload, checked by
   `puspa.policy.tokens.is_subset`). Without a verified parent
   token the child runs under a minimal synthetic parent payload
   built from the child toolset; with one, the child budget
   narrows (at most 15 steps, 20,000 tokens, the parent rupiah
   ceiling) and expires no later than the parent. No signed token
   is minted here: signing needs the runtime key, which the
   lifecycle does not hold.
2. **Parent tool restore** — the child only ever receives a
   frozen view of its toolset names, never the registry, so the
   parent tools are restored by construction. The lifecycle still
   verifies the parent toolset is unchanged at finish and records
   the outcome in the finish report.
3. **Memory notification** — an injected `memory_notify` callback
   receives the finished-child payload (id, agent, task, state,
   result hash, cost). Best-effort: a failing notifier is recorded
   in the finish report, never fatal.
4. **Serial `subagent_stop` hooks** — fired once per finished
   child, in registration order, each as
   `handler("subagent_stop", payload)`. A failing hook is isolated
   and recorded; it never changes the child's terminal state.
5. **Cleanup** — the pool entry completes, freeing a slot and
   advancing the pool queue.
6. **Cost rollup** — the child's recorded cost rolls up into the
   parent `BudgetTracker`. An exhausted parent budget is recorded
   in the finish report, not fatal: the child's own cost stays on
   the record.

Events (`subagent_launched`, `subagent_steered`,
`subagent_cancel_requested`, `subagent_finished`) go to the same
`sink(event_type, content)` shape the harness uses; the default
sink keeps them in memory (`lifecycle.event_log()`).

## Child body

The child body is a caller-supplied `child_fn(ctx) -> str`, where
`ctx` is a `ChildContext`:

- `ctx.boundary()` — a safe iteration boundary; raises
  `ChildCancelled` when a cancel was requested.
- `ctx.drain_steer()` — takes all queued steered texts, in order.
- `ctx.record_cost(rp)` — charges rupiah, rolled up at finish.
- `ctx.should_stop()` — True when a cancel was requested.

The runner is injectable: the default `ThreadRunner` starts one
daemon thread per child; a test runner may capture the body and
run it later, which is how `PENDING` is observed without a race.

## Honest limits and follow-ups

- **No `delegate_task` exists in this repo.** The task brief
  assumed one; the code was searched (`puspa/harness/`,
  `puspa/runtime/`, `puspa/tools/`) and no `delegate_task`
  function exists. The lifecycle therefore wires to the actual
  host path: `SubagentPool` spawn accounting, token subset,
  `BudgetTracker` rollup, the harness-shaped sink, and the serial
  stop-hook dispatch. If a `delegate_task` host function lands
  later, the child body should route through it.
- **No harness-backed child yet.** The child body is a plain
  callable. Running a `CodingHarness` task as a child (with
  iteration boundaries so cancel/steer land mid-run) needs
  boundary hooks in `CodingHarness.run` first.
- **No signed child token.** The pool only ever sees payload
  dicts; minting a real signed token via `issue_child` needs the
  runtime signing key.
- **`subagent_stop` is not a plugin event yet.**
  `puspa.plugins.context.HOOK_EVENTS` does not list it, so plugins
  cannot register it through `PluginContext.register_hook` today.
  Until that event is added (G27 area), stop hooks are passed to
  the lifecycle as plain `(event, payload)` callables;
  `collect_stop_hooks` already understands plugin contexts, so it
  starts working the moment the event is allowed there.
- **Memory notification is a callback, not a wiring.**
  The default is no notifier. Wiring it to the learning loop
  (`LearningLoop.record_feedback`) needs a defined mapping from a
  child result to a feedback verdict.
- **Child budget is not enforced mid-run.** The child token
  payload carries a narrowed budget, but nothing stops a child
  mid-run when it exceeds it; enforcement belongs in the
  harness-backed child adapter.
- **No live LLM call was used.** Everything here is proven with
  stub child functions; a live model child is out of scope for
  this item.
