# Feature flags and canary releases (feature flags and canary releases, plan section 9.6)

One central flags file, one beta profile, one canary procedure, one
rollback command, and one migration shape. This document is the
operator's copy; the code is the enforcement (`puspa/feature_flags.py`
plus the gate at each surface's entry point).

## 1. The flags

All flags live in `puspa/feature_flags.py` — one source of truth.
Before feature flags and canary releases the only flag lived inside
`puspa/marketplace/discovery.py` (the skills.sh install flag from
skill revocation, hashed dependencies, secrets and git history, plan 6.7); that flag now lives here too and the discovery
module delegates to it, so there is exactly one place that decides
what is on. A flag is read on every call (nothing is cached), so
an operator or a test can flip one without a restart.

| Flag | Env var | Default | Beta | Off blocks | Off leaves alone |
|---|---|---|---|---|---|
| `marketplace` | `PUSPA_FLAG_MARKETPLACE` | on | off | Importing a skill folder, installing/enabling a skill | Listing entries, reading an entry, rolling an install back |
| `discovery` | `PUSPA_FLAG_DISCOVERY` | on | off | Searching the skills.sh registry (live or cached — the cache path calls the same gated function, so a disabled surface serves no cached result either) | Nothing else: discovery is search-only |
| `workflow` | `PUSPA_FLAG_WORKFLOW` | on | off | Creating, composing, activating, running, ticking (the scheduler pass), executing an approved action | Reads (get/list), dry runs (simulation only), pausing, deleting |
| `teams` | `PUSPA_FLAG_TEAMS` | on | off | Creating a team, adding a member, issuing or accepting an invitation | Reads, role changes, removing a member, revoking an invitation, deleting a team |
| `payment_execution` | `PUSPA_FLAG_PAYMENT_EXECUTION` | on | off | Billing checkout (`POST /billing/checkout`), the payment execution endpoint (`POST /api/payments/{id}/execute`) | Preparing a payment, quoting, catalog, overview, invoices, marking paid by the user |
| `skill_install` | `PUSPA_SKILL_INSTALL_ENABLED` | off | off | Installing a skill discovered via skills.sh | Searching the registry, installing built-in skills and the self-signed allowlist |

Rules that hold for every flag:

- **Off means unreachable, not hidden.** The gate sits at the
  entry point, first statement, before any database write,
  network call, or provider call. The gated function raises
  `FeatureDisabledError` (or, for the plain-function billing
  routes, returns a 403 `feature_disabled` response). A surface
  that is off cannot be reached by calling around a menu.
- **Off never blocks reads or cleanup.** Listing, getting, dry
  runs, revoking, deleting, and rolling back stay available, so
  turning a surface off cannot trap a user with data they cannot
  remove.
- **A flag is on only when it is said on, plainly.**
  `1` / `true` / `yes` / `on` (case-insensitive) count as on;
  anything else — including an empty value — counts as off.
- **Turning a surface back on is an explicit operator act** —
  set its variable to `1`. Never edit code to re-enable a beta
  surface.
- **Flag-on never enables what does not exist.** Payment
  execution has no executor anywhere in the codebase
  (`puspa/payments/prepare.py` always refuses); turning
  `payment_execution` on only removes the flag layer, the
  always-refuse design still applies.

Resolution order for one flag: an explicit per-flag variable
wins; otherwise the profile (`PUSPA_PROFILE`) applies; otherwise
the built-in default applies. An unknown profile name is
rejected loudly instead of silently behaving like the default.

## 2. The beta profile

The limited beta runs with the frozen surfaces off
(`config/beta.env` — source it, or pass it to the container
with `--env-file`):

```sh
PUSPA_PROFILE=beta
PUSPA_FLAG_MARKETPLACE=0
PUSPA_FLAG_DISCOVERY=0
PUSPA_FLAG_WORKFLOW=0
PUSPA_FLAG_TEAMS=0
PUSPA_FLAG_PAYMENT_EXECUTION=0
PUSPA_SKILL_INSTALL_ENABLED=0
```

The same values live in code as
`puspa.feature_flags.BETA_DEFAULTS` and apply whenever
`PUSPA_PROFILE=beta`, even without the env file; the file spells
them out so the deployed profile is auditable without reading
code. A per-flag variable overrides the profile, so re-enabling
one surface for one operator is one line, not a rebuild.

## 3. Canary release (one container first)

Every release rolls out canary-first: one container runs the new
image while the rest stay on the old one, and the rollout only
continues when the canary proves itself. The nanobot pin bump
follows the same shape for the chassis pin alone
(`docs/nanobot-canary-rollback.md`); this section is the same
procedure for a whole PuspaBot release.

Terms: **old tag** — the pinned image tag in production before
the release; **new tag** — the pinned image tag being released;
**canary** — one production container running the new tag; **fleet**
— every other production container, still on the old tag.

### 3.1 Before the canary starts

1. The release is built and tagged with a pinned tag — a git sha
   (`puspa:81016e3a`) or a release tag (`puspa:v0.1.0-beta`),
   never `latest` or `local`. The full suite is green on that
   tag.
2. Write down the old tag and the new tag. The rollback in
   section 4 needs the old tag exactly; do not rely on
   remembering it.
3. Confirm a fresh backup exists (the backup drill in
   `docs/backup-restore-drills.md` is the reference). A release
   rollback does not need a data restore, but a rollout never
   starts without a known-good backup.
4. Confirm the migrations in the release are expand-then-contract
   (section 5). If a migration drops a column or a table in its
   `up` section, the release cannot roll back safely — stop here.

