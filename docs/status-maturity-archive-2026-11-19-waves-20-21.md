# Status archive: maturity Waves 20-21 (19 Nov 2026)

Archived from `docs/status-maturity.md` on 19 Nov 2026, when the
live log passed the soft cap again (980 lines before the Wave 28
entry). Frozen, never appended to; history is never deleted, only
archived. The living log continues in `docs/status-maturity.md`
(Wave 22 onwards).

## Wave 20: G17 (skill narrowing + search_tools, DONE 19 Nov 2026)

G17 is the LAST of the original 28 gaps: per-run narrowing and
`search_tools`. Built on top of the existing G23 registry (worker
20103db5, verified centrally by the coordinator).

What was built (new module `puspa/tools/narrowing.py`, 644 lines;
tests 572 lines; docs 236 lines):
- `prepare_run_tools(message, catalog, skills, granted, selector,
  config, audit_sink)`: refusal check first (catalog + granted
  > 128 -> audit + raise), then skill selection, then the ~10
  offered cap (tools ranked by message<->name/description token
  overlap, ties by name), granted tools always kept, overflow
  past 50 behind `search_tools`.
- Default selector `KeywordSelector`: deterministic trigger-term
  (2 pts) + description-token (1 pt) overlap, no LLM call, no
  cost. The selector is a pluggable `ToolSelector` protocol
  (`select(message, skills)`); an LLM-backed selector is NOT
  built — the seam (implement the protocol with the router's
  `llm_complete` callable, fail to `None` so it falls back to
  keywords then fail-open) is documented in
  `docs/skill-narrowing.md`.
- Real `search_tools` tool: registered via
  `register_search_tools()` (idempotent) and by
  `register_builtin_tools`, so discovery picks it up. The
  handler searches the run's hidden tools when bound via a
  `ContextVar` (`hidden_tools_context`, same pattern as the
  token binding in `puspa/chassis/context.py`), otherwise the
  registry it was registered on. Results ranked by keyword
  overlap, capped (default 10, max 50), each carrying name +
  description + parameters. Pure function `search_tools(query,
  tools)` is directly testable.
- Refusal audit goes through the G4/G5/G6 ledger-shape sink
  `sink(event_type, content)` — no new audit path.

Wiring:
- `puspa/turns/config.py`: `TurnConfig` gains
  `max_direct_tools=50`, `max_tools=128`,
  `max_offered_tools=10`, `search_result_limit=10` with
  validation (mirrors narrowing defaults as literals so config
  never imports the tools package).
- `puspa/turns/loop.py`: `TurnLoop(tool_catalog=...,
  narrowing_skills=..., narrowing_selector=...)`; `run_turn`
  narrows on the last user message when a catalog is wired and
  no explicit `tools=` passed (explicit tools win), binds
  hidden tools for the whole turn, and returns a failed
  `TurnResult` naming the limit before any model call when
  refused. `run_turn`'s body moved verbatim into
  `_run_turn_inner`; `run_subagent_turn` inherits narrowing via
  delegation.
- One test expectation updated honestly: `test_discover_imports_
  tool_modules` expected exactly 2 discovered tools; discovery
  now legitimately registers `search_tools`, so the assertion
  is 3 + `search_tools` present.

