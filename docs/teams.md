# Teams, invitations, visibility, entitlements, moves, deploy (G13)

Date: 18 Nov 2026. Package: `puspa/teams/`. Pattern sources:
DuskBot `server/cloud-team*.ts` + `server/team-*.ts` +
`server/cloud-entitlement.ts` + `server/bot-moves.ts` +
`server/move-freeze.ts` (PolyForm Noncommercial — study only);
OpenBot organisations, roles, org audit (MIT); hermes-agent
multiplex + hosted room + `Dockerfile` + `docker-compose.yml`
(MIT). All patterns were rewritten as Puspa Python code, never
copied verbatim. No new dependencies: everything runs on
`cryptography==50.0.2` (already pinned), `psycopg` (already
pinned), `PyYAML` (already pinned), and the standard library.

Google login (`puspa.auth`) stays in front. G11 (`puspa/hosting/`)
stays the server-to-server path behind it (loopback trust, pairing,
card seal, link/relay). This package is the team layer on top of
both: who belongs to a team, who may see what, whether a home may
run at all, and how a bot moves between workspaces.

## Schema: how to apply it

The migration is `infra/schema_teams.sql` (seven tables: `teams`,
`team_members`, `invitations`, `bot_access_grants`,
`bot_access_requests`, `entitlements`, `workspace_moves`). Apply it
AFTER `infra/schema_core.sql` (the `users` table must exist first),
as a Postgres superuser, from the `build/` directory:

```sh
psql "dbname=puspa_test user=root host=/var/run/postgresql" -f infra/schema_teams.sql
```

It is idempotent (`CREATE TABLE IF NOT EXISTS` + `DROP POLICY IF
EXISTS`), so re-running it is safe. The programmatic entry point is
`puspa.teams.create_schema()` (the test suite calls it; `conftest`
does NOT apply it — teams tables are created and truncated by the
team tests themselves, in `tests/teams_helpers.py`).

## 1. Teams (`teams.py`)

A team is its own entity, separate from billing (G2 owns money;
this module owns identity and membership). Two roles only:

- `owner` — the only role that can delete the team, add or remove
  members, and promote another member to owner. The creator becomes
  the first owner.
- `member` — can see the team and use what the team grants. A member
  cannot change any role, including their own (`set_role` from a
  member raises `NotTeamOwnerError`).

Invariants, enforced in code (never left to the caller):

- A team always has at least one owner. Demoting or removing the
  last owner raises `LastOwnerError`.
- `delete_team` is owner-only; members, invitations, grants,
  requests, entitlements, and moves cascade in the database
  (`ON DELETE CASCADE`).
- Re-adding an existing member with the same role is a no-op; with
  a different role it is a role change under the same owner-only
  rule.

RLS (`infra/schema_teams.sql`, same rules as
`puspa/accounts/schema.sql`, no `FORCE`): members see their team;
only owners update/delete it; `team_members` rows are visible to
the member themselves or to a team owner; membership INSERT/UPDATE
is owner-only; DELETE is owner-only or self-removal. Three
operations run as the table owner (the "privileged operation"
decision, same as user registration in accounts): team creation
(the first owner row cannot pass the owner-only INSERT policy yet),
invitation acceptance (the invitee has no membership row yet), and
entitlement issuance (the server holds the signing key). Everything
else runs as `puspa_app` under the policies.

## 2. Invitations (`invitations.py`)

`InvitationStore.issue_invitation(team_id, inviter_user_id,
invited_email)` — owner-only. Returns `{"invitation_id", "token",
"expires_at"}`; the token is returned exactly once, at issue time.
Only the SHA-256 hash is stored (same pattern as G11 pairing:
a database leak yields no usable invitation).

- TTL is 7 days (`INVITATION_TTL_DAYS`). An expired token is marked
  `expired` on first sight and can never be accepted afterwards.
  `purge_expired()` marks all overdue pending invitations (safe to
  run on a schedule).
- Single use: accepting consumes the invitation in the same
  transaction that inserts the membership.
- Idempotent acceptance: accepting the same token twice (same user,
  or a user who is already a member) still leaves exactly one
  membership row. The second accept returns `already_member=True`
  instead of failing — a double-click or a retried request must not
  error. A *different* user presenting a consumed token is refused.
- Revocation: `revoke_invitation(invitation_id, revoked_by=...)`
  is owner-only; a revoked token is dead immediately.

## 3. Visibility (`visibility.py`)

Visibility is a function of (thread, member), never a global flag.

```python
can_see_thread(user_id=..., team_ids=[...], thread=ThreadRef(...))
```

- A personal thread (`team_id` None) is visible only to its owner.
- A team thread is visible to every member of that team, and to
  nobody else.
