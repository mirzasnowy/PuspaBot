# Split Design: Public `puspa-ai` vs Private `puspa-cloud`

- Status: design only. No files moved, no repo created, no code pushed.
- Decided by Nero, 21 Nov 2026 ~05:25 WIB: split the single public repo into two repos.
  Public repo (`mirzasnowy/puspa-ai`): everything a user can self-host.
  Private repo (`puspa-cloud`, name approved 21 Nov ~05:37 WIB): everything only Nero runs.
- The demarcation decision (recorded 21 Nov 2026 ~05:48 WIB) — the public/private
  demarcation principle: PRIVATE is business logic and infra, i.e. anything only
  Nero himself manages; PUBLIC is everything else (the product a user runs
  themselves). public/private demarcation principle governs the whole split, including ambiguous modules.
- Correction, Nero 21 Nov 2026 ~05:29 WIB (overrides the earlier sandbox straddle):
  the ENTIRE sandbox goes private. The public repo gets only a minimal local
  runner instead: run code directly on the user's own machine, with a
  permission prompt first (the Claude Code model). No sandbox machinery in public.
- Correction, Nero 21 Nov 2026 ~05:44 WIB (supersedes the 05:29 runner-only
  plan): public gets a sandbox TOO — a public-only sandbox for single-user
  local use, so self-host users get safe code execution. It is a SEPARATE
  implementation, written fresh, and must contain ZERO private sandbox
  machinery: no private files, no private code copied over, nothing in the
  public history. The private sandbox (multi-tenant isolation, seccomp
  profiles, warmpool, cross-tenant quotas, egress controls for shared infra)
  stays entirely private. Decision (coordinator): `puspa/localexec/` GROWS
  into the public sandbox (see 2.3) — there is NO public module named
  `sandbox`, so the no-leak rule is verifiable by path and by symbol. NO-LEAK
  RULE (absolute): after the scrub, grep the public repo and its whole
  rewritten history for any private sandbox symbol/path and confirm zero
  hits before pushing.
- Correction, Nero 21 Nov 2026 ~05:45 WIB (same pattern as the sandbox):
  channels ALSO go public. A self-host user runs their own Telegram /
  WhatsApp bot locally with their own tokens, so the public repo needs the
  channel transports too. Split like the sandbox: PUBLIC gets the local
  channel transports (the `puspa/channels/` pipeline as it exists today —
  it is already single-profile shaped: one runner per profile, tokens from
  env, polling, JSON heartbeat file, no tenant concept anywhere in the
  package, verified by reading every file); PRIVATE keeps the hosted
  channel relay (the server running bots for many users, holding their
  tokens, routing per tenant — new code, built in the private repo against
  the public transports). No private code leaks into public: the public
  transports must be public-only code. File split: all of
  `puspa/channels/` stays public EXCEPT `schema.py` (the hosted-DB schema
  entry point: top-level `psycopg` + `resolve_dsn` from private
  `accounts.users`, loads Postgres migration 0008 — private); `ledger.py`
  stays public whole, its Postgres variant rewired to resolve its DSN
  through the `DsnResolver` port at extraction (its private import is
  already lazy, inside the method, and the runner uses the in-memory
  ledger). `docs/channels.md` stays public (local transports); the relay
  gets a short private doc.
- Base: `main` HEAD `34cd79d7`, release tag `v0.1.0-beta`, 216 commits on `main`.
- History scrub ORDERED by Nero, 21 Nov 2026 ~05:31 WIB: the public repo's
  entire published history is rebuilt without the private paths (see
  History Scrub). The split alone would only stop future exposure; the scrub
  removes the private code from history too. Backups are done and verified
  readable (see History Scrub): `hidden_files/backups/backup-main-34cd79d7.zip`
  (GitHub zipball of `main`, 4,761,131 bytes, 1,312 entries) and
  `hidden_files/backups/backup-build-working-tree-2026-11-21.zip` (local working
  tree including the uncommitted CI fix, 28,736,137 bytes, 3,911 files).
- Contributor-facing cleanup ORDERED by Nero, 21 Nov ~07:56 WIB: the public repo must read like a normal best-practice open-source repo. (a) The scrub ALSO rewrites commit messages to plain English — strip `Wave N` and ticket codes from every subject/body. (b) Purely internal tracking docs (`status-maturity.md` + archives, `status-phase-*.md`, `status-brain-*.md`, `ticket-codes.md`) move OUT of the public repo (their home is Notion/private); remaining public docs are de-coded to plain descriptions. (c) Add `CODE_OF_CONDUCT.md`, `SECURITY.md`, `.github/ISSUE_TEMPLATE/` and `PULL_REQUEST_TEMPLATE.md`. Internal tracking (Notion, GitHub issues) keeps its codes. Verification: grep the scrubbed public repo + its whole rewritten history for Wave/ticket-code patterns = zero hits. The first scrub attempt (messages preserved, old path list) was stopped ~07:57 WIB, ~30 min in, no ref updated; the scrub restarts as ONE combined rewrite (tree filter + message polish).
- Distribution name LOCKED (Nero, 21 Nov ~08:41 WIB): the product is **PuspaBot** and the distribution name is **`puspa`** — `pip install puspa` from PyPI and `npm install -g puspa` / `npx puspa` from npm. `puspa` is free on both registries (404 verified on PyPI and npm, 21 Nov). Import name stays `puspa`. Fallback if `puspa` turns out taken at publish time (same name on BOTH registries, never split): `puspa-bot`, then `puspa-ai`, then `puspabot`. Name rules: PyPI normalizes names (case-insensitive, `-`/`_` / `.` equivalent); npm forbids uppercase in new package names — `puspa` is lowercase, so it is valid on both.
- Branch naming (Nero, 21 Nov ~08:51 WIB): branches follow `feat/` / `fix/` / `hotfix/` / `docs/` / `chore/` + a short plain-English description — no ticket codes (no G/K/B/E/P) and no Wave numbers in branch names. Applies to the split work and the K builds. The extraction branch is therefore `feat/extract-private-modules`, not `split/extraction`. puspa-ai currently has only `main`; puspa-cloud's two Dependabot branches (`dependabot/github_actions/peter-evans/create-pull-request-8`, `dependabot/pip/rich-15.0.0`) are left alone until after the private restructure, then reviewed/merged or closed — not touched during the restructure.
- New-repo checklist, STANDING RULE (Nero, 21 Nov ~08:58 WIB — a repo with only `main` is fatal, never again): every new repo/project has from day one: a `develop` branch; a tidy README; logo/social preview set; GitHub topics set; LICENSE, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY.md present; issue + PR templates present; commit subjects and branch names clean plain English (`feat/`/`fix/`/`hotfix/`/`docs/` + description, no ticket codes, no Wave numbers). The scrub must carry `develop` through: after the force-push, re-point `refs/heads/develop` to the new scrubbed HEAD and verify no ref in the public repo points at any pre-scrub commit.
- The pending CI fix (17 files, `fix(ci)` health-check / mypy / format) does
  NOT go first as a PR to the old history — that PR is now pointless. The
  17 files are re-applied as the first commit(s) on the REWRITTEN public
  history instead: 14 files onto rewritten public history, 3 files
  (`puspa/rooms/research.py`, `tests/test_sandbox_e03_seccomp.py`,
  `tests/test_feature_flags.py`) onto the private repo, which owns those
  paths. See Sequencing. (`test_feature_flags.py` tests the billing checkout
  flow — dry-run finding, 21 Nov.)
- Related: `docs/modes-decision.md` (one web codebase, two serve modes; the hosted
  backend was already named `puspa-cloud` there, "not built yet"). This design
  builds it. A short addendum to `modes-decision.md` is part of the work.
- How this document was built: HEAD tree (1,311 paths) read from the GitHub API,
  plus an import-graph grep over `puspa/` and `tests/` (who imports the
  private-side modules, and what the private-side modules import from public).
  Every module listed here was verified to exist in `build/`.

## Governing principle (Nero, 21 Nov 2026 ~05:45 WIB; recorded as public/private demarcation principle)

The demarcation rule for this split, and for any module the move list is
unclear about — apply this test, not gut feel:

- **PRIVATE = anything related to business logic and infra, i.e. anything
  only Nero himself manages.** Business logic: billing, credits,
  entitlements, trial, marketplace server, teams / paid tiers, payments.
  Infra only Nero operates: multi-tenant hosting, deploy, server backup,
  alarms, operator panel, shared-infra sandbox, hosted channel relay, push
  delivery.
- **PUBLIC = everything else: the product a single user runs themselves.**
  Agent core, PWA, CLI, connectors, skills / modules, price check, local
  sandbox, local channel transports.

Applied to the full move list, the rule confirms every disposition below;
the two corrections (sandbox 05:44, channels 05:45) already follow from
it. When a module is genuinely ambiguous, the tie-break is: if a single
user running the product alone on their own machine needs it, it is
public; if it only makes sense when Nero runs it for many users, it is
private. Record the reasoning next to the module when the call is made.

## Principles

1. The private repo NEVER copies public code. It takes the public repo as a
   package dependency (`pip`/`npm` install from the public repo, version pinned
   per tag) and imports it. The rule runs both ways: public code must not copy
   private code either (the new public local runner is written fresh, small,
   and must not be a copy of the private local backend).
2. The public repo stays installable and usable standalone. A user who installs
   only the public repo gets a complete single-user product: agent, PWA, CLI,
   connectors, skills, price check, personal workflows. Nothing in public
   imports anything private. CI enforces this (see CI).
