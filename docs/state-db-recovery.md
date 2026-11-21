# State Database Recovery

How to diagnose and recover a corrupt PuspaBot session database
(`<home>/state.db`). Pattern source: hermes-agent
`state-db-recovery.md` + housekeeping (`maintenance.py`,
`state_meta.py`), rewritten as PuspaBot's own procedure.

## The one rule

**Fail closed. Never delete, truncate, or overwrite `state.db`
automatically.** A corrupt file may still contain recoverable
sessions, and an automatic repair that guesses wrong destroys the
evidence. Every step below inspects first and only writes when a
human (or an operator script with an explicit backup) says so.

## Diagnose first

```python
from puspa.sessions import recover_report

report = recover_report()  # or recover_report("/path/to/state.db")
print(report["status"])    # missing | empty | healthy | corrupt
print(report["problems"])
print(report["steps"])
```

`recover_report()` opens the file read-only (`mode=ro`) and never
modifies it. It checks, in order:

1. The file exists (`missing` — open the writer to create it).
2. The file is not zero bytes (`empty` — a zero-byte file is safe
   to delete, then open the writer).
3. The first 16 bytes are the SQLite magic (`SQLite format 3\0`).
4. `PRAGMA integrity_check` returns `ok`.
5. The `sessions`, `messages`, and `session_lineage` tables exist.

A healthy report needs no action. Anything else follows the
procedure below.

## Procedure

### 1. Stop writers

Close the profile gateway (`gateway.close()`) and stop any process
that may hold the database open. Do not run recovery against a live
writer: WAL sidecars (`state.db-wal`, `state.db-shm`) may still hold
un-checkpointed writes, and copying the file mid-write produces a
copy that looks corrupt.

### 2. Preserve the evidence

Copy the corrupt file (and its `-wal` / `-shm` sidecars, if any) to
a backup location before doing anything else:

```sh
cp <home>/state.db /tmp/state-corrupt-<date>.db
cp <home>/state.db-wal /tmp/state-corrupt-<date>.db-wal  # if present
cp <home>/state.db-shm /tmp/state-corrupt-<date>.db-shm  # if present
```

### 3. Try the SQLite recovery tools (read-only first)

```sh
# Read-only dump: succeeds when the damage is limited to indexes
# or freelist pages; fails loudly when data pages are damaged.
sqlite3 "file:<home>/state.db?mode=ro" ".dump" > /tmp/state-dump.sql
sqlite3 "file:<home>/state.db?mode=ro" "PRAGMA integrity_check;"
```

If `.dump` completes, rebuild into a fresh file and verify it:

```sh
rm -f /tmp/state-recovered.db
sqlite3 /tmp/state-recovered.db < /tmp/state-dump.sql
sqlite3 /tmp/state-recovered.db "PRAGMA integrity_check;"
sqlite3 /tmp/state-recovered.db "SELECT COUNT(*) FROM messages;"
```

If the dump is incomplete (missing rows), note the gap: the
recovered database is authoritative only for what it contains, and
the loss must be reported, not silently papered over.

If `.dump` fails, try `.recover` (SQLite 3.37+), which salvages
what it can and reports lost rows:

```sh
sqlite3 "file:<home>/state.db?mode=ro" ".recover" > /tmp/state-recover.sql
```

### 4. Restore from backup when salvage is not enough

When the project backup holds a `state.db` copy, restore it over
the corrupt file (after step 2's preservation copy), then run the
routine health pass:

```python
from puspa.sessions import maintenance, open_persistence

with open_persistence() as gateway:
    report = maintenance(gateway.store, rebuild_index=True)
    assert report["ok"], report["problems"]
```

Note: the encrypted backup in `puspa/backup/` covers the Postgres
ledger, not `state.db`. A scheduled `state.db` backup is a
follow-up; until it exists, step 3 (salvage) is the recovery path.

### 5. Rebuild what the database cannot give back

When neither salvage nor a backup restores the sessions, rebuild
from the source of truth that remains:

- Session history is partially recoverable from the ledger
  (`puspa/ledger/`): ledger events record what the agent did, but
  not the full message text of every turn. Rebuilt sessions are
  marked as recovered in their metadata.
- The FTS5 index is derived, never source: after any restore or
  rebuild, run `maintenance(store, rebuild_index=True)` to rebuild
  it from `messages` and verify it (`verify_fts()` must report
  `ok`).

### 6. Verify before returning to service

```python
from puspa.sessions import maintenance, open_persistence

with open_persistence() as gateway:
    report = maintenance(gateway.store, vacuum=True, rebuild_index=True)
    assert report["ok"], report["problems"]
    assert report["fts"]["ok"]
```

Only when the report is clean does the gateway go back into
service. Record the incident: what was lost (row counts before and
after), which procedure step restored it, and the backup gap that
allowed the loss.

## Routine prevention

Run the housekeeping pass regularly (see
`docs/session-storage.md`):

```python
from puspa.sessions import maintenance, open_persistence

with open_persistence() as gateway:
    report = maintenance(gateway.store, vacuum=True)
```

`maintenance()` checks integrity, verifies the FTS5 index against
`messages`, reports row counts, and stamps
`state_meta.last_maintenance`. A report with `ok == False` is the
early warning — act on it before the next write makes the damage
worse.
