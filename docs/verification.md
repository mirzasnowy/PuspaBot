# Verification discipline

How a claim of "tested" is earned in this repo. A claim that cannot be
checked against this document is not a verification, it is an opinion.

## The SOLO rule

The full test suite runs **SOLO**: one `pytest` process, no parallel
runner (`-n` / `xdist`), because the tests share one Postgres database
(`puspa_test`). Two suite runs at the same time step on each other's
data. Parallel workers run **only their own test file(s)**; nobody runs
the full suite while another worker's suite is running.

```sh
python -m pytest            # full suite, SOLO, from the repo root
```

Never use `pkill` with a broad pattern (for example `pkill -f pytest`):
it kills other workers' suite runs. Kill only a process you started
yourself, by its exact PID.

## Isolated fixtures, never the live app

- Tests use **isolated fixtures**: a unique `task_id` per test (or a
  fresh in-memory object), and each test cleans up the rows it created.
  A test that depends on rows left by another test, or on data that
  already exists in the database, is broken.
- **Never verify a mutation against the live user app or live user
  data.** A write path (ledger append, account change, billing, file
  write) is verified against a throwaway database or a sandbox
  directory, never against the user's real database, real files, or a
  running production service. If a test cannot run without touching
  real data, the test is wrong, not the rule.
- Tests that need a real container (Docker) or a real browser
  (Chromium) **skip** on machines where those are unavailable. A skip
  is honest; a fake pass is not. The sandbox is fail-closed: when it
  cannot run, the test skips instead of pretending.

## Test database

Postgres 16 + pgvector, database `puspa_test`, peer auth as the OS
user (no password in any file). Setup, from the repo root:

```sh
pg_ctlcluster 16 main start                                  # if the cluster is down
sudo -u postgres psql -c "CREATE ROLE root LOGIN SUPERUSER;" -c "CREATE DATABASE puspa_test OWNER root;"
.venv/bin/python -m puspa.migrations up
```

The migration runner is idempotent: it applies every migration in
`puspa/migrations/versions/` that has not run yet, and records it in
`schema_migrations`. The roles + database are created once, before
the first run (the runner connects to an existing database). See
`docs/migrations.md` for the full story — adding a migration, rolling
one back, the committed schema snapshot, and the container setup.
Individual test modules create their own extra schema through their
fixtures. Postgres does not survive a VM replacement here: if
`pg_lsclusters` finds nothing, reinstall (`apt-get install -y
postgresql-16 postgresql-16-pgvector`) and run the setup again. The
default DSN is `dbname=puspa_test user=root host=/var/run/postgresql`;
override it with `PUSPA_TEST_DSN`.

## How to claim "tested"

A claim of "tested" must name all four:

1. **The exact command** (for example `python -m pytest
   tests/test_ledger.py --noconftest -q`, or the full SOLO run).
2. **The result**: passed / failed / skipped counts, copied from the
   output, not rounded or remembered.
3. **What was not run**: anything skipped, anything that needs a
   network/container/browser the machine does not have, and any suite
   that was deliberately not run (for example the full suite during
   parallel work).
4. **What the test actually covers**: the behavior checked, and the
   honest gaps (a test that checks the happy path does not prove the
   error path).

"Tests pass" without a command and counts is not a verification. A
green run of a subset is not a green run of the suite; say which one
it was.

## Reproduce first

A bug fix starts from a failing reproduction, not from a reading of
the code. The reproduction test is committed with the fix. A fix
without a failing-before / passing-after test is an untested change.

## Docs must not drift from code

Some docs restate code, and a restated fact rots. The rule is
mechanical where it can be: `tests/test_docs_drift.py` checks the one
concrete rule this repo enforces today — the 31 ledger event types
must be identical in `puspa/ledger/ledger.py` (`EVENT_TYPES`), in both
`CHECK` lists in `puspa/migrations/versions/0001_core.sql`, and in the "Closed type
set" line in `docs/contracts.md`. Adding an event type means updating
all three in one commit. New drift rules are added the same way: a
test, not a reminder.

## Size discipline