3. The split is a pure move plus interface extraction. No behavior change, no
   feature work, no renames of user-visible behavior. Test cadence v2 (Nero,
   21 Nov ~06:34 WIB — see Sequencing): after each step run ONLY the
   affected modules' tests + neighbor tests (fast); the FULL central suite
   runs only twice (after Step 0, and final after everything is merged,
   before the PyPI publish). Extraction work happens on a branch; branches
   merge when their module+neighbor tests pass, and the final full suite is
   the gate before the PyPI publish. Each step is one
   commit (or one short series); rollback is reverting that commit.
4. Moved code is re-namespaced. The private repo must not ship a second `puspa`
   package (it would collide with the installed public `puspa` on `sys.path`).
   Moved modules live under `puspa_cloud.*` and their internal imports are
   rewritten mechanically (`from puspa.billing import ...` becomes
   `from puspa_cloud.billing import ...`). Imports of public modules
   (`from puspa.ledger import ...`) stay unchanged. No logic change in the
   rewrite.
5. The access boundary is decided here, up front, and recorded. The split
   plus the ordered history scrub (see History Scrub) removes private code
   from the public repo going forward AND from its published history. The
   lesson is recorded in `AGENTS.md`: the access boundary is a day-1 design
   decision, never a cleanup task.

## 1. The two repos

| | Public: `mirzasnowy/puspa-ai` | Private: `puspa-cloud` (name approved 21 Nov ~05:37 WIB) |
| --- | --- | --- |
| Visibility | Public | Private (Nero only) |
| Who runs it | Any user, on their own machine | Nero, on his VPS, serving many users |
| What it is | Single-user personal agent: agent core, PWA, CLI, connectors, skills, price check, personal workflows, minimal local runner | Multi-tenant hosted service: tenant hosting, billing/credits, payments, trial/entitlement, teams/sharing, marketplace server, channels, push delivery, server backup, alarms, operator panel, Docker deploy |
| Install | One `npm`/`pip` command, Docker-optional, SQLite file + `sqlite-vec`, no Postgres server locally | Docker + Postgres + RLS on the VPS; depends on the public package at a pinned tag |
| Python package | `puspa` (see Package Boundary) | `puspa_cloud` (new; never shadows `puspa`) |
| PWA | `apps/pwa` source lives here; the app is served by the local gateway and by the hosted server | Builds the PWA from public source at the pinned tag (Node stage in its Dockerfile); see Open Questions for the prebuilt-bundle alternative |
| License | Still undecided (license choice). The repo has NO LICENSE file today, which legally means all rights reserved. The split makes license choice urgent: a public repo without a license is not open in any usable sense. | Private; license choice is Nero's, same decision |

## 2. Package boundary

### 2.1 The public package (to be created; it does not exist yet)

Today `pyproject.toml` has NO `[project]` table: no distribution name, no
version, no console entry points. Entry today is ad hoc (`python -m puspa_cli`,
`puspa/pwa/app.py run()`). The split creates the package properly:

- Distribution name: `puspa` (locked 21 Nov ~08:41 WIB; free on both registries, 404 verified). Import name stays `puspa`.
- Version: from the release tag (single source of truth is the tag, e.g.
  `v0.1.0-beta`). The private repo pins that tag.
- Entry points to create (aligned with the install model Nero chose, the
  opencode shape: one command installs everything, the web UI opens in the
  browser, the terminal UI ships in the same package):
  - `puspa` — the CLI entry (`puspa web` starts the local gateway and opens
    the PWA; `puspa` alone starts the terminal interface when the TUI lands;
    TUI is still deferred, so the first version ships `puspa web`, `puspa serve`,
    and the one-shot CLI commands).
  - `puspa-cli` — alias for the existing `cli/puspa_cli` (moved into the
    public repo; see Move List — the CLI is local-only today and its bootstrap
    still references the old `build/fase-0` layout, which no longer exists).
- npm wrapper (acceptance criterion, Nero 21 Nov ~05:53 WIB): the public
  repo must be installable with ONE command from BOTH registries, no Docker,
  no manual Postgres: `pip install puspa` from PyPI AND
  `npm install -g puspa` / `npx puspa` from npm. The npm package
  `puspa` is a wrapper, not a second product: its `postinstall` installs
  the matching Python core from PyPI (`pip install puspa==<same version>`)
  and its `bin` (`puspa`) invokes the installed `puspa` entry point.
  Only prerequisite: Python 3.12+ with `pip` on the machine (documented in
  the README). The wrapper is a deliverable of Steps 1+6, not an
  afterthought: it is built, versioned, and verified (install from a local
  tarball + `npx` from a local path) in this split. Registry publishing
  itself needs Nero's PyPI + npm accounts; if either is missing, the
  wrapper is finished and verified locally and the publish step is reported
  as blocked on credentials — the wrapper is NEVER claimed working without
  a verified install.
- Dependencies stay in `requirements.txt` (pinned), including `nanobot-ai`
  from its git pin (`git+https://github.com/HKUDS/nanobot@e767174`, tracked in
  `puspa/chassis/pin.json`). Both repos use the same pin; the chassis-contract
  workflow and `nanobot-bump` stay public.

### 2.2 What the private repo may import from public

Allowed import surface (verified: these are the public modules the
private-side code actually uses today, plus the new ports):

- Core: `puspa.ledger`, `puspa.memory`, `puspa.retrieval`, `puspa.graphs`,
  `puspa.router`, `puspa.turns`, `puspa.sessions`, `puspa.runtime`,
  `puspa.workflow`, `puspa.scheduler`, `puspa.decider`, `puspa.intent`,
  `puspa.evidence`, `puspa.policy`, `puspa.feature_flags`, `puspa.migrations`
  (runner only — see below), `puspa.local_data`, `puspa.proactivity`,
  `puspa.guardrails`, `puspa.consent`, `puspa.pwa` (serve-mode config only),
  `puspa.harness` (eval harness), `puspa.accounts.vault` (local encrypted
  vault — the ONLY part of `puspa.accounts` public code may also use),
  `puspa.channels` (transports + pipeline — the private relay builds on
  them), `puspa.localexec` (the public sandbox + `ExecPort`).
- Ports (interfaces defined in public, implemented in private; see
  Interfaces): `ExecPort`, `BudgetPort`, `EntitlementPort`, `DsnResolver`,
  `SessionStore`, `BackupPort`, `PushPort`.
- Forbidden: anything under `puspa_cloud`, anything in the private repo's
  `docs/`, and any import of a moved module under its old `puspa.*` name.
  A CI check in the public repo greps for imports of private-side names
  (`puspa.billing`, `puspa.hosting`, `puspa.teams`, `puspa.payments`,
  `puspa.channels`, `puspa.push`, `puspa.backup`, `puspa.rooms`,
  `puspa.marketplace` server parts, `puspa.sandbox`, `puspa.computer`) and
  fails if any appear in public code.

### 2.3 The execution port (new, because the sandbox went entirely private)

The public core needs to run code (tool calls, readers, evidence checks, eval
harness). Today those call sites import `puspa.sandbox` / `puspa.computer`
directly (verified: `puspa/chassis/__init__.py`, `factory.py`,
`sandbox_tool.py`; `puspa/evidence/evidence.py`, `verifier.py`,
`eval_harness.py`; `puspa/pricing/readers.py`;
`puspa/harness/executor.py`, `loop.py`, `__init__.py`, `modes.py`;
`puspa/tools/builtin.py`; `puspa/router/complete.py`). After the split they
call the port instead (`puspa/quarantine/reader.py` mentions the sandbox only in a docstring — not a call site):

- `puspa/localexec/` (NEW, public): the PUBLIC SANDBOX for single-user
  local use. It grows out of the minimal local runner: `run(command, cwd,
  timeout)` — permission prompt first (approve / deny, per command or per
  session, the Claude Code model), then a direct subprocess run on the user's
  own machine, stdout/stderr captured, with single-user safety bounds: run
  only inside the user's workspace directory, a timeout, stdout/stderr size
  caps, and secrets stripped from the child process environment. What it
  NEVER contains (all private-sandbox machinery, stays private): seccomp
  profiles, warm pool, cross-tenant quotas, multi-tenant isolation,
  container backends, network egress rules for shared infra. Written fresh;
  it must not copy `puspa/computer/local.py` or anything under
  `puspa/sandbox/` or `puspa/computer/`. The public core calls it through
  `ExecPort`; the private repo binds its own sandbox behind the same port.
- `ExecPort` (NEW, public, in `puspa/localexec/`): the `Executor` protocol
  (`execute(command, cwd, timeout) -> result`). The public core calls the
  port. The local install binds the local runner. The private repo binds its
  sandbox behind the same protocol (private implementation, injected by the
  hosted server). The core never knows which one is bound.

## 3. Move list

Disposition codes: **PUB** = stays in the public repo. **PRIV** = moves to the
private repo (re-namespaced to `puspa_cloud.*`). **SPLIT** = the package is
divided file by file (details in Straddlers). **NEW** = created during the
split. Every path below was verified to exist in `build/` (local dir) or in
the HEAD tree on GitHub (1,311 paths) as noted.

### 3.1 `puspa/` subpackages

