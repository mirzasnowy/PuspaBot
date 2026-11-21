# Skill narrowing + `search_tools` (skill narrowing and tool search)

Before a run, PuspaBot picks the skills that fit the user message and
offers the model only those skills' tools plus the granted tools.
A model picks reliably from ~10 tools and unreliably from 30, so
the offered set stays small; when the catalog is big anyway, the
rest waits behind one `search_tools` tool; past the hard limit the
run is refused. Pattern from OpenBot (`app/src/lib/skills/` +
`docs/architecture.md`), rewritten as PuspaBot code — never copied
verbatim.

This is the registry-level twin of the marketplace narrowing
(`puspa/marketplace/narrowing.py`, module marketplace (registry, trust, install, rollback)). That module narrows
marketplace skill manifests (plain dicts); this module
(`puspa/tools/narrowing.py`) narrows `ToolDefinition` objects
from the registry, and it is what the turn loop actually runs.
The two share the thresholds, the event name, and the fail-open
rule, but not code: the marketplace module must never depend on
the registry, and this module must never import the marketplace
package (importing it would drag the whole marketplace `__init__`
into the tools layer). The `ToolLimitError` here is likewise a
tools-layer twin of the marketplace one.

## Thresholds

| Threshold | Default | Meaning |
| --- | --- | --- |
| `max_offered_tools` | 10 | A successful narrowing offers at most this many catalog tools directly. |
| `max_direct_tools` | 50 | Past this many offered tools, the rest wait behind `search_tools`. |
| `max_tools` | 128 | Past this many tools in the run's catalog, the run is refused. |
| `search_result_limit` | 10 | Results one `search_tools` call returns (at most 50). |

The defaults are the OpenBot starting points from the comparison
report — usable as-is, not tuned to PuspaBot data. Configure them in
`NarrowingConfig` (`puspa/tools/narrowing.py`) or, for the turn
loop, in `TurnConfig` (`puspa/turns/config.py`: `max_direct_tools`,
`max_tools`, `max_offered_tools`, `search_result_limit`). The
`TurnConfig` numbers are plain literals mirroring the narrowing
defaults, so the config module never imports the tools package;
the loop builds a `NarrowingConfig` from them.

## How a run is prepared

`prepare_run_tools(message, tools, skills=..., granted=...,
selector=..., config=..., audit_sink=...)` in
`puspa/tools/narrowing.py`:

1. **Refusal check.** The catalog plus granted tools not already
   in it is counted first. Past `max_tools`, the function records
   the `agent.tool_limit_refused` audit event and raises
   `ToolLimitError`. Nothing is narrowed, no model call happens.
2. **Skill selection.** The selector picks skill ids for the
   message (below). Skills whose tools are picked are the
   narrowed set; tools of unpicked skills become hidden tools.
3. **Cap.** When the narrowed set is over `max_offered_tools`,
   tools are ranked by token overlap between the message and each
   tool's name + description (ties broken by tool name) and the
   top `max_offered_tools` are offered; the rest join the hidden
   tools.
4. **Granted tools** (names resolved against the catalog, or
   definitions passed directly) are always appended, whatever
   narrowing picked. They are not counted against the offered cap.
5. **Overflow.** When the offered set is over `max_direct_tools`,
   only the first `max_direct_tools` stay directly offered; the
   rest become hidden tools and one `search_tools` tool is
   appended. When narrowing hid tools but there is no overflow,
   `search_tools` is appended anyway, so the model can always
   recover a tool narrowing missed.

The result (`NarrowingResult`) carries the offered tools, the
hidden tools, the selected skill ids, `narrowed` / `fail_open`,
and the audit records. `schemas()` returns the OpenAI-style
schemas for the offered tools, in offered order.

## Fail-open rule

When narrowing finds nothing — no skill matches the message, the
selector raises, the selector returns `None`, or it names only
unknown skills — the whole catalog is offered and the fall-back is
recorded as a `narrowing_fail_open` audit record. A fail-closed
narrowing would silently remove capabilities the model might
need. A selector that raises falls back to the keyword selector
first (recorded as `narrowing_selector_failed`); only when that
also finds nothing does the run fail open.

## The selector interface and the LLM seam

```python
class ToolSelector(Protocol):
    def select(self, message: str, skills: Sequence[Skill]) -> list[str] | None: ...
```

Return skill ids (possibly empty), or `None` when the selector
cannot decide. Empty and `None` both lead to fail-open; raising
falls back to keywords, then to fail-open.

The default selector is `KeywordSelector`: deterministic
trigger-term + description overlap. Per skill: 2 points per
trigger term that matches the message (whole-word token overlap,
or the term appearing as a substring), plus 1 point per
description token shared with the message. Only positive scores
are picked, best first, ties broken by skill id. No LLM call, no
cost, no network, fully testable.

**The LLM selector is not built.** The seam for it is this
protocol plus the router's completion callable. A future
LLM-backed selector implements `ToolSelector.select` by calling
an `llm_complete(prompt)` callable — the same seam the retrieval
modules use (`puspa.router.complete.resolve_llm_complete`, which
accepts a callable or the string `"default"` for the router's
cheapest model for a role). The prompt lists the skill ids +
descriptions and asks for JSON `{"skills": [...]}`; any failure
of that call (exception, unparsable reply, unknown ids) must
return `None` so the run falls back to keywords and then to
fail-open — an LLM selector must never fail a run closed. Do not
wire an LLM selector into the default path without a test that
proves the failure fallback.

