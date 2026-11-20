# Agent payments, phase 1: prepare only (B8)

Source: plan section 8.3 (phase 1 only) and section 6.4
("prepare, do not execute"); ticket B8 in
`docs/tickets-beta-plan.md`.

## The model

The agent prepares a payment. The user pays in their own
bank or e-wallet app. That is the whole of phase 1.

A prepared payment is four things, always together:

- a **verified recipient** — a name, an account reference
  (account number, virtual account number, wallet id, or
  QRIS merchant id), and what verified them;
- an **amount** in integer Rupiah;
- a **rail** named through B5 (`puspa.local_data.rails`):
  QRIS, virtual account, bank transfer, GoPay, OVO, DANA;
- the **evidence** that proves the recipient and the amount.

A prepared payment that lacks any of the four is refused:
no evidence, no preparation; an unverified recipient, no
preparation; evidence that does not name the recipient, no
preparation. Cash on delivery cannot be prepared: a prepared
payment needs an account to pay into, and COD has none.

Every user-facing surface shows recipient + amount + rail +
evidence + expiry, and states plainly: **Puspa never pays.
Pay in your own bank or e-wallet app.**

## Approval before payable

A preparation starts as `awaiting_approval`. It becomes
visible as payable only after the owner approves it
(`approve_preparation`, or `POST /api/payments/{id}/approve`).
Approval is recorded on the ledger as an `approval_granted`
event; the preparation itself creates the approval record
(an `approval_requested` event, G4 gate kind
`prepare_payment`, classified by the G4 taxonomy as a pay
action with an amount). Approval never authorises execution,
because there is no executor: even a fully approved
preparation cannot be executed.

## The ledger record

Every preparation is written to the ledger twice, through
existing writers — no new event type, no second ledger:

- a `payment_prepared` outcome record through the shared B2
  writer (`puspa.evidence.outcomes.write_outcome`), with the
  evidence attached and the preparation as its receipt;
- the G4 `approval_requested` event described above.

Marking a preparation paid-by-user writes a second outcome
record (`payment_paid_by_user`) whose evidence is the user's
own assertion. It is recorded as an assertion, never as a
verified payment: phase 1 has no gateway to check against.

## Expiry and idempotency

A prepared payment expires 24 hours after preparation by
default (the G4 24-hour approval window), or at an explicit
`expires_at`. An expired preparation cannot be approved or
marked paid; reading it back shows `expired`. Prepare it
again instead.

`idempotency_key` is optional and scoped per user. The same
key with the same recipient, amount, and rail returns the
same preparation and writes nothing new — no duplicate row,
no second outcome record. The same key with different
content is refused.

## The Rp0 execution limit is architectural

`EXECUTION_LIMIT_RUPIAH = 0` in `puspa/payments/prepare.py`
is a module constant, not a setting. The limit holds because
no code path can execute a payment at all:

- there is no executor, no gateway client, and no payment
  credential handling anywhere in `puspa/payments/`;
- the only execution-named entry point,
  `execute_payment`, always raises `ExecutionRefused`, after
  recording the refusal to the ledger as an
  `approval_rejected` event (content kind
  `execution_refused`);
- the same guard is exposed over HTTP
  (`POST /api/payments/{id}/execute` → 403);
- raising a spending limit (`puspa.policy.spending`) changes
  nothing here: the gate's spending check guards a path that
  does not exist.

A preparation's `executed` flag is always `False` in every
ledger record, because execution is not a state a
preparation can reach.

## API

All routes require login; all responses are JSON. See
`puspa/pwa/payments_api.py` for the contract.

- `GET /api/payments` — the user's prepared payments.
- `GET /api/payments/payable` — approved and unexpired only.
- `GET /api/payments/{id}` — one preparation, with the
  plain-text summary and the pay notice.
- `POST /api/payments` — prepare. Body: `recipient`
  (`name`, `account_ref`, `verified`, `verified_by`),
  `amount_rupiah` (positive integer Rupiah), `rail` (a rail
  id such as `"qris"` or a mention such as `"bayar pake
  qris"`), `evidence` (a non-empty list of `{"kind": ...}`
  entries naming the recipient), optional `idempotency_key`,
  `expires_at`, `room_id`, `task_id`. 400 on bad input.
- `POST /api/payments/{id}/approve` and `.../reject` —
  owner only.
- `POST /api/payments/{id}/mark-paid` — the user asserts
  they paid in their own app (optional `assertion` text).
  Only an approved, unexpired preparation can be marked.
- `POST /api/payments/{id}/execute` — always 403.

The schema lives in migration `0013_payments.sql` (apply after
`0001_core.sql`: the `users` table must exist first).
The API also creates it lazily on the first request (the B7
mockup pattern, once per app), so a server that only ran
migration `0001_core.sql` still serves payments. The full contract
is `docs/openapi-payments.yaml`.

## Relation to other tickets

- B5 (`puspa/local_data/rails.py`) names the rail; B8 reads
  it, never re-implements it.
- B11 (`puspa/rooms/splitting.py`) already had a
  `PreparedSettlement` and a `payment_prepared` outcome
  record; B8 builds on that shape — the same outcome type,
  the same "prepared only, the user pays in their own app"
  note, the same `executed: False` in the details — for
  standalone payments instead of split settlements.
- G2 (billing/credits) is separate: Puspa credits pay for
  Puspa services; B8 prepares payments to outside recipients
  in the user's own app. No credit is spent or moved by a
  preparation.
- B17 (creator support purchases) follows the B8 phase-1
  rule in beta: the user pays in their own app, and the
  creator's share is paid in Puspa credits only, never cash.

## Honest limits

- No live payment gateway, no real rails integration, no
  execution — phase 1 only.
- "Paid by user" is an assertion, not a verified payment.
  Nothing in phase 1 confirms the money arrived.
- The recipient is verified against the evidence the agent
  was given, not against a bank or wallet registry: a forged
  screenshot is still a forged screenshot. The evidence is
  attached so a human can check it.
- Recipient verification does not check that the account
  reference is well-formed for its rail (no checksum, no
  bank-code lookup).

## What phase 2 would need

Phase 2 (agent executes) is not ticketed. It would need, at
minimum: a licensed payment gateway integration, credential
handling for payments, a per-user execution limit the user
sets explicitly, an approval flow that authorises a specific
execution (not just a preparation), execution receipts
verified against the gateway, and a new decision from Nero.
None of that exists in this ticket, on purpose.
