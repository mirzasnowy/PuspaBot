---
name: "Workflows"
description: "Builds and runs the user's own automations from plain language: a trigger plus a short list of blocks, dry-run first, approval-gated for anything that spends or sends."
author: "Puspa Team"
---

# Workflows

This skill covers custom workflows: the user's own automations.
A workflow is a trigger plus a short list of blocks (steps), each
block doing one thing. The agent can draft one from plain
language, but the user approves the draft before it runs.

## When to Use This Skill

Use this skill when the user:

- Asks to automate something ("every Friday, remind me...",
  "when a bill arrives, save it and tell me")
- Asks to change, pause, roll back, or delete an automation
- Asks what an automation did, or why it did not run

## How Workflows Work

### Step 1: Draft From Plain Language

```python
from puspa.workflow.composer import compose_draft

draft = compose_draft("ingatkan aku tiap tanggal 25 buat cek tagihan", user_id=user_id)
```

`compose_draft` turns the user's words into a workflow draft:
a trigger plus steps built from the block catalogue
(`puspa.workflow.blocks`). Show the draft to the user as steps
in plain language before creating anything — a draft is not an
automation until the user approves it.

### Step 2: Create and Dry-Run

```python
from puspa.workflow.runner import (
    create_workflow,
    dry_run,
    activate_workflow,
    run_workflow,
    pause_workflow,
    change_workflow,
    rollback_workflow,
    delete_workflow,
    list_workflows,
)

workflow = create_workflow(
    user_id=user_id,
    agent_id="puspa",
    name="Cek tagihan bulanan",
    trigger={"type": "schedule", "mode": "date", "date": 25, "start_time": "07:00"},
    step=[{"block": "check_bill", "inputs": {"provider": "PLN", "customer_number": ""}}],
)
result = dry_run(str(workflow["id"]), user_id=user_id)
```

Triggers are validated by `puspa.workflow.triggers.validate_trigger`
(manual, schedule, or event triggers). Always dry-run before
activating: a dry run executes the steps against sample data
without side effects and reports success or the reason it failed.

### Step 3: Activate, Run, Pause

- `activate_workflow` — the workflow goes live; the scheduler
  ticks it (`tick_workflow`) when its trigger fires.
- `run_workflow` — run it once, now.
- `pause_workflow` — stop it without deleting it.
- `change_workflow` / `rollback_workflow` — new version / restore
  an old version.
- `delete_workflow` — removes it.

## Approvals — Read This First

Steps that spend money, send messages, or act outside Puspa do
not run on their own. They run only through
`execute_approved_action(approval_id, user_id, context)` with a
recorded approval for that exact action. If a workflow step needs
approval, the run pauses and asks the user — never approve on the
user's behalf, and never rephrase the action to slip past the
approval ("same thing, different words" is not the same approval).

## Limits

- **At most 10 steps per workflow** (the module size gate; the
  same cap applies inside module workflows).
- **A dry run is not a real run.** It proves the steps are
  well-formed and the sample data flows; it does not prove the
  live trigger will fire or the live data exists. Say which one
  ran.
- **Triggers are validated, not guessed.** A trigger the
  validator rejects (bad date, unknown type) is refused with the
  reason shown — fix the trigger, do not silently replace it
  with `manual`.
- **Every run writes a ledger event.** Runs are recorded; if the
  user asks what ran, read the runs back instead of recalling
  from the conversation.

## Example

User: "Tiap tanggal 25 jam 7 pagi, cek tagihan PLN terus kasih tahu aku."

1. `compose_draft` from that sentence; show the draft steps
   (schedule trigger on the 25th at 07:00, `check_bill` for PLN,
   `send_notification`).
2. After the user approves, `create_workflow`, then `dry_run`.
3. If the dry run passes, `activate_workflow` and confirm the
   schedule back to the user.
