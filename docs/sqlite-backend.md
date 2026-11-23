# SQLite backend (desktop)

The desktop app runs the same Python backend on SQLite instead of
Postgres (COW-10, D-SQLite-1). One factory, two backends: `puspa/db.py`
picks the backend from the DSN string, and every call site keeps one
code path. Postgres behaviour is unchanged — a Postgres DSN still
opens a real psycopg connection with the SQL passed through
untouched.

## DSN resolution (D-SQLite-2)

`puspa.db.resolve_dsn`, in order: the explicit argument, env
`PUSPA_SQLITE_PATH`, env `PUSPA_DSN`, env `PUSPA_TEST_DSN`, else the
default file `~/.puspa/puspa.db` (`%APPDATA%\Puspa\puspa.db` on
Windows). A DSN is SQLite when it is `None`, a `Path`, `:memory:`,
a `sqlite:` URL, or a bare path ending in `.db` / `.sqlite` /
`.sqlite3`; it is Postgres when it starts with `postgres://` /
`postgresql://` or is a keyword/value string containing `=`
(`dbname=...`). `pwa/app.py` stores the resolved DSN in
`app.state.dsn`.

## The SQL translator

`puspa/db_translate.py` rewrites the Postgres dialect in the call
sites at execute time, so the 100+ files that talk to the database
did not need hand-rewritten SQL. Postgres connections never see
the translator.

| Postgres | SQLite |
| --- | --- |
| `%s` placeholders | `?` (outside literals/comments) |
| `::jsonb`, `::uuid`, `::text`, `::int`, `::double precision`, `::timestamp ...`, `::type[]` casts | dropped |
| `now()`, `CURRENT_TIMESTAMP` | `strftime('%Y-%m-%dT%H:%M:%f+00:00','now')` |
| `<expr> +/- INTERVAL 'N unit'` | `strftime(..., <expr>, '<sign>N unit')` |
| `CURRENT_DATE` | `(date('now'))` |
| `gen_random_uuid()` | SQLite v4-UUID expression |
| `ILIKE` | `LIKE` |
| `IS [NOT] DISTINCT FROM` | `IS NOT` / `IS` |
| `array_agg(...)` | `group_concat(...)` |
| `= ANY(%s)` / `<> ANY(%s)` | `IN (?,?,...)` / `NOT IN (...)` (empty list becomes `IN (NULL)`) |
| `FOR UPDATE [SKIP LOCKED\|NOWAIT]` | dropped |
| `pg_advisory_*` lock calls | `1` (params dropped) |
| `TRUNCATE t1, t2 CASCADE` | one `DELETE FROM` per table, with `PRAGMA defer_foreign_keys=ON` for the batch |
| `SET ...`, `RESET ...`, `DO $$...$$`, `CREATE/ALTER/DROP ROLE`, `GRANT`/`REVOKE`, `CREATE/DROP/ALTER POLICY`, `CREATE EXTENSION`, `CREATE/DROP FUNCTION`, `COMMENT ON`, `LISTEN`/`NOTIFY`/`UNLISTEN`, `ALTER TABLE ... ENABLE/DISABLE ROW LEVEL SECURITY`, `ALTER TABLE ... DROP/ADD CONSTRAINT` | dropped (no-op) |
| `CREATE INDEX ... USING btree/hash` | `USING` dropped; `USING hnsw/gin/gist` indexes dropped |
| `CREATE INDEX CONCURRENTLY` | `CONCURRENTLY` dropped |
| `DROP TRIGGER ... ON table` | `DROP TRIGGER ...` |
| `SERIAL`/`BIGSERIAL` in `CREATE TABLE` | `INTEGER` |
| `vector(n)` in `CREATE TABLE` | `BLOB` |
| `ADD COLUMN IF NOT EXISTS` | checked against `PRAGMA table_info` at execute time, skipped when the column exists |

Two things the translator cannot do are hand-fixed at the call
site: `array_agg(...)[1]` in `puspa/pwa/replay.py` (SQLite has no
scalar subscript — rewritten as a correlated subquery) and
`DISTINCT ON` in `puspa/ledger/anchor.py`, `puspa/graphs/identity.py`,
`puspa/world/store.py` (rewritten as window functions, which work on
both backends).

`executemany` uses a placeholders-only translation mode (`%s` to
`?` without consuming parameters); `ANY(%s)` and advisory locks
with parameters raise a clear error there instead of misaligning
parameters.

