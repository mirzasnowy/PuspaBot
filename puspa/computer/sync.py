"""File sync, separated from execution (G16).

A backend runs commands; a :class:`FileSync` moves files in and
out of the backend's scope. The split matters because the two
have different failure modes: a command can fail and still be a
result, while a sync outside the scope is always refused, and a
backend with no persistent filesystem (Docker: every run is a
fresh container) has no sync at all.

- :class:`LocalFileSync` reads and writes under a
  :class:`puspa.computer.scope.SandboxScope`, enforcing the
  scope's profile on every write.
- :class:`NoFileSync` is what a backend without a persistent
  filesystem returns: every operation raises
  :class:`puspa.computer.errors.BackendError` with a clear
  reason, mirroring the harness sandbox executor (edits in
  production run through ``run_python`` only).
"""

from __future__ import annotations

import abc
from pathlib import Path

from .errors import BackendError
from .scope import SandboxScope

__all__ = ["FileSync", "LocalFileSync", "NoFileSync"]

_MAX_FILE_CHARS = 1_000_000


class FileSync(abc.ABC):
    """Move files in and out of a backend's scope."""

    @abc.abstractmethod
    def write(self, path: str | Path, content: str, *, profile: str | None = None) -> Path:
        """Write ``content`` to ``path`` inside the scope; return the resolved path."""

    @abc.abstractmethod
    def read(self, path: str | Path) -> str:
        """Read the file at ``path`` inside the scope."""

    @abc.abstractmethod
    def exists(self, path: str | Path) -> bool:
        """True when ``path`` exists inside the scope."""


class LocalFileSync(FileSync):
    """File sync for a backend with a real directory on this host.

    Writes go through the scope's profile (``read_only`` refuses
    every write; protected paths stay read-only). Reads stay
    inside the scope root. ``profile`` on :meth:`write` overrides
    the scope profile for that one call, when the caller needs a
    narrower profile than the scope default.
    """

    def __init__(self, scope: SandboxScope) -> None:
        self.scope = scope
        self.scope.root.mkdir(parents=True, exist_ok=True)

    def write(self, path: str | Path, content: str, *, profile: str | None = None) -> Path:
        if not isinstance(content, str):
            raise TypeError("content must be str")
        if len(content) > _MAX_FILE_CHARS:
            raise ValueError(f"content exceeds {_MAX_FILE_CHARS} characters")
        if profile is not None:
            from puspa.harness.sandbox_profiles import check_write_path

            resolved = check_write_path(profile, path, workspace_root=self.scope.root)
        else:
            resolved = self.scope.resolve_write(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return resolved

    def read(self, path: str | Path) -> str:
        resolved = self.scope.resolve_read(path)
        return resolved.read_text(encoding="utf-8")

    def exists(self, path: str | Path) -> bool:
        try:
            return self.scope.resolve_read(path).exists()
        except Exception:
            return False


class NoFileSync(FileSync):
    """File sync for a backend with no persistent filesystem.

    ``reason`` names the backend so the error tells the operator
    what to use instead (for Docker: run the edit through
    ``run_python``).
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def write(self, path: str | Path, content: str, *, profile: str | None = None) -> Path:
        raise BackendError(
            f"this backend has no persistent filesystem ({self.reason}); "
            "edits run through run_python only"
        )

    def read(self, path: str | Path) -> str:
        raise BackendError(
            f"this backend has no persistent filesystem ({self.reason}); "
            "read files through run_python only"
        )

    def exists(self, path: str | Path) -> bool:
        return False
