# Harness architecture: channels, agent core and models as decoupled layers

PuspaBot is an **agent harness**: a thin shell that carries messages
between the user and a model, with everything the model needs —
memory, knowledge, tools, skills — plugged in as replaceable layers.
Each layer talks to its neighbours through a small, named seam, so a
contributor can change one layer without rewiring the rest.

> **Honest limits, up front.** This doc describes what exists in the
> repo at this commit. Where a layer is built but not yet live — the
> Telegram/WhatsApp channels have never talked to a real bot token,
> and the channel agent seam still fails closed instead of answering
> — the doc says so. Nothing here describes a planned feature as if
> it were shipped.

Pattern attribution: the *framing* — channels in, core in the middle,
models out, each layer decoupled and independently extensible — is
studied from zhayujie/CowAgent (MIT). It is a documentation pattern
only: no CowAgent code was copied, and every module path below is
PuspaBot's own code. See `REUSE.md` and `NOTICE.md`.

## The layers

```text
  user
   |
   |  Layer 1 — CHANNELS: translate + transport only, never decide
   v
+------------------------------- channels -------------------------------+
| PWA web app        puspa/pwa/app.py          (create_app)              |
| CLI                puspa/cli.py              (puspa web / puspa mcp)   |
| Telegram adapter   puspa/channels/telegram.py                          |
| WhatsApp adapter   puspa/channels/whatsapp.py                         |
| MCP server         puspa/mcp_server/server.py (stdio JSON-RPC)        |
| Gateway            puspa/channels/run.py     (GatewayRunner)           |
+----------------------------------+-------------------------------------+
                                   | MessageEvent
                                   v
+------------------------------- agent core -----------------------------+
| Task lifecycle     puspa/runtime/tasks.py     (TaskRunner: accept ->   |
|                                              plan -> run -> verify ->  |
|                                              present)                  |
| Turn loop          puspa/turns/loop.py       (TurnLoop,               |
|                                              make_task_executor)       |
| Governor           puspa/governor/           (DepthGovernor, budgets, |
|                                              SkillCompiler)            |
+----------------------------------+-------------------------------------+
          |                |                 |                |
          v                v                 v                v
   memory            knowledge           tools             skills
   puspa/memory/     puspa/retrieval/    puspa/tools/      skills/*/SKILL.md
   working.py        puspa/world/        puspa/localexec/  puspa/governor/
   episodic.py       puspa/graphs/       puspa/computer/   compiler.py
   longterm.py                         (registry.py)     (SkillCompiler)
   consolidate.py
   recall.py
                                   |
                                   v
+------------------------------- models ---------------------------------+
| Router             puspa/router/router.py    (choose, default_chain)   |
| Fallback chain     puspa/router/fallback.py  (ChainEntry,              |
|                                              AllTiersFailed)           |
| Provider login     puspa/router/provider_login.py                      |
|                    (ProviderLoginService, known_provider_configs)      |
| Provider list      docs/subscription-providers-9router.md (23 entries)|
+------------------------------------------------------------------------+
                                   |
                                   v
                              model (LLM)
```

One rule holds the whole picture together: **a layer never reaches
past its neighbour.** A channel adapter never calls a model and never
decides authorisation; the turn loop never touches a platform API;
the router never reads memory. Each seam below is the only crossing
point.

## Layer 1 — Channels: translate and transport only

A channel turns a platform event into one `MessageEvent` and turns a
reply back into a platform send. That is all it does.

- **Contract** — `puspa/channels/base.py`: every adapter implements
  `parse_update(payload)`, `send_text(chat_id, text)` and
  `verify_webhook(request)`. Adapters never decide authorisation and
  never call the agent; the pipeline in `puspa/channels/inbound.py`
  owns the rest.
- **Telegram** — `puspa/channels/telegram.py`: raw Bot API over HTTPS
  (`TelegramAdapter`, long polling via `getUpdates` or webhook via
  `parse_update`), bounded send retries (3 attempts, 1s / 2s
  backoff), bot-loop guard on `is_bot`, group messages answered only
  when a room is linked to the group chat (B6, `puspa/rooms/`).
- **WhatsApp** — `puspa/channels/whatsapp.py`: raw Cloud API over
  HTTPS (`WhatsAppAdapter`, webhook only), GET handshake verification
  plus HMAC-SHA256 signature check (`X-Hub-Signature-256`), same
  bounded retry. WhatsApp groups do not exist on the Cloud API, so
  `is_group` is always False here.
- **Gateway** — `puspa/channels/run.py`: `GatewayRunner` is the
  facade, one live runner per profile (enforced process-wide).
  `puspa/channels/delivery.py` (`DeliveryService`) sends through an
  adapter and records pending -> sent/failed in its own Postgres
  table `channel_deliveries` (migration `0008_channels.sql`), not in
  the core ledger, with idempotency via `dedup_key`.