- There is no "visible to everyone" case. Call
  `require_thread_visible(...)` BEFORE reading thread content —
  the same check-first order as `require_owner` in
  `puspa/accounts/access.py`.

Puspa has no threads table yet, so `ThreadRef` (`thread_id`,
`owner_user_id`, `team_id`) is the record shape the future table
must carry. The gate is deliberately decidable from the membership
list alone, without reading thread content.

Bot access (`BotAccessGate`): team membership alone is NOT access.
The bot owner always has access; a team member needs an explicit
grant row for (bot_id, user_id). A member without access may open a
request (`request_access` — one pending request per (bot,
requester), a duplicate returns the existing one); a team owner —
or the bot owner, when `bot_owner_lookup` is wired — approves or
denies. Approving inserts the grant and marks the request approved
in one transaction; approving an already-approved request is a
no-op. Decisions are final: denying a decided request raises
`BotAccessError`; a new request must be opened instead. Requests
are recorded durably in `bot_access_requests` (who asked, who
decided, when) — that table is the audit trail for this iteration.

## 4. Entitlements (`entitlements.py`)

A cloud home only runs with a signed entitlement for one
machine-id + team + tier + validity window. The token is
`v1.<payload>.<signature>` (base64url, no padding); the payload is
canonical JSON (`sort_keys`, compact separators) with `v`,
`entitlement_id`, `machine_id`, `team_id`, `tier`, `issued_at`,
`valid_until`; the signature covers the exact payload bytes.

Why Ed25519, not the HMAC of G2's billing entitlements: a billing
entitlement is verified by the server that holds the HMAC secret.
A home entitlement is verified by the home itself, which must NOT
hold the server secret. So the entitlement is signed with the
server's Ed25519 private key and verified against the public key
pinned in the release — the same pinning pattern as the G11
identity key (`puspa/hosting/identity.py`): the fingerprint is
SHA-256 of the raw 32-byte public key.

Tiers are the G2 billing tier ids (`free` / `plus` / `pro` /
`team`) plus `admin`: an `admin` entitlement is the signed admin
override — it answers the liveness probe and the admin path, but it
is still an entitlement (it expires, it can be revoked). An unknown
tier is invalid, fail-closed.

`HomeGate(public_key, dsn=None).decide(machine_id, token)` is the
one call a home makes at startup:

- valid entitlement for THIS machine-id → allowed
  (`via="entitlement"`);
- valid `admin` entitlement → allowed as admin (`via="admin"`);
- anything else → denied with the exact reason (`via="denied"`):
  no token, malformed token, bad signature, unknown tier, expired
  (`entitlement expired on ...`), revoked, or an entitlement for
  another machine-id. The reason is printed, never swallowed —
  an expired or tampered entitlement must be diagnosable, not
  silent. Without a valid entitlement, only a signed admin
  entitlement and the liveness probe are answered: the probe is
  the `GET /health` endpoint on the G11 service allowlist, not
  this gate.

`issue_entitlement(..., dsn=None)` records the entitlement in the
`entitlements` table when a DSN is given, so it can be revoked
later (`revoke_entitlement`); without a DSN it is token-only and
revocation cannot be checked (verification says so in its reason
only when the token is otherwise valid — the check is skipped,
not faked).

## 5. Moves (`moves.py`)

`MoveCoordinator` moves a bot (or its threads) from one
workspace/home to another. Statuses: `pending` → `in_progress` →
`done`; `pending|in_progress` → `failed`; `failed` →
`in_progress` (resume); `pending|in_progress|failed` →
`cancelled`. Anything else raises `MoveError`.

Move-freeze: while a move is active, the bot's automation is
frozen — no task, scheduler run, or cron job may start for that
bot. The freeze is derived from the move records (`is_frozen`
reads `workspace_moves` for a status in `pending`, `in_progress`,
`failed`), never from a separate flag that could drift. The
enforcement point is `require_not_frozen(bot_id)`, which raises
`MoveFrozenError`; the scheduler and the task runner must call it
before starting work for a bot.

A failed move STAYS frozen and keeps the failing step, the error,
and the resumable `state` payload, so an operator can `resume_move`
(retry from the recorded step) or `cancel_move` (abandon the move;
the bot stays in the source workspace). A failed move never leaves
the bot half-moved and silently running. One move per bot at a
time: `start_move` refuses when the bot already has an active move.

The actual copy of bot data between workspaces is NOT in this
module: the mover is an injected callable at the call site. The
coordinator records intent, freeze, and outcome; no workspace
transport exists in this repo yet, so there is nothing to copy
with. This is recorded as a limit below, not hidden.

## 6. Deploy (`Dockerfile`, `docker-compose.yml`, `deploy.py`)

