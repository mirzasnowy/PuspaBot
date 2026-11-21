# Bitemporal State (Kosmos bitemporal state)

Two times for everything the brain remembers
(`docs/kosmos-design.md` section C point 3: "Bitemporal state:
what is true, and when the system learned it"). A belief is
never just "the price is Rp10.000": it is "the price was
Rp10.000 from Monday to Thursday, and we have believed that
since Tuesday". The two times answer different questions, and
collapsing them into one timestamp is how a system ends up
unable to say what it believed yesterday — or to correct the
past without rewriting it.

## The two time pairs

| Pair | Question it answers | Beliefs | Ledger entries |
| --- | --- | --- | --- |
| **Valid time** | When was this true in the world? | `valid_from` / `valid_to` (an interval: a fact holds over a span) | `valid_at` (one instant: an event is a point, not a span) |
| **Known time** | When did the system believe / record this? | `known_from` / `known_to` | `created_at` (the recorded time, which is also the chain order) |

Both intervals are half-open: `[from, to)`. A `NULL` end
means "still open" — the fact is still true, or the version is
still believed. Every belief key has exactly one open version
at any moment (the current belief) and any number of closed
ones.

A new belief's valid time defaults to its known time when the
caller does not know better: "true since we learned it" is the
honest default, and it keeps existing callers working unchanged
(they only know "now").

## Beliefs

`puspa/world/bitemporal.py`: `BitemporalBeliefStore` holds one
key's full history in memory; `BeliefVersion` is one frozen
version (key, value, version number, the two intervals,
`supersedes` pointing at the version it replaced).

```python
from datetime import datetime, UTC
from puspa.world import BitemporalBeliefStore, belief_key

store = BitemporalBeliefStore()
key = belief_key("price", "P1@S1")
t0 = datetime(2026, 11, 10, tzinfo=UTC)
t1 = datetime(2026, 11, 12, tzinfo=UTC)

store.record(key, {"price": 10000}, known_at=t0)
# The price really rose on the 12th: the old fact stops being
# true then, and the new one holds from then.
store.correct(
    key, {"price": 12000},
    known_at=t1, valid_from=t1, end_previous_valid_at=t1,
)
store.current(key).value  # {"price": 12000}
```

### Correction semantics: never update in place

A correction appends a new version and closes the old
version's `known_to` at the moment of correction. The old
value stays readable forever. There are two different kinds
of correction, and they must not be conflated:

1. **The world changed.** The old fact was true, then stopped
   being true (the price really did rise). Pass
   `end_previous_valid_at`: the old version's `valid_to`
   closes there, and the new version's `valid_from` starts
   there. The old row now reads "true from Monday until
   Thursday, believed until Thursday".
2. **We were wrong about the past.** A late-arriving truth
   (the price on Monday was actually Rp9.000, not Rp10.000).
   Do *not* pass `end_previous_valid_at`: the old version's
   valid interval stays as recorded — it still says what the
   system believed was true then — and the new version
   carries the corrected valid interval (pass an earlier
   `valid_from`). Closing the old `valid_to` here would
   rewrite what was believed, which is exactly what
   bitemporal state exists to prevent.

Corrections are recorded in known-time order: a `known_at`
earlier than the current version's `known_from` is rejected.
All times must be timezone-aware datetimes; a naive datetime
is rejected (fail-closed: a naive time silently read as the
wrong timezone would corrupt history). Values must be
JSON-serializable dicts.

### As-of queries

```python
# What did the system believe on the 11th? The old price —
# the correction on the 12th does not rewrite yesterday.
store.known_as_of(key, datetime(2026, 11, 11, tzinfo=UTC)).value
# {"price": 10000}

# What was true on the 11th, as the system understands it now?
store.valid_as_of(key, datetime(2026, 11, 11, tzinfo=UTC)).value
# {"price": 10000} — or the corrected value, after a late correction

# Both at once: valid then AND known then.
store.as_of(key,
            valid_at=datetime(2026, 11, 11, tzinfo=UTC),
            known_at=datetime(2026, 11, 11, tzinfo=UTC))
```

`valid_as_of` returns the latest version whose valid interval
covers the asked time: a later correction overrides an earlier
claim for the valid times it covers, and a world-change
correction does not cover the old fact's interval, so the old
fact still reads true then. `known_as_of` considers all
versions. At the exact instant a correction was learned,
the new version already counts as believed (half-open
intervals).

### Durable storage

Table `belief_versions` (migration `0006_world.sql`): one row per belief version,
with the same two time pairs plus the same invariants enforced
by the database (versions count 1..n per key, `valid_to >
valid_from`, `known_to > known_from`, RLS like
`world_beliefs`).

`puspa/world/store.py` persists and queries it:

```python
from puspa.world import (
    apply_bitemporal_migration,
    beliefs_known_at,
    beliefs_valid_at,
    load_belief_history,
    save_belief_versions,
)

save_belief_versions(user_id, agent_id, store, dsn=dsn)
history = load_belief_history(user_id, agent_id, dsn=dsn)
beliefs_known_at(user_id, agent_id, at, dsn=dsn)  # one row per key
beliefs_valid_at(user_id, agent_id, at, dsn=dsn)  # current knowledge only
```

Versions are immutable in the database too: re-saving the
same history inserts nothing (`ON CONFLICT DO NOTHING` on the
(user, agent, key, version) primary key). The JSON snapshot in
`world_beliefs.state` is untouched — the version table is
additive, and existing callers (`save_world_state` /
`load_world_model`) never see it unless they ask for it.

## Ledger entries

`puspa/ledger/ledger.py`: `Ledger.append_event(..., valid_at=...)`.
The recorded time is `created_at` (taken from the database in
the same transaction as the INSERT, and also the chain order);
the valid time is `valid_at`, when the described event was true
in the world. A payment that happened on Monday but was only
recorded on Friday gets `valid_at` Monday and `created_at`
Friday. When the caller does not pass `valid_at` it defaults
to `created_at`.

```python
from datetime import datetime, UTC
from puspa.ledger import Ledger

ledger = Ledger(dsn)
ledger.append_event(
    task_id=task_id, agent_id="puspa", event_type="tool_completed",
    content={"bill": "PLN-123", "paid": True},
    valid_at=datetime(2026, 11, 17, tzinfo=UTC),  # paid Monday, recorded Friday
)
ledger.events_by_valid_time(task_id)  # ordered by when things were true
```

`events_by_valid_time(task_id, valid_from=..., valid_to=...)`
answers "what happened in the world between these two
moments", in the order the events were true — not the order
they were recorded. Bounds are half-open. Rows without a
recorded valid time fall back to their recorded time, so the
query also works on a database that predates the migration.

### The hash decision: valid time stays out of the hash

`compute_event_hash` takes exactly the same inputs as before
(`task_id`, `agent_id`, `subagent_id`, `type`, `content`,
`parent_id`, `previous_hash`, `time_iso` from `created_at`).
`valid_at` is **not** a hash input. It is metadata recorded
alongside, in its own column, outside the hashed payload.

This is deliberate, and it is the only decision that keeps the
existing chain verifiable: if `valid_at` entered the hash,
every entry written before bitemporal state would recompute to a different
hash (the old entries have no separately recorded valid time,
so any value assigned to them in the hash would be invented),
and `verify_chain` would report the whole history as broken.
Instead the chain proves what it always proved — that the
recorded sequence was not tampered with — while `valid_at`
carries the world-time claim, which is a statement about the
world, not about the record. The regression test pins the hash
of a fixed event vector, so any future change to the hash
inputs fails loudly instead of silently redefining history.

Consequences, stated plainly:

- Changing `valid_at` on an existing row does **not** break
  the chain (and `verify_chain` will not catch it). That is
  by design, but it means `valid_at` is a claim the ledger
  does not authenticate. The application role (`puspa_app`)
  has no UPDATE privilege on `events` at all, so in practice
  only the database owner can change it — the same trust
  boundary as before.
- Backfill is honest: existing entries get `valid_at =
  created_at`, because the past has no separately recorded
  valid time. "The event was true when it was recorded" is
  the only claim the record supports for old entries.
- No 32nd event type was added. The closed set of 31
  revision-7 types stays closed (audit trail with initiator precedent: new
  information rides as data, not as a new type).

## Migration

Migration `0006_world.sql` — idempotent and additive, safe
to run repeatedly against an existing database, drops nothing
(apply it with `python -m puspa.migrations up --target 0006`,
or from Python with
`puspa.world.store.apply_bitemporal_migration()`).

It creates `belief_versions` when missing and adds
`events.valid_at` (backfilled from `created_at`) when the
ledger table exists. Fresh databases do not need it: the same
objects come from migration `0006_world.sql` (world) and
`0001_core.sql` (ledger) via `create_schema()` /
`Ledger.create_schema()`. `Ledger.append_event` also tolerates
a database that predates the migration: the append still
succeeds (without the column) and the event reads back with
`valid_at` None, so a missed migration degrades to "no valid
time recorded" instead of failing the write.

## Honest limits

- **Valid time is a claim, not a measurement.** For beliefs,
  `valid_from` defaults to "when we learned it" whenever the
  caller does not know better — that is an assumption, and it
  is recorded as one. For ledger entries, `valid_at` is
  whatever the caller states; nothing checks it against the
  world. A wrong `valid_at` is a wrong claim stored
  faithfully, not a detected error.
- **The ledger hash does not cover `valid_at`.** See "The hash
  decision" above: tampering with `valid_at` alone is not
  detected by `verify_chain`. The UPDATE ban on the
  application role is the protection, not the hash.
- **No automatic correction of the world model.** The
  bitemporal store is a history layer, not a replacement for
  `WorldModel`: the model's beliefs (`_PriceBelief` and
  friends) still update in place as before, and nothing yet
  records their changes into `BitemporalBeliefStore`
  automatically. Wiring model updates to record versions is
  a follow-up; until then, callers that need history record
  versions explicitly.
- **No deletion.** There is deliberately no delete API for
  versions: history is append-only. Erasing a belief and its
  derivatives (the user asked to delete something) belongs
  to the data-deletion work (external audit log, kill switch and export), not to this module.
- **One valid instant per ledger event.** An event that spans
  a period (a task that ran for three days) is recorded with
  a single `valid_at` — by convention its start, unless the
  caller states otherwise. A valid interval for events would
  be a different model, and nothing in the ledger needs one
  yet.
- **Known-time order is enforced on write.** A correction
  whose `known_at` predates the current version is rejected
  rather than inserted out of order. Genuinely out-of-order
  ingestion (a delayed correction that was learned earlier
  than a correction already recorded) has no path yet: it
  must be reconciled by the caller, not silently spliced in.
- **No cross-key queries in memory.** The in-memory store
  answers per key; "all beliefs known at time T" is a
  Postgres query (`beliefs_known_at`), not an in-memory one.
- **Times are instants, not model days.** The world model
  counts days (`WorldModel.day`); bitemporal times are wall
  clock (`datetime`, UTC). The two clocks are not reconciled
  here: a caller that wants "day 17" as a valid time must
  convert it to a datetime itself.