### 3.2 Start the canary

1. Start exactly one container from the new image, beside the
   fleet — do not replace a fleet container with it:

   ```sh
   PUSPA_IMAGE_TAG=puspa:<new-tag> docker compose \
     -f docker-compose.yml -f docker-compose.canary.yml up -d gateway-canary
   ```

   The canary gets its own home volume and its own loopback
   port (8002); it never shares the fleet's `state.db`.
2. Route real turns to the canary from the operator account
   only (about 5% of new turns when traffic splitting is
   available, else the operator account alone). The canary must
   serve real turns: a canary that only answers health checks
   proves nothing.

### 3.3 Watch the canary (minimum 24 hours)

Check each row, for the canary only, against the same window on
the fleet:

| Signal | Where to look | Pass bar |
|---|---|---|
| Turn errors | Turn loop failures, hook exceptions | No error class that is new to the canary |
| Ledger writes | Ledger task records for canary turns | No missing cycle or task records |
| Tool refusals | Ledger `tool_dipanggil` / `tool_selesai` denial reasons | No new denial reason on the canary |
| Flag behaviour | Disabled surfaces on the canary | Every frozen surface still refuses (a flag that silently turned on is a failure) |
| Latency | Turn completion time | No sustained regression vs the fleet |
| Cost per task | Billing usage rows | No sustained increase vs the fleet |

Any row that fails is a canary failure, not a tuning note. Do
not "watch it a bit longer" past 24 hours without a written
reason: a canary that needs more time is a canary that has not
passed.

### 3.4 Promote or roll back

- **All rows pass for 24 hours**: roll the new tag out to the
  fleet one container at a time, watching the same signals
  after each container. When the last fleet container is on
  the new tag, stop the canary container and record the
  release (tag, date, canary window) in the release notes.
- **Any row fails**: roll back immediately (section 4). Do not
  try to fix the release while the canary is serving traffic.

## 4. Rollback (one command)

Rollback is the release in reverse: the previous tag is already
known, already tested, and already recorded. It takes one
command, no new code, and no data migration — because the
migrations are expand-then-contract (section 5), the old image
still runs against the current schema.

```sh
# From the repo root. Dry-run first: it validates the tag and
# shows what would change, and changes nothing.
python scripts/rollback.py --to-tag puspa:<old-tag>

# Apply: rewrite the compose file's puspa: image lines to the
# old tag, then restart the stack.
python scripts/rollback.py --to-tag puspa:<old-tag> --apply
docker compose up -d

# Or apply and restart in one step (needs Docker):
python scripts/rollback.py --to-tag puspa:<old-tag> --apply --run
```

The script refuses unpinned tags (`latest`, `local`, anything
that is not a git sha or a `v` release tag) and refuses to run
when the compose file has no `puspa:` image line to rewrite.
If the fleet was already partly promoted, the same command
moves those containers back: the compose file now points at
the old tag, so `docker compose up -d` replaces the promoted
containers with it.

What rollback is not:

- It is not a data restore: the release touches no stored data
  in a way the old image cannot read (section 5), so rolling
  back the image needs no restore. If stored data is itself the
  problem, that is a different incident — use the backup
  restore runbook, not this procedure.
- It is not a fix: do not re-attempt the same new tag until the
  failure is understood. File the issue or the fix first, and
  let the next release carry it.

## 5. Migrations: expand, then contract

Old and new versions must be able to run together during a
rollback, so every schema change ships in three phases, never
one destructive step:

1. **Expand.** The migration only *adds*: a new nullable column,
   a new table, a new index. Old code ignores the new objects;
   new code writes both the old and the new shape when both
   exist. Nothing is dropped, renamed, or made non-nullable
   here.
2. **Deploy both versions.** The new version rolls out (canary
   first, section 3) while the old version is still able to
   run — because nothing the old version needs was removed.
   Rollback during this phase is just the image rollback in
   section 4.
3. **Contract.** Only after the old version is gone everywhere
   — a later release, never the same one — a second migration
   removes what the old version needed: the old column, the old
   table. The contract migration's `down` section restores it.

Rules the runner already enforces (`docs/migrations.md`): one
transaction per migration, a recorded migration is never
re-applied, a `down` section marked `cannot be reversed` refuses
to roll back, and an applied version with no file on disk fails
loudly. feature flags and canary releases adds the shape rule on top: **no `up` section may
drop a column or a table.** `tests/test_feature_flags.py`
checks every migration file in `puspa/migrations/versions/`
for that — a release whose migration drops something in `up`
fails the suite before it can ship. A contract migration is
the one exception, and it is only allowed as its own numbered
migration in a later release, after the old version is gone.

Current state (2026-11-20): no migration's `up` section drops
a column or a table — the whole set is expand-only, so any
release built on the current schema can roll back by image
alone. That is verified by the test, not asserted by hand.

## Honest limits

- This procedure has never been run against a real container
  host: there is no production fleet yet, the canary has never
  been started, and `scripts/rollback.py --run` has never
  restarted a real stack. The signals table names real ledger
  events and real surfaces, but the 5% traffic split and the
  24-hour window are choices to validate on the first real
  rollout, not measurements from one.
- Docker containers cannot run on the dev VM (the kernel
  rejects the namespace calls), so the canary override file
  and the rollback script are checked offline only — by
  parsing the compose files and by unit tests, never by
  starting a container.
- The container host itself (the Contabo VPS decision) is
  still open, and the beta has no users yet, so the "operator
  account" traffic in section 3.2 is one person until the
  beta starts.
