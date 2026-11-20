# Verified outcome records (B2)

Every task ends in a structured, evidenced outcome record:
a price checked, a seller checked, a task done, a payment
prepared, a decision recorded — with receipts, ledger entries,
and sources attached. Not raw text.

The shared writer is `puspa.evidence.outcomes`. Every feature
writes through it; no feature writes outcome events directly.

## The record

One outcome record is one `evidence_recorded` event on the
existing ledger (`puspa.ledger`, the closed set of 31 event
types is untouched — no new event type, no second ledger, no
second event system). The event content is:

```json
{
  "kind": "outcome_record",
  "outcome_type": "price_checked",
  "user_id": "<uuid of the user the outcome belongs to>",
  "room_id": "<uuid of the group, or null for a single-user outcome>",
  "subject_id": "<id of the thing the outcome is about, or null>",
  "summary": "price checked: Samsung Galaxy S24 Rp12.999.000",
  "evidence": [
    {"kind": "evidence_record", "evidence_id": "ev-9f2c41a0",
     "source_url": "https://…", "verdict": "Verified"}
  ],
  "receipts": [
    {"kind": "split_settlement", "settlement_id": "…",
     "amount_cents": 10000000, "status": "prepared"}
  ],
  "sources": ["https://www.tokopedia.com/…"],
  "details": {"price": 12999000.0, "seller": "Toko Galaxy", "…": "…"}
}
```

| Field | Meaning |
| --- | --- |
| `kind` | Always `"outcome_record"`. This is the marker the queries filter on. |
| `outcome_type` | What happened. See the types below. |
| `user_id` | The user the outcome belongs to. Always present. |
| `room_id` | The group (room) the outcome belongs to, or `null`. |
| `subject_id` | The thing the outcome is about: a task id, a split id, a link id, a meeting id, a draft id. |
| `summary` | One human-readable line. |
| `evidence` | The proof. **Required, non-empty** — see the evidence rule. |
| `receipts` | Artefacts the outcome produced (a settlement row, an approval). May be empty. |
| `sources` | The URLs the outcome rests on. May be empty. |
| `details` | The type-specific payload. For the paths B2 generalised, this holds the pre-B2 event content verbatim. |

The ledger event id is the record's id (`OutcomeRecord.outcome_id`).
`created_at` is the ledger's recorded time; `valid_at` (bitemporal,
see `docs/bitemporal-state.md`) may be passed when the caller knows
when the outcome was true in the world. The record's `ledger_task_id`
is the chain the record joined: the meeting, the split, the task,
or the comparison — the caller picks the chain the outcome belongs
to, so the chain for one meeting, split, or task still verifies on
its own.

## Record types

The named types (plan section 7, mechanism 1):

- `price_checked` — a price was read and verified for a link.
- `seller_checked` — a seller was checked for a link.
- `task_done` — a board task or a delegated task was completed.
- `payment_prepared` — a settlement payment was prepared (never executed).
- `decision_recorded` — a decision was recorded (meeting, group, draft approval).

The set is open: `write_outcome` accepts any other non-empty
`outcome_type` string, so a new feature names its own type
instead of waiting for a schema change.

## The evidence rule

**A record without evidence is rejected by the writer.**
`write_outcome` raises `OutcomeError` (a `ValueError`) before
anything is written when:

- `evidence` is empty, missing, or not a list;
- any evidence entry is not a dict, is empty, or does not name
  its `"kind"`.

An evidence entry names what proves the outcome and carries the
ids a reader needs to find the proof:

| Evidence `kind` | Proof it points at |
| --- | --- |
| `evidence_record` | A saved row in the `evidence` table (`evidence_id`, `source_url`, `verdict`). Build it with `evidence_ref(evidence)`. |
| `meeting_minutes` | The minutes of a meeting (`meeting_id`, `decision`). |
| `group_decision` | A group decision (`room_id`, `decision`, `agreed_by_user_ids`). |
| `money_approval` | A money-gate approval (`action_id`, `amount_cents`). |
| `board_task` | The board task row itself (`task_id`, `title`, `status`). |
| `delegated_step` | A decided step of a delegated task (`step_id`, `status`, `result`). |
| `draft_approval` | A draft approval (`approval_id`, `content_hash`). |

A failed write leaves nothing behind: the rejection happens
before the ledger append, so there is no half-written record.

## The write API

