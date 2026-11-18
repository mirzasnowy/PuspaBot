"""Local execution backend (G16).

Runs commands in a subprocess on this host, with the sandbox
policy enforced as far as a subprocess can enforce it:

- The working directory is the scope root (or a directory under
  it); anything outside the scope is refused.
- The environment is scrubbed (``PATH`` and ``HOME`` only, plus
  ``TMPDIR`` when set; ``PYTHONPATH`` and user site packages are
  removed) and any credential-looking key in the caller's ``env``
  is refused outright — credentials travel through the broker
  (:mod:`puspa.computer.broker`), never in raw env.
- A timeout kills the run and reports ``timed_out=True``.

Honest limit: this backend is NOT a security boundary. The code
still runs on the host, so it is test/dev only — the same role
as the harness :class:`puspa.harness.LocalTestExecutor`, whose
behaviour this backend preserves exactly. Production selects
``docker``; the harness constructor refuses a local-backed
executor when ``production=True``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from puspa.harness.sandbox_profiles import ProfileDenied

from .backends import CommandResult, ExecutionBackend, register_backend
from .errors import BackendError
from .scope import SandboxScope
from .sync import FileSync, LocalFileSync

__all__ = ["CREDENTIAL_ENV_HINTS", "LocalBackend"]

# Environment keys that smell like a credential. Any of these in a
# caller-supplied ``env`` is refused: the broker hands out
# short-lived tokens instead, and a raw credential in a child env
# is exactly the leak the broker exists to prevent. Matching is
# case-insensitive and substring-based, so ``MY_API_KEY`` and
# ``GITHUB_TOKEN`` are both caught.
CREDENTIAL_ENV_HINTS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE",
    "BEARER",
    "SESSION",
    "COOKIE",
    "AUTH",
)


def _looks_like_credential(key: str) -> bool:
    upper = key.upper()
    return any(hint in upper for hint in CREDENTIAL_ENV_HINTS)


class LocalBackend(ExecutionBackend):
    """Subprocess backend on this host (test/dev only)."""

    name = "local"

    def __init__(
        self,
        workdir: str | Path | None = None,
        *,
        profile: str = "workspace_write",
    ) -> None:
        if workdir is None:
            workdir = tempfile.mkdtemp(prefix="puspa-computer-local-")
        self._scope = SandboxScope(Path(workdir), profile)
        self._scope.root.mkdir(parents=True, exist_ok=True)
        self._sync = LocalFileSync(self._scope)

    # -- ABC ---------------------------------------------------------------

    @property
    def scope(self) -> SandboxScope:
        return self._scope

    @property
    def sync(self) -> FileSync:
        return self._sync

    def check_available(self) -> bool:
        """Always True: the host Python is the backend."""
        return True

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
        run_dir = self._resolve_cwd(cwd)
        child_env = self._child_env(env)
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(run_dir),
                env=child_env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                ok=False,
                stdout=_text(exc.stdout),
                stderr=_text(exc.stderr) or "timeout: execution exceeded the limit",
                exit_code=None,
                timed_out=True,
            )
        except OSError as exc:
            raise BackendError(f"local backend could not start {argv[0]!r}: {exc}") from exc
        return CommandResult(
            ok=proc.returncode == 0,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
        )

    def run_python(self, code: str, *, timeout_s: int = 30) -> CommandResult:
        if not isinstance(code, str):
            raise TypeError("code must be str")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        program = self._scope.root / "attempt.py"
        program.write_text(code, encoding="utf-8")
        return self.run_command(
            [sys.executable, str(program)],
            timeout_s=timeout_s,
        )

    # -- helpers -------------------------------------------------------------

    def _resolve_cwd(self, cwd: str | Path | None) -> Path:
        if cwd is None:
            return self._scope.root
        target = Path(cwd)
        if not target.is_absolute():
            target = self._scope.root / target
        resolved = target.resolve()
        try:
            resolved.relative_to(self._scope.root)
        except ValueError:
            raise ProfileDenied(
                f"cwd {str(cwd)!r} is outside the sandbox scope {str(self._scope.root)!r}"
            ) from None
        return resolved

    def _child_env(self, env: dict[str, str] | None) -> dict[str, str]:
        """Scrubbed base env plus the caller's additions.

        The base drops ``PYTHONPATH`` and user site packages and
        keeps only ``PATH``, ``HOME`` (and ``TMPDIR`` when set),
        mirroring the harness test executor. Any caller key that
        looks like a credential is refused instead of passed on.
        """
        base = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if "TMPDIR" in os.environ:
            base["TMPDIR"] = os.environ["TMPDIR"]
        if env:
            for key in env:
                if _looks_like_credential(key):
                    raise BackendError(
                        f"refusing credential-looking env key {key!r}: "
                        "credentials travel through the broker, never in raw env"
                    )
            base.update(env)
        return base


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


register_backend(LocalBackend.name, LocalBackend)