Coordinator decision — refusal audit vs the ledger's closed 31
(the worker flagged this for a decision): NO ledger change.
The worker's concern was that `agent.tool_limit_refused` is
not one of the 31 revisi-7 event types, so a raw Ledger sink
(best-effort `suppress(Exception)` in `make_ledger_sink`)
swallows it. Verified by the coordinator: that is exactly how
EVERY turn-loop event behaves — `turn_failed`,
`turn_completed`, `narrowing_selected` and the rest are also
outside the 31, kept in the turn sink (in-memory list by
default), and swallowed by a ledger sink the same way. The
refusal is recorded twice in the turn sink (the refusal event
itself, then the loop's `turn_failed` carrying the message),
exactly like any other turn failure. Singling the refusal out
for a 32nd type — or shoehorning it into `task_failed` — would
be inconsistent with the whole turn-loop vocabulary, and the
G6 precedent is explicit: the closed set stays untouched.
The same gap exists in the G3 marketplace narrowing (same
event name, same sink shape); it is a shared known limit,
documented in `docs/skill-narrowing.md`, not a G17 regression.

Suite (coordinator, SOLO, 19 Nov 2026): 3,149 passed, 23 skipped,
1 warning, 20m48s, EXIT 0 (log hidden_files/suite-wave20-g17.log).
Reconciliation: 3,116 (Wave 19) + 33 (G17 module tests) = 3,149;
skipped unchanged at 23. Module tests (coordinator re-run): 33 passed
(test_narrowing.py 24 + test_narrowing_turn.py 9, incl. a real
run where the fake model calls `search_tools` mid-turn and
reaches a hidden tool). Worker's exact command
(test_narrowing*.py + test_tools*.py): 48 passed. Neighbor
regression (coordinator re-run): 191 passed (registry,
approval, check, footprint, result-compression, turn loop,
turn intercepts, marketplace narrowing — the worker's "239"
was the combined total of both runs: 48 + 191). Ruff check +
format: clean. Mypy on the changed files: clean.

Two bugs found by the worker's own tests and fixed in code
(not papered over): narrowed-away tools were never added to
`hidden` when the cap didn't trigger, and the `search_tools`
handler always searched the default registry instead of the
registry it was registered on.

Honest limits: a wrong-but-confident narrowing hides tools
(the keyword selector has no synonyms, no stemming, no
understanding; `search_tools` is the recovery path and only
helps if the model calls it). Narrowing runs once per turn on
the last user message; a mid-turn topic change does not
re-narrow. With no skills registered, narrowing is a no-op
fail-open (overflow/refusal rules still apply). 50/128 are
OpenBot's starting points, not numbers measured on Puspa's
models. Intercepted tools (`todo`, `memory`, `session_search`,
`delegate_task`) are not registry tools and are not counted
toward the limits.

All 28 gaps G1-G28 are now DONE. Remaining (order locked by Nero
19 Nov 2026 ~05:41 WIB): Kosmos K1, K3, K4, K5, K6, K10, K11, K12,
K13, then B features (B6 group chat, B9 tasks, B10 meetings, B11
patungan, B12 joint shopping, B13 drafts), then B15 culture LAST
(the BUDAYA GO! 2026 batik demo order from ~05:37 WIB was
cancelled by Nero before any work started). Then uji + 15 gates
+ 10 reference tasks (waiting on Nero's decisions).

## Wave 21: Kosmos K1 (Intent Engine, DONE 19 Nov 2026)

First Kosmos item after all 28 G gaps. K1 builds the intent
SIGNAL (kosmos-design.md section B); the act-vs-ask RULE itself
is K2, already folded into G4/G5 — K1 adds what the rule needs
from intent, and the ask always goes through the existing G4
approval mechanism. No second approval system was built
(worker 5b70678c, verified centrally by the coordinator).

What was built (new package `puspa/intent/`, all files under
1000 lines; tests 786 lines; docs 222 lines):
- `distribution.py` — `IntentDistribution` with the three
  levels (desire, goal, values), each a scored set of
  `IntentHypothesis` values normalized to sum to 1 per level.
  Empty levels are allowed and recorded as empty, never
  invented. Unnormalized direct construction raises
  ValueError. Conflict = top-two weights within 0.15.
- `inference.py` — default deterministic inferrer: keyword /
  topic coverage against the user's known goals/values from
  the profile (only intent-bearing keys: `goal*` -> goal,
  `value*` / `principle*` -> values, `want*` / `need*` /
  `desire*` / `preference*` -> desire; identity facts like
  name/address ignored) plus world beliefs (unpaid bills,
  known prices -> desire; repeated habits, schedules -> goal).
  A small hand-written signal lexicon is a fallback only at
  the desire level when nothing grounded scored, and concept
  expansion is how one message reads at all three levels.
  `IntentInferrer` is the pluggable interface;
  `build_llm_prompt` documents the LLM seam contract (JSON,
  weights sum to 1, empty level = `[]`) — the LLM inferrer
  itself is NOT built, per spec.
- `act_ask.py` — the intent signal feeding act-vs-ask.
  Decision order: G4 policy first when wired in and the kind
  is recognized (deny/ask/escalate wins; allow lets intent
  run) -> fully empty distribution fails open to act -> desire
  unread asks -> conflicting asks -> top weight < 0.6 asks ->
  irreversible/high-stakes asks -> otherwise act. Plus
  `clarifying_question` (one calm question naming what was
  read).
- `turn_wiring.py` — `IntentTurnAdvisor` (profile + world +
  `ActionSpec` per tool: reversible/stakes) and the ask
  payload (`tool_was_run: false` + question).

Wiring into the turn loop (`puspa/turns/loop.py`): optional
`intent_advisor` on `TurnLoop`. Consulted before a registry
tool call (never for intercepted tools); on `ask` the tool is
NOT run and the model gets the ask payload; on `act` the call
runs as before. Verdicts land on `TurnResult.intent_decisions`
plus an `intent_ask` sink event. A broken advisor fails open
to running the call. Without an advisor behavior is unchanged.
Also a short K1 paragraph in `docs/turn-loop.md`.

Two bugs found by the worker's own tests and fixed in code:
`_world_state` returned `{}` for a `WorldModel` (its `state`
property yields a dict, not a callable), and the conflict check
was unreachable after the confidence check (normalized weights
make a conflicting top necessarily < 0.575 < 0.6), so conflict
is now checked before low confidence, with the math documented
in the module docstring.

Suite (coordinator, SOLO, 19 Nov 2026): 3,200 passed, 23 skipped,
1 warning, 21m32s, EXIT 0 (log hidden_files/suite-wave21-k1.log).
Reconciliation: 3,149 (Wave 20) + 51 (K1 module tests) = 3,200;
skipped unchanged at 23. Module tests (coordinator re-run): 51 passed
(test_intent.py + test_intent_turn_wiring.py, incl. the run
test proving the distribution changes the decision: same
message "buy the laptop" with one recorded preference -> act
and the tool runs; with two equally-matching preferences ->
ask and the tool never runs; same for world state, "pay the
bill" acts with one unpaid bill, asks with two). Regression
(coordinator re-run): 72 passed (test_turn_loop.py +
test_approvals_taxonomy.py). Ruff check + format: clean. Mypy
on the changed files: clean.

Honest limits: keyword overlap, not understanding
(paraphrases with no shared words read nothing; "save the
file" false-matches a "save money" goal); no morphology
("prices" != "price", "frugality" != "frugal"); thresholds
0.6/0.15 are chosen, not calibrated; no learning from
corrections yet; the advisor reads only the last user message,
not turn history; profile freshness is the caller's job (or
use `context_provider`); LLM inferrer is a documented seam
only, no live model call exists.

Remaining: Kosmos K3, K4, K5, K6, K10, K11, K12, K13, then B
features (B6, B9, B10, B11, B12, B13), then B15 culture LAST.
Then uji + 15 gates + 10 reference tasks (waiting on Nero's
decisions).

