# Memory screen: what already existed vs what was added

Plan section 12.6, GitHub issue #15. The deliverable is the "What
PuspaBot knows about me" screen plus its read endpoints: facts with
source and confidence, edit / delete / export / "forget this",
guest mode, and room memory kept separate from personal memory.
The point is data-subject rights made visible: access, correction,
deletion, consent withdrawal.

## What already existed (wired to, not rebuilt)

- **Versioned profile facts** (`puspa/memory/facts.py`,
  `puspa/memory/profile.py`): record / supersede / expire /
  restore, history per key, proposals for inferred facts
  (confirm / reject), soft delete. The memory read endpoints and memory screen service reads and
  writes through these stores; it adds no second fact store.
- **Episodic memory** (`puspa/memory/episodic.py`): chat history
  with embeddings; the graph view (second-brain graph view) seeds from it.
- **Second-brain graph view** (second-brain graph view, `puspa/pwa/graph_api.py`,
  `docs/openapi-graph.yaml`): node/edge endpoints and graph
  edits on the Memory screen. memory read endpoints and memory screen does not touch the graph; it
  covers the facts list the graph view never showed.
- **Full user export** (`puspa/ledger/export.py`:
  `build_user_export`): facts with history, episodes,
  procedures, summaries, grants, consents, ledger events, vault
  metadata. memory read endpoints and memory screen wires it to `GET /api/memory/export` as a
  download instead of rebuilding it.
- **Room memory** (`puspa/rooms/memory.py`, `room_memories`
  table): three layers (personal / room / private-answer),
  membership-gated reads and deletes. There were no HTTP routes
  for it; memory read endpoints and memory screen adds them. The service never reads personal
  memory for a room request.
- **Account deletion** (`puspa/consent/account.py`): hard delete
  of all user data. memory read endpoints and memory screen's per-fact forget is the smaller,
  per-fact version of the same right; account deletion is
  unchanged.

## What memory read endpoints and memory screen added

- **Migration 0023** (`puspa/migrations/versions/0023_memory_rights.sql`):
  `profile_facts.confidence` (REAL, 0-1) and
  `profile_facts.source_detail` (TEXT, a short provenance note
  such as "from the chat on 3 November 2026"), plus
  `data_consents.withdrawn_at`. `puspa/memory/schema.py`
  `create_schema` now targets 0023; `tests/test_migrations.py`
  counts moved 22 to 23; `infra/schema_snapshot.sql` was
  regenerated with the documented procedure.
- **Confidence persistence** (`puspa/memory/facts.py`:
  `record_fact(..., confidence, source_detail)`, validated 0-1;
  `puspa/memory/profile.py`: `add_fact` passes both through).
  Consolidation and dream already computed confidence values
  (0.9 / 0.7) and dropped them; they can now be stored when the
  caller passes them.
- **`facts.forget_fact`** (hard delete): removes the fact and
  every past version of the same key, and logs a journal entry
  carrying the fact id and key only — never the content.
- **Read service + routes** (`puspa/pwa/memory_api.py`,
  `docs/openapi-memory.yaml`): `MemoryReadService` and
  `register_routes`, mounted in `puspa/pwa/app.py` as
  `app.state.memory_service`:
  - `GET /api/memory` — facts page (limit clamped to 100),
    proposals, consent state, guest-mode flag.
  - `GET /api/memory/facts/{id}` — fact plus its history.
  - `PATCH /api/memory/facts/{id}` — correct (supersede with a
    user-confirmed replacement).
  - `POST /api/memory/facts/{id}/forget` and
    `DELETE /api/memory/facts/{id}` — forget this fact.
  - `POST /api/memory/facts/{id}/confirm|reject` — proposals.
  - `GET /api/memory/export` — JSON download.
  - `GET /api/memory/consent`,
    `POST /api/memory/consent/withdraw` — consent state and
    withdrawal (idempotent).
  - `POST /api/memory/guest` — start/end a guest turn.
  - `GET|POST /api/memory/rooms/{room_id}[/memories]`,
    `DELETE /api/memory/rooms/{room_id}/memories/{id}` — room
    memory, membership-gated.
