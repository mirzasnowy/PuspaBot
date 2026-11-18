"""Backend selection by configuration (G16).

One place decides which execution backend runs: a
:class:`ComputerConfig` built from a dict, from the environment,
or from defaults. The default backend is ``local`` — the safe
choice for development, where Docker containers cannot start —
and production must set ``docker`` explicitly. An unknown backend
name is refused at config time, never silently replaced with
another backend.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .backends import BACKENDS, get_backend
from .errors import BackendError

__all__ = ["ENV_BACKEND", "ComputerConfig"]

#: Environment variable that selects the backend.
ENV_BACKEND = "PUSPA_COMPUTER_BACKEND"

#: Default backend for development (Docker cannot run on this VM).
DEFAULT_BACKEND = "local"


@dataclass(frozen=True)
class ComputerConfig:
    """Which execution backend runs, and with what scope.

    ``backend`` must be a registered backend name. ``workdir`` is
    the scope root for backends that have a local directory
    (``local``); the Docker backend ignores it (fresh container
    per run, no mounts). ``profile`` is the harness sandbox
    profile (``read_only``, ``workspace_write``, ``full``).
    ``broker_token_ttl_s`` is the default lifetime of broker
    tokens issued for this backend's processes.
    """

    backend: str = DEFAULT_BACKEND
    workdir: Path | None = None
    profile: str = "workspace_write"
    broker_token_ttl_s: int = 300

    def __post_init__(self) -> None:
        if self.backend not in BACKENDS:
            raise BackendError(
                f"unknown execution backend {self.backend!r}; registered backends: "
                f"{', '.join(BACKENDS) or '(none)'}"
            )
        if self.broker_token_ttl_s <= 0:
            raise ValueError("broker_token_ttl_s must be > 0")

    @classmethod
    def from_dict(cls, data: dict) -> ComputerConfig:
        """Build from a plain dict (config file, operator input)."""
        known = {"backend", "workdir", "profile", "broker_token_ttl_s"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown ComputerConfig keys: {', '.join(unknown)}")
        workdir = data.get("workdir")
        return cls(
            backend=data.get("backend", DEFAULT_BACKEND),
            workdir=Path(workdir) if workdir is not None else None,
            profile=data.get("profile", "workspace_write"),
            broker_token_ttl_s=data.get("broker_token_ttl_s", 300),
        )

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ComputerConfig:
        """Build from the environment (``PUSPA_COMPUTER_BACKEND``)."""
        source = os.environ if env is None else env
        return cls(backend=source.get(ENV_BACKEND, DEFAULT_BACKEND))

    def build_backend(self):
        """Instantiate the configured backend."""
        kwargs: dict = {}
        if self.workdir is not None:
            kwargs["workdir"] = self.workdir
        kwargs["profile"] = self.profile
        return get_backend(self.backend, **kwargs)