| Package | Disposition | Note |
| --- | --- | --- |
| `puspa/accounts/` | SPLIT | `vault.py` PUB (local encrypted vault; used by `connectors/token_apps.py`, `pwa/model_settings.py`, `auth/google_oauth.py`). `users.py`, `access.py`, `rls.py`, `user_sessions.py` PRIV (multi-user). Public code that needs them today (`resolve_dsn` in `evidence/outcomes.py`, `graphs/shared.py`, `pwa/model_settings.py`; `validate_session`/`create_session` in `pwa/app.py`, `auth/google_oauth.py`; `register_agent` in `runtime/agents.py`; `delete_user` in `consent/account.py`) goes through the `DsnResolver` / `SessionStore` ports instead (see Interfaces). |
| `puspa/auth/` | SPLIT | `email_link.py` PUB (local magic-link auth). `google_oauth.py` PUB as a client (standard OAuth flow) with hosted app credentials/config PRIV. Hosted account management PRIV. |
| `puspa/autonomy/` | PUB | Autonomy dial, interruption budget (interruption budget/autonomy dial). Core agent behavior. |
| `puspa/backup/` | SPLIT | `local_state.py` PUB (local export). `backup.py`, `wal.py`, `drill.py` PRIV (server WAL backup, archiving, restore drills, WAL backup and restore drills). `scheduler/scheduler.py` imports `create_backup` today — that call moves behind `BackupPort`. |
| `puspa/billing/` | SPLIT | `config.py` PUB (pricing is public data: Plus/Pro/Team prices). `fixture.py` PUB (test fixture only). `billing.py`, `client.py`, `contract.py`, `entitlements.py`, `insights.py`, `ledger.py`, `providers.py`, `routes.py`, `webhook.py` PRIV. `scheduler/curriculum.py` imports `task_budget_remaining` from `billing.ledger` today — that becomes the `BudgetPort`. `pwa/lite.py` imports `BillingConfig`/`default_config` (stays, public) and `billing.entitlements` (becomes the `EntitlementPort`). Four PWA API files import `billing.insights` today (`pwa/insights_api.py`, `chat_cards_api.py`, `controls_api.py`, `weekly_report.py`): they stay public and re-point through an insights port in Step 3 (public/private demarcation principle: showing a user their own data is product; the ledger behind it is private). |
| `puspa/cassette/` | PUB | Recorded provider responses (test/dev data). |
| `puspa/channels/` | SPLIT | Local transports PUB, hosted relay PRIV (Nero 05:45 correction; see Governing principle). The package is already single-profile shaped — one `GatewayRunner` per profile, tokens from env, Telegram polling, JSON heartbeat file, no tenant concept (verified by reading every file). PUB (13 files + `__init__.py`): `telegram.py`, `whatsapp.py`, `agent.py`, `authz.py`, `base.py`, `config.py`, `delivery.py`, `errors.py`, `events.py`, `heartbeat.py`, `inbound.py`, `ledger.py`, `notify.py`, `run.py`. `ledger.py` stays whole: its Postgres variant's private import is lazy (inside the method) and the runner uses the in-memory ledger; rewire its DSN through the `DsnResolver` port at extraction. PRIV: `schema.py` only (hosted-DB schema entry point: top-level `psycopg` + `resolve_dsn` from private `accounts.users`, loads Postgres migration 0008). The hosted relay (per-tenant runner orchestration, token custody from the hosted registry, per-tenant routing) is NEW code in the private repo, built against the public transports. Importers: `puspa/rooms/routing.py` (PRIV) and `puspa/nightly/probes.py` (PRIV) import public transports — fine. Tests: `test_channels_telegram.py`, `test_channels_whatsapp.py`, `test_channels_gateway.py` stay public; `test_channels_ledger.py` splits at extraction (in-memory tests public, Postgres/schema tests private with `schema.py`). |
| `puspa/chassis/` | PUB | Nanobot integration (hooks into official nanobot extension points). `nanobot-ai` stays a public dependency. Pre-existing repo-rule violation to fix during extraction: `puspa/chassis/__init__.py` docstring is in Indonesian — translate it. |
| `puspa/check_before_buy/` | PUB | Price-check wedge (local). |
| `puspa/computer/` | PRIV (whole) | Follows the sandbox correction: this is the per-bot sandbox (computer use) per-bot sandbox backend layer (`backends.py`, `broker.py`, `browser.py`, `config.py`, `docker.py`, `errors.py`, `local.py`, `scope.py`, `sync.py`). The public minimal runner (`puspa/localexec/`) is written fresh and must not copy `local.py`. The one file whose fate is a judgment call is `local.py` — it moves private with the rest; Nero can override (see Open Questions). |
| `puspa/connectors/` | PUB | Connectors + local vault use. Note: `connectors/__init__.py` imports `.accounts` (relative — that is `puspa/connectors/accounts.py`, not `puspa/accounts/`). No action. |
| `puspa/consent/` | PUB | Consent + data-consent logic (local). Its `account.py` imports `user_sessions`/`delete_user` — goes through `SessionStore` after the split. |
| `puspa/consistency/` | PUB | Postgres/SQLite consistency (Postgres and SQLite consistency rules: write-ahead, recovery, reconciliation). Already dual-engine aware; this is the layer the SQLite move builds on. |
| `puspa/credentials/` | PUB | Credential scopes for the local vault. |
| `puspa/decider/` | PUB | Decision engine (core). |
| `puspa/drafting/` | PUB | Co-drafting with context memory (co-drafting). |
| `puspa/eval/` | PUB | Eval harness, attack corpus, scorecard (attack corpus, benchmark, mutation and scorecard). Dev tooling; public. |
| `puspa/evidence/` | PUB | Evidence, outcomes, verifier. Its sandbox imports (`evidence.py`, `verifier.py`, `eval_harness.py`) re-point to `ExecPort`. Pre-existing Indonesian docstring in `__init__.py` — translate during extraction. |
| `puspa/feature_flags.py` | PUB | Feature flags + canary (feature flags and canary releases). |
| `puspa/graphs/` | PUB | Knowledge graph, shared graphs (second-brain graph view). |
| `puspa/guardrails/` | PUB | Programmable guardrails (input/output/execution rails). |
| `puspa/harness/` | PUB | Eval harness execution. Its `executor.py`/`loop.py` import `puspa.sandbox` and `puspa.computer` today — re-point to `ExecPort` / local runner. |
| `puspa/hosting/` | SPLIT | Nearly self-contained (imports only stdlib + `cryptography` — verified). `pairing.py` (PairingStore) PUB: the PWA pairs with a gateway in both modes, and `pwa/app.py` imports it. Everything else PRIV: `gateway.py`, `relay.py`, `link.py`, `loopback.py`, `identity.py`, `seal.py`, `service_allow.py`, `fallback.py`, `errors.py`. |
| `puspa/i18n/` | PUB | Indonesian server lines (internationalisation). |
| `puspa/intent/` | PUB | Intent engine (core). |
| `puspa/ledger/` | PUB | Append-only ledger, audit trail (core). |
| `puspa/local_data/` | PUB | Indonesian data layer (local Indonesian data layer: prices, sellers, payment rails). |
| `puspa/marketplace/` | SPLIT | `registry.py`, `discovery.py`, `manifest.py`, `authoring.py`, `store.py`, `trust.py`, `narrowing.py`, `learning.py` PUB (install/use modules locally; `nightly/probes.py` imports `MarketplaceRegistry`/`install_discovered` — the registry stays public so this keeps working). Server side PRIV: the marketplace service that lists, serves, and settles paid modules (new code in private; the current `share.py`/`errors.py` split by use — `share.py` audit during extraction). |
| `puspa/meetings/` | PUB | Meeting minutes, reminders. Imports `puspa.rooms` stores today (`RoomStore`, `RoomTaskStore`, `RoomMemoryStore`, `NotRoomMemberError` in `reminders.py`/`store.py`) — those come from the public rooms store layer after the rooms split (see Straddlers). |
| `puspa/memory/` | PUB | Episodic memory, embeddings, recall (core). pgvector use stays until the SQLite backend lands (separate workstream — see Sequencing). |
| `puspa/migrations/` | SPLIT | Runner (`runner.py`, `__main__.py`, `__init__.py`) PUB, engine-agnostic. Postgres versions (`versions/`, 27 migrations) PRIV — they are Postgres DDL for the hosted DB. SQLite versions PUB, new files created by the install-model workstream (not by this split). |
| `puspa/moat/` | PUB | Reliability score / false-report metrics (the product moat instrumentation). Not the culture package (the culture capability module and its culture package items are parked separately). |
| `puspa/mockup/` | PUB | Prompt-to-mockup (prompt-to-visual-mockup). |
| `puspa/modules/` | PUB | Custom modules (mini-apps). |
| `puspa/morning/` | PUB | Morning notes, prayer times (morning summary and notes-to-action). |
| `puspa/nightly/` | PRIV | Whole package (`probes.py`, `run.py`, `store.py`): nightly smoke of live hosted paths. Its imports are `channels` (public transports, public-bound since the 05:45 correction) + `marketplace` (server side private-bound). The `nightly-smoke.yml` workflow moves with it to private CI. |
| `puspa/observability/` | SPLIT | `metrics.py`, `tracing.py`, `logging.py`, `health.py` PUB — cross-cutting primitives imported by the whole core today (`router/`, `turns/`, `sandbox/` (PRIV after move), `scheduler/`, `retrieval/`, `evidence/`, `pwa/app.py`). `alarms.py` PRIV (hosted alerting). |
| `puspa/onboarding/` | PUB | First-run setup (local). |
| `puspa/payments/` | PRIV | Whole package (agent payments (prepare-only phase) prepare-only payments: prepare, verify, evidence; no execution). Importers today: `puspa/pwa/payments_api.py` (moves with it — see `puspa/pwa/`) and `puspa/feature_flags.py` (a docstring mention only). |
| `puspa/persona/` | PUB | Agent persona. Pre-existing Indonesian docstring — translate during extraction. |
| `puspa/plugins/` | PUB | Plugin boundary (local skills/plugins). |
| `puspa/policy/` | PUB | Policy, signing, sigstore verification. |
| `puspa/pricing/` | PUB | Price comparison (wedge). Its `readers.py` imports `puspa.sandbox` today — re-points to `ExecPort`. |
| `puspa/proactivity/` | PUB | Interruption budget, proactive engine (interruption budget). |
| `puspa/push/` | SPLIT | Subscription capture PUB as an interface (`save_subscription`, imported by `pwa/app.py` today): the PWA registers push locally through `PushPort`. Delivery (VAPID sending, `psycopg` use — verified) PRIV. |
| `puspa/pwa/` | PUB except one file | All PWA server code, API modules, Jinja lite mode (`lite.py`), serve modes. EXCEPT `payments_api.py` → PRIV with `puspa/payments/` (it is the hosted billing UI over `puspa.payments.prepare`; the PWA shows payment screens only in hosted mode). `serve_modes.py` stays PUB (its `hosting` mention is a docstring only, not an import — verified). `app.py` is the coupling hot spot: `hosting.pairing` (→ public `PairingStore`), `push.push.save_subscription` (→ `PushPort`), `rooms.RoomStore` (→ public rooms store layer), `observability.health`/`metrics` (→ public primitives), `accounts.user_sessions.validate_session` (→ `SessionStore`). |
| `puspa/quarantine/` | PUB | Prompt-injection guard + quarantined reader. `reader.py` mentions the sandbox only in a docstring (no import — nothing to re-point). Pre-existing Indonesian docstring — translate during extraction. |
| `puspa/receipts/` | PUB | Receipt photos / parsing (local). |
| `puspa/retrieval/` | PUB | Retrieval, HippoRAG, embedding health (core). |
| `puspa/rooms/` | SPLIT | Store layer PUB: `rooms.py`, `tasks.py`, `memory.py`, `errors.py`, `schema.py` (the data layer `meetings/` and `pwa/app.py` use: `RoomStore`, `RoomTaskStore`, `RoomMemoryStore`, `NotRoomMemberError`). Sharing/policy layer PRIV: `invites.py`, `delegation.py`, `routing.py`, `job_queue.py`, `money.py`, `research.py`, `splitting.py`, `puspa/pwa/rooms_api.py`, `puspa/pwa/rooms_membership_api.py` (corrected paths — the design named `rooms/rooms_api.py`, which does not exist in HEAD; `rooms_membership_api.py` is the same sharing layer). `rooms/` imports `puspa.accounts` (11×), `puspa.evidence`, `puspa.scheduler`, `puspa.ledger`, `puspa.channels` (3×), `puspa.pricing`, `puspa.policy`, `puspa.sandbox`, `puspa.pwa` — all of those imports live in the PRIV files except the store layer's own (audit during extraction; any store-layer import of a private module must be cut or inverted). |
| `puspa/router/` | PUB | Router, fallback, tiers (core). |
| `puspa/runtime/` | PUB | Agent runtime, agents, planner, handoff. `runtime/agents.py` imports `accounts.access.register_agent` — goes through the agent-registration port (part of `SessionStore`'s area; see Interfaces). |
| `puspa/sandbox/` | PRIV (whole, corrected) | ALL files, no exceptions: `__init__.py`, `allowlist.py`, `browser.py`, `egress.py`, `exec.py`, `filesystem.py`, `policy.py`, `quota.py`, `runner.py`, `runtime.py`, `seccomp.py`, `seccomp-default.json`, `seccomp-strict.json`, `session.py`, `terminal.py`, `warmpool.py`. This overrides the earlier plan (core public + Docker backend private). Public call sites re-point to `puspa/localexec/` + `ExecPort` (see 2.3). The sandbox imports `puspa.computer` (17×), `puspa.observability` (3×), `puspa.harness` (1×) — all satisfied inside private or from public. |
| `puspa/scheduler/` | PUB | Scheduler, curriculum runner. Its `backup` import (`create_backup`) becomes `BackupPort`; its `billing.ledger` import (`task_budget_remaining`) becomes `BudgetPort`. |
| `puspa/sentinel/` | PUB | Self-observability of the agent (self-observability (Sentinel and decision log): detectors, scanner, analytics). This is the agent watching itself, not hosted anti-abuse (that is operator-side and private). |
| `puspa/sessions/` | PUB | Session persistence (sessions and persistence (single-writer, full-text search, lineage)). Note: already SQLite per profile (`<home>/state.db`) — this is the existing local-storage precedent the SQLite move follows. |
| `puspa/subagents/` | PUB | Public subagent lifecycle (public subagent lifecycle). |
| `puspa/teams/` | PRIV | Whole package. Verified: NO importers outside itself (excluding tests) — clean move. Includes `deploy.py` (the offline Dockerfile validator — moves with the Dockerfile it validates). |
| `puspa/tools/` | PUB | Tool execution (builtin tools). Its sandbox/computer mentions re-point to `ExecPort`. |
| `puspa/turns/` | PUB | Interruptible turn loop (interruptible turn loop with layered fallback and budget). |
| `puspa/voice/` | PUB | Voice pipeline (transcription is a stub; no live ASR). |
| `puspa/workflow/` | PUB | Personal workflows. Its `billing.fixture` import is test-only (`DATA_TEST`). |
| `puspa/world/` | PUB | World model (brain phase). |
| `puspa/localexec/` | NEW PUB | The PUBLIC SANDBOX + `ExecPort` (see 2.3): permission prompt, workspace-bounded subprocess run, timeout, output caps, env scrubbing. Fresh code, zero private machinery. Created in Step 1. |
| `puspa/__init__.py`, `puspa/feature_flags.py` | PUB | Package root + flags. |

### 3.2 Everything else

| Path | Disposition | Note |
| --- | --- | --- |
| `apps/pwa/` | PUB | React 19 + Vite PWA (`package.json` name `puspa-pwa`, version `0.1.0`, `private: true` — the flag stays: the PWA is distributed as source/bundle, not published to npm). `dist/` and `node_modules/` are gitignored build output, never committed (verified: no `dist/` paths in HEAD). The private Dockerfile rebuilds it from public source at the pinned tag (its Node stage already does this). |
| `apps/pwa/templates/` | PUB | Jinja lite mode (WebUI lite, WebUI lite as a real mode). |
| `infra/sandbox.Dockerfile` | PRIV | The sandbox image definition — private with the sandbox. |
| `infra/pgbackrest.conf.example` | PRIV | Server WAL backup config (WAL backup and restore drills). |
| `infra/schema_snapshot.sql` | PRIV | Postgres schema snapshot for the hosted DB. (The public repo keeps its schema documentation in `docs/migrations.md` + the new SQLite migrations.) |
| `Dockerfile` | PRIV | Multi-tenant deploy image (multi-tenant hosting and teams): multi-stage build, s6-overlay, Node stage builds the PWA. Never built (see Honest Limits). Moves with `puspa/teams/deploy.py`, which validates it offline. |
| `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.canary.yml` | PRIV | Compose services `gateway`, `dashboard`, `puspa-home` (image `puspa:local`). |
| `.dockerignore` | PRIV | Serves the Dockerfile. |
| `scripts/vps-rebuild.sh` | PRIV | VPS rebuild/deploy tooling. |
| `scripts/affected.py`, `scripts/pr_lane.py` | PUB | PR-lane tooling for public CI. |
| `scripts/cla_check.py` | PUB | CLA check (public repo contributor flow). |
| `scripts/nanobot_bump.py`, `scripts/run_chassis_contract.py` | PUB | Nanobot dependency tooling (public dependency). |
| `scripts/setup_test_db.py`, `scripts/test_postgres_tmpfs.sh` | PUB | Public test-DB tooling (Postgres service in CI until the SQLite backend lands). |
| `scripts/rollback.py` | PUB | Canary rollback for the nanobot pin (see `docs/nanobot-canary-rollback.md`). |
| `tests/` (326 test files) | SPLIT by module | Each test file moves with the module it covers (same disposition as its module). 87 test files are private by the import rule (a test file is private iff it imports a private-side module, plus the explicit `test_sandbox*.py` / `test_computer_backends.py` filename rules and the 3 private helpers). Channel tests stay public except the Postgres/schema tests, which move with `schema.py`. Helpers `tests/marketplace_helpers.py`, `tests/rooms_helpers.py`, `tests/teams_helpers.py` → PRIV. `tests/conftest.py` splits: the public conftest keeps the Postgres service setup for now (see Sequencing — the test DB follows the SQLite workstream, not this split); the private conftest installs the public package at the pinned tag and sets up its own Postgres. `tests/fixtures/` — audit during extraction. |
| `tests/test_sandbox*.py`, `tests/test_computer_backends.py` | PRIV | They test the private sandbox/computer backends. The public CI sandbox lane is re-scoped (see CI). |
| `cli/` (local-only today, 0 paths in HEAD) | PUB (move in) | The CLI is NOT in the public repo yet (verified: `cli/` has 0 paths in HEAD; it sits in the local `NEVER_PUSH` list alongside `nanobot/`, `web/`, `backups/`). It belongs in public per the decision, so the split moves it in: `cli/puspa_cli/` + `cli/tests/` + `cli/README.md` + `cli/requirements.txt` PUB. Two fixes ride with the move: (a) the bootstrap still references the old `build/fase-0` layout, which no longer exists — rewrite it to import the installed `puspa` package; (b) the `puspa_cli/__init__.py` docstring is in Indonesian — translate it. `cli/assets/` (ASCII art, logo) PUB. `cli/tools/` (ascii art helpers) PUB. `cli/infra/setup_cli_db.sql` — audit (SQLite setup for the CLI; PUB if it is local-only). `cli/outbox/` and `cli/work/` — local run artifacts (dry-run outputs, notes, screenshots) — do NOT move; leave local-only. |
| `config/beta.env` | VERIFY | The only `config/` file in HEAD. Audit contents during extraction; if it holds hosted beta settings, it moves PRIV; if it holds local defaults, it stays PUB. (Not read while writing this design.) |
| `demo/` (4 paths in HEAD) | PUB | BYOK demo (`byok_provider` — the CLI imports it). |
| `cassettes/` (13 paths) | PUB | Recorded provider responses used by tests. |
| `eval-results/` (4 paths) | PUB | attack corpus, benchmark, mutation and scorecard data. |
| `skills/` (4 paths) | PUB | Skill format/examples (OpenClaw-compatible `SKILL.md`). |
| `docs/` (135 paths in HEAD) | SPLIT | Private docs move to the private repo's `docs/`: `agent-payments.md`, `backup-restore-drills.md`, `backup-restore-runbook.md`, `hosting-auth.md`, `rls-differential.md`, `rooms-b6.md`, `rooms-b9.md`, `rooms-b11.md`, `rooms-b12.md`, `teams.md`, `vps-validation.md`, `nightly-smoke.md`, `openapi-payments.yaml`, `openapi-rooms.yaml`, `openapi-rooms-membership.yaml`. `channels.md` stays PUB (it documents the local transports; the hosted relay gets a short private doc). `gateway.md` — verify (the gateway exists in both modes; split or keep public with a hosted section). Everything else stays PUB, including the legal docs (`terms-of-service.md`, `privacy-policy.md`, `data-consent.md`, `content-policy.md`, `legal.md`, `cla.md` — user-facing product docs already public; the private repo links to them), the status history (`status-maturity.md`, `status-maturity-archive-*.md`, `status-phase-*.md`, `status-brain-*.md`, and `ticket-codes.md` MOVE OUT — purely internal tracking; their home is Notion/private — supersedes the earlier "public record stays public" line), `kosmos-design.md` (brain design), `nanobot-*.md` (public dependency docs), `serve-modes.md` (gets the split addendum), and `docs/assets/` (logo). Remaining public docs are de-coded to plain descriptions (Step 0 follow-up commit; see Sequencing). Local-only docs (`docs/modes-decision.md`, `docs/tickets-beta-plan.md`) stay local-only — they are not in HEAD and this split does not change that. THIS document (`docs/split-public-private.md`) stays PUB and is de-coded to plain descriptions with the other kept docs. |
| `README.md`, `CONTRIBUTING.md`, `CLA.md`, `NOTICE.md`, `REUSE.md` | PUB | Public repo governance. The private repo gets its own `README.md` (short, operator-only) and does NOT need `CLA.md`/`CONTRIBUTING.md` initially (no external contributors — see Open Questions). `NOTICE.md`/`REUSE.md` (license discipline) stay public; the private repo references them. |
| `.github/workflows/ci.yml`, `cla.yml`, `chassis-contract.yml`, `nanobot-advisory-watch.yml`, `nanobot-bump.yml` | PUB | Public CI (see CI). |
| `.github/workflows/nightly-smoke.yml` | PRIV | Moves to private CI (it tests live hosted paths). |
| `.gitignore`, `.gitleaks.toml` | PUB | Kept; the private repo gets its own copies (`.gitignore` extended for its `dist/`/`.venv/`). |
| `pyproject.toml`, `pytest.ini`, `requirements.txt`, `requirements-dev.txt`, `requirements-hashed.txt` | PUB | Public tool config + deps. The `[project]` table is ADDED here (see Package Boundary). The private repo gets its own `pyproject.toml` / `pytest.ini` / `requirements.txt` (private deps + the pinned public package). |
| `backups/`, `web/`, `web-replica/`, `nanobot/`, `build_rename_map.py`, `rename.py`, `rename_map.json` | EXCLUDED | Local-only, 0 paths in HEAD. Not part of either repo. `nanobot/` is a read reference; the real dependency is `nanobot-ai` from git. `web/`/`web-replica/` are the landing-page working copies (landing stays separate and private, per `modes-decision.md`). |

### 3.3 What the private repo looks like (recommended layout)

```text
puspa-cloud/
  README.md                  # operator-only: what it is, how to deploy, who may see it
  pyproject.toml             # [project] name "puspa-cloud", private; ruff/mypy config
  pytest.ini
  requirements.txt           # private deps +: puspa @ git+https://github.com/mirzasnowy/puspa-ai@<tag>
  requirements-dev.txt
  puspa_cloud/
    __init__.py
    hosting/ billing/ payments/ teams/ rooms/ channel_relay/ push/ backup/
    observability_alarms/ sandbox/ computer/ marketplace_server/
    auth_hosted/ accounts_hosted/ nightly/ deploy/ entitlements/
    localexec_binding.py     # binds the private sandbox behind the public ExecPort
  Dockerfile                 # deploy image (moved from public)
  docker-compose.yml  docker-compose.dev.yml  docker-compose.canary.yml
  infra/sandbox.Dockerfile  infra/pgbackrest.conf.example
  scripts/vps-rebuild.sh
  .github/workflows/ci.yml   # minimal private CI (see CI)
  docs/                      # private docs moved from public docs/
  tests/                     # private tests moved from public tests/
```

## 4. Straddlers in detail

- **Sandbox — private sandbox private, public sandbox public.** The private
  sandbox is entirely private: all of `puspa/sandbox/` and all of
  `puspa/computer/` go private (Nero 21 Nov ~05:29 WIB), including seccomp
  profiles, warmpool, cross-tenant quotas, egress controls, and container
  backends. Public gets its OWN sandbox (Nero 21 Nov ~05:44 WIB): the
  `puspa/localexec/` public sandbox for single-user local use (see 2.3) —
  a separate implementation, written fresh, with zero private machinery in
  it. NO-LEAK RULE: after the scrub, grep the public repo and its whole
  rewritten history for any private sandbox symbol/path (including
  `puspa/sandbox`, `puspa/computer`, `seccomp`, `warmpool`, `puspa_cloud`)
  and confirm zero hits before pushing. Public call sites (`chassis/`,
  `evidence/`, `pricing/readers.py`, `harness/`,
  `tools/builtin.py`, `router/complete.py`) re-point to `ExecPort`.
  (`quarantine/reader.py` mentions the sandbox only in a docstring — not a call site.)
  The `tests/test_sandbox*.py` and `tests/test_computer_backends.py` suites
  move private with the code; the public sandbox gets its own new tests
  (`tests/test_localexec*.py`). The public CI sandbox lane is re-scoped to
  the public-sandbox tests + Chromium reader (see CI).
- **Channels — local transports public, hosted relay private.** The
  transports (`telegram.py`, `whatsapp.py`, adapters, inbound pipeline,
  delivery, ledger, `GatewayRunner`) are the product a single user runs
  with their own bot tokens — public. The hosted relay (per-tenant runner
  orchestration, token custody, per-tenant routing) is infra only Nero
  operates — private, built new against the public transports. Only
  `schema.py` moves private from the existing package (hosted-DB entry
  point); `ledger.py` stays public whole with its DSN rewired through the
  `DsnResolver` port. Same no-leak rule as the sandbox: the relay must not
  appear in public in any form.
- **Marketplace — install local, server private.** Registry, discovery,
  manifest, authoring, local store, trust, narrowing, learning stay public
  (a user installs and uses modules locally without any server). The server
  that lists, serves, and settles paid modules — including the 70/30 split
  accounting and the credit-only creator payout rule (creator support purchases (credit-only revenue share), never cash) — is
  private, built new in the private repo against the public registry.
- **Auth — local public, hosted private.** `email_link.py` (magic link) and
  the `google_oauth.py` client flow stay public. Hosted account management
  (user creation, hosted OAuth app credentials, hosted session policy) is
  private. The `SessionStore` port is the seam.
- **Accounts — vault public, users private.** See the `puspa/accounts/` row:
  `vault.py` is the local encrypted vault (public); `users.py`, `access.py`,
  `rls.py`, `user_sessions.py` are multi-user (private). The `DsnResolver`
  and `SessionStore` ports carry the public code's needs across.
- **Rooms — store public, sharing private.** The data layer stays public so
  `meetings/` and the PWA keep working locally; invites, delegation, routing,
  job queue, money splits, research, and the rooms API move private.
- **Billing — config public, everything else private.** Prices are public
  data; the ledger, entitlements, providers, webhook, and routes are the
  hosted money path. `BudgetPort` and `EntitlementPort` are the seams.
- **Backup — local export public, server backup private.** `local_state.py`
  stays; WAL archiving, `pgbackrest`, and restore drills move.
- **Observability — primitives public, alarms private.** Metrics, tracing,
  logging, and health are imported by the whole core; they stay. Alarms (the
  hosted alerting that pages the operator) move.
- **Hosting/pairing — pairing public, the rest private.** Pairing is how any
  PWA (local or hosted) attaches to a gateway. Relay, link, loopback,
  identity, seal, and the gateway itself are hosted-only.
- **Push — capture public, delivery private.** The PWA registers its
  subscription through `PushPort`; the server that sends pushes is private.

## 5. Interfaces the split needs

Each port is a small protocol/ABC defined in public, implemented in private
(or locally). No port may import anything private. Each port is one file,
plus its local implementation. The private implementation lives in
`puspa_cloud/` and is bound by the hosted server at startup; the local
implementation lives in public and is the default.

| Port | Public definition | Local implementation (public) | Private implementation |
| --- | --- | --- | --- |
| `ExecPort` | `puspa/localexec/` — `Executor.execute(command, cwd, timeout) -> result` | `puspa/localexec/` public sandbox — permission prompt, workspace-bounded subprocess, timeout, output caps, env scrubbing | Private sandbox bound by the hosted server (`puspa_cloud/localexec_binding.py`) |
| `BudgetPort` | `puspa/billing/` port module (new, public) — `task_budget_remaining(task) -> credits` | Unlimited / BYOK: the user pays their own provider directly, so the local budget is unbounded | `puspa_cloud/billing/ledger.py` (moved) — real credit ledger |
| `EntitlementPort` | `puspa/billing/` port module (new, public) — `current_tier()`, `is_pro()`, `trial_days_left()` | Free tier, always (local install has no trial clock) | `puspa_cloud/billing/entitlements.py` (moved) — trial + paid tiers |
| `DsnResolver` | `puspa/accounts/` port module (new, public) — `resolve_dsn(user_id) -> str` | Local SQLite file path (`<home>/state.db`) | Per-tenant Postgres DSN from the hosted user registry |
| `SessionStore` | `puspa/accounts/` port module (new, public) — `validate_session`, `create_session`, `list_sessions`, `register_agent`, `delete_user` | Single local operator session in SQLite | Multi-user sessions in Postgres (`puspa_cloud/accounts_hosted/`) |
| `BackupPort` | `puspa/backup/` port module (new, public) — `create_backup() -> path` | Local export to a file (`local_state.py`) | Server WAL backup + `pgbackrest` (`puspa_cloud/backup/`) |
| `PushPort` | `puspa/push/` port module (new, public) — `save_subscription(sub)` | Stored locally; nothing is sent (no server) | VAPID delivery (`puspa_cloud/push/`) |

Naming note: the port modules live next to the feature they abstract (e.g. the
`BudgetPort` lives in a new public `puspa/billing/ports.py`), so the public
code keeps importing from the same package path and only the implementation
changes. If a port grows beyond one file, it becomes its own public package.

## 6. History scrub (ordered by Nero, 21 Nov 2026 ~05:31 WIB)

The public repo's published history contains the private code (billing,
hosting, sandbox, payments, and the rest — 216 commits on `main`). Splitting
the working tree would only stop future exposure. The scrub rebuilds the
entire public history without the private paths.

