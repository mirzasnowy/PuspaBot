# Turn loop: interruptible, fallback-layered, budgeted (G21)

The turn loop is the conversational engine inside Puspa's task
lifecycle. One loop serves every surface (PWA, Telegram/WhatsApp
channels, scheduler): the loop that was split is Puspa's
`TaskRunner` (`puspa/runtime/tasks.py`), and the split keeps the
task lifecycle where it is (accept -> plan -> run -> verify ->
present) while the model work inside the `run` stage goes
through this loop, injected as the step executor. There is no
second parallel loop.

Pattern attribution: the shape (thin facade + one module per
turn phase, interruptible call on a background thread, one
internal message format for three API modes, layered fallback,
per-agent and per-subagent iteration budgets, alternation
enforced in code, tools intercepted before the registry) is
studied from NousResearch/hermes-agent (MIT) and rewritten as
Puspa's own code. No hermes-agent code was copied. See `REUSE.md`
and `NOTICE.md` (maturity phase, G21).

## Layout

```
puspa/turns/
  __init__.py        thin facade: re-exports only
  loop.py            TurnLoop facade + make_task_executor (TaskRunner wiring)
  messages.py        internal message format + 3-mode converters + alternation
  config.py          TurnConfig (all limits live here, never hardcoded)
  budget.py          IterationBudget + SubagentIterationBudget
  interrupt.py       run_interruptible (background thread + event + timeout)
  turn_prepare.py    phase: iteration preparation
  turn_api.py        phase: the model call (fallback chain, interruptible)
  turn_api_error.py  phase: API error handling (auth refresh-first)
  turn_overflow.py   phase: overflow decision
  turn_truncate.py   phase: truncation
  turn_recover.py    phase: recovery (checkpoints)
  intercepts.py      tools intercepted before the registry
  todos.py           in-memory per-turn todo store
```

Each phase module is one phase only; the facade (`loop.py`)
only orchestrates. Every file is handwritten and under 1000 lines.

## The loop that was split

`TaskRunner` is the task lifecycle. Its `run` stage calls the
injected `executor(step_name, inputs)` per plan step. The turn
loop plugs in there via `make_task_executor`:

```python
from puspa.turns import TurnLoop, make_task_executor
from puspa.runtime.tasks import TaskRunner

runner = TaskRunner(executor=make_task_executor(model_call=...))
```

The channels reach the same loop through the existing
`TaskRunnerAgent` seam (`puspa/channels/agent.py`): build the
runner with the turn-loop executor and pass it in. The PWA and
the scheduler use `TaskRunner` the same way. One history, one
budget, one interruption rule everywhere.

What was NOT changed: `puspa/runtime/tasks.py` needed no edit
(the executor injection already exists), and zero lines of
nanobot code were touched (`puspa/chassis/` is untouched). The
production model executor (a chassis call) is still the
follow-up named in `puspa/channels/agent.py`; until it lands,
the default model adapter is the router's live OpenRouter
caller (`make_openrouter_model_call` in `turn_api.py`, built on
`puspa.router.complete.complete_messages`).

## One turn, phase by phase

Per iteration, in order:

1. **Prepare** (`turn_prepare.prepare_iteration`): interrupt
   check first (a pending interrupt ends the iteration before a
   token is spent), then one iteration of budget, then
   alternation repair on the history. The prepared history is a
   new list; the caller's history is never mutated by
   preparation.
2. **Overflow** (`turn_overflow.check_overflow`): when the
   history passes the caps (`max_history_messages` / 100,
   `max_history_chars` / 120000), the decision is `truncate`
   (or `compress` when a compressor callable is wired — the
   middle becomes one summary message). Truncation
   (`turn_truncate.truncate_history`) never touches the system
   block at the start or the last `keep_last_messages` (10)
   messages. After truncation the alternation enforcer runs
   again.
3. **API call** (`turn_api.call_model`): the internal history is
   converted to chat-completions wire format and sent through
   the fallback chain (below), inside an interruptible call.
4. **Tool calls**: the reply's tool calls are dispatched —
   intercepted tools first (below), then the injected registry
   callable (`tool_call(name, arguments) -> result dict`). Each
   result returns as a tool message and the loop runs again.
5. **Done / stop**: the model answers with text (completed), the
   budget runs out (`budget_exceeded`), the user interrupts
   (`interrupted`), or the call fails (`failed`). Every ending
   carries a clear message; a turn never stops silently and
   never crashes on budget exhaustion.

