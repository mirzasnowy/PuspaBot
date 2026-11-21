---
name: "Custom Modules"
description: "Builds, activates, changes, and removes the user's own declarative modules — one manifest holding a workflow, views, tables, and notification rules — through five validation gates."
author: "Puspa Team"
---

# Custom Modules

This skill covers custom modules: the user's own small apps,
written as ONE declarative manifest. A module is flow + display +
data + notifications, as data — never as program code. The Bills
module (`bills` skill) is the built-in proof that the format is
expressive enough.

## When to Use This Skill

Use this skill when the user:

- Asks to build a tracker, a list, a reminder flow, or any small
  personal tool ("make me a module that tracks...")
- Asks to change, pause, roll back, export, or delete a module
- Asks why a module was rejected or cannot be activated

## The Manifest

One manifest object (`puspa.modules.manifest`):

```json
{
  "name": "My Tracker",
  "table": [{"name": "items", "columns": [
    {"name": "title", "type": "text"},
    {"name": "amount", "type": "Rupiah"}
  ]}],
  "views": [{"type": "table", "title": "Items",
             "table": "items", "columns": ["title", "amount"]}],
  "workflows": {"triggers": {"type": "manual"}, "step": []},
  "notifications": [{"table": "items", "condition": "amount > 100000",
                     "message": "Item over Rp100.000."}],
  "example_data": {"items": [{"title": "Sample", "amount": 50000}]}
}
```

The parts each have their own skill: `tables` for the tables,
`views` for the views, `notifications` for the rules,
`workflows` for the flow.

## The Five Gates

Every manifest runs five gates in `run_gates` before it can be
activated. A manifest failing any gate cannot be activated, and
the reason is shown — never fixed silently:

1. **Schema** — name, at least one view, tables with named
   columns, workflows and notifications well-formed.
2. **Scanner** — only allowed view components and column types;
   no code anywhere in the manifest (`<script`, `javascript:`,
   `onclick=`, `SELECT `, `INSERT `, `DROP `, `--`, `/*`).
3. **Permission** — the permission manifest is derived from the
   workflow blocks used, and must be a subset of the user's own
   permission.
4. **Dry run** — every view renders with `example_data`, and the
   workflow dry-runs with sample data.
5. **Size** — at most 3 views, 5 tables, 10 columns per table, 10
   workflow steps, 64 KB total; at most 3 active modules.

## Lifecycle

```python
from puspa.modules.manifest import (
    create_module,      # draft version 1; gates 1, 2, 5 run now
    activate_module,    # all five gates + permission subset + dry run
    change_module,      # new draft version; gates re-run
    rollback_module,    # restore an old version as a new version
    delete_module,      # returns the data export first, then deletes
    export_data_module, # all table data as JSON
    list_modules, get_module,
)
```

- `create_module(user_id, manifest)` — creates a draft. Gates
  1, 2, and 5 must pass at creation.
- `activate_module(module_id, user_id, permission_user)` —
  runs all five gates, checks the derived permission is inside
  the user's permission, checks the 3-active-module cap, and
  dry-runs the workflow. Only then is the module `active`.
- `change_module` — a new version, back to `draft`; re-activate
  to go live again.
- `delete_module` — returns the data export together with the
  deletion, so the user keeps their data. Offer the export before
  deleting.

## Limits — Read These First

- **Manifests are data, not programs.** If what the user wants
  cannot be expressed in the manifest format, that is a finding
  (the format gets fixed), not a reason to smuggle code in. Never
  paste code into a manifest to work around a gate.
- **At most 3 active modules.** If three are already active,
  activation is refused. Help the user pick which one to pause
  or delete instead of overriding the cap.
- **A module cannot read another module's tables.** Cross-module
  reads are always refused (see the `tables` skill).
- **Activation needs the user's permission.** The derived
  permission manifest must fit inside the user's permission; if
  it does not, name the missing permission instead of widening
  it silently.

## Example

User: "Buatin modul buat nyatet pengeluaran harian."

1. Draft a manifest: one table (`expenses`: title `text`, amount
   `Rupiah`, date `date`), one `table` view, one `number` view
   for the month's total, no workflow yet.
2. `run_gates` — fix any problem it reports, showing the reason
   to the user.
3. `create_module`, then `activate_module`. Show the rendered
   views with `example_data` first, so the user sees what they
   are activating.
