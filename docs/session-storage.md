# Session Storage

How Puspa persists sessions: one SQLite database per profile, a
single writer per profile, full-text search, and compression lineage.
Pattern source: hermes-agent `persistence/` + `persistence_assembly.py`
+ `gateway/` + `session-storage.md`, rewritten as Puspa's own code
(no verbatim copying). License note: hermes-agent is MIT.

## Layout

```
<home>/
  state.db                 # the profile's session database
  state.db-wal / -shm      # WAL sidecars (created by SQLite)
  config.json              # profile config (future; not owned here)
  logs/                    # profile logs (future; not owned here)
<home>/profiles/<name>/
  state.db                 # that named profile's database
  ...
```

`<home>` is resolved by `puspa.sessions.home.resolve_home()` with
this precedence (highest first):

1. An explicit `home` argument.
2. A context-local override (`override_home()`).
3. The `PUSPA_HOME` environment variable.
4. The platform default: `~/.puspa`.

A named profile lives at `<root>/profiles/<name>/` with its own
`state.db`, config, and logs, so two profiles never share a database
file.

## The storage contract

`state.db` holds five tables plus one FTS5 index:

| Table | Contents |
|---|---|
| `sessions` | Session metadata: id, title, timestamps, `parent_id` (compression lineage), model, metadata JSON |
| `messages` | The full message history: `session_id`, `role` (`system`/`user`/`assistant`/`tool`), `content`, `seq` (unique per session) |
| `session_lineage` | Explicit parent/child edges between sessions, with `kind` (`compression`) and an optional reason |
| `model_config` | The profile's model configuration (key/value JSON) |
| `state_meta` | Housekeeping metadata: `schema_version`, `last_maintenance` |
| `messages_fts` | FTS5 full-text index over message content (external-content table over `messages`) |

Schema version is stamped in `state_meta` (`SCHEMA_VERSION = 1`).
`create_schema()` is idempotent, so opening an existing database is
safe.

## One entry point

```python
from puspa.sessions import open_persistence, open_persistence_reader

gateway = open_persistence(profile="work")   # the single writer
reader = open_persistence_reader(profile="work")  # read-only
```

There is no other supported way to open `state.db`.

## Single-writer authority

Only the profile gateway writes the canonical session database.
The rule is enforced in code, not just in this document:

- `SessionStore` refuses a writable open without a writer token,
  and only `puspa.sessions.gateway` mints tokens. A worker that
  opens `SessionStore(path)` directly gets `SingleWriterError`.
- `SessionGateway.open()` keeps a process-wide registry keyed by
  database path. A second live gateway for the same profile raises
  `SingleWriterError`. Closing the gateway releases the claim, and
  a new gateway may then open.
- Readers are opened with SQLite `mode=ro`: they physically cannot
  write, however many of them exist.

All other clients — workers, subagents, tools — reach session state
through the gateway object (or a reader), never by opening
`state.db` themselves. A second writer is a bug, not a race to win:
it is rejected loudly instead of interleaving writes.

## Atomic writes and contention

- Every mutation runs in a single `BEGIN IMMEDIATE` transaction, so
  a multi-message append is atomic: either every row lands or none
  does (a single bad role rolls the whole batch back).
- WAL mode keeps readers from blocking the writer.
- `busy_timeout` (default 5000 ms) makes SQLite wait for a lock
  holder. When the lock still cannot be acquired after retries, the
  write raises `WriteContentionError` instead of silently dropping
  the write.
- The store serializes access from multiple threads with one
  re-entrant lock, so concurrent appends from worker threads keep
  unique, gapless `seq` values.

## Full-text search (FTS5)

Message content is searchable with SQLite FTS5 (`messages_fts`,
BM25-ranked). The mechanism is the one built in G7
(`puspa.memory.recall.FtsIndex` over a SQLite FTS5 sidecar) — not a
second mechanism:

- The MATCH query is built by G7's safe builder
  (`puspa.memory.recall._fts5_query`), so user punctuation can never
  break out of the FTS5 query syntax.
