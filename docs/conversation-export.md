# Conversation export and import

Export a conversation (one or all) as a portable file so the user
can back it up and move chats. The code lives in
`puspa/sessions/export.py`; the store restore it relies on is
`SessionStore.restore_conversations` in
`puspa/sessions/store.py`. The design is in
`docs/kosmos-brain-v2.md` (the v2 backlog item V2-11; the v2 doc
covers memory export only, this item is Nero's addition).

A conversation is one session in the per-profile session store:
its title, timestamps, model, metadata, parent link, and every
message in order, with role, text and timestamp.

## Archive format

A conversation archive is a zip file holding
`conversations.jsonl` (one JSON object per conversation, with its
messages inline and a per-conversation content hash),
`lineage.jsonl` when any compression lineage edge falls inside
the export, and `manifest.json`. The manifest carries the format
name (`puspa-conversation-archive`), schema version (`1`), the
export time, per-file SHA-256 hashes, counts, and a per
conversation summary (id, title, message count, content hash).

## Pipeline

1. **Validate.** Wrong format or schema version, manifest hash
   mismatch, count mismatch, a bad role or timestamp, a
   content hash that does not match its conversation, or a
   duplicate id inside the archive refuses the whole archive
   before anything is written.
2. **Dry run, then approval.** `import_conversations` without
   `approve=True` only plans and writes nothing. With approval
   it restores the new conversations in one transaction, so a
   failed import writes nothing.
3. **Merge by id and content hash.** A conversation whose id
   already exists with identical content is a duplicate and is
   skipped, so importing the same archive twice changes
   nothing. A conversation whose id exists with different
   content is a conflict: the whole import is refused, never
   settled silently, and nothing is written.
4. **Dropped links are reported, not invented.** A parent link
   whose parent is neither in the archive nor in the target
   store is dropped and listed in the result; the same goes for
   a lineage edge whose endpoint is missing. A dropped parent
   does not break duplicate detection: the content hash is
   computed on the record as stored, so re-importing the same
   archive still recognises the conversation as a duplicate.

## CLI

```sh
# Export one conversation.
puspa conversation export --conversation <id> --out ./chat.zip --home /path/to/home

# Export every conversation in the profile.
puspa conversation export --out ./all-chats.zip --home /path/to/home

# Dry run: show the plan, write nothing.
puspa conversation import --file ./chat.zip --home /path/to/home

# Apply it.
puspa conversation import --file ./chat.zip --home /path/to/home --approve
```

`--profile` picks a named profile under the home;
`--home` points at a Puspa home directory (otherwise
`PUSPA_HOME`, otherwise `~/.puspa`). Touching the live home
(`~/.puspa`) needs both `--live-home` and
`PUSPA_ALLOW_LIVE_HOME=1` in the environment, the same two-key
guard the session store itself uses. The import command opens
the gateway without the Postgres advisory lock: the CLI runs on
one machine, the file lock on `state.db` is the operative
cross-process guard there, and a CLI run must not assume a
database is reachable.

## API

```python
from puspa.sessions import open_persistence, open_persistence_reader
from puspa.sessions.export import export_conversations, import_conversations

reader = open_persistence_reader(home="/path/to/home")
result = export_conversations(reader, out="./all-chats.zip")

with open_persistence(home="/path/to/other-home") as gateway:
    plan = import_conversations("./all-chats.zip", writer=gateway)  # dry run
    done = import_conversations("./all-chats.zip", writer=gateway, approve=True)
```

## Honest limits

- Message row ids are local database ids and are not preserved:
  sequence number, role, text and timestamp are. A restored
  conversation compares equal on those, not on row ids.
- The archive holds plain text. A message that carries an image
  or file reference keeps the reference text only; the file
  itself is not bundled. The session store keeps no attachment
  blobs today, so nothing is lost from what it stores.
- Profile-level model configuration (`model_config`) is not
  part of a conversation and is not exported.
- There is no encryption here: a full memory export is
  encrypted by default, but a conversation archive is a plain
  zip. Do not store it where strangers can read it.
- "Export all" reads at most 10,000 sessions; a profile with
  more must export by id.
