# Puspa — Phase 0 (Security + backbone)

The foundational slice of the Puspa MVP (revision 7 definition, approved by Nero 17 Nov 2026).
Phase 0 period: 18 Nov - 1 Dec 2026. Detailed status: see `docs/status-phase-0.md`.
Inter-package contracts: see `docs/contracts.md`.

## Repo

The Puspa repo is a **standalone repo, NOT a fork**: **mirzasnowy/puspa-ai**
(https://github.com/mirzasnowy/puspa-ai, public, main branch).
Phase 0 was pushed there on 17 Nov 2026 (commits `8e91a92` +
`657ee78`); `nanobot/` was not pushed (local read-only reference only).
**No-fork rule (Nero):** never create a GitHub fork of any repo.
Other people's code is used as a pip dependency, or vendored with its
original LICENSE file + attribution (README and NOTICE). Non-commercial /
copyleft licenses (GPL, CC BY-NC-SA, PolyForm Noncommercial) must not
be used.

## Contents

- `nanobot/` — local read-only reference clone of HKUDS/nanobot (HEAD e767174,
  MIT). Not the base repo; no code vendored yet. Inventory:
  `docs/nanobot-inventory.md`. From a fresh clone, nanobot is used from
  the git-pinned dependency in `requirements.txt`
  (`nanobot-ai @ git+https://github.com/HKUDS/nanobot@e767174`).
- `puspa/ledger/` — Ledger: append-only Postgres table `events`,
  per-task hash chain, 31 closed event types.
- `puspa/policy/` — Ed25519 permission tokens + subset, signed skills,
  single gate for outbound actions, spending limit (default Rp0).
- `puspa/accounts/` — email login + Argon2id, per-device sessions (only
  the sha256 of the token is stored), per-user Fernet vault, Postgres
  Row Level Security (6 policies, enforced on the `puspa_app` role),
  cross-user ownership checks (`require_owner` + owned readers +
  agent registry). Password login is replaced by Google login in
  phase 1; sessions + vault are reused.
- `puspa/quarantine/` — Quarantined reader (strict schema, `[DATA: ...]`,
  max 200 characters) + prompt-injection scanner (ID+EN).
- `puspa/sandbox/` — fail-closed Docker sandbox (network allowlist, no
  fallback to host execution).
- `puspa/chassis/` — integration with nanobot as the chassis: per-turn
  permission token (ContextVar + `RequestContext`), `PuspaTool` (token +
  skill + gate + audit checks), `PuspaRegistry` (automatic wrapping +
  `prepare_call` backstop), `PuspaHook` (task lifecycle into the ledger),
  `PuspaSkillsLoader` (workspace/plugin skills must be signed),
  `bind_turn` (login session -> agent permission token), `SandboxTool`
  (`run_code`), `PuspaChassis` + `assemble` (composition root).
  ZERO lines of nanobot code changed.
- `tests/` — 244 tests passing + 4 skipped (live-container tests, need a
  normal Docker host; see docs/status-phase-0.md for this VM's limits).
  Breakdown: 105 foundational + 5 `check_tool` gap tests + 26 integration
  tests (`tests/test_integration.py`) + 21 ledger event-type tests + 12
  RLS tests + 7 vault tests + 26 cross-user access tests (23 in
  `tests/test_access.py` + 3 path-8 tests in `tests/test_attacks.py`) +
  1 `resolve_session_as` side-effect test + 41 generic BYOK factory
  tests + mock harness (`tests/test_byok_provider.py`).

## Running the tests

From the repo root:

```bash
.venv/bin/python -m pytest tests -q
```

Needs a local Postgres running (cluster 16/main, DB `puspa_test`, role `root`,
peer auth; DSN can be overridden with env `PUSPA_TEST_DSN`) and the vault
master key in env `PUSPA_VAULT_MASTER_KEY` for the vault tests (set
automatically by the test fixture). Without Postgres, the ledger/accounts
tests fail.

## Database setup

This single command sets everything up (idempotent — safe to re-run),
as the Postgres superuser, from this directory:

```bash
psql "dbname=postgres user=root host=/var/run/postgresql" -f infra/schema_core.sql
```

(The `sudo -u postgres psql -f ...` variant also works, but the file must
be readable by the postgres user — copy it to `/tmp` first if workspace
permissions refuse.)

What `infra/schema_core.sql` does: create role `root` (superuser, peer
auth) and `puspa_app` (the application role subject to RLS) if they do
not exist yet, create database `puspa_test` if it does not exist yet,
then apply the ledger schema (table `events`) + accounts (users/sessions/
vault tables + RLS policies). There is no password anywhere — auth uses
peer auth over the local socket. Do not add a password to that file.

**Recovery after a VM restart:** Postgres on this VM does not survive
restarts — roles + database can disappear, and even the Postgres apt
package itself can vanish entirely (happened 17 Nov 2026). If `psql`
fails to connect / roles are gone, recover with this sequence:

```bash
# 1. If the cluster itself is gone (reinstall the package first if needed —
#    apt-get update is slow, ~10 minutes via the proxy):
DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-16
# 2. Start the cluster:
pg_ctlcluster 16 main start
# 3. Run setup again (recreates roles + DB + schema):
psql "dbname=postgres user=root host=/var/run/postgresql" -f infra/schema_core.sql
# 4. Check the tests are still green:
.venv/bin/python -m pytest tests -q
```

Fallback if the system cluster cannot be used: a temporary Postgres
via pgserver (cluster in `/tmp/pgdata-puspa-test`, start command in
`/tmp/pgserver-start.py`), then test with
`PUSPA_TEST_DSN="dbname=puspa_test user=root host=/tmp/pgdata-puspa-test"`.
Full details are in the header comments of `infra/schema_core.sql`.

Cross-user isolation is enforced by Postgres (Row Level Security) on
the users/sessions/vault tables: the app sets the current user per
transaction via `puspa.accounts.rls.as_user(conn, user_id)` — see
`puspa/accounts/schema.sql` for the policies and the no-FORCE decision,
and `tests/test_rls.py` for behavioural evidence. The `agents` table
(agent registry -> owner) is NOT created by `schema_core.sql` — it is
created on demand by `puspa.accounts.access.create_agent_schema()`.

## Attack-test results (definition 2.6)

8/8 paths failed to break through — see `tests/test_attacks.py` + `tests/test_access.py`:
unsigned skill, tool outside the manifest, web/email content injection,
reading another agent's memory, listing/email injection ordering send/pay
(executor 0 times), subagent outside its token, content changed after
approval, user A reading user B's data/connectors/events via the API
(rejected fail-closed). No critical findings.

## Terms (used in this README)

- **Puspa** — an open-source personal AI agent for Indonesia (the product
  this repo builds). **MVP** — Minimum Viable Product, the first version
  tested with users (revision 7 definition).
- **Ledger** — the append-only Postgres table `events` that records
  everything that happens, with a per-task hash chain (revision 7
  definition §2.3).
- **Quarantined reader** — a subagent that may only read outside data
  through a strict schema and marks suspicious content as DATA
  (revision 7 definition §2.6).
- **Permission token** — a signed permission (Ed25519) limiting what one
  agent turn may do: tools, steps, model tokens, and spending limit.
  **Gate** — the only path for outbound actions (send, pay); without a
  permission token + approval, the action is rejected.
- **Ed25519** — the digital-signature algorithm for permission tokens and
  skills. **Argon2id** — the password-hashing algorithm for login.
  **Fernet** — symmetric encryption for the per-user vault. **RLS**
  (Row Level Security) — enforcement of cross-user isolation inside
  Postgres.
- **DSN** — Data Source Name, a database connection address (here Postgres).
  **HKUDS** — Hong Kong University Data Science Lab, the makers of nanobot.
  **Chassis** — the agent framework reused (nanobot) and wrapped in
  Puspa's security layers; not a fork, none of its code changed.
