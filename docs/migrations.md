# Migrations — one source of truth for the schema

Before migration tool and schema snapshot the schema lived in two places: a dozen SQL files under
`infra/` that a human ran by hand, and `CREATE TABLE` statements
inside Python modules that ran at runtime (`agents`,
`model_settings`, and others). A user with real data had no safe
upgrade path — the first schema change would have meant re-running
a hand-edited file and hoping nothing was lost. This document is the
canonical description of the replacement: numbered SQL migrations
plus a small runner, and a committed schema snapshot that CI
compares against.

## Tool choice

**Numbered SQL migrations in dbmate-compatible format, applied by
the embedded runner in `puspa/migrations/runner.py`.** No new
dependency: the runner is plain `psycopg`, and the only DDL it holds
is the one `CREATE TABLE IF NOT EXISTS schema_migrations` bootstrap
statement.

Why this shape:

- SQL stays SQL. The schema is ~2,500 lines of DDL with RLS
  policies, triggers, and `DO` blocks; translating it into a
  Python migration framework would have been a rewrite, not a move.
- dbmate-compatible markers (`-- migrate:up` /
  `-- migrate:down`) and the `schema_migrations` table (`version
  TEXT PRIMARY KEY, applied_at TIMESTAMPTZ`) mean the real dbmate
  binary could apply the same files later, if the project ever
  wants it. Nothing in the files depends on the embedded runner.
- Alembic was rejected: it would add SQLAlchemy plus Python
  migrations for a schema that is already SQL. Downloading the
  dbmate or Atlas binaries was rejected too: they cannot be
  vendored, so CI would have to fetch them on every run.

## The migrations

All files live in `puspa/migrations/versions/`, one per schema area,
in dependency order. Every file has a real `down` section (a
rollback that drops what the `up` created) unless it is marked
`cannot be reversed` — no migration in the current set carries that
marker. The `puspa_app` role is created by migration 0001 and is
never dropped by any `down` section.

| # | File | Area |
| --- | --- | --- |
| 0001 | `0001_core.sql` | `puspa_app` role, ledger (`events`, `kill_switch`), accounts (`users`, `sessions`, `vault`), `agents` |
| 0002 | `0002_memory.sql` | Memory tables, `vector` extension |
| 0003 | `0003_runtime.sql` | Runtime tables (`tasks`, `task_runs`, `task_steps`, `plans`, `handoff_requests`, `approvals`, `heartbeats`), `tolak_delete_puspa` trigger |
| 0004 | `0004_graphs.sql` | Graph tables, including the shared-graph tables |
| 0005 | `0005_workflow.sql` | Workflows, modules, module tables/rows |
| 0006 | `0006_world.sql` | World beliefs, belief versions, causal hypotheses, agent goal models |
| 0007 | `0007_learning_loop.sql` | Learning-loop tables |
| 0008 | `0008_channels.sql` | Channel deliveries |
| 0009 | `0009_teams.sql` | Teams, members, invitations, entitlements |
| 0010 | `0010_rooms.sql` | Rooms, splits, delegation, research links |
| 0011 | `0011_meetings.sql` | Meeting minutes |
| 0012 | `0012_drafting.sql` | Drafts, draft approvals |
| 0013 | `0013_payments.sql` | Prepared payments, billing usage rows |
| 0014 | `0014_marketplace.sql` | Marketplace store |
| 0015 | `0015_mockup.sql` | Mockups |
| 0016 | `0016_model_settings.sql` | PWA model settings |
| 0017 | `0017_onboarding_hints.sql` | Onboarding hints |
| 0018 | `0018_scheduler.sql` | Scheduler work items, event receipts |
| 0019 | `0019_conflicts.sql` | Conflict records |

## Using the runner

```sh
# Apply every migration not yet recorded (idempotent, safe to re-run):
python -m puspa.migrations up

# Apply up to a specific version, or roll back to one:
python -m puspa.migrations up --target 0006
python -m puspa.migrations down --to 0006

# Show what is applied and what is pending:
python -m puspa.migrations status
```