- **Agent seam** — `puspa/channels/agent.py`: `TaskRunnerAgent`
  connects the channel pipeline to the core's `TaskRunner`. Until its
  production model executor is wired, it fails closed instead of
  pretending to answer (see `docs/channels.md`).
- **PWA (the home)** — `puspa/pwa/app.py`: `create_app()` serves the
  web app; channels are doors, the PWA is the home. See `docs/pwa.md`.
- **CLI** — `puspa/cli.py`: `puspa web` / `puspa serve` start the
  local gateway and open the PWA; `puspa mcp` serves the MCP server;
  `puspa memory export/import`, `puspa service`, `puspa diagnostics
  export` are the other subcommands.
- **MCP server** — `puspa/mcp_server/server.py`: a small JSON-RPC 2.0
  server over stdio (`initialize`, `ping`, `tools/list`,
  `tools/call`), standard library only. Every `tools/call` goes
  through the policy gateway (`puspa/policy/gateway.py`) first; a
  refused call is returned as a tool result with `isError: true`
  and the handler never runs. Stdio only: no HTTP/SSE transport, no
  sampling, no prompts or resources. See `docs/` notes on V2-37.

**Live vs not yet live.** The Telegram and WhatsApp adapters, the
authorisation, pairing, delivery ledger and heartbeat are built and
tested against fake HTTP transports (`MockTransport`) — but no live
bot has been verified: no real Telegram/WhatsApp token, no message
ever sent to a real chat. The first live check (a real bot token, one
real DM) is a follow-up. The PWA, the CLI and the MCP server are the
live surfaces today.

**To extend:** add one adapter implementing the `base.py` contract
and register it with the gateway. No core code changes; the pipeline,
the delivery ledger and the agent seam are shared by every channel.

## Layer 2 — Agent core: plan, reason, remember, act

The core is the only layer that decides anything. It has three parts:
a task lifecycle, a turn loop, and a governor.

### Task lifecycle — `puspa/runtime/tasks.py`

`TaskRunner` owns the task lifecycle: **accept -> plan -> run ->
verify -> present**. It stays the lifecycle; the conversational engine
inside its `run` stage is the turn loop, injected as the step
executor (`make_task_executor` in `puspa/turns/loop.py`). There is no
second parallel loop: the PWA, the channels and the scheduler all
reach the same loop through `TaskRunner`, so one history, one budget
and one interruption rule serve every surface.

### Turn loop — `puspa/turns/`

`TurnLoop` (`puspa/turns/loop.py`) is the conversational engine. One
turn, in order, per iteration (each phase is its own module):

1. **prepare** (`turn_prepare.py`): interrupt check, one iteration
   of budget, alternation repair.
2. **overflow** (`turn_overflow.py` / `turn_truncate.py`): shrink the
   history when it passes the caps, never touching the system block
   or the protected tail.
3. **API call** (`turn_api.py`): the internal history goes out
   through the fallback chain, interruptibly, with refresh-first auth
   handling (`turn_api_error.py`).
4. **tool calls** (`intercepts.py` first — intercepted tools never
   reach the registry — then the injected registry callable):
   results return as tool messages; the loop runs again until the
   model answers with text, the budget runs out, or the user
   interrupts.
5. **recovery** (`turn_recover.py`): a checkpoint is taken before
   every call; an interrupt, timeout, or all-tiers failure restores
   it, so no partial reply ever enters the history.

A turn ends with status `"completed"`, `"interrupted"`,
`"budget_exceeded"` or `"failed"` — always with a clear message,
never a silent stop. Budgets live in `puspa/turns/budget.py`
(`IterationBudget`, `SubagentIterationBudget`); limits live in
`puspa/turns/config.py` (`TurnConfig`), never hardcoded. See
`docs/turn-loop.md`.

### Governor — `puspa/governor/`

The governor bounds what a turn may do: `governor.py`
(`DepthGovernor`, depth decisions per step), `budgets.py`
(compilation and step budgets), `skills.py` (skill execution
context), `compiler.py` (`SkillCompiler`, see the Skills section
below). The governor is a limiter, not a planner: it says how deep
and how far, the turn loop decides what.

**To extend:** add a turn phase as its own module under
`puspa/turns/` and wire it into `TurnLoop`; add a governor rule in
`puspa/governor/` without touching the loop. Both are injected, not
hardcoded into the loop body.

## Memory — `puspa/memory/`

Three tiers, one recall path. The turn loop reads memory through
recall; it never queries a tier directly.

- **Working memory** — `puspa/memory/working.py` (`WorkingMemory`,
  `WorkingItem`): what is live in this session, attention-weighted.