```python
from puspa.evidence.outcomes import write_outcome

record = write_outcome(
    ledger,
    task_id=meeting_id,          # the ledger chain to join
    agent_id=agent_id,           # required, as in every ledger write
    outcome_type="decision_recorded",
    user_id=author_user_id,
    room_id=room_id,             # None for a single-user outcome
    subject_id=meeting_id,
    summary="buy from seller A.",
    evidence=[{"kind": "meeting_minutes", "meeting_id": meeting_id,
               "decision": "buy from seller A."}],
    receipts=[...],              # optional
    sources=[...],               # optional
    details={...},               # optional, type-specific payload
)
```

The writer validates every field (`OutcomeError` on a missing or
malformed one) and returns an `OutcomeRecord` built from the
ledger event row it just wrote. `OutcomeRecord.to_dict()` is the
record as plain JSON-safe data.

## The query API

```python
from puspa.evidence.outcomes import (
    get_outcome,
    list_outcomes_for_group,
    list_outcomes_for_user,
)

list_outcomes_for_user(dsn, user_id, outcome_type="task_done", limit=100)
list_outcomes_for_group(dsn, room_id, outcome_type="decision_recorded")
get_outcome(dsn, outcome_id)  # None when missing or not an outcome record
```

Records are oldest first. The per-user query returns only that
user's records (`user_id` in the content); the per-group query
returns only that room's records (`room_id` in the content).
Single-user records (`room_id` null) never appear in a group
query. The queries are served by expression indexes on the
`events` table (migration `0001_core.sql`).

`is_outcome_event(event)` reports whether a ledger event dict
is an outcome record.

## Which paths write through it

| Path | Outcome type | How it got there |
| --- | --- | --- |
| B10 `MeetingMinutesStore.write_minutes`, per decision | `decision_recorded` | **Migrated.** The old `evidence_recorded` event (`{"kind": "meeting_decision", …}`) is now written by the shared writer; the old payload is preserved verbatim under `details`. |
| B10 meeting action items | — | Not an outcome. Creating a task stays a plain `task_created` event. |
| B11 `SplitService.prepare_settlement` | `payment_prepared` | **Migrated.** The old payload (`{"kind": "split_settlement_prepared", …}`) is preserved verbatim under `details`; the money approval is the evidence, the settlement row is the receipt. |
| B6 `RoomTaskStore.complete_task` | `task_done` | **Wired.** Optional `ledger` + `agent_id`; the board task is the evidence. A caller with richer proof passes it as `evidence`. Completing an already-done task is a no-op and writes no second record. |
| B9 `DelegationService.complete_task` | `task_done` | **Wired.** Optional `ledger` + `agent_id`; forwards to the B6 board call with the board task plus the delegation's decided steps as evidence, so there is exactly one record per completion. |
| B12 `ResearchService.compare`, per checked link | `price_checked`, `seller_checked` | **Wired.** Optional `ledger` + `agent_id`; both records point at the same saved evidence row. Unchecked links write nothing — there is no evidence for them. |
| B12 `ResearchService.record_group_decision` | `decision_recorded` | **Wired.** Optional `ledger` + `agent_id`; the decision and its agreers are the evidence. |
| B13 `DraftingService.approve_draft` | `decision_recorded` | **Wired.** Optional `ledger` + `agent_id`; the approval itself is the evidence. `request_send_approval` passes the ledger through when the policy approves at once. |

In every wired path the ledger is optional and `agent_id` is
required with it (`ValueError` otherwise), matching the
existing pattern: the feature's own work never depends on the
ledger. The outcome record is written after the state change,
so a ledger failure never rolls back the user's work.

## Honest limits

- **A completion whose record failed to write is not
  re-recorded.** The record is written after the status
  change; if the ledger append fails, the task is already done
  and a later call is a no-op, so the record stays missing.
  Re-writing it needs an explicit repair call, which does not
  exist yet.
- **B13 records the approval, not the send.** `send_draft`
  writes no outcome record yet: there is no live delivery
  channel (B13's own limit), so a "draft sent" record would
  have no delivery proof behind it. When a real channel
  exists, `send_draft` should write a `task_done` record with
  the delivery result as its receipt.
- **No retroactive records.** Outcomes from before B2 exist
  only as the old event shapes (or not at all, where a path
  never wrote ledger events). The queries only see records
  written through this writer.
- **No cross-user reads.** The queries filter by `user_id` /
  `room_id` in the event content, but they do not check that
  the caller is that user or a member of that room — the
  caller (API layer, chat surface) owns authorisation, as it
  does for the rest of the room reads.