`tests/test_file_size_ratchet.py` enforces the file-size ratchet
(adapted from DuskBot): no Puspa-owned file over 2000 lines; a file
over 1000 lines may only shrink against its recorded baseline, and a
new file over 1000 lines needs an explicit baseline entry with a
reason. The vendored `nanobot/` reference copy, the frozen
`web-replica/` snapshot, `backups/` and `cassettes/` are out of scope;
see the test for the full list.

## CI

`.github/workflows/ci.yml` runs one workflow with per-area lanes behind
one aggregate gate (`all-checks-pass`) that is the single required
check. A `changes` job (detect-changes) runs first and each lane runs
only when something changed in its area; a push that changes Python
code, dependencies, or the workflow itself runs every lane, while a
docs-only push runs only the checks lane. A skipped lane is fine; only
a failure or a cancellation fails the gate.

Lanes:

- `test` — the full suite SOLO on Postgres 16 + pgvector.
- `sandbox` — the live container + Chromium tests that can never run
  on the dev VM (its kernel rejects container namespaces): the 4 live
  tests in `tests/test_sandbox.py`, the Docker backend conformance
  tests in `tests/test_computer_backends.py`, and the Chromium reader
  test in `tests/test_readers.py`. The lane builds
  `infra/sandbox.Dockerfile` first (the runner's default image,
  `puspa-sandbox:3.12-local`, is built locally on the dev VM and is on
  no registry), installs Chromium with `playwright install --with-deps
  chromium`, and fails if any of those live tests skip (it parses the
  JUnit XML), so a broken image or browser can never silently turn the
  lane back into skips. Honest limit: the lane has never run on GitHub
  yet; its first real run happens there.
- `checks` — the file-size ratchet and the docs-drift test, no database.
- `lint` — `ruff check puspa tests demo` (rules in `pyproject.toml`
  `[tool.ruff.lint]`).
- `format` — `ruff format --check puspa tests demo` (style in
  `pyproject.toml` `[tool.ruff]`).
- `typecheck` — `python -m mypy puspa tests demo` (config in
  `pyproject.toml` `[tool.mypy]`).

Ruff and mypy are pinned in `requirements-dev.txt` (dev tools only,
never imported by Puspa code). The lint and format lanes install only
that file (fast: no torch); the typecheck lane installs
`requirements.txt` too, because mypy needs the runtime dependencies to
resolve imports. The same three commands run locally from the repo root
with the venv (`.venv/bin/ruff check ...`, `.venv/bin/ruff format
--check ...`, `.venv/bin/python -m mypy ...`); a lane that fails in CI
must fail the same way locally before it is pushed.

The ruff selection is explicit in `pyproject.toml` and curated: the
families it does not enable (blind-except, tryceratops, bandit) are
deliberate resilience patterns in this codebase, and enabling one is a
follow-up task with its reason recorded in the config comments.

## Sentinel self-checks

The sentinel (`puspa/sentinel/`, G12) watches Puspa's own
records, so its own verification follows the same rule as
everything else here — as a written rule, not an exhortation:

- A sentinel finding is a claim about the ledger, and it is
  verified the same way: the independent verifier
  (`puspa/sentinel/verifier.py`, K8) re-reads fresh ledger
  events and re-runs the single detector for the issue's scope.
  A detector result that cannot be reproduced from a fresh read
  is `stale`, never confirmed.
- Sentinel tests use synthetic events and throwaway directories
  (`tmp_path`), never the live ledger or the live state file.
  The scanner's event source is injected, so a test never needs
  a database.
- The sentinel only reads the ledger. Its writes (state file,
  decision log, traces, analytics) go to its own home
  directory (`~/.puspa/sentinel` by default), never into the
  user's live data. A test that writes sentinel state into a
  live home is wrong, not the rule.

## License — pending decision

This repo has **no LICENSE file**, deliberately. Choosing the license
is Nero's decision, not a worker's guess; a wrong license file is
worse than no license file, because it grants rights the owner did not
intend to grant. Until Nero chooses, the repo stays unlicensed and
third-party reuse stays governed by `NOTICE.md` and `REUSE.md`. Do not
create a LICENSE file, and do not guess a license in any doc. When the
decision lands, record it here and add the file in the same commit.