Pattern (hermes-agent `Dockerfile` + `docker-compose.yml`,
rewritten for Puspa): multi-stage image, s6-overlay as PID 1,
dashboard bound to loopback by default, remote access only
through an SSH tunnel or an authenticated reverse proxy, UID/GID
remap so home files stay owned by the host user, API server off
unless its key is uncommented.

### The image (`Dockerfile`)

- PWA stage (E-05): Node 20-slim, `npm ci` + `npm run build`,
  builds the React SPA into `/pwa/dist`; the runtime stage
  copies it to `/app/apps/pwa/dist`, so the deployed image
  serves React at `/app`, not the Jinja fallback. The stage
  also copies `apps/pwa/static` in, so the build output is
  byte-identical to a local `npm run build`. `.dockerignore`
  keeps the context lean (no `dist/`, `node_modules/`, `.venv/`).
- Builder stage: Python 3.12-slim, venv at `/opt/venv`, pinned
  `requirements.txt` installed.
- Runtime stage: Python 3.12-slim + s6-overlay 3.2.3.2 (noarch +
  x86_64 tarballs, same URLs as the upstream README quickstart) +
  `curl`/`xz-utils`/`ca-certificates`; `puspa` user created with
  `PUSPA_UID`/`PUSPA_GID` build args (default 1000/1000);
  `ENTRYPOINT ["/init"]` (s6-overlay as PID 1); one s6 service
  (`puspa`) running uvicorn on the FastAPI app, dropped to the
  puspa user with `s6-setuidgid`; cont-init script fixes home
  ownership on first mount (a fresh named volume is root-owned).
- `PUSPA_DASHBOARD_HOST` defaults to `127.0.0.1`. `PUSPA_API_KEY`
  is intentionally absent: the API server stays off unless the key
  is uncommented in `docker-compose.yml`.
- `HEALTHCHECK` hits `/manifest.json` (not `/health`): the PWA app
  has no `/health` route yet, and `/manifest.json` is a stable,
  unauthenticated route proving the app answers HTTP. A real
  `/health` route is a follow-up for the PWA owner.
- BuildKit is required (heredoc RUN blocks):
  `DOCKER_BUILDKIT=1 docker build .` (or `docker buildx build .`).

### Exposure model — the subtle part

`PUSPA_DASHBOARD_HOST=127.0.0.1` is the bind address when the app
runs directly on a host (`puspa/pwa/app.py run()` defaults to it).
Inside the container the service binds `0.0.0.0` instead: Docker's
port publishing DNATs to the container IP, so a process bound to
the container's loopback would never receive the published port.
The exposure boundary is therefore NOT the in-container bind
address — it is the compose file, which publishes each port on the
HOST's `127.0.0.1` only (`127.0.0.1:8000:8000`). Nothing is
reachable from the LAN or the internet. Remote access is only via
an SSH tunnel (`ssh -L 8000:127.0.0.1:8000 <user>@<host>`) or an
authenticated reverse proxy in front of the host loopback.

### Compose (`docker-compose.yml`)

Two services, `gateway` (port 8001, `PUSPA_SERVICE_MODE=service`)
and `dashboard` (port 8000), same image, one named volume
`puspa-home` mounted at `/home/puspa`, UID/GID passed as build
args, `restart: unless-stopped`. Both publish host-loopback only.
Today both services run the same FastAPI app (the PWA serves
dashboard + API in one app); the split is the deployment shape
for the gateway + dashboard topology, and splitting them into two
different processes is a follow-up once the gateway has its own
entrypoint. The two services share one home volume on purpose
(one home per host user); known limit: G26's single-writer rule
is process-wide, not machine-wide — two containers writing the
same `state.db` are not refused at open. Do not run a third
writer against this volume until the file-lock follow-up lands.

### Offline validation (`deploy.py`)

Docker containers cannot run on this dev VM (the kernel rejects
setns), so a live `docker build` / `docker compose up` is
impossible here. `validate_dockerfile(path)` and
`validate_compose(path)` are the validation that IS possible here:
they parse the files with pure Python and check the deploy rules
(multi-stage, s6-overlay + `/init` entrypoint, non-root app via
`s6-setuidgid`, UID/GID args, dashboard host default 127.0.0.1,
no baked-in API key, HEALTHCHECK present; E-05: a Node stage
builds the PWA with `npm run build` and the runtime stage copies
its `dist` in; gateway + dashboard services exist, every published
port binds 127.0.0.1, no API key set, home volume mounted, UID/GID
args passed). They return a list of issue strings; empty means
clean. The tests assert the shipped files are clean, plus negative
cases on synthetic files. Passing these checks proves the files are
well-formed and follow the rules — NOT that the image builds or
runs. That proof is the image smoke test
(`tests/test_image_smoke.py`): it builds the image, runs one
container, requests `/app`, and checks the React bundle is served.
It runs in the CI sandbox lane (E-01), which fails if the live
test skips.

