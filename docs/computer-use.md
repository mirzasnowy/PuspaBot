# Computer use / per-bot sandbox (G16)

G16 is **BIG and deferred until after the MVP**. Puspa does not
build computer use / live browser automation now: the price and
bill wedges run through APIs, not browser clicks. What exists
today is the principles plus the real seams, so the post-MVP
work plugs into tested interfaces instead of starting from
nothing.

Patterns studied (never copied verbatim): DuskBot `server/bot-box/`
+ `server/browser/` + credential broker, OpenBot container per
bot + HMAC token + egress filter, hermes-agent
`tools/environments/` (one ABC + one module per variant + a
name-to-variant table + conformance tests) + `browser_use_cli.py`
(CDP, per-task sessions, launcher that reaps orphans).

## The 4 principles

1. **One browser profile per agent.** A profile directory
   belongs to exactly one agent id (`profile_dir_for` in
   `puspa/computer/browser.py`); two agents never share cookies,
   logins, or history.
2. **Explicit human control for sign-in / captcha / 2FA.** The
   bot never sees a password. When a page needs a human, the
   launcher opens a takeover request and waits; the human
   completes the step in their own browser. There is no password
   field anywhere in the browser module — by design.
3. **Credentials via broker, not env.** A spawned process
   receives only `PUSPA_BROKER_TOKEN` — an opaque, short-lived,
   scope-bound token. The credential value never appears in an
   environment, a log line, a `repr`, or an error message; only
   fingerprints (first 12 hex chars of the SHA-256) are shown.
   Tokens die on expiry, on explicit revoke, and when the owning
   process exits.
4. **Sandbox as a spawn wrapper, not a parallel world.** The
   backend ABC wraps execution; scope and file sync are separate
   objects with their own rules. There is no second world with
   its own state — one scope, one profile, one backend at a time.

## Backends

| Backend | Module | Where it runs | Production? |
| --- | --- | --- | --- |
| `local` | `puspa/computer/local.py` | Subprocess on this host, scrubbed env, scope-enforced cwd | No — test/dev only, not a security boundary |
| `docker` | `puspa/computer/docker.py` | Hardened Docker sandbox (`puspa/sandbox/`: `--network none`, read-only, `--cap-drop ALL`, no new privileges, non-root, mem/CPU/pids limits) | Yes — the only production backend |

Selection is by configuration (`ComputerConfig`, env
`PUSPA_COMPUTER_BACKEND`, default `local`). An unknown name is
refused at config time, never silently replaced. The coding
harness runs behind the same ABC (`BackendExecutor` in
`puspa/harness/executor.py`): local backend preserves
`LocalTestExecutor` behaviour exactly, and the harness
constructor refuses a local-backed executor when
`production=True`.

## Conformance rules

- The same suite (`tests/test_computer_backends.py`) runs
  against **every** registered backend. A backend passes by
  behaving like the others.
- A backend that cannot run on a host **skips with a clear
  reason** instead of failing or faking. Docker skips on this
  VM (see limits below).
- A new variant is added by writing its module, registering it
  in `BACKENDS`, and adding it to the conformance
  parametrisation — nothing else.
- File sync and scope are separate from execution: a backend
  with no persistent filesystem (Docker: fresh container per
  run) refuses sync and edits run through `run_python` only.

## Broker rules

- Register a credential by name once; issue tokens against it
  (`CredentialBroker.issue`, default TTL 5 minutes).
- A token resolves only its own credential and scope.
- `child_env(token)` returns exactly one entry:
  `PUSPA_BROKER_TOKEN`. The child presents the token to the
  broker; the broker resolves it.
- Tokens are revoked on context exit, on `close()`, and on
  process exit (an `atexit` hook).
- **What exists is an interface plus an in-memory test broker
  (`InMemoryBroker`).** It holds values in this process's
  memory and is test-only. The real broker service (separate
  process, HTTP, per-process revocation on the host — the
  DuskBot port-18080 pattern) is a post-MVP follow-up.
- Relation to the G22 credential pool (`puspa/credentials`):
  the pool stores keys with per-model cooldown rotation and is
  the source of keys. The broker is the separate layer that
  hands a key to a spawned process. This package does not
  duplicate the pool.

## Browser-session contract (interface only)

- **One session per task.** A session is born for a task and
  dies with it; there is no global browser the bot keeps.
- **One live session per agent at a time.** A second launch for
  the same agent raises `BrowserSessionBusy` until the first
  closes or is reaped.
- **Orphan reaping.** A session with no heartbeat for
  `max_idle_s` (default 900s) is an orphan; the launcher reaps
  it (`SessionRegistry.reap_orphans`). The post-MVP launcher
  additionally kills the Chromium process.
- **Human takeover.** `TakeoverRequest` carries session,
  reason (`sign_in` / `captcha` / `two_fa` / `other`), and
  status (`pending` / `approved` / `denied` / `completed`).
  No password, token, or secret field exists.
- **No live browser yet.** `UnavailableBrowserLauncher` raises
  `BrowserUnavailable` on launch and on takeover. There is no
  CDP driving and no Chromium launch in this package.

## Honest limits

- **Docker containers cannot run on this dev VM.** The kernel
  rejects the `setns` syscall, so the sandbox probe fails,
  `DockerBackend.check_available()` returns False, and every
  run raises `BackendUnavailable` with a message that says so.
  That is correct fail-closed behaviour, not a bug. No code is
  executed anywhere when the backend is unavailable, and the
  Docker backend never fakes a passing run.
- **The local backend is not a security boundary.** Code still
  runs on the host. It exists so the harness loop is testable
  where Docker cannot start.
- **No real broker service, no live browser, no CDP driving.**
  The broker is interface + in-memory test broker; the browser
  is contract + registry. Anything that claims otherwise is
  wrong.

## Follow-up schedule (post-MVP)

1. **Docker host / VPS first.** First live Docker check is on
   a host where containers start. Until then, every Docker test
   skips with the reason above. No Docker work is faked green
   on this VM.
2. **Real broker service.** Separate process, HTTP endpoint,
   per-process token revocation on the host, wired to the G22
   pool as the key source.
3. **Human-takeover UI.** The surface where a human takes over
   a browser session for sign-in / captcha / 2FA, then hands
   control back. Without it, no browser automation ships.
4. **Browser launcher + CDP driving.** Per-task sessions,
   Chromium reuse, orphan reaping with process kill — only
   after (1) and (3) exist. This is the foundation for the
   price-comparison wedge extracting shop pages without an API.
