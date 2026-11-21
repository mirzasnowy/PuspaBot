---
name: "Tables"
description: "Creates and reads the per-module tables that hold a module's data: typed columns, one row at a time, isolated per user and per module."
author: "Puspa Team"
---

# Tables

This skill covers module tables: the small typed tables that hold
a module's own data (bills, inventory, scores — whatever the
module tracks). Tables belong to exactly one module and one user.

## When to Use This Skill

Use this skill when the user:

- Asks to store rows of data ("keep a list of...", "record
  this...") inside a module
- Asks to read, add to, or check a module's table
- Asks what columns a table has, or to add a column

## How Tables Work

Tables are created from the module manifest (see the
`custom-modules` skill) or directly:

```python
from puspa.modules.tables import (
    create_table,
    list_tables,
    add_rows,
    read_table,
)

table = create_table(
    module_id=module_id,
    user_id=user_id,
    name="bill",
    columns=[
        {"name": "provider", "type": "text"},
        {"name": "amount", "type": "Rupiah"},
        {"name": "due_date", "type": "date"},
        {"name": "status", "type": "choice", "choice": ["unpaid", "paid"]},
    ],
)
```

### Column Types

Only these five types exist (`COLUMN_TYPES` in
`puspa.modules.tables`):

| Type | Meaning |
| ---- | ------- |
| `text` | Free text |
| `number` | A number |
| `Rupiah` | Money in Rupiah, rendered with Rp formatting |
| `date` | A calendar date |
| `choice` | One of a fixed list — requires a `choice` list |

### Reading and Writing Rows

```python
rows = read_table(table["id"], user_id=user_id)
added = add_rows(table["id"], user_id=user_id, data={
    "provider": "PLN", "amount": 250000,
    "due_date": "2026-11-25", "status": "unpaid",
})
tables = list_tables(module_id, user_id=user_id)
```

`add_rows` checks every value against the column type and
rejects values for columns that are not in the schema. `read_table`
returns all rows of one table.

## Limits — Read These First

- **Size caps.** At most 5 tables per module, at most 10 columns
  per table (enforced by the manifest size gate).
- **Isolated per user.** Every call takes `user_id`, and a table
  belonging to another user is refused. Never read or write
  another user's table.
- **No cross-module reads.** Module A cannot read module B's
  table — `read_table_other_module` always refuses, unless the
  user has explicitly granted a shared slice (not in the MVP).
  If the user asks for data that lives in another module, say
  the tables are separate and offer to copy the rows instead.
- **Schema is fixed at creation.** Adding a column means changing
  the manifest (the `custom-modules` skill) and creating a new
  version; there is no silent in-place schema edit.

## Example

User: "Simpan tagihan PLN bulan ini: Rp250.000, jatuh tempo 25 November."

1. Find the user's `Tagihan` module and its `bill` table
   (`list_tables`).
2. `add_rows` with provider `PLN`, amount `250000`, due date
   `2026-11-25`, status `unpaid`.
3. Confirm the stored row back to the user.
