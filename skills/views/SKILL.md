---
name: "Views"
description: "Renders a module's views — cards, tables, numbers, charts, forms, buttons — from its manifest and its table data. Views are data, never hand-written HTML."
author: "Puspa Team"
---

# Views

This skill covers module views: how a module's data is shown to
the user. A view is a small declaration inside the manifest —
never hand-written HTML, JavaScript, or SQL.

## When to Use This Skill

Use this skill when the user:

- Asks to see a module's data ("show me my bills", "how much in
  total", "show it as a chart")
- Asks to add or change how a module is displayed
- Asks what a module shows

## How Views Work

Views live in the manifest's `views` list. Render them with
`puspa.modules.views`:

```python
from puspa.modules.views import render_module, render_view

html = render_module(manifest, data)   # all views, with table data
one = render_view(view, data)          # a single view
```

`data` maps table names to their rows (the same shape the
`tables` skill reads). When no data is given, the manifest's
`example_data` is used — that is how the dry-run gate previews a
module before activation.

### Allowed Components

Only these seven components exist (`ALLOWED_COMPONENTS`):

| Component | What it shows |
| --------- | ------------- |
| `card` | One highlighted value or summary |
| `table` | Rows of a table, chosen columns |
| `items` | A simple list |
| `form` | An input form bound to a table |
| `number` | One computed number (sum, count), with a format such as `Rupiah` |
| `line_chart` | A line chart over a numeric column |
| `action_button` | A button that runs one workflow step |

A view names its table and columns, for example the Bills
module's total:

```json
{"type": "number", "title": "Total tagihan bulan ini",
 "table": "bill", "columns": "amount", "format": "Rupiah"}
```

### Changing a View

Views live in the manifest, so change one through the
`custom-modules` skill (`change_module`, which re-runs the gates
and stores a new draft version). The scanner gate rejects any
view type outside the seven above, and the schema gate requires
each view to be an object.

## Limits — Read These First

- **At most 3 views per module.** Enforced by the manifest size
  gate. If the user wants a fourth, help them pick the three that
  matter most instead of silently dropping one.
- **Views are data, not code.** A manifest containing HTML,
  JavaScript, or SQL (`<script`, `javascript:`, `onclick=`,
  `SELECT `, and friends) is rejected by the scanner gate — it
  is never cleaned up silently. If a view the user describes
  needs custom code, say it cannot be expressed as a view and
  offer the closest allowed component.
- **Rendering needs data.** A view over an empty table renders
  empty, not zero. Say "no rows yet" instead of reporting a
  total of zero when the table is empty.
- **No cross-module views.** A view reads its own module's
  tables only.

## Example

User: "Tampilin total tagihan bulan ini."

1. Read the `bill` table rows for the user's `Tagihan` module.
2. `render_view` the module's `number` view with that data.
3. Show the rendered total. If the table is empty, say there are
   no bills recorded yet instead of showing Rp0 as a fact.