**Recovery** (`turn_recover`): a checkpoint (deep copy) is taken
before every model call. An interrupt, timeout, or all-tiers
failure restores it, so the history is exactly what it was
before the call. No partial reply ever enters the history.

## Interruptible model calls

`interrupt.run_interruptible(call_fn, interrupt_event, timeout)`
runs the call on a daemon thread while the main thread waits for
three things: the response, the interrupt event, or the timeout.
On interrupt the API thread is abandoned and its result is
discarded when (if) it arrives. The underlying HTTP request
cannot be cancelled from here, only abandoned — the daemon
thread dies with the process and a late response is dropped, never
delivered.

```python
loop = TurnLoop(model_call=...)
loop.request_interrupt()  # from any thread: the turn stops, history clean
```

## One internal message format

`messages.InternalMessage` is a role plus content blocks
(`text`, `image`, `tool_call`, `tool_result`). Three API modes
meet in it:

| Mode | To internal | From internal |
| --- | --- | --- |
| Chat completions (OpenAI) | `from_chat_completions` | `to_chat_completions` |
| Responses (OpenAI) | `from_responses` | `to_responses` |
| Anthropic Messages | `from_anthropic` | `to_anthropic` |

The Anthropic converters go through the router's existing wire
translator (`puspa.router.translate`), so the two formats cannot
drift apart. Every converter returns a `MessageConversion`
(value + dropped + notes): a field with no equivalent is
reported, never silently dropped. Responses `reasoning` items
are dropped with a report entry (the loop keeps no reasoning
trace in history). Round-trips (internal -> wire -> internal)
preserve text and tool calls; see `tests/test_turn_messages.py`.

## Alternation, enforced in code

`messages.enforce_alternation` repairs a history before any
call; `validate_alternation` lists violations without fixing.
Rules: system messages only at the start; after that, roles
strictly alternate user / assistant, with tool messages
belonging to the assistant turn that called them. Repairs are
deterministic and reported: consecutive same-role messages
merge (text joined, tool calls kept in order), a stray system
message becomes a user message (merged into a previous user
message when one is adjacent), an orphan tool result becomes a
user message the same way. The model is never trusted to fix its
own history.

## Fallback, layered and config-driven

Chains live in `puspa/router/chains.py` (`FallbackChainConfig`):
a `main` chain for the turn's own calls and one independent
chain per auxiliary kind (`vision`, `compression`, `title`). An
auxiliary failure never touches the main chain. Defaults come
from the router catalog (subscription -> cheap -> free); any
chain can be configured with an explicit provider list, in
order — config wins, nothing is hardcoded. Refs beyond the
three tiers are dropped and reported; an unlisted model's cost
is unknown (rate 0), never invented.

Execution is the existing `puspa.router.fallback.call_with_fallback`
(3-tier chain, quota-aware, Rupiah-budget enforcing). What G21
added to it: `auth_error` is a retryable kind (401, or 403 with
an auth signal), so an auth failure moves to the next tier —
but only after a credential refresh has been tried first.

**Auth refresh-first** (`turn_api_error.wrap_with_auth_refresh`):
on an auth failure the shared OAuth refresher
(`puspa.router.oauth.TokenRefresher`, via `router.py` — the one
the router already owns, no second refresher written here) is
asked for a fresh token and the same tier is retried once. If
the retry still fails auth, the chain moves on. The retry only
helps when the per-tier call resolves its credentials on every
call — the built-in OpenRouter adapter does (`resolve_api_key`
per call), so a refreshed token is picked up; an adapter with a
fixed key baked in at build time cannot benefit.

```python
# 429 on tier 1 -> tier 2 answers; auth error -> refresh, retry, then tier 2
outcome = call_model(history, config=config, model_call=fake)
outcome.tier_used        # the ChainEntry that answered
outcome.attempts         # one FallbackAttempt per tier tried
outcome.hops             # every tier switch, with its reason
```

## Budgets

Two independent guards:

- **Iteration budget** (`turns/budget.py`, this loop): counts
  model calls per turn. Default 500 per agent, 50 per
  subagent, from `TurnConfig`. A subagent budget is capped by
  the smaller of its own limit and the parent's remaining
  iterations, and every subagent iteration also consumes one
  parent iteration. Exceeding it raises `TurnBudgetExceeded`
  and the turn ends with status `budget_exceeded` and a message
  stating the limit and the iterations used — a clean stop,
  never a crash, never a silent loop.
- **Rupiah budget** (`puspa.runtime.budget.BudgetTracker`,
  Rp2.000 per task): unchanged, enforced by the fallback
  executor on every call; a fallback never dodges it.