`Skill` is a small frozen dataclass: `id`, `description`,
`trigger_terms`, `tools` (registry tool names). Skills are built
by the caller — from a marketplace manifest, a module manifest,
or a hand-written list — never guessed from tool names.

## `search_tools`

`search_tools` is a real registered tool, not a special case in
the loop:

- Registration: `register_search_tools(registry)` in
  `puspa/tools/narrowing.py`; `puspa/tools/builtin.py` calls it
  during discovery, so the tool exists on the default registry.
  Registration is idempotent.
- Schema: `query` (required string) + `limit` (integer, default
  10, at most 50). It is read-only.
- Behavior: the handler searches the run's hidden tools when the
  run bound them, and otherwise searches the whole registry the
  tool was registered on. Results are ranked by token overlap
  between the query and each tool's name + description, best
  first, ties by name; an empty query returns the pool in name
  order. Each result carries `name`, `description`, and
  `parameters` — enough for the model to call a found tool. The
  reply says which scope was searched (`"hidden"` or
  `"registry"`) and how many results came back. Bad arguments
  return an error JSON string, never an exception.
- Per-run binding: the turn loop binds the run's hidden tools
  with `hidden_tools_context(...)` (a `ContextVar`, the same
  pattern as the token binding in `puspa/chassis/context.py`)
  for the duration of the turn, so the handler sees exactly
  this run's hidden set. The binding is restored on exit; nested
  runs do not leak hidden tools into each other.
- The pure function `search_tools(query, tools, limit=...)` does
  the ranking and is directly testable without a registry.

The loop dispatches a `search_tools` call like any other tool
call, through the injected registry callable — no intercept, no
special path. A found tool is then callable by name through the
same registry dispatch.

## Turn-loop wiring

`TurnLoop` (`puspa/turns/loop.py`) takes an optional
`tool_catalog` (a list of `ToolDefinition`, already filtered by
toolset / allowlist / availability), `narrowing_skills`, and
`narrowing_selector`. In `run_turn`, when a catalog is wired in
and the caller did not pass explicit `tools`:

- the last user message in the history is narrowed on (a history
  with no user text narrows on `""` and fails open);
- the model is offered the narrowed schemas;
- the hidden tools stay bound for the whole turn, so the model
  can call `search_tools` in any iteration;
- a catalog past the hard limit returns a failed `TurnResult`
  (`status == "failed"`, message naming the limit) before any
  model call, with the refusal audit recorded through the loop
  sink.

Explicit `tools=` passed by a caller win over narrowing: an
explicit list is already a chosen set, and narrowing it would be
surprising. `run_subagent_turn` delegates to `run_turn`, so
subagent turns narrow the same way. `make_task_executor` passes
no catalog, so its behavior is unchanged.

## Audit

Narrowing records go through the `audit_sink` callable with the
approvals taxonomy/fail-closed gateway with CEL-style rules/audit trail with initiator ledger shape `sink(event_type, content)` — the same
shape `TurnLoop.sink` and `make_ledger_sink` use. No new audit
path is invented. Event names:

- `agent.tool_limit_refused` — the run was refused
  (`tool_count`, `limit`, `message`).
- `narrowing_selected` — narrowing picked skills (`skills`,
  `tool_count`).
- `narrowing_fail_open` — nothing matched, whole catalog offered
  (`message`, `tool_count`).
- `narrowing_selector_failed` — the injected selector raised
  (`selector`, `message`).
- `tool_overflow_search` — overflow put tools behind
  `search_tools` (`direct_tools`, `overflow_tools`).

**Honest limit — the ledger does not keep this event name.**
The ledger keeps a closed set of 31 event types (revision 7,
section 2.3), and `agent.tool_limit_refused` is not one of them,
so a raw Ledger sink drops the refusal record silently
(best-effort — the same as the existing `world_prediction`
precedent in `puspa/runtime/tasks.py`). The record is kept by
the turn sink, and the loop's `turn_failed` record carries the
refusal message. Making the refusal durable in the ledger needs
a decision about the closed set (a 32nd type, or riding inside a
`task_failed` content) — that decision belongs to the
coordinator, not to this item.

## Honest limits

- A wrong-but-confident narrowing hides tools: the keyword
  selector has no synonyms, no stemming, and no understanding —
  "cheapest" matches a `cheapest` trigger but "lowest price"
  matches only if some skill's description or triggers contain
  those words. `search_tools` is the recovery path, and it only
  helps if the model thinks to call it.
- The offered cap ranks by token overlap, which is a shallow
  signal: two tools with the same overlap are ordered by name,
  not by usefulness.
- Narrowing runs once per turn, on the last user message. A
  mid-turn change of topic does not re-narrow; the model reaches
  the new topic's tools through `search_tools`.
- Skills are caller-built. With no skills registered, narrowing
  is a no-op fail-open (the catalog is offered; the overflow and
  refusal rules still apply).
- The 50 / 128 thresholds are OpenBot's starting points, not
  numbers measured on PuspaBot's models or catalog. If the catalog
  grows past a handful of skills, re-measure where the model
  actually starts choosing unreliably.
- The refusal counts the catalog plus granted tools. Tools the
  loop intercepts (`todo`, `memory`, `session_search`,
  `delegate_task`) are not registry tools and are not counted.
