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
  routes the container through a configured, running
  `EgressProxy` (E-03, `puspa/sandbox/egress.py`), and
  *refuses to run* (`SandboxUnavailable`) when none is
  configured — running with the network on and no proxy
  would be a silent widening. The refusal happens at
  admission (`run()` checks the policy before probing
  Docker), and `docker_flags()` raises the same refusal
  instead of returning flags that open the network.
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
   The first live check is the CI sandbox lane
   (.github/workflows/ci.yml), which builds the sandbox image and
   runs the live container tests on every PR.
2. **Filtered egress proxy: BUILT (E-03).** A policy that
   admits hosts is now enforced by
   :mod:`puspa.sandbox.egress` (`EgressProxy`: HTTP CONNECT +
   absolute-form forwarding, policy check before DNS, pinned
   DNS to 1.1.1.1/8.8.8.8, blocked-range check including the
   cloud metadata address, every request logged to a
   hash-chained egress ledger). The runner routes a non-empty
   policy through a configured, running proxy (internal Docker
   network, dead container DNS, proxy env) and still refuses
   when no proxy is configured. The proxy itself is tested
   live in-process over real sockets; the container side
   awaits a Docker host (see the E-03 section below).
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
6. **Kernel enforcement: seccomp profiled, not yet executed
   (E-03).** Puspa now ships a pinned seccomp profile (the
   vendored Docker default plus a strict variant, validated at
   load; see the E-03 section below) and passes it with
   `--security-opt seccomp=<path>`, alongside `--read-only`,
   `--cap-drop ALL`, `no-new-privileges`, non-root, and
   resource caps. There is still no Landlock layer, and no
   container has executed under the profile on this VM — the
   profile is constructed and validated here, and its live
   effect awaits a Docker host.

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
tests/test_sandbox_e03*.py tests/test_sandbox.py tests/test_computer*.py -q` (SOLO, no `-n`).

## E-03: hardening (plan 4.3, 20 Nov 2026)

What already existed (G34 + earlier), not rebuilt here:

- Rootless-ish execution: non-root user 65534, `--cap-drop
  ALL`, `no-new-privileges`, `--read-only` root filesystem
  (`puspa/sandbox/runner.py`).
- Per-job limits: CPU, memory, process count from
  `ResourceQuota` (`puspa/sandbox/quota.py`); runtime limit
  per job (`timeout_s` in `Sandbox.run`); disk quota enforced
  on the workspace before writes (`measure_disk_usage`).
- Fresh container per job (`docker run --rm`, one container
  per exec; `puspa/computer/docker.py` runs one ephemeral
  container per exec too).
- No volume mounts anywhere (code arrives on stdin); the
  Docker socket was never mounted — but nothing *checked* that.
- Deny-by-default network policy (`NetworkPolicy`,
  `NetworkEndpoint`, allowlist narrowing) — with the runner
  refusing any non-empty policy for lack of a proxy.

What E-03 added (the gaps in plan 4.3):

- **Egress proxy** (`puspa/sandbox/egress.py`): `EgressProxy`
  speaks CONNECT (https) and absolute-form requests (http).
  Policy is checked *before* DNS, so a denied host is never
  even resolved. DNS is pinned: a minimal stdlib UDP client
  talks only to 1.1.1.1 / 8.8.8.8 (no new dependency); the
  container's own resolver is dead (`--dns 127.0.0.1`) and the
  proxy is reached by a static `/etc/hosts` entry
  (`host.docker.internal:host-gateway`). After resolving, any
  non-global address is refused — RFC 1918, loopback,
  link-local (including `169.254.169.254`, the cloud metadata
  endpoint), CGNAT, multicast, reserved, documentation ranges
  — so DNS rebinding cannot reach the internal network. Every
  request (allowed *and* denied) is recorded once in an
  `EgressLedger`: append-only JSON lines, each entry carrying
  the previous entry's SHA-256, so tampering breaks the chain
  (`verify_chain`). That file is the egress ledger: the
  Postgres `Ledger` has a closed set of 31 event types with no
  network event, so bridging these entries into Postgres needs
  a new event type (an operator/schema decision, not smuggled
  in under an unrelated type). The container runs on a Docker
  `--internal` network (`ensure_internal_network` creates it,
  idempotently; a creation failure fails closed), which has no
  external route at all — the proxy is the only way out.
- **Seccomp** (`puspa/sandbox/seccomp.py` +
  `seccomp-default.json` + `seccomp-strict.json`): Docker's
  own default profile, vendored verbatim from moby/moby
  `v28.0.0` (Apache-2.0, attribution in `REUSE.md` /
  `NOTICE.md`), plus a strict variant (default minus 26
  syscalls: ptrace, bpf, perf_event_open, cross-process memory
  access, module loading, mount/umount2, reboot, clock
  setting, hostname changes, and friends — all verified
  present in the default, none needed by a Python workload).
  Profiles are validated at load (`defaultAction` must be
  `SCMP_ACT_ERRNO`, non-empty `syscalls` list, known actions);
  a missing, malformed, or unsafe profile fails before any
  container is built. The runner passes
  `--security-opt seccomp=<path>` by default (`"strict"` and
  `"docker-default"` are also selectable).
- **Writable tmpfs** (runner): `--tmpfs
  /tmp:rw,size=<n>m`, where `n = min(disk_mb, max(16,
  memory_mb // 2))` — tmpfs pages count against the memory
  cgroup, so the cap is half the memory limit and a runaway
  write cannot OOM the workload. Default: 128 MiB.
- **Flag audit** (`audit_docker_flags` in the runner): every
  flag list is checked before use — any volume/mount flag, any
  `docker.sock` reference, or any missing hardening flag
  (`--read-only`, `--cap-drop ALL`, `no-new-privileges`,
  non-root `--user`, `--network`, `--tmpfs`) refuses the run.
  "Never mount the Docker socket" is now a check, not a
  comment.
- **Stronger-isolation runtimes** (`puspa/sandbox/runtime.py`):
  `RuntimeConfig` for `runc` (default) / `runsc` (gVisor) /
  `kata` (Kata), `--runtime` flag derivation, refusal when the
  daemon does not offer the runtime, and a spawn measurement
  harness (`measure_spawn`, `evaluate_runtimes`,
  `docker_spawn_probe`) against the gate-9 target of under 3
  seconds. The runtime choice is *not* made here: gVisor and
  Kata both cost spawn time and CPU, so the harness exists to
  decide from measured numbers on the real host (E-02's VPS).
- **Warm pool** (`puspa/sandbox/warmpool.py`): pre-created,
  never-run containers; `acquire` hands one to exactly one
  user; `release` always destroys it (`docker rm -f`) and
  refills with a fresh one. There is no "clean" path and no
  code path that returns a used container to the pool; a
  double release or a release by the wrong user raises. The
  runner borrows a slot for one run and releases it in a
  `finally`, even on timeout. The pool is opt-in; without one
  the runner keeps its `docker run --rm` path.

Honest limits (E-03, in addition to the list above):

- No live container has run through any of this on this VM:
  flag derivation, the proxy-mode network flags, and the
  pool's create/start/remove path are constructed and tested
  with fakes, not executed. First live check is a Docker host
  (E-02's VPS): the internal network must actually let the
  container reach the host gateway where the proxy listens,
  and the seccomp profile must let a Python workload start.
- The pinned-DNS live test is skipped on this VM (the VM
  blocks UDP send with EPERM); the DNS wire format is still
  tested without sockets, and the live test runs where UDP
  works.
- `puspa/sandbox/egress.py` is 967 lines, close to the 1000-line
  ratchet soft cap: split it (proxy vs. ledger vs. DNS client)
  before it grows further.
