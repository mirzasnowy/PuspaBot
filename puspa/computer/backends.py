"""Execution-backend ABC and the name -> variant registry (G16).

Pattern studied, then rewritten as Puspa's own code (see the G16
section of the repo-comparison report): hermes-agent keeps one
ABC plus one module per variant plus a name -> variant table
(``tools/environments/``: ``local``, ``docker``, ``ssh``,
``modal``, ``daytona``, ``vercel_sandbox``, plus backend
plugins). The terminal tool never knows which backend runs it;
selection is by configuration; one conformance suite verifies
every backend behaves the same.

Puspa starts with two variants:

- ``local`` (:mod:`puspa.computer.local`) — subprocess on this
  host with the sandbox policy enforced. Test/dev only: it is
  NOT a security boundary, and production must never select it.
- ``docker`` (:mod:`puspa.computer.docker`) — the hardened
  Docker sandbox from :mod:`puspa.sandbox`. The production
  backend. On hosts where containers cannot start (this dev VM
  rejects the ``setns`` syscall) it raises
  :class:`puspa.computer.errors.BackendUnavailable` honestly
  instead of faking a passing backend.

Conformance rule: every registered backend must pass the same
suite in ``tests/test_computer_backends.py``. A backend that
cannot run on this host (Docker here) skips with a clear reason
instead of failing or faking. A new variant is added by writing
its module, registering it in :data:`BACKENDS`, and adding it to
the conformance parametrisation — nothing else.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path

from .errors import BackendError
from .scope import SandboxScope
from .sync import FileSync

__all__ = [
    "BACKENDS",
    "CommandResult",
    "ExecutionBackend",
    "get_backend",
    "list_backends",
    "register_backend",
]

_MAX_OUTPUT_CHARS = 100_000


@dataclass
class CommandResult:
    """Outcome of one command or program execution on a backend.

    ``ok`` is False for a program that ran and failed (non-zero
    exit, assertion error): a failing program is a result, not a
    backend failure. Backend failures raise instead (see
    :class:`puspa.computer.errors.BackendUnavailable`).
    """

    ok: bool
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False

    def __post_init__(self) -> None:
        self.stdout = self.stdout[:_MAX_OUTPUT_CHARS]
        self.stderr = self.stderr[:_MAX_OUTPUT_CHARS]


class ExecutionBackend(abc.ABC):
    """Runs commands and programs inside one sandbox scope.

    Subclasses implement one module per variant. Callers use the
    ABC only: ``run_command`` for an argv, ``run_python`` for a
    program text, ``sync`` for file movement, ``check_available``
    for a cheap liveness probe. File sync and scope are separate
    objects — a backend never invents its own path rules.
    """

    #: Registry name (``"local"``, ``"docker"``). Unique per variant.
    name: str = "base"

    @property
    @abc.abstractmethod
    def scope(self) -> SandboxScope:
        """The filesystem boundary this backend may touch."""

    @property
    @abc.abstractmethod
    def sync(self) -> FileSync:
        """File movement for this backend (separate from execution)."""

    @abc.abstractmethod
    def check_available(self) -> bool:
        """True only when this backend can actually run code now.

        Cheap and side-effect free apart from its own caching: the
        Docker backend probes the daemon and a throwaway container
        here, the local backend always returns True.
        """

    @abc.abstractmethod
    def run_command(
        self,
        argv: list[str],
        *,
        cwd: str | Path | None = None,
        timeout_s: int = 30,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run ``argv`` and return its result.

        ``cwd`` defaults to the scope root and must stay inside
        it. ``env`` is merged over a scrubbed base environment;
        credential-looking keys are refused (credentials travel
        through the broker, never raw env — see
        :mod:`puspa.computer.broker`).
        """

    @abc.abstractmethod
    def run_python(self, code: str, *, timeout_s: int = 30) -> CommandResult:
        """Run ``code`` as a Python program and return its result."""

    def close(self) -> None:
        """Release backend resources (default: nothing to release)."""
        return


# -- registry ------------------------------------------------------------
#
# Name -> variant class. Populated by the variant modules at import
# time so importing a variant registers it; :func:`get_backend`
# is the only way callers pick a variant.

BACKENDS: dict[str, type[ExecutionBackend]] = {}


def register_backend(name: str, cls: type[ExecutionBackend]) -> None:
    """Register one variant class under ``name`` (idempotent)."""
    if not isinstance(name, str) or not name:
        raise ValueError("backend name must be a non-empty string")
    if not (isinstance(cls, type) and issubclass(cls, ExecutionBackend)):
        raise TypeError("backend class must subclass ExecutionBackend")
    BACKENDS[name] = cls


def list_backends() -> list[str]:
    """Registered backend names, in registration order."""
    return list(BACKENDS)


def get_backend(name: str, **kwargs) -> ExecutionBackend:
    """Build the backend registered as ``name``.

    Raises :class:`puspa.computer.errors.BackendError` for an
    unknown name, listing the registered names so a typo is
    diagnosable instead of silently falling back to another
    backend. There is no implicit default: the caller (or the
    config, see :mod:`puspa.computer.config`) picks the name.
    """
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise BackendError(
            f"unknown execution backend {name!r}; registered backends: "
            f"{', '.join(list_backends()) or '(none)'}"
        ) from None
    return cls(**kwargs)
