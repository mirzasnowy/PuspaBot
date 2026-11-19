# Sandbox deepening (G34, NVIDIA/OpenShell pattern)

Date: 19 Nov 2026. Status: implemented and tested on this VM; live
container verification waits for a Docker host / VPS.

## What was studied

NVIDIA/OpenShell (https://github.com/NVIDIA/OpenShell, Apache-2.0,
~21,780 stars), shallow-cloned 19 Nov 2026 at commit
`5c0d71641fbc9d62a4ee2f33de235f6662ae196c` (last push 19 Nov 2026).
OpenShell is a Rust workspace (~2,130 files): a gateway (control
plane), a supervisor (the security boundary, one per sandbox), and
an isolation backend (Docker / Podman / Kubernetes / VM drivers
behind one interface). Its own summary: "the safe, private runtime
for fleets of autonomous AI agents". Only the *mechanisms* below
were taken. No OpenShell code was copied, ported, or vendored;
every Puspa module here was written from scratch in Python
against Puspa's existing backend ABC. Attribution is recorded in
`REUSE.md` and `NOTICE.md` (G34 sections).

Sources read (all in the clone above):

- `README.md` — the gateway / supervisor / sandbox split and the
  two enforcement layers (kernel-level enforcement + formally
  verified policy changes).
- `rfc/0001-core-architecture/README.md` — gateway subsystems
  (compute, credentials, control-plane identity, sandbox
  identity) behind driver interfaces; supervisors connect
  outbound to the gateway; the gateway never dials a sandbox.
- `rfc/0004-sandbox-resource-requirements/README.md` — typed
  resource requirements (`compute` CPU/memory, `device` GPU)
  used to prefilter drivers and provision; driver-specific
  settings stay in a separate `driver_config` escape hatch.
- `proto/sandbox.proto` — `SandboxPolicy` (filesystem allow
  lists, Landlock config, process policy with run-as user/group,
  named network rules with `host:port` endpoints, middleware
  with fail-closed default, L7 rules).
- `proto/sandbox_protocol.proto` — the supervisor protocol:
  `Attach` + `Confirm` (policy validated *before* `StartAgent`),
  `Exec` with an admission deadline, `Resize`, `Signal`,
  `Terminate`, bounded output frames (64 KiB I/O payloads,
  1 MiB control messages), session/process snapshots.
- `proto/openshell.proto` — `Sandbox` / `SandboxSpec` /
  `SandboxPhase` (provisioning, ready, error, deleting,
  stopping, stopped, completed, suspended) and
  `ResourceRequirements`.
- `crates/openshell-sandbox/src/sftp.rs` — file transfer over
  SFTP backed by a directory file descriptor (root-anchored,
  bounded handles/packets/entries).
- `crates/openshell-sandbox/src/pty.rs` — PTY setup: dedicated
  process group, controlling terminal, window size.
- `docs/how-it-works/sandboxes/overview.mdx` — lifecycle
  (retained login shell in a PTY by default, `--detach`,
  `--no-keep`, restart policies), `--cpu` / `--memory` flags,
  `sandbox exec` / `sandbox upload` behaviour, the 4 MiB
  pending-stdin cap per exec channel.
- `crates/openshell-supervisor-network/src/` — network
  enforcement: HTTP CONNECT proxy + OPA + DNS mediation; a
  supervisor-dependent operation fails closed, never opens an
  unmediated egress path.

Not taken (out of scope for G34): the Rust code, the TUI, the
Helm chart, the gateway/supervisor split as processes, kernel
enforcement (Landlock/seccomp), formal policy verification
(the prover), SPIFFE identity, and credential injection at the
proxy. Puspa already has its own credential broker
(`puspa/computer/broker.py`); G34 does not change it.

## Mechanisms taken, and where they map to Puspa code

| OpenShell mechanism | Puspa code (written from scratch) |
| --- | --- |
| `Sandbox` resource with `SandboxPhase` lifecycle, named + retained identity, one sandbox per agent task | `puspa/sandbox/session.py`: `SandboxSession` + `SessionManager` (create/get/list/kill, `SessionStatus` created/ready/running/closed/error) |
| `Attach` + `Confirm` before `StartAgent`: unsupported requirements fail before the workload starts | `SessionManager.create`: validate policy, reserve quota, then build the backend; any failure releases the reservation and leaves no session behind |
| Idle/orphan reaping, supervisor recovery timeout | `SessionManager.reap_expired` (lifetime + idle timeout, default 1 h / 15 min), `SandboxSession.is_expired` / `is_idle`, `heartbeat` |
| `SandboxPolicy`: filesystem allow lists (`include_workdir`, `read_only`, `read_write`), process policy (`run_as_user`/`run_as_group`) | `puspa/sandbox/policy.py`: `FilesystemPolicy`, `SandboxPolicy` (non-root enforced, `--user 65534:65534`) |
| Network policy: deny-by-default, named rules, explicit `host:port` endpoints, middleware fail-closed by default | `puspa/sandbox/policy.py`: `NetworkPolicy` / `NetworkEndpoint` (deny-all default; hosts must already be on the session allowlist — a policy narrows it, never widens it; `docker_network_mode()` is `"none"` or `"proxy"`) |
| Typed resource requirements (RFC 0004): portable `compute` + `device`, driver prefilter, separate driver config | `puspa/sandbox/quota.py`: `ResourceQuota` (CPU, memory, disk, pids; `gpu_count > 0` refused, never silently served on CPU), `docker_flags()`, `QuotaRegistry` (per-user overrides, live-session cap, reserve/release) |
| Disk accounting for the workspace | `puspa/sandbox/quota.py`: `measure_disk_usage` (symlinks not followed); enforced before writes by the filesystem API |
| SFTP file transfer, root-anchored, bounded | `puspa/sandbox/filesystem.py`: `SandboxFilesystem` (read/write/list/stat/delete, upload/download), `LocalSandboxFilesystem` (real, scoped to the backend scope + disk quota), `DockerSandboxFilesystem` (uniform refusal: no persistent filesystem), `filesystem_for_backend` |
| `Exec` with admission deadline, bounded output frames, exit reported separately from output | `puspa/sandbox/exec.py`: `ExecRequest` (argv *or* code, never a shell string), `ExecResult`, `exec_stream` yielding `ExecChunk` values (stdout/stderr/exit, 64 KiB chunks, 100,000-char total cap) |
| PTY main process + `Resize`, dedicated process group | `puspa/sandbox/terminal.py`: `TerminalSession` (real PTY on the local backend: `send_input`, `read_output`, `resize` via `TIOCSWINSZ` + `SIGWINCH`, `close` kills the process group), `start_terminal` |
| Browser as a workload inside the sandbox, reached over its own endpoint; network still mediated | `puspa/sandbox/browser.py`: `SandboxBrowserSession` + `SandboxBrowserManager` (one live browser session per sandbox session, profile dir inside the sandbox workdir, planned CDP endpoint; `launch` refuses until a container host exists) |
| Existing runner flags now derived from quota + policy | `puspa/sandbox/runner.py`: `Sandbox(quota=..., policy=...)`, `docker_flags()` (defaults unchanged: 256 MB, 0.5 CPU, 64 pids, `--network none`, non-root) |

