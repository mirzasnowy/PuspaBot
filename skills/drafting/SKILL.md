---
name: "Drafting"
description: "Drafts messages, documents, or emails in the user's own voice, learned from their past decisions and preferences. Sending is approval-gated: only the exact approved text, once."
author: "Puspa Team"
---

# Drafting

This skill drafts messages, documents, or emails in the user's
voice — and, when the user approves the exact text, sends them.
The voice comes from the user's own memory, not from a template,
and sending is impossible without a recorded approval.

## When to Use This Skill

Use this skill when the user:

- Asks to draft a message, email, reply, or document
- Asks Puspa to write "like I write" or "in my style"
- Asks to send a draft
- Corrects how a draft sounds ("don't say it like that")

## How Drafting Works

### Step 1: Draft in the User's Voice

```python
from puspa.drafting import DraftingService, PostgresDraftStore

service = DraftingService(draft_store=PostgresDraftStore(), fact_store=fact_store)
draft = service.draft(user_id=user_id, kind="message", prompt=..., context=...)
```

The voice profile is built from the existing memory stores
(`puspa.memory.facts`): voice rules, preferences, and past
decisions are profile facts. This skill invents no second memory
system. Draft kinds are in `DRAFT_KINDS` (message, document,
email, and friends).

### Step 2: Correct the Voice

A correction is recorded as a fact, so it persists and changes
later drafts:

```python
service.correct_voice(user_id=user_id, correction="...")
```

If the user says "too formal" or "I never say that word", record
the correction — do not just fix this one draft and forget it.
The next draft reads the same facts, so the correction sticks.

### Step 3: Approve, Then Send — Only in That Order

```python
approval = service.approve_draft(draft_id, user_id=user_id)
sent = service.send_draft(draft_id, user_id=user_id, approval_id=approval.id)
```

Sending is approval-gated by construction: a draft can only be
sent through `send_draft` with a recorded approval for that
exact draft text, granted by the draft owner, not yet expired
(`APPROVAL_TTL_SECONDS`) and not yet consumed. There is no other
send path in this package. Any failure raises
`DraftNotApprovedError`.

## Limits — Read These First

- **Exact text only.** The approval binds to the draft's
  current text (via `content_hash`). Editing the draft after
  approval voids the approval — approve again. Never send a
  reworded version under an old approval.
- **One approval, one send.** An approval is consumed when used.
  Sending the same draft twice needs a second approval.
- **The owner approves.** Only the draft's owner may approve it.
  Never approve on the user's behalf, and never treat "yeah,
  send it" about a different text as approval for this one.
- **No delivery channel, no send.** If no sender is configured
  for the draft's kind, `send_draft` raises
  `NoDeliveryChannelError`. Say which channel is missing instead
  of claiming it was sent.

## Example

User: "Tulisin balasan buat Pak Irfan, gaya aku biasa."

1. Create the draft from the user's voice facts.
2. Show the draft text to the user for approval.
3. Only after the user approves that exact text,
   `approve_draft` then `send_draft` with the approval id.
4. If the user edits a word first, the approval is void — show
   the new text and approve again.
