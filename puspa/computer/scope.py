"""Sandbox scope: the directory a backend may touch (G16).

A scope is the boundary every backend shares. File sync and
execution both resolve paths through it, so a backend can never
be tricked into reading or writing outside its own workdir:

- ``SandboxScope.resolve_write`` enforces the harness sandbox
  profile (``read_only`` refuses every write, other profiles
  allow writes only under the scope root and never inside
  protected paths such as ``.git``), reusing
  :func:`puspa.harness.sandbox_profiles.check_write_path`.
- ``SandboxScope.resolve_read`` allows reads only under the
  scope root. A backend that wants to read a file outside its
  scope must ask for a wider scope at construction time, not
  smuggle the path in per call.

The scope is deliberately separate from execution: the backend
ABC runs commands, the scope decides which paths those commands
may touch, and file sync is its own object
(:mod:`puspa.computer.sync`). That split is the hermes-agent
pattern G16 adopts (file sync + sandbox scope separated from
execution).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from puspa.harness.sandbox_profiles import (
    PROFILE_WORKSPACE_WRITE,
    ProfileDenied,
    check_write_path,
    get_profile,
)

__all__ = ["SandboxScope"]


@dataclass(frozen=True)
class SandboxScope:
    """One backend's filesystem boundary.

    ``root`` is the only directory the backend may read from or
    write to. ``profile`` is the harness sandbox profile name
    (``read_only``, ``workspace_write``, ``full``); unknown names
    are refused at construction time.
    """

    root: Path
    profile: str = PROFILE_WORKSPACE_WRITE

    def __post_init__(self) -> None:
        get_profile(self.profile)
        object.__setattr__(self, "root", Path(self.root).resolve())

    def resolve_read(self, path: str | Path) -> Path:
        """Resolve ``path`` for reading; refuse anything outside the root.

        Relative paths resolve under the root. Absolute paths must
        already sit under the root. ``..`` segments that escape the
        root are refused with :class:`ProfileDenied`.
        """
        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        resolved = target.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise ProfileDenied(
                f"read path {str(path)!r} is outside the sandbox scope {str(self.root)!r}"
            ) from None
        return resolved

    def resolve_write(self, path: str | Path) -> Path:
        """Resolve ``path`` for writing under this scope's profile.

        Delegates to
        :func:`puspa.harness.sandbox_profiles.check_write_path`
        with the scope root as the workspace root, so the profile
        rules (``read_only`` refuses all writes, protected paths
        stay read-only) apply identically here and in the harness.
        """
        return check_write_path(self.profile, path, workspace_root=self.root)