### 6.1 What the scrub does

- Every commit on `main` is rebuilt with its tree filtered: all private paths
  (Section 3 move list, disposition PRIV) are removed from every tree,
  recursively, bottom-up. File modes are preserved. Author, committer, dates,
  and messages are preserved (messages are already code-free after the 20 Nov
  message rewrite).
- Messages are ALSO rewritten (Nero ~07:56 WIB): every subject/body is plain English — strip `Wave N` tokens and ticket codes (reuse the 20 Nov code-strip transform as a safety net). For commits whose subject becomes meaningless after stripping (the `docs: record Wave N` class), derive a plain subject from the commit's changed files + cleaned body (what the change actually did). Verify: grep ALL rewritten messages for `Wave\s+\d+` and ticket-code patterns = zero hits.
- Scope (dry-run verified, 21 Nov): HEAD tree is 1,215 blobs (the 1,311
  figure was blobs + 96 directories). The private-path rules remove 222
  blobs, leaving 993. Of 216 commits, 215 are kept and 1 is dropped
  (`4d3129a9`, all-private files). 27 signed commits lose their
  signatures in the rewrite (author/committer/dates/messages preserved).
  The move list is the authority; the dry-run mapping is
  `hidden_files/history-scrub-mapping.json`.
- Three generations of SHAs exist after this: Gen 1 (original, pre-20-Nov),
  Gen 2 (message-rewritten, current HEAD `34cd79d7`), Gen 3 (scrubbed). The
  scrub writes a new mapping file (`hidden_files/history-scrub-mapping.json`,
  Gen 2 → Gen 3). The 20 Nov mapping (`history-rewrite-mapping.json`,
  Gen 1 → Gen 2) is kept as history.
