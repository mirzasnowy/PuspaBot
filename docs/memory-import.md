# Memory import

Import memory into PuspaBot from a Kosmos archive, a hand-edited
`MEMORY.md` file, or another assistant's export. The code lives in
`puspa/memory/importer.py`; the design is in
`docs/kosmos-brain-v2.md` (section "Memory import and export").

## Sources

| Source | Input | Trust |
| --- | --- | --- |
| `kosmos` | Archive directory or zip file | Own backup: facts restore as active |
| `memory_md` | A `MEMORY.md` file | Own file: facts restore as active |
| `generic_json` | Another assistant's JSON export | Untrusted: quarantined until approved |
| `generic_markdown` | Another assistant's Markdown export | Untrusted: quarantined until approved |

## Archive format

A Kosmos archive holds `MEMORY.md`, `events.jsonl`,
`graph.jsonl`, `people.jsonl`, `values.jsonl`, `skills/`,
`causal.jsonl`, `ignorance.jsonl` and `manifest.json`. The
manifest carries the format name, schema version (`1`), per-file
SHA-256 hashes, counts, and the ledger head hash. Embeddings are
never in the archive: they are derived data and are recomputed
on import by the durable rebuild. Secrets are never exported,
and the importer redacts any secret-shaped text it meets anyway.

## Pipeline

1. **Validate.** Wrong schema version, manifest hash mismatch,
   count mismatch, or a broken ledger hash chain in
   `events.jsonl` refuses the whole archive before anything is
   written.
2. **Screen.** Secrets are redacted. Instruction-like text is
   blocked and kept for review, never stored as a fact. Text
   from another assistant is quarantined until the user
   approves it.
3. **Merge by bitemporal rules.** Same key and text merges as a
   duplicate. A newer valid time supersedes the stored fact
   (the old row stays as history). An older value against a
   newer stored fact is a conflict: reported, never settled
   silently.
4. **Dry run, then approval.** `import_memory` without
   `approve=True` only plans and writes nothing. With approval
   it writes the batch, the facts (tagged `import:<batch id>`),
   the quarantine rows, one ledger event, and rebuilds the
   durable `MEMORY.md`.
5. **Rollback.** `rollback_import` expires exactly the facts
   one batch created, as new history rows. The ledger stays
   append-only.
6. **Forget list.** `forget_memory_item` hard-deletes a fact
   and records its (key, content hash). A later import skips
   any candidate that matches, so an erased item cannot come
   back through an old archive.

## CLI

```sh
# Dry run: show the plan, write nothing.
puspa memory import ./backup.zip --user <user-id>

# Apply it.
puspa memory import ./backup.zip --user <user-id> --approve

# Roll back one import, or forget one fact.
puspa memory rollback <batch-id> --user <user-id>
puspa memory forget fact:city --user <user-id>
```

## API

```python
from puspa.memory.importer import import_memory, rollback_import, forget_memory_item

plan = import_memory(user_id, "./backup.zip")  # dry run, writes nothing
done = import_memory(user_id, "./backup.zip", approve=True)
rolled = rollback_import(user_id, done["batch_id"])
forgotten = forget_memory_item(user_id, "fact:city")
```

## Honest limits

- Graph edges are validated and counted, but not re-materialised
  into a separate graph store: the graph is rebuilt from
  episodes by the durable rebuild. Person nodes are restored as
  `person:` facts.
- Skill files are validated (each skill needs a `SKILL.md`)
  and counted, but not installed into the procedural store.
- Archived ledger events are validated against their hash
  chain, but not replayed into the live ledger by default:
  replaying them would fork the live chain. Pass
  `replay_events=True` with a ledger to replay them as fresh
  events (same content, new hashes) on a fresh system.
- Encrypted archives are not read: a full export is encrypted
  by default, so decrypt it before importing.
