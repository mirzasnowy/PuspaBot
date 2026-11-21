---
name: "Notifications"
description: "Defines and evaluates per-module notification rules: when a table row matches a condition, the user gets a message. Evaluation only — sending is the caller's job."
author: "Puspa Team"
---

# Notifications

This skill covers module notification rules: small
condition-then-message rules that live inside a module manifest
and fire when a table row matches. Evaluation is done by code,
never by the model.

## When to Use This Skill

Use this skill when the user:

- Asks to be told when something happens in a module's data
  ("tell me when a bill is over Rp500.000", "notify me when a
  task is overdue")
- Asks to add, change, or remove a notification rule on a module
- Asks why a notification did or did not fire

## How Rules Work

A rule is one object in the manifest's `notifications` list:

```json
{"table": "bill", "condition": "amount > 500000", "message": "Tagihan di atas Rp500.000 — cek sebelum jatuh tempo."}
```

- `table` — which module table the rule watches.
- `condition` — a `<field> <operator> <value>` expression over
  one row, evaluated by `puspa.workflow.runner.evaluate_expression`
  (the workflow condition evaluator, reused here). It is code,
  not model judgement: the same row always gives the same answer.
- `message` — the text the user gets when a row matches.

### Evaluating Rules

```python
from puspa.modules.notifications import evaluate_notifications

fired = evaluate_notifications(manifest, data)
# [{"table": "bill", "rows": {...matched row...}, "message": "..."}]
```

`data` maps table names to their rows. Every rule is checked
against every row of its table; each matching row produces one
notification. A rule whose condition raises (bad field, bad
operator) is skipped silently — it never crashes the evaluation,
and it never fires.

### Adding a Rule

Rules live in the manifest, so add one the same way a manifest
changes: through the `custom-modules` skill (`change_module`,
which re-runs gates 1, 2, and 5 and makes a new draft version).
The manifest schema gate requires every rule to have both a
`condition` and a `message`; a rule missing either is rejected
at creation time.

## Limits — Read These First

- **Evaluation only.** `evaluate_notifications` returns the list
  of notifications that should be sent. Sending them (push, chat
  message, email) is the caller's job. Never claim a notification
  was sent when only the evaluation ran.
- **One row, one message.** A rule fires per matching row. If ten
  rows match, the user gets ten notifications — say so when the
  user writes a broad condition.
- **Conditions are expressions, not questions.** The condition
  language is field comparisons, not natural language. If the
  user asks for something the expression language cannot say
  ("notify me when something feels wrong"), say so and help them
  phrase it as a field comparison instead of guessing.
- **No cross-module rules.** A rule watches a table in its own
  module only; it cannot watch another module's table.

## Example

User: "Kasih tahu aku kalau ada tagihan di atas Rp500.000."

1. Add to the `Tagihan` manifest's `notifications`:
   `{"table": "bill", "condition": "amount > 500000",
   "message": "Tagihan di atas Rp500.000 — cek sebelum jatuh tempo."}`
2. `change_module` to store the new version (gates re-run), then
   activate it again.
3. On each evaluation, `evaluate_notifications` returns one entry
   per bill row over Rp500.000; the caller sends those messages.