- Three G7 shortcomings are fixed here:
  1. G7 commits after every single indexed row; here the FTS5 table
     is kept in sync by SQLite triggers inside the writer's own
     transaction — zero extra commits.
  2. G7 has no way to detect index drift; here `verify_fts()`
     probes the index itself (a MATCH query per message — `COUNT(*)`
     on an external-content FTS5 table reads the content table, not
     the index) and runs the FTS5 `integrity-check`, and
     `rebuild_fts()` restores the index.
  3. G7's default index is in-memory and lost on close; here the
     FTS5 table lives in the same `state.db` file as the messages,
     so the index is persistent by construction.

FTS5 is the cheap recall layer before embeddings: keyword search
over history, not a replacement for pgvector semantic recall.

```python
hits = gateway.search_messages("PLN electricity bill", limit=20)
# [{"id", "session_id", "role", "content", "created_at", "seq", "bm25"}]
```

## Compression lineage

A compression births a child session. The G19 compressor
(`puspa.runtime.context_compression.ContextCompressor`) mints a fresh
`lineage_id` per compression and returns a `CompressionResult` with
the compressed messages and `stats["compressed"]`. The seam is
`puspa.sessions.lineage.adopt_compression_result()`:

- When `stats["compressed"]` is False, nothing is recorded — the
  caller keeps the original session (the compressor's own rule:
  adopt the new lineage only when something was actually
  summarized).
- When True, the compressor's summary message (the assistant message
  starting with `[Context summary of earlier turns]`) becomes the
  first message of a new child session, linked to the parent by a
  `session_lineage` edge of kind `compression`.
- The store writes session + summary + edge in one transaction, so
  a compression can never leave a child without its edge or an edge
  without its child.

`lineage_chain(session_id)` walks the ancestry
`[self, parent, grandparent, ...]`; `children(session_id)` lists a
session's direct children. The summary marker value is shared with
G19 (`puspa.runtime.context_compression.SUMMARY_MARKER`) and a test
asserts the two stay equal.

## Profile isolation

Two profiles are two database files; nothing in the code ever opens
another profile's file. `puspa.memory.scope.CredentialScope` (G22)
applies the same rule to credentials: no fallback to another
profile.

The subprocess trap: a context-local home override never crosses a
process boundary, and a child process launched without `PUSPA_HOME`
silently falls back to the default profile. Spawners must build the
child's environment with `puspa.sessions.spawner.child_env()`,
which always sets `PUSPA_HOME` explicitly, and check it with
`require_home_in_env()` before spawning.

## Live-system guard

Tests must never write the real home (`~/.puspa`). The guard is in
`puspa.sessions.home.guard_not_live()`, enforced on every
`open_persistence()` / `open_persistence_reader()` call: resolving
to the live home raises `LiveHomeError` unless the caller passes
`allow_live_home=True` **and** the environment escape hatch
`PUSPA_ALLOW_LIVE_HOME=1` is set. One flag alone is not enough.

## Housekeeping

`puspa.sessions.housekeeping.maintenance(store)` runs the routine
health pass: integrity check, schema check, FTS5 verification, row
counts, and (on request) an FTS5 rebuild and a `VACUUM`. It stamps
`state_meta.last_maintenance` and never deletes session data.

Recovery from a corrupt database is fail-closed and documented in
`docs/state-db-recovery.md`. `recover_report()` inspects a database
file read-only and names the applicable recovery steps.

## Honest limits

- The single-writer registry is process-wide, not machine-wide.
  Two separate processes opening the same profile both get a
  gateway; SQLite locking (`BEGIN IMMEDIATE` + `busy_timeout`)
  keeps them from corrupting the file, but the second process is
  not rejected at open time. Cross-process single-writer
  enforcement (file lock on open) is a follow-up.
- The gateway holds one SQLite connection; very large histories
  (tens of thousands of messages in one session) have no paging in
  `get_session()` — callers that need a window should query with a
  limit (follow-up: add a paged message reader).
- Model config is a flat key/value store, not a validated schema.
  A typed config with defaults is a follow-up.
- Session data is local-only: there is no sync, export, or
  encrypted backup of `state.db` yet (the encrypted Postgres backup
  in `puspa/backup/` covers the ledger, not this file).