- **Episodic memory** — `puspa/memory/episodic.py`: past turns as
  episodes, dual-level search (`dual_level_search`,
  `search_episodes_dual`) over entities and concepts, with vector
  search through the active embedder.
- **Long-term memory** — `puspa/memory/longterm.py` (`MemoryItem`):
  durable facts, services and profile material.
- **Consolidation** — `puspa/memory/consolidate.py`:
  `consolidate_idle` folds old episodes into summaries
  (`list_summaries`), `reflect_and_propose` reflects episodes into
  proposed memories. Consolidation runs when idle, never inside a
  turn.
- **Recall** — `puspa/memory/recall.py`: `hybrid_recall` merges
  full-text search (`FtsIndex`, FTS5) with recency and vector ranks
  (reciprocal rank fusion). Durable storage details live in
  `puspa/memory/durable.py`; background review in
  `background_review.py`, the dream/consolidation pass in `dream.py`.

See `docs/durable-memory.md` and `docs/memory-export-format.md`.

**To extend:** add a tier by implementing the same record/search
shape and registering it with recall; add a consolidation rule in
`consolidate.py`. A new tier never changes the turn loop.

## Knowledge — `puspa/retrieval/`, `puspa/world/`, `puspa/graphs/`

Knowledge is what the agent knows about the world, as distinct from
what it remembers about the user. Retrieval (`puspa/retrieval/`)
fetches it, the world model (`puspa/world/`) holds the agent's
picture of the current state, and the graph layer (`puspa/graphs/`)
holds relations between things (the user-visible, user-editable
second-brain view is documented in `docs/graph-view.md`). The turn
loop asks retrieval for context the same way it asks memory: through
one call, not by reaching into a store.

**To extend:** add a retriever behind the retrieval seam; the core
sees new knowledge without a new code path.

## Tools — `puspa/tools/`, `puspa/localexec/`, `puspa/computer/`

Tools are how the core acts on the world. The registry is the seam;
the executors behind it are interchangeable.

- **Registry** — `puspa/tools/registry.py`: `ToolRegistry`
  (`register`, `discover`, `schemas`, `handle_function_call`),
  `default_registry()`. Tools are discovered from packages, not
  hardcoded into the loop; `puspa/tools/builtin.py` holds the
  built-ins, `toolsets.py` groups them, `narrowing.py` trims the
  visible set per turn, `approval.py` gates acting tools.
- **Local execution** — `puspa/localexec/executor.py`: runs a command
  on the user's own machine behind a permission prompt
  (`PermissionPrompt`, `PermissionDecision`), workspace-scoped
  (`OutsideWorkspaceError`), with a scrubbed environment
  (`scrub_env`). This is the minimal local runner that replaced the
  private sandbox for self-hosted use (see
  `docs/split-public-private.md`).
- **Computer use** — `puspa/computer/`: hands on the desktop
  (`hands.py`), accessibility tree (`tree.py`), screenshots
  (`screenshot.py`), vision fallback (`vision.py`), input
  (`input.py`), window management (`windows.py`), with tiered
  permissions and a stop switch (`tiers.py`, `stop.py`), safety
  gating (`safety.py`, `gating.py`, `allowlist.py`) and an audit
  trail (`audit.py`). See `docs/computer-hands.md` and
  `docs/computer-visual-fallback.md`. The computer layer is
  capability-gated by design: reading, clicking and verifying are
  the core; full input and window management are later tiers.

**To extend:** register a tool with the registry (or drop a module
into a discovered package) and it appears in `schemas()` for the
model. A new executor — a new sandbox, a new desktop backend — plugs
in behind the same tool names.

## Skills — `skills/`, `puspa/governor/compiler.py`

Skills are reusable know-how, not code paths. Each skill is a
`SKILL.md` file under `skills/` (for example `skills/price-check/`,
`skills/bills/`, `skills/memory/`, `skills/find-skills/`), written so
the agent can read the procedure and follow it with the tools it
already has. Discovery and narrowing are documented in
`docs/skill-discovery.md` and `docs/skill-narrowing.md`.

The **skill compiler** (`puspa/governor/compiler.py`,
`SkillCompiler`) is the other half: it generalises repeated runs
(`generalise_runs`, `GeneralisedPlan`) into a reusable plan, so a
procedure the agent performed three times by hand becomes a skill
it can replay. `puspa/memory/skill_repair.py` keeps compiled skills
honest when the world changes.

**To extend:** add a `skills/<name>/SKILL.md` file — no code, no
registration, no restart of the core. To teach the agent a new
reusable procedure from its own history, run the compiler over the
recorded runs.

## Models — `puspa/router/`

