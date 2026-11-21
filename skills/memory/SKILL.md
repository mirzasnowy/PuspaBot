---
name: "Memory"
description: "The user's second brain: remembers facts and preferences across conversations, recalls them by meaning and keywords, and exports or imports the whole memory as an encrypted archive."
author: "Puspa Team"
---

# Memory

This skill covers the second brain: what Puspa remembers about
the user, how it recalls it, and how the user takes their memory
with them. Memory is facts, versioned — never silently
overwritten, never silently deleted.

## When to Use This Skill

Use this skill when the user:

- Asks Puspa to remember something ("remember that I...", "my
  PLN number is...")
- Asks what Puspa remembers about them, or to correct or forget
  something
- Asks to export or import their memory
- Asks a question whose answer lives in something said earlier

## Recording Facts

```python
from puspa.memory.facts import (
    record_fact,
    supersede_fact,
    expire_fact,
    restore_fact,
    list_active_facts,
    get_fact_history,
)

record_fact(user_id, key="pln_customer_number", value="...", store=store)
```

- A fact has a key, a value, and a confidence. Recording a new
  value for a key does not erase the old one — `supersede_fact`
  marks the old value superseded, and `get_fact_history` shows
  the whole chain. Corrections are history, not erasure.
- `expire_fact` retires a fact (it stays in history);
  `forget_fact` removes it. Forgetting is for the user's explicit
  request only — never forget something to tidy up.
- `list_active_facts` lists what is currently believed. When the
  user asks "what do you remember about me", read this list and
  report it — do not summarise from the conversation.

## Recalling

```python
from puspa.memory.recall import hybrid_recall

results = hybrid_recall(query, facts, embedder=embedder, limit=10)
```

Recall is hybrid: keyword matching plus embedding similarity,
merged by reciprocal rank with a recency boost. A fact is
returned only if it clears the similarity threshold — a weak
match is not returned as a guess. If recall returns nothing, say
so instead of answering from general knowledge and pretending
it was remembered.

The PWA Memory screen shows the same facts as a graph the user
can see and edit (add, edit, delete). An edit there is recorded
as a user correction, and retrieval reads the same graph — so a
correction changes later answers. Deletes are recorded as
tombstones so the ledger stays append-only.

## Export and Import

```python
from puspa.memory.memory_export import export_memory
from puspa.memory.importer import load_archive, validate_archive

export = export_memory(user_id, path, passphrase=passphrase, encrypted=True)
```

- Export writes one archive file for the user, encrypted with a
  passphrase by default. The passphrase is the user's — the
  agent must never store it in the archive, in a note, or in a
  fact.
- Import reads an archive back (`load_archive`,
  `validate_archive`) and parses its candidates
  (`parse_memory_md_candidates`, `parse_generic_json`,
  `parse_generic_markdown`) into an import batch the user reviews
  before anything is written. Import never silently overwrites
  live facts.

## Limits — Read These First

- **Memory is per user.** Facts are always stored under the
  user's id. Never record one user's facts under another user,
  and never answer from another user's memory.
- **Confidence is not certainty.** A low-confidence fact is
  labelled as such. Do not present a guess the user once
  mentioned as a settled fact.
- **Export needs a passphrase the user controls.** If the user
  asks for an unencrypted export, say the default is encrypted
  and confirm explicitly before writing it unencrypted.
- **The graph view is the same memory.** Do not treat the PWA
  graph and the fact store as two memories — they are two views
  of one store.

## Example

User: "Ingat ya, nomor PLN aku 1234567890."

1. `record_fact(user_id, key="pln_customer_number",
   value="1234567890")`.
2. Confirm back the key and value.
3. Later, when the user asks for a bill check, recall the fact
   instead of asking for the number again — and if recall finds
   nothing, ask, do not guess.