### Live validation (required, on a VPS)

On a machine with Docker (the natbox VPS or any VPS with Docker;
never on this VM):

```sh
PUSPA_UID=$(id -u) PUSPA_GID=$(id -g) docker compose up --build -d
ssh -L 8000:127.0.0.1:8000 <user>@<host>   # from your own machine
# open http://127.0.0.1:8000, log in, check the dashboard loads
docker compose logs -f
docker compose down
```

Also verify: `docker exec` into a container and confirm the app
process runs as the `puspa` user (not root), `/home/puspa` files
are owned by the host UID/GID, and the dashboard is NOT reachable
from another machine on the LAN (only through the tunnel).

## What is NOT built here (honest limits)

- **No HTTP routes.** Teams, invitations, visibility, entitlement
  issuance, and moves are plain Python functions over Postgres;
  no FastAPI/HTTP handler calls them yet. The PWA (`puspa/pwa/`)
  is the natural call site. Wiring them in is a follow-up once
  the team HTTP surface exists.
- **No workspace transport.** `MoveCoordinator` records intent,
  freeze, and outcome; nothing copies bot data between workspaces
  yet, because no workspace transport exists in this repo. The
  freeze and the state machine are real and tested; the mover
  itself is an injected callable at the call site (follow-up).
- **No threads table.** `can_see_thread` is a pure function over
  `ThreadRef`; no thread reader calls it yet, because no threads
  table exists. The record shape is the contract the future table
  must carry.
- **No core-ledger event type for access decisions.** Bot access
  requests/approvals/denials are recorded durably in
  `bot_access_requests` (the audit trail for this iteration). A
  ledger event type is a follow-up owned by the ledger — the core
  ledger stays at 31 types.
- **No live deploy validation on a VPS yet.** The Dockerfile and
  compose file are validated offline, and the image smoke test
  (E-05) builds the image and checks `/app` serves React in the
  CI sandbox lane — but the image has not been built on this VM
  (no Docker daemon here), the containers have not run, and no
  login through them has been observed. First live run is on a
  VPS.
- **Image size.** The image installs the full pinned
  `requirements.txt` including torch (~2.5 GB installed). A slim
  server-only requirements split is a follow-up.
- **Entitlement revocation needs the DB.** A token-only check
  cannot see revocations; the home gate must be constructed with
  a DSN for revocation to apply.

## Tests

- `tests/test_teams.py` — team create/delete, roles (member
  cannot change roles, cannot delete), last-owner guard,
  list/role checks, RLS as `puspa_app` (non-member sees nothing,
  member sees own rows, non-owner writes refused).
- `tests/test_teams_invitations.py` — issue (owner-only, hash-only
  storage), accept (membership created, idempotent second accept,
  different user refused after consumption), revoke, expiry
  (7-day TTL, purge), unknown/revoked tokens refused.
- `tests/test_teams_visibility.py` — `can_see_thread` (personal
  vs team, non-member denied), `require_thread_visible`,
  `can_access_bot` (owner, granted member, ungranted member,
  personal bot), request/approve/deny (owner-only decisions,
  idempotent approve, final decisions, duplicate pending request
  returns the existing one), grant/revoke.
- `tests/test_teams_entitlements.py` — issue/verify round trip,
  tampered payload/signature rejected, expired rejected, wrong
  machine rejected, unknown tier rejected, revocation (with DSN),
  `HomeGate` decisions (valid, admin, missing, expired, wrong
  machine), pinned-key loading from raw/hex.
- `tests/test_teams_moves.py` — start/begin/advance/complete,
  freeze during pending/in_progress/failed, release on done/
  cancelled, failed stays frozen and resumable (resume/cancel),
  illegal transitions refused, one active move per bot.
- `tests/test_teams_deploy.py` — shipped Dockerfile + compose
  validate clean; negative cases (single-stage Dockerfile,
  missing s6-overlay, 0.0.0.0 compose binding, API key set,
  missing Node stage, missing dist copy, missing PWA build).
- `tests/test_image_smoke.py` — image smoke (E-05): bundle
  extraction + SPA-shell detection, `check_spa_at` against a
  throwaway server (SPA ok, Jinja rejected, missing bundle
  rejected, unreachable server reported), `GET /app` serves the
  SPA shell from dist (fake and real) without login and falls
  back to Jinja without dist; the live test builds the image,
  runs one container, and checks `/app` serves React (skips
  without a Docker daemon; the CI sandbox lane fails on skip).
