# Intent Engine (Kosmos K1)

Intent as a distribution over three levels (`docs/kosmos-design.md`
section B): the **current desire**, the **ongoing goal**, and
**long-term values**. One message is read at all three levels at
once: "I'm so tired" is a desire to rest now, a goal to protect
sleep, and a value around health — when the profile and the world
say so.

## The distribution

`puspa/intent/distribution.py`: `IntentDistribution` holds one
`IntentLevel` per level. Each level is a scored set of
`IntentHypothesis` values (label, weight, evidence, source) whose
weights are normalized to sum to 1. An empty level is allowed and
recorded as such: no evidence means no hypothesis, never an
invented one. A level is *conflicting* when its top two weights
are within 0.15 of each other — two readings that close cannot be
told apart, so the turn must ask.

| Level | What it reads |
| --- | --- |
| `desire` | What the user wants right now |
| `goal` | The goal in progress this serves |
| `values` | The long-term value it expresses |

`distribution.confidence` is the desire level's top weight: the
act-vs-ask rule decides on what the user wants right now, while
the goal and values levels inform the question asked, not the
confidence number. `to_dict()` renders the whole reading as
plain JSON-safe data, for logging and for the layered "why?" door.

## Inference (deterministic by default)

`puspa/intent/inference.py`: the default inferrer reads a user
message against what Puspa already knows — the profile and the
world model — by keyword and topic overlap. No LLM call, no cost,
fully testable, and the same input always gives the same
distribution.

Candidates come from three sources:

1. **Profile** (`IntentContext.profile`, the plain `key -> value`
   dict `puspa.memory.profile.get_profile` returns). Only keys
   that name an intent become candidates: `goal*` at the goal
   level, `value*` / `principle*` at the values level, `want*` /
   `need*` / `desire*` / `preference*` / `like*` at the desire
   level. Any other key (a name, an address) is an identity fact,
   not an intent, and is ignored. Inferred facts never reach here
   in the first place: the profile keeps them as proposals until
   the user confirms them, so only stated or confirmed goals and
   values can become hypotheses.
2. **World** (`IntentContext.world`: a `WorldModel`, its `state`
   dict, or None). Unpaid bills and known prices read at the
   desire level ("pay bill pln", "check price of ... at ...");
   repeated habits and learned schedules read at the goal level
   ("keep up ...", "finish ..."). Capability beliefs are not
   intent (they say what the agent can do, not what the user
   wants) and contact beliefs belong to K5 (other-agent models);
   neither becomes a hypothesis here.
3. **The message itself**, through a small hand-written signal
   lexicon: "I'm so tired" reads as a desire to rest even when
   the profile says nothing. Signal candidates are a fallback
   only — they are added at the desire level only when no profile
   or world candidate scored there, so grounded evidence always
   wins over the lexicon.

Scoring is coverage: a candidate's score is the fraction of its
keywords present in the message. Message tokens are expanded
through a concept lexicon first, so "tired" also matches a "sleep
well" goal and a "health" value — that expansion is how one
message is read at all three levels at once. A candidate with no
matching keyword scores 0 and is dropped.

```python
from puspa.intent import IntentContext, infer_intent
from puspa.memory.profile import get_profile

distribution = infer_intent(
    "I'm so tired",
    IntentContext(profile=get_profile(user_id, dsn=dsn), world=model),
)
distribution.desire.top.label  # "rest"
distribution.goal.top.label    # "sleep well" (profile goal)
distribution.values.top.label  # "health" (profile value)
```

## The LLM seam (not built yet)

`IntentInferrer` is the pluggable interface: `infer(message,
context) -> IntentDistribution`. The deterministic inferrer is the
default; pass any `IntentInferrer` to `infer_intent` to plug a
different one in. A future LLM-backed inferrer plugs in through
the router's `llm_complete` seam — the same `complete(prompt)`
callable the retrieval modules take
(`puspa.router.complete.default_llm_complete`) — and
`build_llm_prompt(message, context)` renders the prompt it must
answer, as JSON with per-level weights summing to 1 and empty
levels as `[]` (never invented). The LLM inferrer itself is NOT
built here: it needs a live model, a key, and a JSON-parsing
contract that belongs to its own item, and building it now would
put a network call and a cost on every turn. Until it exists, the
deterministic inferrer is the whole inference path.

## How it feeds act-vs-ask

`puspa/intent/act_ask.py`: `decide_act_or_ask` is the intent
SIGNAL feeding the act-vs-ask rule — it is not a second approval
system. The approval rule itself (K2, folded into the G4
approvals taxonomy and the G5 gateway) already decides which
action kinds always ask; this module only adds what the formula
needs from intent: how confident the reading is, and whether it
conflicts. The ask itself always goes through the existing G4
`ApprovalPolicy` when one is wired in, and the policy decision is
attached to the outcome.

Decision order:

