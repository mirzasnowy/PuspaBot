---
name: "Bills"
description: "Checks and records the user's recurring bills (electricity, water, mobile credit, data) through the built-in Bills module, and reminds the user what is due. Never pays a bill."
author: "Puspa Team"
---

# Bills

This skill covers the built-in Bills module (`Tagihan`): the
bill-autopilot wedge. It checks recurring bills, stores the results
in the module's own table, shows totals, and reminds the user. It
never pays a bill — paying is a separate, approval-gated action
outside this skill.

## When to Use This Skill

Use this skill when the user:

- Asks what bills are due, how much they owe, or what they paid
- Says "check my electricity / water / phone bill"
- Wants a monthly reminder about bills
- Asks to set up, change, or remove bill checking
- Mentions PLN, PDAM, pulsa, or data packages

## How Bills Work

Bills are a declarative module, not hand-written code. The whole
module is one manifest in `puspa/modules/bills.py` (`MANIFEST_BILL`):

- A scheduled workflow runs monthly on the 25th at 07:00.
- It runs a `check_bill` step for PLN, then one for PDAM.
- Results are saved into the module table `bill` (columns:
  `provider`, `amount`, `due_date`, `status`).
- A `send_notification` step tells the user the check ran.
- Views: a `number` view for the month's total (Rupiah), a
  `table` view listing every bill, and a `line_chart` of amounts.

### Step 1: Create the Module

```python
from puspa.modules.bills import create_module_bill

module = create_module_bill(user_id=user_id)
```

This creates the `Tagihan` module in `draft` status from the
manifest. The manifest passes the same five gates as any custom
module (see the `custom-modules` skill).

### Step 2: Activate It

```python
from puspa.modules.manifest import activate_module

active = activate_module(
    str(module["id"]),
    user_id=user_id,
    permission_user=permission_user,
)
```

Activation runs the full dry run and checks the derived permission
manifest is a subset of the user's permission. At most 3 modules
may be active at once.

### Step 3: Read the Results

```python
from puspa.modules.tables import list_tables, read_table

tables = list_tables(str(module["id"]), user_id=user_id)
bill_table = next(t for t in tables if t["name"] == "bill")
rows = read_table(bill_table["id"], user_id=user_id)
```

Each row is one checked bill: provider, amount in Rupiah, due
date, and status. Present the total from the `number` view and
the rows from the `table` view; do not recompute totals by hand
from a different source.

## Limits — Read These First

- **Customer numbers are placeholders.** The manifest ships with
  `customer_number` empty for PLN and PDAM. Until the user gives
  their real customer numbers, a check cannot return a real amount.
  Ask for the number, store it in the module (change the manifest
  through the `custom-modules` skill), and only then report a
  checked amount. Never guess a customer number or an amount.
- **This skill checks and records; it never pays.** If the user
  asks to pay a bill, say so plainly and route it through the
  approval-gated payment path instead of doing it here.
- **Monthly, on the 25th.** The check runs once a month at 07:00
  by schedule. If the user wants a check right now, run the
  module workflow manually through the `workflows` skill
  (dry-run first) instead of claiming the schedule ran.
- **One module, one table.** Bill rows live only in this module's
  `bill` table. Another module cannot read them (cross-module
  reads are always refused), and rows are isolated per user.

## Example

User: "Tagihan listrik bulan ini berapa?"

1. Read the `bill` table for the user's `Tagihan` module.
2. If the PLN row has an amount, answer with it and its due date.
3. If the row is missing or the customer number is still empty,
   say the check has not run with a real number yet, and ask for
   the PLN customer number.