`TurnResult` records `iterations_used`, `iterations_limit`,
`subagent_iterations_used`, and `cost_rp`.

## Tools intercepted before the registry

`intercepts.InterceptedTools` handles four tools in the loop,
before the registry — they change agent state directly and
return synthetic results, so they never reach the registry:

| Tool | Real store behind it |
| --- | --- |
| `todo` | `turns/todos.TodoStore` (in-memory per turn — see limit below) |
| `memory` | `puspa.memory.facts` (`record_fact` / `list_active_facts` / `hybrid_recall`; the trust gate runs on record) |
| `session_search` | `SessionGateway.search_messages` (SQLite FTS5, G7 mechanism) |
| `delegate_task` | injected `delegate` callable, or the real `SubagentPool.spawn` (one level only, pool-enforced) |

Actions: `todo` takes `add` / `list` / `complete` / `remove`;
`memory` takes `record` / `list` / `recall`; `session_search`
takes `query` (+ `limit`, `session_id`); `delegate_task` takes
`purpose` (+ `permission_class`, `role`). Every intercepted call
returns `{"intercepted": True, "tool": ..., "result": ...}` (or
`"error": ...` when the store is not wired — an honest error
result, never a fake empty list or a fake subagent id). An
unknown tool name returns `None` and goes to the registry as
usual.

## Using it

```python
from puspa.turns import TurnConfig, TurnLoop, InternalMessage

loop = TurnLoop(
    config=TurnConfig(max_iterations=500),
    model_call=None,        # default: live OpenRouter adapter
    tool_call=my_registry,  # (name, arguments) -> result dict
    intercepts=...,         # InterceptedTools with real stores
)
result = loop.run_turn(
    [InternalMessage.user("Compare rice prices.")],
    user_id="user-1",
    task_id="task-1",
    tools=[...],            # OpenAI-format function tools
)
result.status  # "completed" | "interrupted" | "budget_exceeded" | "failed"
result.text    # the reply text ("" unless completed)
result.message # what happened, in one clear line
result.messages  # the full history afterwards
```

Subagent turn (own narrowed budget):

```python
result = loop.run_subagent_turn(
    [InternalMessage.user("Research this.")],
    parent_budget=parent_iteration_budget,
)
```

TaskRunner wiring (the one loop for every surface):

```python
from puspa.turns import make_task_executor
from puspa.runtime.tasks import TaskRunner

runner = TaskRunner(executor=make_task_executor(model_call=...))
# Channels: TaskRunnerAgent(runner=runner) — the existing seam.
```

## Honest limits

- **No live provider call is proven here.** The default adapter
  is the router's live OpenRouter caller, but no test in this
  package touches the network (all model calls in tests are
  injected fakes) and no live turn has been run. A live turn
  needs a key in the BYOK path and a real run.
- **No durable todo store exists in Puspa.** `todo` is backed
  by an in-memory store scoped to one turn loop instance: it
  works inside a turn and is forgotten when the process ends. A
  durable store (a table next to the session store, or a
  task-plan projection) is a follow-up.
- **The production chassis executor is not wired.** The loop's
  default model path is the router, not `puspa.chassis`; the
  chassis executor named in `puspa/channels/agent.py` is still
  a follow-up. Until it lands, channels with no executor raise
  `ChannelError` fail-closed, as before.
- **OAuth refresh is in-memory.** The refresher's tokens do not
  survive a restart (see `puspa/router/oauth.py`); the
  simulated provider registrations raise clear errors until a
  real registration is supplied.
- **Compression is a seam, not a feature.** The overflow
  decision can ask for `compress`, but without an injected
  `compressor` callable the loop truncates instead and says so.
  No LLM summariser is wired to the compress path yet.
- **Turn events are advisory.** The loop's sink records
  `turn_started`, `model_called`, `tool_intercepted`,
  `tool_called`, `turn_completed`, `turn_interrupted`,
  `turn_failed`, `budget_exceeded` — best-effort, like the
  TaskRunner sink. They are NOT in the core ledger's closed set
  of 31 event types, so a production Ledger sink drops them
  silently (the same rule as `world_prediction`); the loop's
  own `event_memory()` is the record for tests.
- **The Responses API has no live caller.** The converters exist
  and round-trip in tests, but the loop's live path is chat
  completions only; a Responses-mode caller is a follow-up.
- **Interruption abandons, never cancels.** The in-flight HTTP
  request keeps running on its daemon thread until the provider
  answers or its own timeout fires; only the result is
  discarded.