## Value mapping

Declared-type converters (registered with `sqlite3.PARSE_DECLTYPES`)
make reads match psycopg: `TIMESTAMPTZ`/`TIMESTAMP` to `datetime`,
`DATE` to `date`, `UUID` to `uuid.UUID`, `JSONB`/`JSON` to the parsed
value, `BOOLEAN`/`BOOL` to `bool`, `NUMERIC`/`DECIMAL` to `Decimal`,
`UUID[]`/`TEXT[]` to lists (JSON first, then the Postgres `{a,b}`
literal). Parameters adapt the other way: `datetime`/`date` to
ISO-8601 text, `UUID` to text, `dict`/`list`/`Jsonb` to JSON text,
`Decimal` to float.

Two sharp edges, both found by testing:

- **Timestamps are text, always with milliseconds** (D-SQLite-3).
  The translator stores `now()` with milliseconds, and bound
  datetimes use `.isoformat(timespec="milliseconds")`. A datetime
  without a fractional part sorts *after* its own stored form in
  a TEXT comparison, so due triggers and range queries would miss
  rows.
- **Aggregates return raw text.** `max(created_at)` and friends
  are expressions, so no declared type applies and no converter
  runs. Read sites normalise them with `puspa.db.parse_timestamp`
  (`puspa/memory/self_evolution.py`, `puspa/graphs/shared.py`).

## Concurrency (D-SQLite-7)

WAL mode, `busy_timeout` 5000, `PRAGMA foreign_keys = ON` on every
connection. Writers take `BEGIN IMMEDIATE` through
`puspa.db.maybe_begin_immediate(conn)` (a no-op on raw psycopg
connections) wherever a select-then-write must stay atomic
(`pricing/credits.py`, `pricing/watchlist.py`,
`scheduler/scheduler.py`, `scheduler/claim.py`,
`ledger/ledger.py`). Claims are single atomic
`UPDATE ... RETURNING` statements, so no row lock is needed:
`scheduler/claim.py` and `runtime/tasks.py` claim this way, and
`pwa/rooms_jobs.py` skips its session advisory lock on SQLite.
The single-writer lock (`sessions/single_writer.py`) uses a sidecar
file lock next to the database file on SQLite instead of an
advisory lock.

## Vector search (D-SQLite-4)

No `sqlite-vec`, no new native dependency. `memory/episodic.py`
and `knowledge/store.py` branch on the backend: on SQLite they
fetch the newest 2000 candidates for the user/embedder and score
them in Python with `cosine_distance` (`memory/embeddings.py`,
which also parses the stored `"[0.1,...]"` literal, packed float32
bytes, or a list). The Postgres path keeps the `<=>` pgvector
query unchanged. Exact ties at distance 1.0 (no shared tokens)
order arbitrarily on either backend; the top result is stable.

## RLS removal (D-SQLite-5)

The SQLite migration set has no roles, grants, or policies:
ownership is enforced 100% in code (every query filters
`user_id`). Tests that assert role enforcement skip on SQLite
with that reason (`tests/test_rls_memory.py` instead asserts the
guarantee through the read APIs: another user sees `[]`).
`SET ROLE` is a translator no-op on SQLite.

## LISTEN/NOTIFY

`policy/gateway.py` has no LISTEN/NOTIFY on SQLite: the watcher
sleeps in small slices until its timeout and returns False, and
callers refresh on their own schedule. The watcher has no
production callers (only a test fake), so no version-row polling
was built.

## psycopg compatibility

The factory keeps the psycopg surface the codebase uses, so the
Postgres path is not a second dialect:

- `puspa.db.dict_row` is a marker that is also callable exactly
  like `psycopg.rows.dict_row`: psycopg calls it with the cursor
  on the Postgres path (`connect(row_factory=dict_row)` and
  `conn.cursor(row_factory=dict_row)` both work).
- `puspa.db.Jsonb` subclasses psycopg's `Jsonb` (guarded import:
  without psycopg it stands alone), so raw psycopg connections
  adapt it natively; the SQLite path reads its `.value`.