The model layer turns a role ("answer this turn") into a live model
call, with a fallback chain when a provider fails. The core never
names a provider; it asks the router for a chain.

- **Choosing** — `puspa/router/router.py`: `choose(role)` picks a
  model for a role, `default_chain(role)` builds the fallback chain,
  `choose_with_quota` respects a quota store, `compute_cost` prices
  a call. `build_provider` constructs the provider client.
- **Fallback** — `puspa/router/fallback.py`: `ChainEntry` is one
  tier of the chain; `ProviderFailure` (with `RateLimited`,
  `QuotaExhaustedError`, `AuthError`, `ProviderError`,
  `NonRetryableError`) classifies a failure; `AllTiersFailed` is
  raised only when every tier failed, and the turn loop's recovery
  phase handles it. Failure classes are in `classify_failure`.
- **Provider login** — `puspa/router/provider_login.py`:
  `ProviderLoginService` owns the login flows (PKCE OAuth via
  `build_pkce_auth_url` / `exchange_pkce_code`, device-code flow via
  `start_device_flow` / `poll_device_code`, pasted-token finish),
  `known_provider_configs()` lists the providers PuspaBot knows how
  to log into, `VaultTokenStore` keeps tokens in the vault.
  Refresh-first auth (a stale token is refreshed before the call,
  not after it fails) is handled in `turn_api_error.py`.
- **Provider list** — `docs/subscription-providers-9router.md`: the
  9Router list, 23 providers, read from the 9Router clone
  (decolua/9router, MIT) as a reference for OAuth shapes. No 9Router
  code was vendored. The default model adapter is the router's live
  OpenRouter caller (see `docs/turn-loop.md`).

See `docs/model-selection.md` and
`docs/subscription-providers-9router.md`.

**To extend:** add a provider config to `known_provider_configs()`
and a chain entry to the router; the core, the channels and the
skills never learn the provider's name.

## How a message flows, end to end

A Telegram DM, a PWA chat message, a CLI line and an MCP
`tools/call` all take the same path once they cross their channel:

```text
1. Channel adapter parses the platform payload into a MessageEvent
   (puspa/channels/telegram.py, whatsapp.py, or the PWA/CLI/MCP
   equivalent) and hands it to the gateway (puspa/channels/run.py).
2. The gateway authorises, dedups (delivery ledger) and routes the
   event to the agent seam (puspa/channels/agent.py).
3. TaskRunner accepts the task (puspa/runtime/tasks.py) and the turn
   loop takes over the run stage (puspa/turns/loop.py).
4. The loop prepares the turn: recall pulls working, episodic and
   long-term memory (puspa/memory/recall.py); retrieval adds
   knowledge; skill narrowing picks the visible skills and tools.
5. The governor sets the depth and budget for this turn
   (puspa/governor/).
6. The loop calls the model through the router's fallback chain
   (puspa/router/); on tool calls it runs the registry
   (puspa/tools/registry.py) and loops until the model answers
   with text, the budget runs out, or the user interrupts.
7. The turn ends, the reply is committed to memory
   (puspa/memory/commit_turn.py) and returned up the same path:
   gateway -> delivery service -> channel adapter -> user.
8. When idle, consolidation folds old episodes into summaries
   (puspa/memory/consolidate.py).
```

Every step is ledger-recorded where it matters (the delivery ledger
for sends, the core ledger for agent actions), so a reply can always
be traced back to the event, the turn and the model call that
produced it.

## What this doc deliberately does not cover

- **`puspa/harness/` is not this harness.** That package is the
  *coding* harness: a write-test-fix loop for code tasks
  (SWE-bench task shape, per-tool permissions, sandbox runs), built
  for the brain's engineering work. The "harness" in this doc is the
  *agent* harness: channels, core and models. Same word, different
  machine; do not confuse the two.
- **Hosting, billing, marketplace and the operator panel are
  private.** They live in the private repo by design
  (`docs/split-public-private.md`, demarcation D-08) and are not
  part of this architecture. This doc covers only what a user can
  self-host.
- **The runtime layer** (VM swap/recovery, watchdog, smart
  CPU/memory allocation) is separate from the app layer and is
  documented in the private repo.

## Reading further

- Channels in depth: `docs/channels.md`
- Turn loop in depth: `docs/turn-loop.md`
- Memory: `docs/durable-memory.md`
- Skills: `docs/skill-discovery.md`, `docs/skill-narrowing.md`
- Models and providers: `docs/model-selection.md`,
  `docs/subscription-providers-9router.md`
- Computer use: `docs/computer-hands.md`,
  `docs/computer-visual-fallback.md`
- Public/private split: `docs/split-public-private.md`
- Brain design (Kosmos): `docs/kosmos-design.md`
- Pattern log: `REUSE.md`, `NOTICE.md`
