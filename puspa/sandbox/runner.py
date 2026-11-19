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
import uuid

from .allowlist import DEFAULT_ALLOWLIST, Allowlist
from .policy import SandboxPolicy
from .quota import DEFAULT_QUOTA, ResourceQuota

__all__ = ["DEFAULT_IMAGE", "DEFAULT_PYTHON_BIN", "Sandbox", "SandboxUnavailable"]

DEFAULT_IMAGE = "puspa-sandbox:3.12-local"
DEFAULT_PYTHON_BIN = "/usr/bin/python3.12"
_PROBE_CODE = 'print("puspa-ok")'
_MAX_OUTPUT_CHARS = 100_000
_MAX_CODE_CHARS = 1_000_000


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
    """

    def __init__(
        self,
        allowlist=None,
        *,
        image: str = DEFAULT_IMAGE,
        python_bin: str = DEFAULT_PYTHON_BIN,
        quota: ResourceQuota | None = None,
        policy: SandboxPolicy | None = None,
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
        self._available_cache: bool | None = None

    def docker_flags(self) -> list[str]:
        """The ``docker run`` hardening + resource flags for this sandbox.

        Derived from the quota (memory/CPU/pids) and the policy
        (network mode, run-as user), so a quota or policy change
        is one place, not a flag hunt. The network flag is
        ``--network none`` when the policy admits no host; a
        policy that admits hosts needs a filtered egress proxy
        Puspa does not have yet, so :meth:`run` refuses that
        mode instead of returning flags that would open the
        network (see :meth:`_check_network_policy`).
        """
        network_policy = self.policy.network
        if network_policy.docker_network_mode() != "none":
            raise SandboxUnavailable(
                "network policy admits hosts, but Puspa has no filtered "
                "egress proxy yet (OpenShell enforces this with an HTTP "
                "CONNECT proxy + DNS mediation); refusing to run with the "
                "network on. Use a deny-all network policy until the "
                "proxy exists. See docs/sandbox-deepening.md."
            )
        return [
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            *self.quota.docker_flags(),
            "--user",
            self.policy.docker_user_flag(),
            "--workdir",
            "/tmp",
        ]

    def _check_network_policy(self) -> None:
        """Refuse a run whose network policy needs the missing proxy."""
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
        baru melakukan probe ulang.
        """
        if self._available_cache is not None:
            return self._available_cache
        ok = False
        if self._daemon_ok():
            if not self._image_present():
                self._pull_image()
            if self._image_present():
                ok = self._probe_container()
        self._available_cache = ok
        return ok

    # -- eksekusi ------------------------------------------------------------

    def _run_container(self, code: str, *, timeout_s: int, name: str) -> dict | None:
        """Satu percobaan `docker run`. None bila container gagal start.

        Mengembalikan dict hasil bila container sempat jalan (berapa pun
        exit code-nya). Gagal start (daemon error / runc gagal) -> None.
        """
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

    def run(self, code: str, *, timeout_s: int = 30) -> dict:
        """Jalankan ``code`` Python di container sekali pakai.

        Mengembalikan ``{"ok", "stdout", "stderr", "exit_code", "timed_out"}``.
        ``ok`` False berarti kode keluar non-zero atau timeout — itu hasil,
        bukan kegagalan sandbox.

        Raise ``SandboxUnavailable`` bila Docker tidak tersedia atau container
        gagal start. Dalam kasus itu TIDAK ADA kode yang dieksekusi di mana
        pun (bukan di host, bukan di container).
        """
        if not isinstance(code, str):
            raise TypeError("code harus str")
        if len(code) > _MAX_CODE_CHARS:
            raise ValueError(f"code melebihi {_MAX_CODE_CHARS} karakter")
        if timeout_s <= 0:
            raise ValueError("timeout_s harus > 0")
        # Admission first (G34): a network policy that needs the
        # missing egress proxy is refused before Docker is even
        # probed, so the refusal does not depend on host state.
        self._check_network_policy()
        if not self.check_available():
            raise SandboxUnavailable(
                "Docker tidak tersedia atau container tidak bisa start; "
                "menolak eksekusi (fail-closed, tidak ada fallback host)"
            )
        result = self._run_container(code, timeout_s=timeout_s, name="puspa-run")
        if result is None:
            # Container gagal start setelah probe lolos (mis. daemon mati
            # di tengah jalan): tetap fail-closed.
            self._available_cache = False
            raise SandboxUnavailable(
                "container gagal start; menolak eksekusi (fail-closed, tidak ada fallback host)"
            )
        return result