DSN resolution, in order: the `--dsn` argument, env `PUSPA_DSN`,
env `PUSPA_TEST_DSN`, else the peer-auth default
`dbname=puspa_test user=root host=/var/run/postgresql`. The runner
connects to an existing database — create the role and the database
first (see "Database setup" in the README).

Rules the runner enforces:

- One transaction per migration. A failed migration leaves the
  database exactly as it was before that migration started.
- Rolling back requires a target (`down --to <version>`); there is
  no "roll back everything" shortcut.
- An unknown applied version (a migration recorded in
  `schema_migrations` but missing from `versions/`) or an unknown
  target refuses to run instead of guessing.
- A `down` section marked `cannot be reversed` refuses to roll
  back instead of destroying data silently.
- **The runner trusts `schema_migrations` and never repairs.**
  Re-applying a recorded migration is a no-op by design — this is
  what every migration tool does, and it is also the one behavior
  change from the old loaders: the old `create_schema` functions
  ran their DDL unconditionally, so they re-created a table
  someone had dropped. `create_schema` now only applies pending
  migrations. If objects were dropped by hand, the fix is to roll
  back properly (`down --to <previous>`, then `up`), not to
  re-call `create_schema`. Tests that simulate a database a
  migration never ran on must drop the objects AND delete the
  version row from `schema_migrations` (see
  `tests/test_bitemporal.py::test_migration_is_idempotent_and_backfills_honestly`
  and `tests/test_pwa_payments_api.py::test_api_creates_schema_when_table_missing`).

## Expand, then contract (feature flags and canary releases, plan 9.6)

Old and new versions must be able to run together during a
rollback, so a migration's `up` section never drops a column or
a table: expand first (add nullable columns, new tables, new
indexes), deploy both versions, and only contract (drop the old
column or table) in a second, later migration, after the old
version is gone everywhere. The full procedure — the three
phases, the canary that rides on them, and the one-command
rollback they make possible — is in
`docs/feature-flags-canary.md` section 5. The shape is enforced
by `tests/test_feature_flags.py`, which fails the suite when any
`up` section drops a column or a table.

## How the loaders use it

Every `create_schema` / `ensure_*` / `apply_*_migration` entry
point in `puspa/` now calls `apply_migrations(dsn, target="NNNN")`
with its own migration as the target. Nothing in `puspa/` creates a
table any more, except two explicit exceptions:

- `puspa/sessions/schema.py` — a SQLite `state.db`, rebuildable by
  design, out of scope for Postgres migrations.
- `puspa/migrations/runner.py` — the single
  `schema_migrations` bootstrap statement.

`tests/test_migrations.py` enforces this with a scanner: any
`CREATE TABLE` / `CREATE INDEX` / `ALTER TABLE` / `DROP TABLE` /
`CREATE POLICY` / `CREATE OR REPLACE FUNCTION` / `CREATE EXTENSION`
in `puspa/**/*.py` (outside those two files) fails the suite, and
so does reading a `.sql` file from anywhere except
`puspa/migrations/`.

## Adding a migration

1. Copy the highest-numbered file, bump the number, keep the
   `-- migrate:up` / `-- migrate:down` markers and the English
   header comment (what it creates, why, what the `down` drops).
2. Make the `up` idempotent where the old code was (`CREATE TABLE
   IF NOT EXISTS`, `DO $$ ... IF NOT EXISTS ... $$` guards), so a
   database that already has the objects from an older setup still
   migrates cleanly.
3. Write a real `down` section. If a rollback genuinely cannot be
   done (data would be destroyed with no way back), write
   `cannot be reversed` in the `down` section instead — the runner
   will refuse to roll it back, and the release notes must say so.
4. **Test the rollback on a realistic-size copy of the data before
   release** (plan §5.3). A rollback that only ever ran on an empty
   database is untested: seed the tables the migration touches,
   roll back, check the data that must survive is still there, roll
   forward again, and check the schema matches the snapshot.