- **Source label + confidence rule** (in the service, stated
  openly): a stored `source_detail` wins, else the label is
  built from source and creation date ("you told PuspaBot on 3
  November 2026"; an unconfirmed inference says so). A stored
  confidence wins; otherwise user-stated/confirmed is 1.0 by
  definition and an unverified inference is 0.5. The response
  marks derived values with `confidence_stored: false`.
- **Rights audit**: corrections, forgets, and consent
  withdrawals are `evidence_recorded` ledger events (the closed
  set of 31 types stays closed) on `memory-rights-<user_id>`,
  with content `{"kind": "memory_right", "operation": ...,
  "fact_id": ...}` — ids only, never fact content. This differs
  deliberately from second-brain graph view graph corrections, which record
  before/after detail: a "forget this" that kept a copy of the
  fact in the audit trail would not be a forget.
- **Guest mode** (a turn that saves no memory): the flag is
  toggled through the API (process-local set on the service; a
  restart ends every guest turn) and enforced at the two real
  write funnels — `DefaultPgVectorProvider.store` (refuses the
  write, returns `{"stored": false, "guest_mode": true}`) and
  `InterceptedTools` (the turn loop's `memory` record action is
  refused; list/recall keep working). Reads are unaffected.
- **Screen** (`apps/pwa/templates/memory.html`): server-rendered
  facts with source label and confidence, inline correction,
  "Forget this" per fact, proposals with confirm/reject, guest
  toggle, export link, consent withdrawal. The PWA `/memory`
  route serves the SPA shell when `dist` is built (facts arrive
  in the bootstrap payload) and this template otherwise.
- **Pre-existing fix found while wiring the screen**: the SPA
  bootstrap `json.dumps` in `puspa/pwa/app.py` crashed on
  datetimes in profile facts, so `/memory` failed whenever a
  fact existed. It now dumps with `default=str` (datetimes
  become ISO strings).

## Honest limits

- Forgetting a fact removes the fact and its versions, not the
  chat episodes it was learned from: episodes are the raw history
  and stay until account deletion. A forgotten fact is never
  recalled as a profile fact again.
- Consolidation's computed confidence (0.9 / 0.7 in
  `consolidate.reflect_episodes`) is still dropped at propose
  time: the injectable `propose` seam's signature
  (`user_id, key, value, source, agent_id, event_id, dsn`) does
  not carry it, and changing that signature would break existing
  callers and test doubles. The screen shows 0.5 (derived,
  marked unmeasured) for those proposals until that seam is
  extended.
- A fact correction commits to Postgres before its ledger record
  is written, so a ledger failure leaves the correction without
  its audit entry (the fact itself is never lost). A forget
  deletes first for the same reason: the deletion is the point,
  the audit entry is secondary.
- Guest mode is process-local and is enforced at the provider
  and turn-loop intercepts. The PWA `/api/chat` handler itself
  is an in-memory stub that writes nothing to memory either way,
  so there is no live chat turn to observe guest mode in yet.
- Withdrawing consent stops future storage; it does not delete
  stored data and does not itself flip the provider into guest
  mode — the two are separate controls on the same screen.

## Tests

`tests/test_memory_screen_p01.py` (17 tests): screen payload
with source labels and confidence in all three cases (stored,
user-stated, inferred), pagination cap and history filter,
single-fact read with history, correct and forget flows with
ledger events checked to carry no fact content, DELETE as forget,
proposal confirm/reject, export download contents, consent
status/withdrawal/idempotency, guest toggle, guest refusal at
both write funnels, room memory separation and membership
gating, page render, and unit tests for `record_fact`
confidence validation and `forget_fact` on an unknown id.
