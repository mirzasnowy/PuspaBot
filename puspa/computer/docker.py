"""Docker execution backend (G16).

Wraps the hardened Docker sandbox from :mod:`puspa.sandbox`
(``--network none``, read-only, ``--cap-drop ALL``, no new
privileges, non-root user, memory/CPU/pids limits, no volume
mounts). It is the production backend: the harness production
path runs generated code only through this backend.

Honest limit, stated plainly: Docker containers CANNOT run on
this dev VM. The kernel rejects the ``setns`` syscall, so the
sandbox probe fails, :meth:`DockerBackend.check_available`
returns False, and every run raises
:class:`puspa.computer.errors.BackendUnavailable` with a message
that says so. That is the correct fail-closed behaviour, not a
bug, and this module never fakes a passing Docker backend to
make a test green. The first live check is the CI sandbox lane
(.github/workflows/ci.yml), which builds the sandbox image and
runs the Docker conformance tests on every PR.

Because the sandbox only runs Python code (``python -`` on
stdin), :meth:`DockerBackend.run_command` is implemented by
generating a small Python wrapper that runs the argv inside the
container and exits with the inner exit code. The wrapper is
built with ``json.dumps`` so argv values are data, never code.
The container has no network and no persistent filesystem, so
:attr:`DockerBackend.sync` refuses every file operation: edits
run through ``run_python`` only, exactly like the harness
sandbox executor.
"""

from __future__ import annotations

import json
from pathlib import Path

from puspa.sandbox import Sandbox, SandboxUnavailable

from .backends import CommandResult, ExecutionBackend, register_backend
from .errors import BackendUnavailable
from .scope import SandboxScope
from .sync import FileSync, NoFileSync

__all__ = ["DockerBackend"]

_UNAVAILABLE_HINT = (
    "Docker containers cannot run on this host right now "
    "(no working Docker daemon, or the sandbox image is missing); "
    "first live check is the CI sandbox lane on a Docker host "
    "(.github/workflows/ci.yml). "
    "No code was executed anywhere (fail-closed, no host fallback)."
)

_COMMAND_WRAPPER = (
    "import json, subprocess, sys\n"
    "_argv = json.loads({argv_json})\n"
    "_proc = subprocess.run(_argv)\n"
    "sys.exit(_proc.returncode)\n"
)


class DockerBackend(ExecutionBackend):
    """Docker-sandbox backend (production; unavailable on this VM)."""

    name = "docker"

    def __init__(
        self,
        sandbox: Sandbox | None = None,
        *,
        workdir: str | Path | None = None,
        profile: str = "workspace_write",
    ) -> None:
        # The scope exists so the ABC contract holds (paths are
        # still resolved against it by callers), but the container
        # itself has no persistent filesystem: sync refuses.
        if workdir is None:
            workdir = Path("/tmp") / "puspa-computer-docker"
        self._scope = SandboxScope(Path(workdir), profile)
        self._sandbox = sandbox if sandbox is not None else Sandbox()
        self._sync = NoFileSync("docker backend: fresh container per run")
        self._available_cache: bool | None = None

    # -- ABC ---------------------------------------------------------------

    @property
    def scope(self) -> SandboxScope:
        return self._scope

    @property
    def sync(self) -> FileSync:
        return self._sync

    def check_available(self) -> bool:
        """True only when Docker + daemon + a probe container work.

        Delegates to :meth:`puspa.sandbox.Sandbox.check_available`
        and caches the answer per instance (the probe container is
        expensive). False on this VM, by kernel design.
        """
        if self._available_cache is None:
            self._available_cache = self._sandbox.check_available()
        return self._available_cache

    def run_command(
        self,
        argv: list[str],
        *,
        cwd: str | Path | None = None,
        timeout_s: int = 30,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        if not isinstance(argv, (list, tuple)) or not argv:
            raise ValueError("argv must be a non-empty list of strings")
        if not all(isinstance(part, str) for part in argv):
            raise TypeError("every argv part must be str")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        if env:
            raise BackendUnavailable(
                "docker backend does not take a raw env: credentials travel "
                "through the broker, and the container runs with no network "
                "and a fixed environment"
            )
        if cwd is not None:
            # The container starts in /tmp with no mounts; a host cwd
            # has no meaning inside it. Refusing beats silently
            # running in the wrong directory.
            raise BackendUnavailable(
                f"docker backend has no host cwd (got {str(cwd)!r}): "
                "the container starts in /tmp with no volume mounts"
            )
        code = _COMMAND_WRAPPER.format(argv_json=json.dumps(list(argv)))
        return self.run_python(code, timeout_s=timeout_s)

    def run_python(self, code: str, *, timeout_s: int = 30) -> CommandResult:
        if not isinstance(code, str):
            raise TypeError("code must be str")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        if not self.check_available():
            raise BackendUnavailable(_UNAVAILABLE_HINT)
        try:
            result = self._sandbox.run(code, timeout_s=timeout_s)
        except SandboxUnavailable as exc:
            # The container failed to start after the probe passed
            # (daemon died mid-run): stay fail-closed and forget
            # the cached availability so the next call re-probes.
            self._available_cache = False
            raise BackendUnavailable(str(exc)) from exc
        return CommandResult(
            ok=bool(result["ok"]),
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            exit_code=result.get("exit_code"),
            timed_out=bool(result.get("timed_out", False)),
        )


register_backend(DockerBackend.name, DockerBackend)