## What the runner enforces, and what it refuses

- Empty network policy: `--network none` (as before).
- Non-empty network policy (hosts admitted): the runner
  *refuses to run* (`SandboxUnavailable`, naming the missing
  filtered egress proxy). OpenShell enforces this with an HTTP
  CONNECT proxy + DNS mediation; Puspa has no such proxy yet,
  so running with the network on would be a silent widening.
  The refusal happens at admission (`run()` checks the policy
  before probing Docker), and `docker_flags()` raises the same
  refusal instead of returning flags that open the network.
- The Docker backend still runs one ephemeral container per
  exec. A Puspa session is therefore a control-plane record
  plus a workspace, not a live process: `READY` means admitted
  (policy + quota + backend available), not "a container is
  running".
- `LocalBackend` executes on the host (not in a container).
  G34 does not change that; it *scopes* it: the filesystem API
  and terminal sessions resolve every path through the backend
  scope (a temp-dir jail), the exec API resolves `cwd` through
  the scope, and credential-looking env keys are refused in
  sessions, exec requests, and terminal starts alike. The
  local backend stays test/dev only; production selects
  `docker`.

## Honest limits (what is NOT verified)

1. **No live container has ever run on this VM.** Docker is
   installed but its daemon cannot start a container here
   (the kernel rejects the `setns` syscall), so
   `Sandbox.check_available()` is False, `DockerBackend`
   admission fails closed, Docker filesystem ops refuse, and
   Docker terminal/browser sessions refuse. That is the
   correct behaviour, tested as such — but it means the Docker
   flag derivation (`docker_flags()`), the quota-derived
   `--memory`/`--cpus`/`--pids-limit`, and the `--network none`
   enforcement are *constructed* here, not *executed* here.
   The first live check is on a Docker host or VPS.
2. **No filtered egress proxy.** A policy that admits hosts is
   validated and stored, but no exec runs under it until the
   proxy exists. Do not "fix" this by passing
   `--network bridge` or similar: that would silently grant
   unmediated egress.
3. **No GPU provisioning.** `gpu_count > 0` is refused at
   quota-build time.
4. **Sessions are in-memory and process-local** (like the G16
   browser `SessionRegistry`). A restart loses them; the
   workspaces on disk survive, but the records do not.
   Durability is a later step, not faked here.
5. **No live browser.** No Chromium runs anywhere in this
   work. `SandboxBrowserManager.launch` refuses honestly; the
   record stays `PENDING_HOST` with `cdp_url: None`. A host
   browser must never be launched as a stand-in: it would not
   be inside the sandbox at all.
6. **No kernel enforcement.** Puspa has no Landlock/seccomp
   layer; the container flags (`--read-only`, `--cap-drop
   ALL`, `no-new-privileges`, non-root, resource caps) are the
   enforcement that exists, plus policy validation at
   admission.

## Tests

- `tests/test_sandbox_g34_policy_quota.py` — policy matching
  (subdomains, ports, allowlist narrowing), filesystem allow
  lists, quota bounds/flags/GPU refusal, registry cap +
  reserve/release, disk accounting.
- `tests/test_sandbox_g34_sessions.py` — create/get/list/kill,
  per-user cap, credential-env refusal, policy validated
  before quota reservation, idle/lifetime reaping with a fake
  clock, Docker admission failing closed (quota released, no
  session left).
- `tests/test_sandbox_g34_exec_filesystem.py` — filesystem
  round-trip, escape refusal, disk-quota + per-file-cap
  enforcement, Docker filesystem refusal, one-shot exec
  (stdout/exit/timeout), streaming exec (chunks + exit),
  runner flag derivation, proxy-policy refusal before Docker.
- `tests/test_sandbox_g34_terminal_browser.py` — real PTY echo
  / exit code / resize / scope jail on the local backend,
  Docker terminal refusal, browser manager bookkeeping +
  one-per-session rule + launch refusal.

Run: `NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1
.venv/bin/python -m pytest tests/test_sandbox_g34*.py
tests/test_sandbox.py tests/test_computer*.py -q` (SOLO, no `-n`).
