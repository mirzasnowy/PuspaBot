# B11 — Patungan / shared splitting

Date: 19 Nov 2026. Source: `docs/tickets-beta-plan.md` section B11,
plan section 8.6. Split a bill or a purchase among friends or
family; the agent tracks who paid and who still owes, sends
reminders, and prepares settlement.

## What it is

B11 extends the B6 rooms — it never builds a second group, a
second task board, or a second money rule. A split belongs to a
room: one member fronted a bill (makan bareng, kado bareng, one
arisan round), the rest owe their shares to them, and the agent
keeps the balance honest across partial payments, reminds only
the people who still owe, and prepares (never executes) the
settlement payment.

## Money: integer sen, never floats

All amounts are integer sen (minor units): 1 rupiah = 100 sen.
Totals, shares, payments, and settlement amounts are all
`int`; a float, a string, or a bool in any of those places
raises `SplitError`. `format_rupiah` renders sen as
`Rp150.000` (`Rp150.000,50` when sen remain); `rupiah_to_cents`
and `cents_to_rupiah` convert, and `cents_to_rupiah` refuses
to round — sen that are not a whole number of rupiah raise
instead of being silently dropped.

## Modules

All new code lives in `puspa/rooms/splitting.py` and
`puspa/pwa/rooms_api.py`; the schema additions are in
`infra/schema_rooms.sql` (applied by `puspa.rooms.create_schema`,
like the rest of the room schema).

| File | What it owns |
| --- | --- |
| `puspa/rooms/splitting.py` | `SplitService`: create splits (equal / custom / weighted shares), record partial payments, debtor lists, reminder queries + G8 scheduler hook, prepare-only settlement through the B6 money gate, split rendering. |
| `puspa/rooms/errors.py` | `SplitError`: bad shares, bad amounts, payments that do not fit what a debtor still owes. |
| `puspa/pwa/rooms_api.py` | PWA HTTP layer for splits (`GET /api/rooms/{id}/splits`, `POST .../splits`, `POST .../splits/{id}/payments`, `POST .../splits/{id}/settlements/{debtor}/prepare`), mounted in `puspa/pwa/app.py` by the existing `register_routes`. |
| `infra/schema_rooms.sql` | `room_splits`, `split_shares`, `split_payments`, `split_settlements`, with RLS policies in the same shape as the other room tables. |
| `docs/openapi-rooms.yaml` | `Split`, `SplitShare`, `PreparedSettlement` schemas and the four split routes. |

## Splits and shares

- **Create** (`create_split`): title, total (sen), payer,
  participants, kind (`meal` / `gift` / `arisan` / `other`),
  optional due date. A split needs at least two participants
  and the payer must be one of them; every participant must be
  an owner or member of the room — a guest may read the room
  but never owes or pays through a split, and a non-member may
  not create one.
- **Equal** (default): the total is divided evenly; leftover
  sen go one each to the earliest participants, in the order
  given, so the shares always sum exactly to the total.
- **Custom**: the caller gives every participant's share in
  sen; the shares must cover exactly the participants and sum
  exactly to the total.
- **Weighted**: one weight per participant; each share is
  `floor(total * weight / weight_sum)` and leftover sen go one
  each by largest fractional remainder (ties broken by
  participant order), so the shares always sum exactly.
- **The payer's own share is settled at creation**
  (`paid_cents == share_cents`): they fronted the bill, so they
  owe themselves nothing. Participant order is stored
  (`split_shares.position`): it decides who gets the leftover
  sen on an equal split, and it is what `list_shares` returns.
- **Arisan**: one round is one split with kind `arisan` — the
  member who collects the pot is the payer, everyone else owes
  one equal share to them. The next round is a new split, not a
  recurring object.

## Partial payments

`record_payment` records one payment against what one debtor
still owes. The amount must be positive and no larger than the
remaining balance: overpaying raises `SplitError` instead of
creating credit. The payer cannot pay themselves, a
non-participant cannot pay, and recording is member-only
(owner/member) — though the debtor may record their own
payment, which is the normal case. Every payment is an
append-only row in `split_payments`. The split status moves in
the same transaction: `open` -> `partially_paid` while anyone
still owes -> `settled` (with `settled_at`) once every share is
fully paid. A settled split refuses further payments. The
database backs the same rules with CHECKs (`share_cents > 0`,
`paid_cents <= share_cents`, `amount_cents > 0`).

## Reminders: debtors only, never sends

`list_debtors` lists who still owes on one split;
`split_reminders` / `run_reminder_sweep` list every outstanding
debtor in a room; `build_reminder_digest` groups them per
debtor, most urgent first (G8 `classify_due` when the split has
a due date, else `open`). The payer never appears — their share
is settled at creation — and neither does anyone who has paid
in full, so a reminder can never go to someone who already
paid. This module only answers who needs a reminder about what
— the caller (a G8 cron job, via `reminder_job_prompt` /
`register_reminder_job`, every 12 hours by default) sends it.
Reminder lines name the amount and the occasion, never single
anyone out beyond what the group already agreed to split
(B14: sungkan — surface disagreement gently, never embarrass a
member in a group).

## Settlement: the B8 prepare-only seam

B8 (agent payments, phase 1) is not built yet, so B11 does NOT
build it — it builds the seam B8 will plug into. A settlement
is a *prepared* payment:

1. `prepare_settlement` takes the debtor, reads what they still
   owe, and checks the B6 money gate (`MoneyGate`) for the
   action `split-settlement:<split_id>:<debtor_id>`: the room
   owner alone passes; anyone else needs their own approval
   plus one other owner/member. Without that approval it raises
   `MoneyApprovalError` and nothing is recorded. No second
   money-approval rule exists anywhere in this ticket.
2. It records a row in `split_settlements` with status
   `prepared`: verified recipient (the payer), amount (what the
   debtor still owes), and evidence (split, share, payments so
   far). Preparing again for the same debtor supersedes the
   earlier prepared row.
3. The user pays in their own bank or e-wallet app; nothing
   executes here. There is deliberately no `execute_settlement`
   in this module (a test asserts it does not exist), and the
   schema has no executed status — only `prepared` /
   `superseded`. Settling means preparing, the user paying
   outside Puspa, and the payment being recorded with
   `record_payment`.
4. When a ledger is passed, the preparation is also appended as
   an `evidence_recorded` event (`executed: false`, the closed
   31-type set untouched). The ledger is optional so the split
   maths never depends on it.

No payment rail is named on a prepared settlement yet: QRIS /
virtual account / bank transfer / GoPay / OVO / DANA naming
belongs to B8, which fills in the rail when it lands. That is
the recorded B8 follow-up: B8 must add the rail to the
preparation (and its own tests), and the settlement row is
where it goes.

## The PWA layer

`GET /api/rooms/{id}/splits` lists a room's splits with shares;
`POST .../splits` creates one (400 on bad input, 403 for a
non-member); `POST .../splits/{id}/payments` records a payment
(400 on overpayment); `POST .../splits/{id}/settlements/{debtor}/prepare`
prepares a settlement (403 while the money gate has not
approved it, 200 with `executed: false` once it has). The API
never approves a settlement itself — the same fail-closed shape
as B9's steps: approval happens through the money gate first,
the API only prepares what the gate already approved. The
routes are documented in `docs/openapi-rooms.yaml` (`Split`,
`SplitShare`, `PreparedSettlement`).

## Honest limits

- **No live group-chat delivery is verified anywhere in this
  repo** (same as B6): the split summary (`render_split`) is
  plain text for the group chat, tested as text, not delivered
  live. There is no `@puspa patungan` chat command yet — the
  board command (B9) is the chat surface; a split command is a
  follow-up for the coordinator to schedule.
- **No real payment rail is called.** A prepared settlement
  carries no rail field until B8 lands (see above).
- **No PWA screen exists yet.** The API serves the splits; the
  frontend screen (like `TasksScreen.tsx` for B9) is a
  follow-up — the generated TypeScript types were not
  regenerated from the spec in this iteration.
- **No B9 task linkage.** A split does not create room tasks
  for its debtors; the reminder sweep is the nudge mechanism,
  and the G8 due classification is shared with B9/B10 rather
  than duplicated. Linking debtor tasks to the B9 board is a
  follow-up if the owner wants the board to show who owes what.
- **The base (B9+B10) was still awaiting central-suite
  verification when B11 was built.** B11 touches only its own
  new files plus the shared room schema, `rooms/__init__.py`,
  and `rooms_api.py`; if the base is fixed, re-run the module
  + neighbour tests listed below.

## Reuse

Mechanisms taken, rewritten as Puspa code, never copied verbatim:

- `puspa/rooms/rooms.py` (B6) — room identity, roles, and the
  owner/member/guest rule; splits reuse `APPROVER_ROLES` for
  who may owe, pay, or record.
- `puspa/rooms/money.py` (B6) — the money gate; settlement
  preparation reuses it, no second approval rule exists.
- `puspa/rooms/tasks.py` (B6) — the member-only read/write
  shape and the `due_soon` / `overdue` reminder-query pattern.
- `puspa/meetings/reminders.py` (B10) — the reminder-query +
  digest + `register_reminder_job` shape (G8 cron store).
- `puspa/scheduler/due.py` (G8) — `classify_due` for reminder
  urgency.
- `puspa/pwa/rooms_api.py` (B9) — the `register_routes`
  pattern for the PWA API module.
- `puspa/ledger` — the optional `evidence_recorded` append for
  a prepared settlement (closed 31-type set untouched).

No outside repository was studied for B11 beyond the mechanisms
already recorded in `REUSE.md` for B6/B9/B10/G8.

## Tests

- `tests/test_rooms_splitting_b11.py` (21 tests) — integer-sen
  money helpers and their rejections, equal / custom /
  weighted shares with exact sums, arisan as one equal split,
  creation validation (guest, non-member, payer not a
  participant, bad kind, bad amounts), partial payments to
  settled with status moves, overpayment / self-payment /
  non-participant refusals, debtor-only reminders and the
  per-debtor digest, the G8 reminder-job registration,
  settlement gating (owner alone, member + second approver,
  unapproved refusal), settlement amounts following partial
  payments with supersede, no execution path, and the ledger
  event for a preparation.
- `tests/test_pwa_rooms_splits_api.py` (6 tests) — the HTTP
  layer on the real `create_app`: routes exist, create + list,
  403 for a non-member, 401 anonymous, bad input 400,
  payment + overpayment, settlement 403 then 200 with
  `executed: false`.