- Error aliases (`Error`, `DatabaseError`, `OperationalError`,
  `IntegrityError`, `errors.UniqueViolation`, ...) are
  `(sqlite_error, psycopg_error)` tuples, so an `except` clause
  catches the error on either backend. Two rules: never nest an
  alias inside another except tuple — `except (ValueError,
  _db_Error)` raises `TypeError` at runtime; splat it (`except
  (ValueError, *_db_Error)`) or give it its own clause — and
  never raise an alias (a tuple is not callable); raise the
  concrete error the backend would really raise.
  `errors.RaiseException` maps to `sqlite3.IntegrityError` on
  SQLite, because that is what the translated delete-guard
  trigger's `RAISE(ABORT, ...)` produces.

## Dependencies

psycopg stays in `requirements.txt`: the hosted path still needs
it, and the desktop bundle installs the same requirements. No new
dependency was added for SQLite (no `sqlite-vec`, no `aiosqlite`).

## Known deviations

- SQLite does not enforce the UUID format: a non-UUID `user_id`
  is accepted on SQLite where Postgres rejects it. Product code
  always passes real UUIDs; only tests hit this.
- SQLite `INTEGER` is always 64-bit, so `BIGSERIAL` ids become
  `INTEGER PRIMARY KEY AUTOINCREMENT` and plain `INT` columns are
  wider than Postgres `int32`. No data loss either way.
- `CHECK` constraints from `ADD CONSTRAINT` are dropped in the
  SQLite set; the ledger validates event types against its closed
  set in Python instead.
- `runtime/tasks.py::claim_next_task` returns the post-update row
  (status `'running'`); the old code returned the pre-update row.
  It has no callers.
- `policy/gateway.py` polls by sleeping (see above).
- One writer at a time: a ledger `append_event` inside another
  open write transaction deadlocks on SQLite (the second
  connection's `BEGIN IMMEDIATE` waits on the first one's lock).
  Callers that share one transaction across stores must use
  `Ledger._insert_event_on(conn, ...)` on the same connection
  instead (`gallery/support.py` does this); the event then
  commits with the outer rows.
- Unaliased aggregate columns are spelled the Postgres way in
  dict rows (`SELECT COUNT(*)` reads as `row["count"]`); the
  `Cursor` maps a `FUNC(...)` column name to the lowercase
  function name.
- Three Postgres-only expression forms have no SQLite
  equivalent and are computed in Python instead (each is the
  only site in the tree): `EXTRACT(EPOCH FROM ...)`
  (`onboarding/onboarding.py`, binds the measured duration),
  `now() - (%s || ' seconds')::interval`
  (`gallery/store.py`, binds the cooldown cutoff), and
  `make_interval(days => %s)` (two tests, bind the backdated
  timestamp). The `(%s || ' seconds')::interval` form strips
  to a text subtraction on SQLite and silently miscompares —
  never use it; bind a datetime.
- The Postgres catalog has no SQLite equivalent: existence
  checks use `sqlite_master` (`rooms/schema.py`,
  `teams/schema.py`) and `PRAGMA table_info`
  (`ledger/ledger.py`, `db.py`); `pg_tables` / `pg_indexes` /
  `information_schema` queries must branch on the backend.
- The `puspa-token-app` end-to-end test spawns the *installed*
  script, which imports the editable install of `build/puspa`,
  not the worktree under test — it passes once this branch is
  merged into `build/`, and fails in the worktree because the
  old script cannot parse a SQLite file DSN.

## Tests

- `tests/test_sqlite_migrations.py` — all 45 SQLite migrations
  apply to a fresh file, rollback/re-apply works, the SQLite set
  defines exactly the same tables/columns as the Postgres set
  (parsed from both sets' SQL), and UUID/timestamp/JSON/bool/
  numeric/array values round-trip with Postgres-like types.
- `tests/test_sqlite_differential.py` — the same operations
  (ledger append + hash-chain verify, profile facts, episodic
  search, work-item claim) run on a migrated SQLite file and on
  a migrated Postgres database and must agree; the live schema
  comparison covers every table and column. Needs a Postgres
  server; every test skips without one. The scratch database is
  `puspa_test_sqlite_diff_<pid>` — never the queue's `puspa_test`.
- `tests/test_worker_db.py` — per-worker SQLite files (template
  file + copy per worker) and the Postgres lane behind a
  live-server guard.
- Run the suite on SQLite with a file DSN, e.g.
  `PUSPA_TEST_DSN=/tmp/puspa_test.db pytest tests/...` (the
  harness in `tests/conftest.py` copies the template file per
  worker; `pytest -m unit` never creates a database file).