- The release tag `v0.1.0-beta` points at Gen 2 HEAD; after the rewrite it is
  re-created on the Gen 3 HEAD (force-update the tag).
- All 63 Notion `Commit` fields (which currently hold Gen 2 SHAs) are
  re-mapped to Gen 3 SHAs via the scrub mapping — same procedure as the 20 Nov
  rewrite. Any SHA cited in `docs/`, `MEMORY.md`, or daily logs is re-mapped
  where it refers to a rewritten commit, or marked dead where it refers to a
  dropped commit.

### 6.2 Technique (same as the 20 Nov rewrite, plus a tree filter)

The 20 Nov commit-message rewrite rebuilt 186 commits via the Git Data API
(script `hidden_files/history_rewrite.py`: fetch, transform, create rewritten
commits, verify, force-update `main` last, save the mapping; dry-run mode;
HEAD-moved guard). The scrub adapts that script with one addition: a tree
filter that drops private paths from every commit's tree before the commit is
re-created. The safety properties carry over: dry run first, the ref update is
the last step and only runs after every commit is created and the new HEAD
tree is verified to equal the old HEAD tree minus the private paths.
RESTART (21 Nov ~07:57 WIB): the first execute attempt (messages preserved, old path list) was stopped ~30 min in, no ref updated, after Nero ordered the message polish + internal-docs move-out. The scrub restarts as ONE combined rewrite: the tree filter (expanded list, incl. internal tracking docs) + the message transform, in a single pass, so there is one SHA generation, one Notion mapping, and one checkpoint 1.