5. Regenerate the snapshot (below) and commit it in the same
   commit as the migration.
6. Add or extend the tests in `tests/test_migrations.py` — at
   minimum the loader-target test must see the new version, and
   the snapshot test must pass.

## The committed snapshot

`infra/schema_snapshot.sql` is a raw `pg_dump -s` of an empty
database with all migrations applied. It is the reference CI
compares against: the migration test applies every migration to an
empty database, dumps it the same way, and diffs the two (the
`schema_migrations` bookkeeping table is part of the comparison).

Regenerate it after any migration change:

```sh
psql "dbname=postgres user=root host=/var/run/postgresql" -c "DROP DATABASE IF EXISTS puspa_snapshot;" -c "CREATE DATABASE puspa_snapshot OWNER root;"
python -m puspa.migrations up --dsn "dbname=puspa_snapshot user=root host=/var/run/postgresql"
pg_dump -s "dbname=puspa_snapshot user=root host=/var/run/postgresql" > infra/schema_snapshot.sql
# then re-add the header comment at the top of the file (regeneration instructions)
psql "dbname=postgres user=root host=/var/run/postgresql" -c "DROP DATABASE puspa_snapshot;"
```

The snapshot was dumped against Postgres 16.15 + pgvector 0.6.0.
The file is 6,000+ lines of generated output, so the file-size
ratchet test skips it by name (see `tests/test_file_size_ratchet.py`).

## Dev Postgres in a container

`docker-compose.dev.yml` runs Postgres 16 + pgvector in a
container, image pinned to `pgvector/pgvector:0.6.0-pg16` (never
`latest`, never an unpinned major tag). The port binds to
`127.0.0.1` only and auth is `trust` on the loopback interface —
this is a local dev database, not a server.

```sh
docker compose -f docker-compose.dev.yml up -d
python -m puspa.migrations up --dsn "dbname=puspa_test user=root host=127.0.0.1 port=5432"
```

That is the whole recovery after a VM replacement: one command to
bring the database back, one to apply the schema. Honest limit:
the container was never run live on the dev VM (no Docker there);
the compose file and the pinned image were verified by reading,
and the container path is CI work.

## CI behavior

The `test` and `sandbox` lanes in `.github/workflows/ci.yml` use
the same pinned image (`pgvector/pgvector:0.6.0-pg16`) as a service.
Each lane creates the `root` role and the `puspa_test` database as
the service superuser, then runs `python -m puspa.migrations up`
(the runner reads `PUSPA_TEST_DSN` from the job environment).
No new lane was added: the migration tests run inside the existing
test lane's suite. The change-detection step treats any `.sql`
change as a Python change, so a migration-only push still runs
the test lane.

## Equivalence with the old setup

Before the loaders were rewired, the migration output was proven
equivalent to the old setup: one database was built by all 27
legacy `create_schema` / `ensure_*` / `apply_*` entry points, and
another by `python -m puspa.migrations up`. Dumping both and
comparing statement by statement gave **786 identical statements;
the only difference was the `schema_migrations` table itself**
(3 statements). The old loaders are gone now, so this proof cannot
be re-run live — it is recorded here and in the migration test
file's docstring instead.

## Known gap found while migrating (not fixed here)

The runtime tables (`tasks`, `task_runs`, `task_steps`, `plans`,
`handoff_requests`, `approvals`, `heartbeats`) have RLS policies
but **no grants to `puspa_app`** — the original
`infra/schema_runtime.sql` never granted them, and the migration
faithfully reproduces that. As `puspa_app` those tables are
unreadable (`permission denied`), so their RLS policies are
currently inert. The migrated schema matches the old schema
exactly, so this is a pre-existing gap, not a migration
regression — but it is a real one, and it needs a decision (grant
the role, or drop the dead policies) in a follow-up ticket.
