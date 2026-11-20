"""Sandbox fail-closed (definisi 2.6).

Kontrak (CONTRACTS.md):
- ``Sandbox(allowlist)``; ``run(code, *, timeout_s=30) -> dict``;
  ``check_available() -> bool``.
- Docker-backed bila tersedia; bila Docker TIDAK tersedia, raise
  ``SandboxUnavailable``. TIDAK PERNAH fallback ke eksekusi host: satu-satunya
  jalur eksekusi kode adalah ``docker run`` di dalam container.

Pengerasan container (fase 0):
- ``--network none`` — tidak ada jaringan sama sekali, lebih ketat dari
  allowlist (allowlist dipakai untuk validasi URL di luar container dan
  untuk jalur jaringan yang diaktifkan nanti).
- ``--read-only``, ``--cap-drop ALL``, ``--security-opt no-new-privileges``,
  user non-root (65534/nobody), batas memori 256 MB, CPU 0.5, maks 64 proses.
- Kode dikirim lewat stdin ke ``python -``; tidak ada volume mount dari host.

Pengerasan E-03 (plan 4.3):
- ``--tmpfs /tmp`` — root filesystem read-only, tapi container tetap
  punya tempat tulis (tmpfs, ukurannya dibatasi dari kuota: setengah
  memori atau disk, mana yang lebih kecil).
- Profil seccomp: ``--security-opt seccomp=<profil>`` — default profil
  Docker yang di-vendor (``puspa/sandbox/seccomp-default.json``) atau
  varian strict (``seccomp-strict.json``); profil dimuat dan
  divalidasi saat ``Sandbox`` dibangun, profil rusak gagal sebelum
  container dibuat (lihat :mod:`puspa.sandbox.seccomp`).
- Runtime isolasi: ``--runtime runsc`` (gVisor) atau ``kata`` (Kata)
  lewat :class:`puspa.sandbox.runtime.RuntimeConfig`; default runc.
- Jaringan berallowlist: policy yang mengakui host tidak lagi selalu
  ditolak — bila sebuah :class:`puspa.sandbox.egress.EgressProxy`
  dikonfigurasi dan berjalan, container jalan di network internal
  Docker (``--internal``, tanpa rute keluar) dengan DNS mati
  (``--dns 127.0.0.1``) dan semua egress lewat proxy
  (``http_proxy``/``https_proxy``). Tanpa proxy, penolakan lama
  tetap berlaku. Lihat :mod:`puspa.sandbox.egress`.
- Audit flag: :func:`audit_docker_flags` menolak flag apa pun yang
  memasang volume/mount (termasuk ``docker.sock``) dan menolak flag
  yang kehilangan pengerasan wajib — "jangan pernah memasang socket
  Docker" ditegakkan sebagai pemeriksaan, bukan sekadar komentar.
- Warm pool: ``Sandbox`` menerima :class:`puspa.sandbox.warmpool.WarmPool`
  opsional; satu run memakai satu slot pool, lalu slot dihancurkan
  (bukan dibersihkan) dan pool diisi ulang dengan container baru.

Catatan lingkungan (VM dev ini, 17 Nov 2026):
- Docker 29.1.3 terinstal; daemon hanya bisa jalan mode userns
  (``--iptables=false --bridge=none --storage-driver=vfs``, TCP
  127.0.0.1:2376) karena VM ini tidak memberi CAP_NET_ADMIN dan menolak
  syscall ``setns`` (runc gagal start container). Akibatnya
  ``check_available()`` di VM ini mengembalikan False dan ``run()`` selalu
  raise ``SandboxUnavailable`` — perilaku fail-closed yang benar, bukan bug.
  Di host dengan Docker normal (mis. VPS Contabo), probe container berhasil
  dan ``run()`` mengeksekusi di container.
- Image default ``puspa-sandbox:3.12-local`` dibangun lokal dari Python
  host (``docker import``) karena registry Docker Hub tidak terjangkau dari
  VM ini (blob download dari production.cloudfront.docker.com selalu
  TLS handshake timeout lewat egress proxy).
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from puspa.observability import metrics as observability_metrics
from puspa.observability import tracing as observability_tracing

from .allowlist import DEFAULT_ALLOWLIST, Allowlist
from .egress import EgressError, EgressProxy
from .policy import SandboxPolicy
from .quota import DEFAULT_QUOTA, ResourceQuota
from .runtime import RuntimeConfig
from .seccomp import SeccompError, SeccompProfile, default_profile, load_profile, strict_profile
from .warmpool import WarmPool

__all__ = [
    "DEFAULT_IMAGE",
    "DEFAULT_PYTHON_BIN",
    "Sandbox",
    "SandboxUnavailable",
    "audit_docker_flags",
]

DEFAULT_IMAGE = "puspa-sandbox:3.12-local"
DEFAULT_PYTHON_BIN = "/usr/bin/python3.12"
_PROBE_CODE = 'print("puspa-ok")'
_MAX_OUTPUT_CHARS = 100_000
_MAX_CODE_CHARS = 1_000_000

#: Flags that would mount host paths into the container. Any of
#: these in a flag list is refused by :func:`audit_docker_flags`:
#: an agent container must never see the host filesystem, and
#: the Docker socket in particular must never be mounted.
_FORBIDDEN_MOUNT_FLAGS = ("-v", "--volume", "--mount", "--volumes-from")


def _tmpfs_size_mb(quota: ResourceQuota) -> int:
    """Writable tmpfs size (MiB) for one container, from the quota.

    The tmpfs is the container's only writable scratch (the
    root filesystem is read-only). tmpfs pages count against
    the container's memory cgroup, so the tmpfs is capped at
    half the memory limit (and at least 16 MiB, so a small
    workload can still write temp files): a runaway write
    cannot OOM the workload itself. The host-side workspace
    disk quota (:mod:`puspa.sandbox.quota`) stays the
    authoritative disk limit.
    """
    return min(quota.disk_mb, max(16, quota.memory_mb // 2))


def _resolve_seccomp(value: object) -> SeccompProfile | None:
    """Resolve the ``seccomp`` constructor argument to a profile.

    ``"default"`` (the default) is the vendored Docker default
    profile, ``"strict"`` the vendored strict profile,
    ``"docker-default"`` / ``None`` means the daemon's built-in
    default (no flag is passed), a path loads and validates
    that file, and a :class:`SeccompProfile` is used as-is.
    Anything else raises :class:`SeccompError` before any
    container is built.
    """
    if value is None or value == "docker-default":
        return None
    if isinstance(value, SeccompProfile):
        return value
    if value == "default":
        return default_profile()
    if value == "strict":
        return strict_profile()
    if isinstance(value, Path):
        return load_profile(value)
    if isinstance(value, str) and Path(value).exists():
        return load_profile(value)
    raise SeccompError(
        f"unknown seccomp setting {value!r}: expected 'default', 'strict', "
        "'docker-default', a profile path, or a SeccompProfile"
    )


def audit_docker_flags(flags: list[str]) -> None:
    """Check a ``docker run`` flag list for hardening violations.

    Refuses (with :class:`SandboxUnavailable`, before anything
    runs): any volume/mount flag, any flag mentioning
    ``docker.sock``, a missing read-only root, a missing or
    weakened capability drop, missing ``no-new-privileges``, a
    missing user flag or a root user, a missing network mode,
    or a missing writable tmpfs. This is the executable form of
    the "never mount the Docker socket" rule: no code path can
    build a container that mounts host paths, because the flag
    list itself is checked.
    """
    for flag in flags:
        if flag in _FORBIDDEN_MOUNT_FLAGS or any(
            flag.startswith(prefix + "=") for prefix in _FORBIDDEN_MOUNT_FLAGS
        ):
            raise SandboxUnavailable(
                f"refusing docker flags: mount flag {flag!r} would expose "
                "host paths to the agent container; the Docker socket "
                "must never be mounted into an agent container"
            )
        if "docker.sock" in flag:
            raise SandboxUnavailable(
                "refusing docker flags: a flag mentions docker.sock; "
                "the Docker socket must never be mounted into an agent container"
            )
    if "--read-only" not in flags:
        raise SandboxUnavailable("refusing docker flags: --read-only is required")
    if "--cap-drop" not in flags or "ALL" not in flags:
        raise SandboxUnavailable("refusing docker flags: --cap-drop ALL is required")
    if "no-new-privileges" not in flags:
        raise SandboxUnavailable(
            "refusing docker flags: --security-opt no-new-privileges is required"
        )
    if "--user" not in flags:
        raise SandboxUnavailable("refusing docker flags: --user (non-root) is required")
    user_index = flags.index("--user")
    user = flags[user_index + 1] if user_index + 1 < len(flags) else ""
    if not user or user in ("0", "root", "0:0") or user.startswith("0:"):
        raise SandboxUnavailable(f"refusing docker flags: --user must be non-root, got {user!r}")
    if "--network" not in flags:
        raise SandboxUnavailable("refusing docker flags: --network is required")
    if "--tmpfs" not in flags:
        raise SandboxUnavailable(
            "refusing docker flags: --tmpfs is required "
            "(read-only root needs a writable scratch area)"
        )


class SandboxUnavailable(Exception):
    """Sandbox tidak bisa dipakai (Docker tak ada / daemon mati / container
    gagal start). Fail-closed: panggil run() lagi nanti, jangan eksekusi
    kode di host."""


class Sandbox:
    """Menjalankan kode Python di container Docker sekali pakai.

    G34: the container flags now derive from a
    :class:`puspa.sandbox.quota.ResourceQuota` and a
    :class:`puspa.sandbox.policy.SandboxPolicy` instead of
    hard-coded values. The defaults are the flags this class
    always passed (256 MB, 0.5 CPU, 64 processes, ``--network
    none``, non-root 65534), so behaviour without an explicit
    quota/policy is unchanged.

    E-03: ``seccomp`` (default/strict/daemon-default/path),
    ``runtime`` (runc/runsc/kata), an optional
    :class:`puspa.sandbox.egress.EgressProxy` (which unlocks a
    non-empty network policy: the container runs on the
    proxy's internal network instead of being refused), and an
    optional :class:`puspa.sandbox.warmpool.WarmPool` (one run
    borrows one slot; the slot is destroyed on release).
    """

    def __init__(
        self,
        allowlist=None,
        *,
        image: str = DEFAULT_IMAGE,
        python_bin: str = DEFAULT_PYTHON_BIN,
        quota: ResourceQuota | None = None,
        policy: SandboxPolicy | None = None,
        seccomp: object = "default",
        runtime: RuntimeConfig | str | None = None,
        egress_proxy: EgressProxy | None = None,
        warm_pool: WarmPool | None = None,
    ) -> None:
        if allowlist is None:
            self.allowlist = Allowlist(DEFAULT_ALLOWLIST)
        elif isinstance(allowlist, Allowlist):
            self.allowlist = allowlist
        else:
            self.allowlist = Allowlist(allowlist)
        self.image = image
        self.python_bin = python_bin
        self.quota = quota if quota is not None else DEFAULT_QUOTA
        self.policy = policy if policy is not None else SandboxPolicy()
        self.policy.validate()
        # E-03: resolve eagerly so a bad seccomp profile or an
        # unknown runtime fails at construction, not at the
        # first run (fail-closed before any container is built).
        self.seccomp = _resolve_seccomp(seccomp)
        if runtime is None:
            self.runtime = RuntimeConfig()
        elif isinstance(runtime, RuntimeConfig):
            self.runtime = runtime
        else:
            self.runtime = RuntimeConfig.from_name(runtime)
        self.egress_proxy = egress_proxy
        self.warm_pool = warm_pool
        self._available_cache: bool | None = None
        self._unavailable_reason: str | None = None

    def docker_flags(self) -> list[str]:
        """The ``docker run`` hardening + resource flags for this sandbox.

        Derived from the quota (memory/CPU/pids/tmpfs), the
        policy (network mode, run-as user), the seccomp profile,
        and the isolation runtime, so a quota, policy, profile,
        or runtime change is one place, not a flag hunt. The
        network flag is ``--network none`` when the policy
        admits no host; a policy that admits hosts routes the
        container through the configured
        :class:`puspa.sandbox.egress.EgressProxy` (internal
        network, dead DNS, proxy env) — and when no proxy is
        configured, :meth:`run` refuses that mode instead of
        returning flags that would open the network (see
        :meth:`_check_network_policy`). Every flag list is
        checked by :func:`audit_docker_flags` before it is
        returned.
        """
        network_policy = self.policy.network
        if network_policy.docker_network_mode() == "none":
            network_flags = ["--network", "none"]
        else:
            if self.egress_proxy is None:
                raise SandboxUnavailable(
                    "network policy admits hosts, but no egress proxy "
                    "is configured for this Sandbox (the filtered "
                    "egress proxy lives in puspa.sandbox.egress: build "
                    "an EgressProxy for the policy, start it, and pass "
                    "it as egress_proxy). Refusing to run with the "
                    "network on. See docs/sandbox-deepening.md."
                )
            if not self.egress_proxy.is_running:
                raise SandboxUnavailable(
                    "network policy admits hosts, but the configured "
                    "egress proxy is not running; refusing to run with "
                    "the network on. Start the proxy first."
                )
            network_flags = self.egress_proxy.container_network_flags()
        flags = [
            *network_flags,
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,size={_tmpfs_size_mb(self.quota)}m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            *(self.seccomp.docker_flag() if self.seccomp is not None else []),
            *self.runtime.docker_flags(),
            *self.quota.docker_flags(),
            "--user",
            self.policy.docker_user_flag(),
            "--workdir",
            "/tmp",
        ]
        audit_docker_flags(flags)
        return flags

    def _check_network_policy(self) -> None:
        """Refuse a run whose network policy has no egress proxy."""
        self.docker_flags()

    # -- ketersediaan --------------------------------------------------------

    def _docker_cmd(self) -> list[str] | None:
        """Perintah `docker`, atau None bila binary tidak ditemukan."""
        path = shutil.which("docker")
        if path is None:
            return None
        return [path]

    def _daemon_ok(self) -> bool:
        cmd = self._docker_cmd()
        if cmd is None:
            return False
        try:
            proc = subprocess.run(
                [*cmd, "info", "--format", "{{.ServerVersion}}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0 and bool(proc.stdout.strip())

    def _image_present(self) -> bool:
        cmd = self._docker_cmd()
        assert cmd is not None
        try:
            proc = subprocess.run(
                [*cmd, "image", "inspect", self.image],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0

    def _pull_image(self) -> bool:
        cmd = self._docker_cmd()
        assert cmd is not None
        try:
            proc = subprocess.run(
                [*cmd, "pull", self.image],
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0

    def _probe_container(self) -> bool:
        """Jalankan satu container `print` untuk membuktikan eksekusi bisa."""
        result = self._run_container(_PROBE_CODE, timeout_s=30, name="puspa-probe")
        return bool(
            result is not None
            and result.get("ok")
            and result.get("stdout", "").strip() == "puspa-ok"
        )

    def check_available(self) -> bool:
        """True hanya bila Docker + daemon + container benar-benar bisa jalan.

        Hasil di-cache per instance (probe container itu mahal). Instance
        baru melakukan probe ulang. In proxy mode the internal
        egress network is created first (when missing); a network
        that cannot be created makes the sandbox unavailable
        (fail-closed) and the reason is kept in
        :attr:`_unavailable_reason` for :meth:`run` to report.
        """
        if self._available_cache is not None:
            return self._available_cache
        ok = False
        self._unavailable_reason = None
        if self.policy.network.docker_network_mode() != "none" and self.egress_proxy is not None:
            try:
                self.egress_proxy.ensure_internal_network()
            except EgressError as exc:
                self._unavailable_reason = str(exc)
                self._available_cache = False
                return False
        if self._daemon_ok():
            if not self._image_present():
                self._pull_image()
            if self._image_present():
                ok = self._probe_container()
        self._available_cache = ok
        return ok

    # -- eksekusi ------------------------------------------------------------

    def _run_container(
        self, code: str, *, timeout_s: int, name: str, user_id: str | None = None
    ) -> dict | None:
        """Satu percobaan `docker run`. None bila container gagal start.

        Mengembalikan dict hasil bila container sempat jalan (berapa pun
        exit code-nya). Gagal start (daemon error / runc gagal) -> None.
        With a warm pool configured, the run borrows one pool slot
        instead (see :meth:`_run_container_via_pool`); without one,
        this is the plain ``docker run --rm`` path.
        """
        if self.warm_pool is not None:
            return self._run_container_via_pool(code, timeout_s=timeout_s, user_id=user_id)
        cmd = self._docker_cmd()
        if cmd is None:
            return None
        container_name = f"{name}-{uuid.uuid4().hex[:8]}"
        try:
            hardening_flags = self.docker_flags()
        except SandboxUnavailable:
            return None
        docker_argv = [
            *cmd,
            "run",
            "--rm",
            "-i",
            "--name",
            container_name,
            *hardening_flags,
            self.image,
            self.python_bin,
            "-",
        ]
        try:
            proc = subprocess.run(
                docker_argv,
                input=code,
                capture_output=True,
                text=True,
                timeout=timeout_s + 15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._kill_container(container_name)
            return {
                "ok": False,
                "stdout": "",
                "stderr": "timeout: eksekusi melewati batas",
                "exit_code": None,
                "timed_out": True,
            }
        except OSError:
            return None
        # Exit code 125 = `docker run` sendiri gagal (daemon/runc error).
        # Pesan "Error response from daemon" artinya container tidak jalan.
        if proc.returncode == 125 or "Error response from daemon" in proc.stderr:
            return None
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout[:_MAX_OUTPUT_CHARS],
            "stderr": proc.stderr[:_MAX_OUTPUT_CHARS],
            "exit_code": proc.returncode,
            "timed_out": False,
        }

    def _run_container_via_pool(
        self, code: str, *, timeout_s: int, user_id: str | None = None
    ) -> dict | None:
        """One run on a borrowed warm-pool slot.

        The slot is checked out for exactly this run and
        released in a ``finally`` — release destroys the
        container and refills the pool, so the container that
        ran this code never serves another user. A start
        failure returns None (same contract as the plain path).
        """
        assert self.warm_pool is not None
        owner = user_id or f"run-{uuid.uuid4().hex[:8]}"
        slot = self.warm_pool.acquire(owner)
        try:
            return self.warm_pool.start(slot, input=code, timeout_s=timeout_s)
        finally:
            self.warm_pool.release(slot)

    def _kill_container(self, container_name: str) -> None:
        cmd = self._docker_cmd()
        if cmd is None:
            return
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                [*cmd, "kill", container_name],
                check=False,
                capture_output=True,
                timeout=10,
            )

    def run(self, code: str, *, timeout_s: int = 30, user_id: str | None = None) -> dict:
        """Jalankan ``code`` Python di container sekali pakai.

        Mengembalikan ``{"ok", "stdout", "stderr", "exit_code", "timed_out"}``.
        ``ok`` False berarti kode keluar non-zero atau timeout — itu hasil,
        bukan kegagalan sandbox.

        ``user_id`` binds a warm-pool run to its owner (ignored
        without a pool); with a pool, the borrowed container is
        destroyed when this run ends, whoever the next user is.

        Raise ``SandboxUnavailable`` bila Docker tidak tersedia atau container
        gagal start. Dalam kasus itu TIDAK ADA kode yang dieksekusi di mana
        pun (bukan di host, bukan di container).
        """
        # E-18: wall-clock start for the sandbox run-time metric.
        run_started = time.monotonic()
        if not isinstance(code, str):
            raise TypeError("code harus str")
        if len(code) > _MAX_CODE_CHARS:
            raise ValueError(f"code melebihi {_MAX_CODE_CHARS} karakter")
        if timeout_s <= 0:
            raise ValueError("timeout_s harus > 0")
        # Admission first (G34): a network policy with no
        # configured egress proxy is refused before Docker is
        # even probed, so the refusal does not depend on host state.
        self._check_network_policy()
        if not self.check_available():
            reason = self._unavailable_reason
            raise SandboxUnavailable(
                "Docker tidak tersedia atau container tidak bisa start; "
                "menolak eksekusi (fail-closed, tidak ada fallback host)"
                + (f" Alasan: {reason}" if reason else "")
            )
        # E-18: one ``sandbox`` span per container run (the trace id
        # comes from the surrounding turn when one is running).
        with observability_tracing.span(
            "sandbox:run",
            kind="sandbox",
            attributes={"user_id": str(user_id or "")},
        ):
            result = self._run_container(
                code, timeout_s=timeout_s, name="puspa-run", user_id=user_id
            )
        # E-18: full wall time of the sandbox run (spawn + execution).
        # The start-time p95 alarm reads the warm-pool / cold-create
        # series instead: this run time includes code execution.
        with contextlib.suppress(Exception):
            observability_metrics.get_metrics().record_sandbox_run(
                time.monotonic() - run_started, user_id=user_id
            )
        if result is None:
            # Container gagal start setelah probe lolos (mis. daemon mati
            # di tengah jalan): tetap fail-closed.
            self._available_cache = False
            raise SandboxUnavailable(
                "container gagal start; menolak eksekusi (fail-closed, tidak ada fallback host)"
            )
        return result
