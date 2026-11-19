# Ignorance map (Kosmos K6)

The world model knows what it knows. The ignorance map is the
other half: a record of where the model is blind, so the system
can go look instead of guessing.

## What an area is

An area is one belief domain plus one topic, keyed
`"domain:topic"`:

| Domain | Topic | Example key |
| --- | --- | --- |
| `price` | product at a store | `price:phone-x1@Shopee` |
| `bill` | bill id | `bill:bill-pln-001` |
| `schedule` | task kind | `schedule:email` |
| `capability` | task kind | `capability:email` |
| `contact` | contact id | `contact:ana` |
| `habit` | task kind | `habit:email` |
| `topic` | free text | `topic:whether ana likes oat milk` |

The first six domains are the belief domains of the world
model. The seventh, `topic`, is a free topic with no belief
behind it: a question the system hit and could not answer
from the model at all.

Each area carries a reason:

- `no_evidence` — no belief exists for the topic, or the
  belief holds no usable value yet (a price belief with no
  price, a habit belief with no repeated pattern).
- `low_confidence` — the belief holds a usable value, but
  the model no longer trusts it (confidence below the floor).

Areas also carry the belief's current confidence and
observation count (when a belief exists), and the model days
a caller first and last hit the gap.

## The threshold

An area is `low_confidence` when its belief's confidence is
below `UNKNOWN_CONFIDENCE` (0.5). The floor is the
fresh-belief default (`DEFAULT_CONFIDENCE`) on purpose:

- One observation creates a belief at exactly 0.5, so one
  observation clears a `no_evidence` area.
- Only scored misses can push a belief under the floor
  (`WorldModel._apply_observation` subtracts 0.2 per miss).
  Correct predictions add 0.05 each, so evidence heals a
  `low_confidence` area too.

The "usable value" test is the same test each forecast
applies (a price belief counts only when it holds a price, a
habit belief only when it holds a repeated pattern, and so
on), so the map and the forecast never disagree about
whether the model can answer a topic. A habit needs a
repeated pattern: one task never clears a habit area, three
repeated tasks do.

## When an area appears and disappears

Two halves, deliberately different:

- **Registered (`no_evidence`).** A caller that hits a topic
  with no answer records it with
  `WorldModel.register_unknown(domain, topic)`.
  Re-registering a gap refreshes its last-seen day instead of
  duplicating it. Registering a topic the model can already
  answer records nothing. The area disappears the moment
  evidence arrives: every read of the map re-checks each
  registered area against the live beliefs and drops the ones
  whose belief now holds a usable value at or above the floor.
- **Derived (`low_confidence`).** Computed from the live
  belief dicts on every read, so a belief the model trusted
  and was then proven wrong about shows up without anyone
  registering it. The derived half is low-confidence only:
  a belief with no usable value yet is "not yet formed", not
  a gap the system hit — every task result grows a young
  habit belief with no pattern yet, and listing all of those
  would flood the map with every task kind the user ever did.

Free topics leave the map only when the caller that asked
the question says it is answered
(`WorldModel.resolve_unknown("topic", ...)`). A belief
domain never resolves by declaration: its area leaves only
when evidence arrives.

## The real caller: the planner probes instead of guessing

Goal `find_cheapest_price` in `puspa.world.planner` is the
caller. When no store has a known price, the plan does not
pick a store to guess. It is infeasible with reason
`"needs_probe"`, the missing price areas are recorded in the
model's ignorance map, and the plan carries one probe step
(check the price at the first store) plus the `"probe"` and
`"unknown_areas"` fields. The caller runs that one probe,
the observation that comes back clears the area from the
map, and the next plan is feasible with evidence. Planning
still never touches beliefs or `model.state`: registering a
gap is metadata about the model's own blindness, not world
state.

The probe for any area is also available directly:
`WorldModel.probe_for(domain, topic)` returns
`{"action": ..., "area": key}`, or `None` when the area is
already known or when no world action can supply the
evidence (a free topic needs a question to a person, not an
action). `WorldModel.is_unknown(domain, topic)` answers the
same question for one area; `WorldModel.ignorance_map()`
lists every gap, sorted by key, optionally filtered to one
domain.

## Persistence: none, on purpose

The map is in-memory and derived. The derived half rebuilds
from the belief state, which `puspa.world.store` already
persists; the registered half rebuilds the next time a caller
hits the same gap (the planner re-registers on every plan
that needs the missing price). No new table, no migration,
no new ledger event type. A free topic, which has no belief
to re-derive from, is dropped on restart and must be
re-registered by whoever hits it.

## Honest limits

- The map is only as good as the threshold. 0.5 is a
  bookkeeping choice tied to the fresh-belief default, not a
  measurement of real uncertainty: one sighting clears an
  area, and the map cannot tell a lucky guess from a solid
  fact.
- The map only covers gaps the system hit (registered) plus
  beliefs it stopped trusting (derived low-confidence). It
  does not enumerate everything the model does not know —
  the unknown unknowns are, by definition, not on it.
- A `low_confidence` area can flicker at the floor: float
  accumulation means 0.5 - 0.2 + 4 x 0.05 lands a hair under
  0.5, so exactly four correct predictions do not clear it;
  a fifth does. The rule is exact, the arithmetic is float.
- The planner probes one store only. If that probe fails,
  the gap stays on the map and the next plan probes the same
  first store again; there is no probe rotation or budget yet.
- The map is per-model-instance, in memory. Two sessions
  with two models have two maps.