### 6.3 Order of operations (do not reorder)

1. Verify the two backups are readable (done — see header; re-verify at
   scrub time).
2. Seed the private repo WITH the full Gen 2 history (the private side keeps
   everything, including the private code's history); only the public history
   is scrubbed. The GitHub Importer API is deprecated (404, verified 21 Nov),
   `gh` is not logged in on this VM, and the git protocol needs credentials
   this environment does not expose — so the seed is a Data-API identity
   rebuild: the same machinery as the scrub with an empty path filter,
   recreating every blob, tree, and commit in the private repo (nothing can
   be SHA-reused across repos). The 27 signed commits get new SHAs in the
   seed (signatures cannot survive a Data-API rebuild); the rest keep their
   Gen 2 SHAs. Save the seed mapping (`hidden_files/history-seed-mapping.json`)
   and verify the seeded HEAD tree equals the Gen 2 HEAD tree exactly, then
   re-create the annotated tag `v0.1.0-beta` on it. The private repo itself
   was already created (21 Nov, id 1465969116). SEED COMPLETED 21 Nov
   ~06:17 UTC: private HEAD `5f3bce89`, annotated tag `0dcdb9fb`, 216
   commits, HEAD tree verified equal to Gen 2 (1,215 blobs); 186/216 SHAs
   kept, 30 changed (28 signed commits lost signatures + 2 descendants).
   Mapping: `hidden_files/history-seed-mapping.json`.
3. Run the scrub script in dry-run mode. Verify: new HEAD tree equals old
   HEAD tree minus the private paths; the dropped-commit log is sane (only
   private-only commits); the mapping file is complete.
4. Run gitleaks over the scrubbed history before force-pushing (recommended
   sub-step: the full-history secret audit was offered 18 Nov and never
   answered; the scrub is the one moment a full-history scan is nearly free).
   Run the NO-LEAK grep over the scrubbed history before force-pushing:
   grep every rewritten commit tree for private sandbox paths/symbols
   (`puspa/sandbox`, `puspa/computer`, `seccomp`, `warmpool`, `puspa_cloud`)
   and confirm zero hits. A single hit blocks the push.
5. Force-push scrubbed history to public `main`; re-create tag `v0.1.0-beta`
   on Gen 3 HEAD. Before the force-push, close the 9 open Dependabot PRs
   (#1–#9) and delete their branches — they are auto-generated bumps and
   Dependabot recreates them against Gen 3; rebasing them by hand is
   pointless. No queue/work PRs are open (verified 21 Nov). Branch
   protection is not enabled (verified 21 Nov, 404) — re-check at push time.
5b. Carry `develop` through the scrub (CRITICAL — a `develop` left pointing at the old HEAD would keep the private files alive in the public repo and defeat the scrub): immediately after the force-push, re-point `refs/heads/develop` to the new scrubbed HEAD (same SHA as new `main`), then verify NO ref in the public repo points at any pre-scrub commit — list ALL refs (heads + tags) and check every SHA against the old mapping; every ref must point at a Gen-final commit. (The private repo's `develop` needs no re-pointing: it points at the seed HEAD, and the private repo keeps the full history by design.)
6. Re-apply the 17 CI-fix files as the first commit(s) on Gen 3 (see
   Sequencing Step 0): 14 files onto public, 3 files
   (`puspa/rooms/research.py`, `tests/test_sandbox_e03_seccomp.py`,
   `tests/test_feature_flags.py`) onto the private repo.
7. Re-map the 63 Notion `Commit` fields and cited SHAs (see 6.1).
8. Announce: after the force-push, every clone of the public repo must
   re-clone (old clones hold Gen 2 history). There are no known external
   contributors yet, so the blast radius is Nero + this workspace.

### 6.4 What the scrub does NOT do

- It does not change file contents at HEAD (except removing the private
  paths). The public HEAD tree after the scrub is the old HEAD tree minus
  private paths, nothing else.
- It does not scrub the private repo: the private repo keeps the full Gen 2
  history, private code included.
- It does not remove the private code from forks or clones made before the
  scrub. No forks are known; if one appears, it is out of scope.
- It does not fix the license choice. The scrubbed public repo still has NO
  LICENSE file until Nero chooses one.

## 7. Dependency mechanics (private depends on public)

- **Recommended first: pip install from a git URL, pinned per tag.** The
  private `requirements.txt` carries:
  `puspa @ git+https://github.com/mirzasnowy/puspa-ai@<tag>` (e.g.
  `@v0.1.0-beta`). Bumping the pin is a one-line change + a full private CI
  run. Zero new accounts, zero publishing ceremony, and the pin is exact (a
  tag, not a floating version). This matches how `nanobot-ai` is already
  consumed (`git+https://github.com/HKUDS/nanobot@e767174` in
  `requirements.txt`).
- **PyPI publish (later, if Nero wants it):** publish `puspa` to PyPI and
  depend on `puspa==<version>`. Costs: PyPI account + project name
  availability check + trusted publishing setup; gains: faster installs,
  version resolution, and a normal `pip install puspa` story for users.
  The public install-model work (one-command install) is what makes PyPI
  worth it; until that lands, the git URL is enough. See Open Questions.
- **PWA:** the private Dockerfile rebuilds the PWA from public source at the
  pinned tag (its Node stage already does this — `apps/pwa` is built and
  copied into the runtime stage). Rebuild-from-source is the recommended
  default: one version source (the tag), no artifact plumbing. The
  alternative — publishing the prebuilt bundle as a release artifact and
  having the private image copy it in — makes deploys faster but needs
  release automation; see Open Questions.
- **Version sync rule:** the private repo pins the latest public release tag
  and bumps within one working day of a new public release (a stale pin is a
  silent drift risk). A private CI job warns when public has a newer release
  tag than the pinned one (warn-only at first; fail later if Nero wants it).
- **No vendoring, no copying.** If private needs a public behavior changed,
  the change is made in public (new release tag), not patched in private.
  Emergency private-side workarounds are forbidden by this design: they
  become invisible forks.

## 8. CI

### 8.1 Public CI (keeps running, one lane re-scoped)

`.github/workflows/ci.yml` keeps its lanes: detect-changes, full suite (public
tests only, 4 workers, per-worker DBs), PR lane, checks (ratchets, docs
drift), supply chain (hashes, audit, SBOM, gitleaks), lint, format, typecheck,
and the aggregate `all-checks-pass` gate. Two changes:

- The sandbox lane is re-scoped: the deploy-image build and the image smoke
  test move to private CI (they build the private deploy image). What stays
  is a renamed "live" lane: the public-sandbox tests (`puspa/localexec/`) and
  the Chromium reader test (`tests/test_readers.py`) — the reader runs in the
  public PWA, not in the sandbox. The lane still runs on `ubuntu-24.04`
  (Docker available on the runner for Chromium, not for the product).
- A new guard job greps public code for imports of private-side names (see
  2.2) and fails the run if any appear. This is the enforcement for
  Principle 2.
- `cla.yml`, `chassis-contract.yml`, `nanobot-advisory-watch.yml`,
  `nanobot-bump.yml` stay public and unchanged.
- The public test DB stays Postgres (service container, health check in the
  QUOTED form from the CI fix) until the SQLite backend lands. The split
  does not change the test DB; the DB switch is the install-model workstream.

### 8.2 Private CI (minimal, new)

`.github/workflows/ci.yml` in `puspa-cloud`, same shape, smaller:

- Checkout private; set up Python 3.12; `pip install -r requirements.txt`
  (installs the public package at the pinned tag); `pip install -r
  requirements-dev.txt`.
- `ruff check`, `ruff format --check`, `mypy` over `puspa_cloud/` + `tests/`.
- `pytest` over the private tests, with a Postgres service container (health
  check in the QUOTED form — copy the fixed form from public `ci.yml`, do not
  reintroduce the unquoted bug).
- Docker build of the deploy image + the image smoke test
  (`tests/test_image_smoke.py`, moved private — it runs the image and checks
  `/app` serves the React bundle).
- Pin-freshness job: warn when public has a newer release tag than the
  pinned one.
- `nightly-smoke.yml` moves here (it tests live hosted paths).
- No CLA bot initially (no external contributors expected in a private repo;
  see Open Questions).

## 9. Sequencing

Each step is one commit (or one short series) and can be reverted independently. Do not start Step 1 until the scrub (Step 0) is fully verified, including the Notion re-mapping.

### Test cadence v2 (Nero, 21 Nov ~06:34 WIB — full suite only twice)

The full suite (~40 min per run) is the biggest time cost, so it runs only
twice in the whole split (v1 ran it three times; Nero cut the mid-extraction checkpoint). After each step, run ONLY the affected
modules' tests + neighbor tests (fast) — that is the per-step mitigation
for catching breakage early. The full central suite runs at exactly two
checkpoints:

1. **After Step 0** (scrub + CI-fix re-application) — the foundation check, KEPT on purpose: everything else builds on the scrubbed history, and a broken foundation (wrong tree, leaked private code, bad mapping) would poison all 7 later steps. The scrubbed tree CANNOT be green here: 44 public files still import a private module until Steps 2–5 re-point them (dry-run finding). The gate is that the failures match exactly the expected dangling-import surface (the dry-run's 44-file list) and nothing else — no surprise breakage.
2. **Final, after EVERYTHING is merged** (split extraction + the 4 brain-mechanism items) **and before the PyPI publish** — the publish gate. The publish happens only when it is green; if it is red, fix forward on `main` (or revert the offending merge) until green.

Extraction work (Steps 1–5) happens on ONE branch (`feat/extract-private-modules`,
branched off Gen 3 `main` after the CI-fix + LICENSE commits) and merges to
`main` via PR when its per-step module+neighbor tests pass — so `main` is never pushed
in an intermediate broken state by the extraction work. There is no mid-extraction full-suite gate in v2: breakage from the extraction is caught at the final checkpoint instead. (Step 0 itself is the one exception: the scrub rewrites `main` by force-push, and `main` is red from Step 0 until the extraction + brain-mechanism merges land and the final suite goes green — that window is unavoidable. The parallel brain-mechanism build (act-or-ask rule, three-speed simulator, outcome watcher, sleep consolidation and ontology review) merges onto `main` the same way, and the final full suite covers split + brain-mechanism items together.) The private repo's work lands directly on its `main` until its CI exists (Step 6); after that, normal branch + PR flow. What changes vs the old "tests green after each step" cadence is only WHEN a breakage is caught (at the final checkpoint instead of per step); the output — a fully tested, green final state — is identical.

- **Step 0 — Scrub + CI-fix re-application.** Section 6 order of operations.
  The 17 CI-fix files land here, not as a PR to old history: 14 files as the
  first commit(s) on Gen 3 public history (subject `fix(ci): quote Postgres
  health check and clear mypy and format failures`, 3-line body as proposed
  in the fix report), 3 files (`puspa/rooms/research.py`,
  `tests/test_sandbox_e03_seccomp.py`, `tests/test_feature_flags.py`) as the
  first commit(s) on the private repo. The `pyproject.toml` part of the fix (mypy override for `bandit`)
  lands on public; the `[project]` table is added in Step 1, not here.
  Verify: new HEAD tree equals old HEAD tree minus exactly the private
  paths; no-leak gate passes (code symbols zero hits, paths zero hits in
  every rewritten tree); CI-fix files applied (14 public + 3 private);
  LICENSE (Apache-2.0) committed on Gen 3; private repo exists with full
  Gen 2 history. Follow-up commits on the same branch: docs-content cleanup (remaining public docs de-coded to plain descriptions) + governance files (`CODE_OF_CONDUCT.md`, `SECURITY.md`, `.github/ISSUE_TEMPLATE/`, `PULL_REQUEST_TEMPLATE.md`). Final verification: grep the scrubbed public repo + its whole rewritten history for `Wave\s+\d+` and ticket-code patterns = zero hits. NOTE (dry-run finding): the scrubbed public tree will NOT
  pass the full suite right after Step 0 — 44 public files import a private
  module and need Steps 2–5 to re-point them. CHECKPOINT 1 runs the full
  suite here as a diagnostic baseline: the gate is that the failures match
  exactly the expected dangling-import surface (the dry-run's 44-file list)
  and nothing else. Final suite-green arrives at checkpoint 2 (after everything is merged), not
  Step 0; main will be red between Step 0 and the final green, and
  the parallel act-or-ask rule/three-speed simulator/outcome watcher/daily sleep consolidation and monthly ontology review build merges the same way, covered by the same final checkpoint.
- **Step 1 — Public package scaffolding.** Add the `[project]` table
  (`puspa`, version from tag, license Apache-2.0), console entry points
  (`puspa`, `puspa-cli`), and create `puspa/localexec/` as the PUBLIC
  SANDBOX (permission prompt + workspace-bounded subprocess + timeout +
  output caps + env scrubbing, with `ExecPort`). No behavior change yet;
  the sandbox is new code with its own tests. Verify: public suite green;
  `pip install -e .` works from a clean checkout.
- **Step 2 — Clean moves.** Move the packages with no public importers
  (verified): `puspa/teams/`, `puspa/nightly/`,
  `puspa/backup/` (wal/drill), `puspa/observability/alarms.py`,
  `puspa/hosting/` minus `pairing.py`. One commit per package (or per small
  group), re-namespaced to `puspa_cloud.*`, imports rewritten mechanically.
  Verify: public suite green after each move (the moved code's tests move
  with it and run in private CI once it exists; until then they run from the
  private working copy).
- **Step 3 — Interfaces.** Define the ports (`BudgetPort`, `EntitlementPort`,
  `DsnResolver`, `SessionStore`, `BackupPort`, `PushPort`) with local
  implementations, and re-point the public call sites (`pwa/app.py`,
  `pwa/lite.py`, `scheduler/`, `consent/account.py`, `runtime/agents.py`,
  `evidence/outcomes.py`, `graphs/shared.py`, `pwa/model_settings.py`).
  This is the fiddliest step; do one port at a time, suite green after each.
- **Step 4 — Sandbox + computer move.** Move all of `puspa/sandbox/` and
  `puspa/computer/` to private (re-namespaced), re-point the public call
  sites (`chassis/`, `evidence/`, `pricing/readers.py`,
  `harness/`, `tools/builtin.py`,
  `router/complete.py`) to `ExecPort`/local runner, move
  `tests/test_sandbox*.py` + `tests/test_computer_backends.py` private.
  Verify: public suite green; public-sandbox tests pass in public CI's
  re-scoped live lane.
- **Step 5 — Remaining splits.** `puspa/accounts/` (vault stays),
  `puspa/auth/`, `puspa/billing/`, `puspa/marketplace/` (server side built
  new in private), `puspa/rooms/` (store layer stays), `puspa/pwa/payments_api.py`,
  `puspa/channels/` (`schema.py` moves private; `ledger.py` DSN rewired
  through the `DsnResolver` port; the hosted relay is built new in the
  private repo against the public transports; channel tests split with
  their modules),
  `puspa/migrations/` (Postgres versions move; SQLite versions are separate
  work). Move the private docs and private tests with their modules.
  Merge the `feat/extract-private-modules` branch to `main` via PR when the per-step module+neighbor tests pass; the branch is deleted after merge. (No mid-extraction full-suite gate in cadence v2 — the final full suite covers everything.)
- **Step 6 — Wire the dependency.** Private `requirements.txt` pins the
  public tag; private CI goes green (lint, typecheck, tests, image build +
  smoke test). Move the CLI in (`cli/` → public repo, bootstrap fixed,
  docstrings translated) and the Dockerfile/compose/infra to private.
  Build the npm wrapper (`puspa` on npm: `postinstall` installs the
  matching `puspa` from PyPI, `bin` invokes `puspa`) and verify it:
  `npm install -g` from a local tarball + `npx puspa` from a local path
  both launch the product with no Docker and no manual Postgres. Publish
  `puspa` to PyPI (license Apache-2.0, decided 21 Nov ~05:38 WIB) and
  the wrapper to npm — both need Nero's registry accounts; if either is
  missing, report the publish as blocked on credentials, never as done.
  Verify: a clean private checkout installs, passes CI, and builds the
  deploy image; a clean machine installs the public product with one
  command from each registry. CHECKPOINT 2 (final): run the FULL central suite
  one final time after everything is merged (split extraction + the 4 brain-mechanism items) and before the PyPI publish — the publish gate. The publish
  happens only when it is green; if red, fix forward on `main` (or revert the offending merge) until green.
- **Step 7 — Deploy validation (needs a Docker host).** `docker build` +
  `docker compose up` + a real login on the VPS (VPS validation and machine-replacement drills territory). The
  Dockerfile has never been built anywhere; this step proves it. Do not
  claim the deploy works until this step is green on a real host.
- **Out of scope for the split:** the SQLite backend migration (install-model
  workstream — the public test DB stays Postgres until it lands), the TUI
  (still deferred), the license choice (Nero's decision), and the
  culture package (parked).

## 10. What does not change + honest limits

- Product behavior does not change. Same PWA screens, same API, same ledger
  format, same CLI commands. The split moves code; it adds no features.
- The modes decision stands: one PWA codebase, two serve modes. The split
  gives each mode its own repo; the PWA stays one codebase in public.
- The public repo's file content at HEAD does not change except for the
  removal of private paths (scrub) and the addition of the package
  scaffolding + local runner. The private repo keeps the full Gen 2 history.
- Honest limits (unchanged by the split): no live deployment, no test users,
  no live Telegram/WhatsApp bot (mock transport), no real-money payments
  (local gateway is a stub), the Docker image has never been built, CI on
  GitHub had 2 failed full runs at last check (the CI fix in Step 0 addresses
  the known causes: unquoted health check, 84 mypy errors, 1 unformatted
  file), the CLA bot has never run on a real PR, minimal legal documents (terms of service, privacy, data consent, content policy) legal docs are draft 0.1
  (not lawyer-reviewed), the repo still has NO LICENSE file (license choice pending).
- The scrub does not remove private code from forks or clones made before
  it. No forks are known.

## 11. Open questions for Nero

1. **PyPI publish vs git-URL dependency?** Recommendation: git URL pinned per
   tag first (zero setup, exact pin); PyPI later when the one-command install
   lands. If PyPI: the distribution name is `puspa` (verified free on PyPI and npm, 21 Nov — 404 on both).
2. **Private repo name?** Recommendation: `puspa-cloud` (the name already
   used in `modes-decision.md`).
3. **PWA: rebuild from source (recommended, current behavior) vs prebuilt
   bundle artifact?** Rebuild is one version source (the tag); prebuilt is
   faster deploys but needs release automation.
4. **CLA for the private repo?** No external contributors are expected; the
   recommendation is to skip the CLA bot there initially.
5. **`puspa/computer/local.py` fate?** Moot after the 05:44 correction:
  `local.py` stays private with the rest of `puspa/computer/`; the public
  sandbox is written fresh and must not use it as a seed. Override only if
  you want the public sandbox to reuse it (not recommended — the no-leak
  rule is cleaner with fresh code).
6. **CLI move fixes?** Moving `cli/` in requires fixing its bootstrap (it
   still references the old `build/fase-0` layout) and translating its
   Indonesian docstrings. Approved as part of the move?
7. **Who runs the scrub?** It needs force-push permission on the public repo
   (owner: Nero) and repo-creation permission for the private repo. The
   operator runs it with Nero's auth, or Nero runs the two privileged steps
   himself.
8. **License choice — now urgent?** The scrubbed public repo is the product
   users will install; without a LICENSE file it is legally all rights
   reserved. Does the license choice move up the queue, or stay parked?

## Appendix — verification notes

- HEAD tree read from the GitHub API: 1,311 paths; 216 commits on `main`.
  Private-path rules (Section 3) match 222 blobs of the 1,215-blob HEAD tree,
  leaving 993 public blobs (dry-run verified). 216 commits: 215 kept, 1
  dropped (`4d3129a9`).
- Import graph: `grep` over `puspa/` + `tests/` for
  `from puspa.(billing|hosting|teams|payments|channels|push|backup|
  observability|rooms|marketplace)` found 87 files (16 public-side `puspa/`
  files + tests). Per-file import lists are in the working notes, not in
  this doc.
- Verified by reading: `docs/modes-decision.md`; `pyproject.toml` (no
  `[project]` table); `apps/pwa/package.json` (`puspa-pwa` 0.1.0, `private:
  true`); `requirements.txt` (`nanobot-ai` git pin); `Dockerfile` (never
  built); `puspa/chassis/pin.json` (nanobot `e767174`); `puspa/computer/`
  (`docker.py` + `local.py` backends); `puspa/sessions/` (already SQLite per
  profile); `puspa/consistency/` (already Postgres/SQLite dual-engine).
- Backups verified readable (zip `testzip` clean): `hidden_files/backups/
  backup-main-34cd79d7.zip` (4,761,131 bytes, 1,312 entries),
  `hidden_files/backups/backup-build-working-tree-2026-11-21.zip`
  (28,736,137 bytes, 3,911 files).
- Known pre-existing repo-rule violations found while inventorying (fix
  during extraction, not blocking): Indonesian docstrings in
  `puspa/chassis/__init__.py`, `puspa/evidence/__init__.py`,
  `puspa/persona/__init__.py`, `puspa/quarantine/__init__.py`,
  `puspa/moat/__init__.py`, `cli/puspa_cli/__init__.py`.
- The 17 CI-fix files (verified present in `build/`): `.github/workflows/
  ci.yml`, `puspa/pwa/chat_cards.py`, `puspa/pwa/chat_widgets.py`,
  `puspa/pwa/chat_widgets_api.py`, `puspa/pwa/chat_cards_api.py`,
  `puspa/autonomy/controls.py`, `puspa/pwa/receipt_api.py`,
  `puspa/pwa/memory_api.py`, `puspa/rooms/research.py`,
  `puspa/morning/prayer.py`, `puspa/morning/notes.py`,
  `puspa/voice/pipeline.py`, `puspa/observability/logging.py`,
  `pyproject.toml`, `tests/test_feature_flags.py`, `tests/test_local_data.py`,
  `tests/test_sandbox_e03_seccomp.py`. Of these, 14 go to rewritten public
  history and 3 (`puspa/rooms/research.py`,
  `tests/test_sandbox_e03_seccomp.py`, `tests/test_feature_flags.py`) go to the private repo.
