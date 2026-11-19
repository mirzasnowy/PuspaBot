"""Filesystem API inside the sandbox (G34, OpenShell SFTP pattern, rewritten).

OpenShell moves files in and out of a sandbox over SFTP
(``crates/openshell-sandbox/src/sftp.rs``): the handler is
backed by a directory file descriptor, so every operation is
root-anchored and cannot escape the workdir; handles, packet
sizes, and directory entries are all bounded. The public CLI
surface is ``sandbox upload`` / ``sandbox download`` against a
named session.

Puspa takes that shape, rewritten against the backend ABC Puspa
already has:

- :class:`SandboxFilesystem` — the API: read/write/list/stat/
  delete plus binary upload/download, all addressed relative
  to the session workdir. Paths are resolved through the
  backend's :class:`puspa.computer.scope.SandboxScope`, so the
  root-anchoring rule is the scope's rule, not a new one.
- :class:`LocalSandboxFilesystem` — the real implementation
  for backends with a directory on this host (the local
  backend). Writes enforce the session's disk quota
  (:func:`puspa.sandbox.quota.measure_disk_usage`) *before*
  the bytes land, and the per-file size cap.
- :class:`DockerSandboxFilesystem` — the Docker implementation:
  every operation raises :class:`puspa.computer.errors.BackendUnavailable`
  with a reason, because a Docker backend has no persistent
  filesystem (every run is a fresh container). This is the
  same answer the existing :class:`puspa.computer.sync.NoFileSync`
  gives, extended to list/stat/delete/upload/download so the
  API is uniform across backends instead of failing only on
  some operations.
- :func:`filesystem_for_backend` — picks the implementation
  from the backend's registry name; an unknown backend is
  refused, never silently given local files.

File size cap: 1,000,000 bytes per file, the same cap the
existing file sync uses (``_MAX_FILE_CHARS`` in
:mod:`puspa.computer.sync`).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from puspa.computer.backends import ExecutionBackend
    from puspa.computer.scope import SandboxScope

from .quota import QuotaRegistry, ResourceQuota, measure_disk_usage

__all__ = [
    "DockerSandboxFilesystem",
    "FileEntry",
    "LocalSandboxFilesystem",
    "SandboxFilesystem",
    "filesystem_for_backend",
    "measure_disk_usage",
]

#: Per-file cap (bytes), matching the existing file-sync cap.
MAX_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class FileEntry:
    """One entry in a sandbox directory listing."""

    path: str
    size_bytes: int
    is_dir: bool
    modified_at: float | None = None


class SandboxFilesystem(abc.ABC):
    """Read/write/list files inside one sandbox session's workdir.

    All paths are sandbox-relative (``"notes/todo.txt"``); an
    absolute path, or a ``..`` that escapes the workdir, is
    refused by the scope. Text methods use UTF-8.
    """

    @property
    @abc.abstractmethod
    def root(self) -> Path:
        """The session workdir this filesystem is anchored at."""

    @abc.abstractmethod
    def read_text(self, path: str) -> str:
        """Read a text file inside the workdir."""

    @abc.abstractmethod
    def write_text(self, path: str, content: str) -> Path:
        """Write a text file inside the workdir; return its resolved path."""

    @abc.abstractmethod
    def download(self, path: str) -> bytes:
        """Read a file as bytes (binary download)."""

    @abc.abstractmethod
    def upload(self, path: str, data: bytes) -> Path:
        """Write bytes (binary upload); return the resolved path."""

    @abc.abstractmethod
    def list_dir(self, path: str = ".") -> list[FileEntry]:
        """List a directory inside the workdir, sorted by path."""

    @abc.abstractmethod
    def stat(self, path: str) -> FileEntry:
        """Metadata for one path inside the workdir."""

    @abc.abstractmethod
    def delete(self, path: str, *, recursive: bool = False) -> bool:
        """Delete a file (or, with ``recursive=True``, a directory tree)."""


class LocalSandboxFilesystem(SandboxFilesystem):
    """Filesystem API for a backend with a real directory on this host.

    ``scope`` is the backend's scope (the jail); ``quota`` is
    the session quota whose ``disk_mb`` bounds the workspace.
    Every write checks the projected disk usage first, so a
    write that would exceed the quota is refused before any
    byte lands.
    """

    def __init__(self, scope: SandboxScope, quota: ResourceQuota | None = None) -> None:
        self.scope = scope
        self.quota = quota
        self.scope.root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self.scope.root

    def read_text(self, path: str) -> str:
        return self.scope.resolve_read(path).read_text(encoding="utf-8")

    def write_text(self, path: str, content: str) -> Path:
        if not isinstance(content, str):
            raise TypeError("content must be str")
        return self.upload(path, content.encode("utf-8"))

    def download(self, path: str) -> bytes:
        resolved = self.scope.resolve_read(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"no file at {str(path)!r} inside the sandbox")
        data = resolved.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(
                f"file {str(path)!r} is {len(data)} bytes, over the "
                f"{MAX_FILE_BYTES}-byte per-file cap"
            )
        return data

    def upload(self, path: str, data: bytes) -> Path:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        payload = bytes(data)
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError(
                f"upload of {len(payload)} bytes exceeds the {MAX_FILE_BYTES}-byte per-file cap"
            )
        resolved = self.scope.resolve_write(path)
        if self.quota is not None:
            existing = resolved.stat().st_size if resolved.is_file() else 0
            extra = len(payload) - existing
            if extra > 0:
                QuotaRegistry.check_disk(self.scope.root, self.quota, extra)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(payload)
        return resolved

    def list_dir(self, path: str = ".") -> list[FileEntry]:
        resolved = self.scope.resolve_read(path)
        if not resolved.is_dir():
            raise FileNotFoundError(f"no directory at {str(path)!r} inside the sandbox")
        entries = []
        for child in sorted(resolved.iterdir(), key=lambda p: p.name):
            entries.append(self._entry(child, base=self.scope.root))
        return entries

    def stat(self, path: str) -> FileEntry:
        resolved = self.scope.resolve_read(path)
        if not resolved.exists() and not resolved.is_symlink():
            raise FileNotFoundError(f"no file at {str(path)!r} inside the sandbox")
        return self._entry(resolved, base=self.scope.root)

    def delete(self, path: str, *, recursive: bool = False) -> bool:
        resolved = self.scope.resolve_write(path)
        if resolved == self.scope.root:
            raise ValueError("refusing to delete the sandbox workdir itself")
        if resolved.is_symlink() or resolved.is_file():
            resolved.unlink()
            return True
        if resolved.is_dir():
            if not recursive:
                try:
                    resolved.rmdir()
                except OSError:
                    raise ValueError(
                        f"directory {str(path)!r} is not empty; "
                        "pass recursive=True to delete a tree"
                    ) from None
                return True
            import shutil

            shutil.rmtree(resolved)
            return True
        return False

    @staticmethod
    def _entry(path: Path, *, base: Path) -> FileEntry:
        try:
            info = path.stat()
            size = info.st_size if path.is_file() else 0
            modified = info.st_mtime
        except OSError:
            size, modified = 0, None
        try:
            rel = str(path.relative_to(base))
        except ValueError:
            rel = path.name
        return FileEntry(
            path=rel,
            size_bytes=size,
            is_dir=path.is_dir(),
            modified_at=modified,
        )


class DockerSandboxFilesystem(SandboxFilesystem):
    """Filesystem API for the Docker backend: always refused.

    A Docker backend has no persistent filesystem — every run
    is a fresh container with no mounts — so there is nothing
    to read, list, or write. Every operation raises
    :class:`BackendUnavailable` naming the reason and the
    alternative (run the edit through ``run_python``), instead
    of pretending a file exists. This is the same answer the
    existing ``NoFileSync`` gives, extended to the whole
    filesystem API so callers get one uniform refusal.
    """

    _MESSAGE = (
        "docker backend has no persistent filesystem "
        "(fresh container per run, no volume mounts); "
        "edits run through run_python only"
    )

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root is not None else Path("/tmp")

    @property
    def root(self) -> Path:
        return self._root

    def read_text(self, path: str) -> str:
        raise _unavailable()

    def write_text(self, path: str, content: str) -> Path:
        raise _unavailable()

    def download(self, path: str) -> bytes:
        raise _unavailable()

    def upload(self, path: str, data: bytes) -> Path:
        raise _unavailable()

    def list_dir(self, path: str = ".") -> list[FileEntry]:
        raise _unavailable()

    def stat(self, path: str) -> FileEntry:
        raise _unavailable()

    def delete(self, path: str, *, recursive: bool = False) -> bool:
        raise _unavailable()


def _unavailable():
    """Build the BackendUnavailable for the refusing filesystem."""
    from puspa.computer.errors import BackendUnavailable

    return BackendUnavailable(DockerSandboxFilesystem._MESSAGE)


def filesystem_for_backend(
    backend: ExecutionBackend, quota: ResourceQuota | None = None
) -> SandboxFilesystem:
    """The filesystem API for ``backend`` (picked by registry name).

    ``"local"`` gets the real scoped implementation;
    ``"docker"`` gets the refusing implementation (no
    persistent filesystem). Any other backend name is refused
    with :class:`BackendError`: an unknown backend never
    silently gets local files.
    """
    if backend.name == "local":
        return LocalSandboxFilesystem(backend.scope, quota)
    if backend.name == "docker":
        return DockerSandboxFilesystem(backend.scope.root)
    from puspa.computer.errors import BackendError

    raise BackendError(
        f"no sandbox filesystem for backend {backend.name!r}; "
        "registered filesystem backends: local, docker"
    )