1. The existing approval policy, when wired in and the action is
   a recognized taxonomy kind: a policy `ask` / `deny` /
   `escalate_to_human` wins over any intent signal. A policy
   `allow` (a usable tiered answer) lets the intent check run. An
   unrecognized action kind is not a policy matter.
2. No intent read anywhere → `act`, **fail-open**: with no signal
   at all the distribution changes nothing, and the existing
   behaviour (policy, gates) stands.
3. Intent at goal/values but not at desire → `ask`: the immediate
   want is unread, so acting would be a guess.
4. Desire level conflicting → `ask`.
5. Desire top weight below 0.6 (`CLEAR_CONFIDENCE`) → `ask`.
6. Irreversible action, or high stakes → `ask` (the K2 shape:
   confidence × reversibility × stakes, all three must pass).
7. Otherwise → `act`: clear, reversible, low-stakes intent.

`clarifying_question(decision)` renders the one calm question for
an `ask` decision: it names what was read (or that nothing was
read), so the user answers one thing instead of re-stating the
whole request.

## Turn-loop wiring

`puspa/intent/turn_wiring.py`: `IntentTurnAdvisor` is the seam
between the intent signal and the turn loop. `TurnLoop` takes an
optional `intent_advisor`; when wired in, the loop consults it
before dispatching a tool call to the registry:

- `act` → the call runs exactly as before.
- `ask` → the call is **not** run. The model gets an ask payload
  (JSON: decision, reason, the clarifying question, the top
  intent reading when one was read, `"tool_was_run": false`) as
  the tool result, and asks the user. The verdict is recorded on
  `TurnResult.intent_decisions` and as an `intent_ask` sink event.

An advisor only gates tools it has an `ActionSpec` for
(reversible, stakes, plus the G4 classification override and the
argument keys copied into the policy content). A tool with no spec
fails open: the advisor defers to the existing gates and the call
runs as it would without an advisor. Intercepted tools (`todo`,
`memory`, `session_search`, `delegate_task`) are internal state
operations, not user-facing actions, so the advisor never sees
them. An advisor failure never breaks the turn: it fails open to
running the call.

```python
from puspa.intent import ActionSpec, IntentTurnAdvisor
from puspa.turns import TurnLoop

advisor = IntentTurnAdvisor(
    profile=get_profile(user_id, dsn=dsn),
    world=model,
    action_specs={"search_prices": ActionSpec(reversible=True, stakes="low")},
    approval_policy=policy,  # optional; the G4 mechanism
)
loop = TurnLoop(model_call=..., tool_call=..., intent_advisor=advisor)
result = loop.run_turn([InternalMessage.user("buy the laptop")])
result.intent_decisions[0].outcome  # "act" or "ask"
```

The proof that the distribution changes the decision is a real
run test (`tests/test_intent_turn_wiring.py`): the same message
"buy the laptop" with one recorded preference acts and runs the
tool, while with two equally-matching preferences it asks and the
tool never runs; and the same message "pay the bill" acts with one
unpaid bill in the world model and asks with two ("which bill?").

## Honest limits

- **Keyword overlap, not understanding.** The inferrer matches
  words, not meaning. A paraphrase with no shared words ("I'm
  wiped out" for a "sleep well" goal) reads nothing, and a word
  used in another sense ("save" in "save the file" for a "save
  money" goal) reads a false match. The concept lexicon is
  hand-written and small; every expansion is visible in
  `inference.py`, and a word with no entry expands to nothing.
  This is why the thresholds are thresholds, not calibrated
  probabilities — and why the LLM seam exists for later.
- **No morphology.** "prices" does not match "price", "frugality"
  does not match "frugal". English and Indonesian share one
  tokenizer; neither language is stemmed.
- **The signal lexicon is the only message-only source.** With an
  empty profile and an empty world, only messages containing a
  listed signal word ("tired", "hungry", "buy", ...) read
  anything at the desire level; every other message fails open.
- **Thresholds are uncalibrated.** `CLEAR_CONFIDENCE` (0.6) and
  `CONFLICT_MARGIN` (0.15) separate "one reading clearly wins"
  from "the readings are muddled" for coverage scores; they were
  chosen, not measured. Calibration against real turns is a
  follow-up, and must not silently retune the constants — change
  them with a test that pins the new behaviour.
- **No learning yet.** The inferrer does not learn from
  corrections (a user answering "no, I meant the other one"
  teaches nothing). Feeding silent corrections back into the
  weights belongs to the learning loop (Kosmos section F), not to
  K1.
- **The advisor sees the last user message only.** It does not
  read the turn's earlier history: a message that only makes
  sense with context ("do that one") reads whatever its own words
  match, or nothing.
- **Profile freshness is the caller's job.** The advisor takes a
  profile dict (or a `context_provider` callable for fresh reads
  per turn); a stale dict reads stale intent. Nothing here polls
  the profile.
