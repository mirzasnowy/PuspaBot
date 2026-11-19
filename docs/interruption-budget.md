# Interruption Budget (Kosmos K10)

Default is silence. Kosmos "pays" for the user's attention from a
limited daily interruption budget and only moves on its own when
the benefit exceeds the cost of the interruption. Good proactivity
is mostly silent reversible action, not notifications.

Implementation: `puspa/proactivity/budget.py`
(`InterruptionBudget`, `Initiative`, `Decision`, `SpendRecord`).
Pure Python, deterministic, no database, no network, no LLM call.

## The numbers

| Setting | Value | Why |
| --- | --- | --- |
| Daily budget | 5 units | See below |
| Scarcity line | 2 units left | Below this, only high-value initiatives pass |
| High-value bar | value >= 0.85 | The bar for passing while scarce |

**Why 5 a day.** The product default is silence, and a user
pinged more than a handful of times a day by their own assistant
stops reading the pings at all. Five fits the real mix — one bill,
one or two price drops, one reminder, one digest — with the
scarcity rule reserving the last units for the urgent. It is a
default, not a law: `InterruptionBudget(daily_budget=...)`
configures it per caller.

## Cost and minimum value per initiative kind

| Kind | Cost | Min value | Reasoning |
| --- | --- | --- | --- |
| `urgent_alert` | 1 | 0.50 | Time-critical (a payment failed, an account locked): cheap, because missing it costs the user more than the ping. |
| `bill_due` | 1 | 0.60 | A bill about to fall due: time-critical, but the user usually already expects it. |
| `price_drop` | 2 | 0.70 | Saves money but is rarely urgent; priced double so a flood of small drops cannot eat the day. |
| `reminder` | 1 | 0.60 | A nudge about something the user asked to be reminded of. |
| `digest` | 1 | 0.50 | A batched summary: one ping carrying many items, so it is cheap per item. |
| `suggestion` | 2 | 0.80 | An unsolicited idea is the spammiest kind of proactivity, so it is priced the highest and needs the highest value. |
| `silent_action` | 0 | 0.00 | A silent reversible action (filing, drafting, preparing): no interruption, no payment, never gated. |

Value is the caller's assessed benefit, 0..1: how much this
interruption is worth to the user right now. A kind not in this
table is rejected — a caller cannot invent a cheap kind to
dodge the budget.

## Allow / deny rules

`InterruptionBudget.try_spend(initiative)` checks, in order:

1. **Value below the kind's minimum** — denied
   (`value_below_threshold`), however full the budget is.
   Benefit must exceed the cost of interrupting.
2. **Silent action (cost 0)** — allowed. It never interrupts,
   so it never pays and is never gated.
3. **Budget empty** — denied (`budget_empty`). Silence: no new
   notification, of any kind, at any value.
4. **Remaining below the cost** — denied
   (`insufficient_budget`). The initiative does not fit in what
   is left.
5. **Budget scarce** (remaining at or below 2) **and value
   below 0.85** — denied (`low_value_when_scarce`). The last
   units are reserved for what matters most. Low-value
   initiatives are suppressed first as the budget thins.
6. Otherwise **allowed**, and the cost is deducted.

Every attempt — allowed or denied — is written to the spend
log, so the log shows both what interrupted the user and what
the budget suppressed.

## Daily reset

The budget resets every day (UTC). Days are independent:
spending on one day never affects another. The spend log keeps
the last 30 days; older days are dropped.

## Callers (the wiring)

- **Scheduler** — `puspa/scheduler/scheduler.py`: `tick`,
  `run_trigger`, and `run_missed` take an optional
  `budget`. Only the proactive trigger types pay:
  `price_drop` (kind `price_drop`, assessed value 0.70) and
  `due_date_bill` (kind `bill_due`, assessed value 0.85),
  mapped in `PROACTIVE_TRIGGER_TYPES`. A denied trigger is
  not run and its `next_trigger_at` is not advanced — it stays
  due and is re-evaluated on the next tick, or the next day
  when the budget resets. Without a budget the scheduler
  behaves exactly as before (no gating).
- **Channel gateway** — `puspa/channels/run.py`:
  `GatewayRunner` owns one `InterruptionBudget` per profile
  (injectable via the `budget` constructor argument), and
  `GatewayRunner.notify(platform, chat_id, initiative=...)`
  pays from it before sending. A denied initiative sends
  nothing, records no delivery, and returns `None`. A
  `notify` call without an `initiative` is ungated: replies
  to the user never go through `notify`, so this path is for
  proactive pings only. The raw send in
  `puspa/channels/notify.py` stays ungated — the gate lives
  in the runner, the one place every channel notification
  passes through.

## What does NOT pay

- **User-initiated traffic.** Replies to the user's messages,
  and anything the user asked for in the moment, never touch
  the budget. Only the system moving on its own pays.
- **User-created routines.** Cron jobs (`puspa/scheduler/cron.py`)
  are solicited: the user wrote the prompt and the schedule.
  Suppressing them would break an explicit request, so they
  are exempt.
- **Silent system work.** Backup, consolidation, dream,
  trust-root refresh, sentinel scan, and curriculum ticks never
  interrupt anyone, so they never pay.
- **Silent reversible action.** Kind `silent_action` (cost 0):
  the system doing quiet work — filing, drafting, preparing —
  is the preferred form of proactivity and is never gated.

## Assessed values are fixed for now (honest limit)

The scheduler maps each proactive trigger type to one fixed
assessed value (bill 0.85, price drop 0.70) because it has no
per-event benefit signal yet — it cannot tell a Rp500 drop on
a rarely bought item from a Rp500.000 drop on a daily
essential. Per-event value scoring (from the world model:
how much the user cares, how big the saving is, how soon the
deadline is) is the follow-up; until then the fixed values
are documented here, not hidden in code.

## Audit, not the ledger (honest limit)

Denials are audited in the budget's own spend log
(`budget.spend_log(day)` / `budget.summary(day)`), not in the
ledger. The closed set of 31 ledger event types (revisi 7)
has no "suppressed" type, and adding a 32nd is forbidden —
so a suppressed initiative leaves no ledger row. If a
suppression ever needs to be ledger-visible, that needs a
revision-7 decision to add an event type first.

## Persistence (honest limit)

The budget is **in-memory**. The spend log lives in the
process that owns the budget (the scheduler tick caller, the
channel gateway runner). A restart resets the day's spend, so
a restart can let through pings the old process would have
suppressed. That is the known limit of this first version
(the same in-memory choice as the K6 ignorance map): the
budget is rebuilt by the next day's spending, and nothing is
silently treated as known. Persisting the spend log (a
Postgres table + idempotent migration, the K3/K4/K5 pattern)
is the follow-up when the budget needs to survive restarts.

## Tests

`tests/test_interruption_budget.py` (23 tests):

- Pure budget rules: defaults, the cost table, the value bar,
  empty-budget silence, insufficient budget, the scarcity
  rule, the spam-vs-high-value flood, silent actions, the
  daily reset, the audit log, and `reset_day`.
- Scheduler wiring through a real Postgres `puspa_test`:
  proactive triggers pay, a denied trigger stays due,
  scarcity lets a bill through but not a price drop, silent
  and user triggers never pay, no budget means no gating,
  missed proactive triggers are gated too.
- Gateway wiring (mock transport, no network): `notify`
  sends when allowed, sends nothing once the budget is empty,
  and stays ungated without an initiative.
