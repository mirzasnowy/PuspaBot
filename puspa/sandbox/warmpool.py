"""Warm pool for sandbox containers (E-03, plan 4.3).

Plan 4.3 allows a warm pool, with one hard rule: it must
never carry state across users — delete and recreate, never
"clean". Before E-03 Puspa had no pool at all (every run was
``docker run --rm``), so the rule had nothing to attach to.
This module is the pool, with the rule enforced structurally
rather than by convention:

- A pooled container is created *stopped* (``docker create``)
  and has never run. :meth:`WarmPool.acquire` hands it to
  exactly one user for exactly one run.
- :meth:`WarmPool.release` always destroys that container
  (``docker rm -f``) and refills the pool with a freshly
  created one. There is no code path that returns a used
  container to the pool, and no "clean" step exists to call:
  the only way a container leaves the checked-out state is
  deletion.
- A slot is bound to its user at acquire time; releasing a
  slot owned by another user, or releasing the same slot
  twice, raises instead of silently freeing the wrong
  container.

The pool talks to Docker through the small
:class:`ContainerRuntime` protocol (create / start / remove /
exists), implemented for real Docker by
:class:`DockerContainerRuntime`. Tests run against a fake
runtime that records every create/remove, so the
delete-not-clean invariant is proven without a daemon. Live
container behaviour (create/start/remove through the real
daemon) awaits a Docker host (E-02's VPS).

The pool does not change the runner's default path: a
:class:`~puspa.sandbox.runner.Sandbox` without a pool still
runs one ``docker run --rm`` per job. A pool is opt-in, and
when one is given the runner borrows a slot for one run and
releases it in a ``finally`` — even on timeout — so a
container that ran for user A is destroyed before user B's
run can start.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = [
    "DEFAULT_POOL_COMMAND",
    "ContainerRuntime",
    "DockerContainerRuntime",
    "PoolError",
    "WarmPool",
    "WarmSlot",
]

#: The command a pooled container runs (read code from stdin,
#: same as the runner's one-shot path). The runner passes its
#: own ``python_bin`` here when it builds a pool; the default
#: matches the runner's default binary.
DEFAULT_POOL_COMMAND: tuple[str, ...] = ("/usr/bin/python3.12", "-")

#: Prefix for pool-created container names (unique suffix per
#: container, so a stale name can never collide with a new one).
_CONTAINER_NAME_PREFIX = "puspa-warm-"


class PoolError(ValueError):
    """Warm-pool misuse (double release, wrong owner, bad size).

    Raised before any container operation, so a misused pool
    fails instead of freeing or reusing the wrong container.
    """


class ContainerRuntime:
    """The container operations a warm pool needs.

    A protocol in the duck-typed sense: any object with these
    four methods works. :class:`DockerContainerRuntime` is the
    real one; tests use a fake that records calls.
    """

    def create(self, image: str, flags: list[str], command: list[str]) -> str:
        """Create a stopped container; return its id (or name)."""
        raise NotImplementedError

    def start(self, container_id: str, *, input: str, timeout_s: int) -> dict | None:
        """Start a created container with ``input`` on stdin.

        Returns the run result (``ok`` / ``stdout`` /
        ``stderr`` / ``exit_code`` / ``timed_out``) or None
        when the container failed to start.
        """
        raise NotImplementedError

    def remove(self, container_id: str) -> None:
        """Force-remove a container (no error when already gone)."""
        raise NotImplementedError

    def exists(self, container_id: str) -> bool:
        """True when the container still exists."""
        raise NotImplementedError


class DockerContainerRuntime(ContainerRuntime):
    """The real container runtime, via the Docker CLI."""

    def __init__(self, docker_cmd: list[str] | None = None) -> None:
        cmd = docker_cmd if docker_cmd is not None else self._docker_cmd()
        if cmd is None:
            raise PoolError("docker binary not found; cannot run a warm pool")
        self._cmd = cmd

    @staticmethod
    def _docker_cmd() -> list[str] | None:
        path = shutil.which("docker")
        return [path] if path is not None else None

    def create(self, image: str, flags: list[str], command: list[str]) -> str:
        name = _CONTAINER_NAME_PREFIX + uuid.uuid4().hex[:12]
        try:
            proc = subprocess.run(
                [*self._cmd, "create", "--name", name, *flags, image, *command],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PoolError(f"docker create failed: {exc}") from exc
        if proc.returncode != 0:
            raise PoolError(f"docker create failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout.strip() or name

    def start(self, container_id: str, *, input: str, timeout_s: int) -> dict | None:
        try:
            proc = subprocess.run(
                [*self._cmd, "start", "--attach", "--interactive", container_id],
                input=input,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_s + 15,
            )
        except subprocess.TimeoutExpired:
            self.remove(container_id)
            return {
                "ok": False,
                "stdout": "",
                "stderr": "timeout: eksekusi melewati batas",
                "exit_code": None,
                "timed_out": True,
            }
        except OSError:
            return None
        if proc.returncode == 125 or "Error response from daemon" in proc.stderr:
            return None
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout[:100_000],
            "stderr": proc.stderr[:100_000],
            "exit_code": proc.returncode,
            "timed_out": False,
        }

    def remove(self, container_id: str) -> None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                [*self._cmd, "rm", "--force", container_id],
                check=False,
                capture_output=True,
                timeout=15,
            )

    def exists(self, container_id: str) -> bool:
        try:
            proc = subprocess.run(
                [*self._cmd, "inspect", container_id],
                check=False,
                capture_output=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return proc.returncode == 0


@dataclass
class WarmSlot:
    """One pooled container, checked out to exactly one user.

    ``released`` guards double release: the pool marks a slot
    released the moment it is returned, so a second release of
    the same slot raises instead of removing a container that
    may already belong to someone else.
    """

    container_id: str
    user_id: str
    created_at: float = field(default_factory=time.time)
    released: bool = False


class WarmPool:
    """A pool of pre-created, never-run containers.

    ``flags_builder`` supplies fresh ``docker run`` flags at
    every creation (the runner passes its own
    ``docker_flags``), so a policy or quota change is picked
    up on the next refill instead of being baked into warm
    containers. ``command`` is the container's command (the
    runner's ``python_bin`` plus ``"-"``); it must match the
    runner that borrows the pool, or the pooled container runs
    the wrong interpreter. ``size`` is how many stopped
    containers are kept warm; acquiring past the warm stock
    creates a fresh container on demand (still never used,
    still destroyed on release). ``clock`` is an injectable
    time source.
    """

    def __init__(
        self,
        runtime: ContainerRuntime,
        *,
        image: str,
        flags_builder: Callable[[], list[str]],
        command: list[str] | tuple[str, ...] | None = None,
        size: int = 2,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not image:
            raise PoolError("warm pool needs a non-empty image")
        if size < 1:
            raise PoolError("warm pool size must be >= 1")
        self._runtime = runtime
        self._image = image
        self._flags_builder = flags_builder
        self._command = list(command) if command is not None else list(DEFAULT_POOL_COMMAND)
        self._size = size
        self._clock = clock if clock is not None else time.time
        self._warm: list[str] = []
        self._checked_out: dict[str, WarmSlot] = {}

    # -- state ---------------------------------------------------------------

    @property
    def size(self) -> int:
        """How many stopped containers the pool keeps warm."""
        return self._size

    @property
    def warm_count(self) -> int:
        """Stopped containers currently waiting in the pool."""
        return len(self._warm)

    @property
    def checked_out_count(self) -> int:
        """Containers currently out on a run."""
        return len(self._checked_out)

    # -- lifecycle -------------------------------------------------------------

    def warm_up(self) -> None:
        """Fill the pool to ``size`` (no-op when already full)."""
        while len(self._warm) < self._size:
            self._warm.append(self._create_fresh())

    def acquire(self, user_id: str) -> WarmSlot:
        """Check out one fresh container for ``user_id``.

        The container has never run. When the warm stock is
        empty, a fresh container is created on demand — still
        never used, still destroyed on release.
        """
        if not user_id:
            raise PoolError("warm pool acquire needs a non-empty user_id")
        container_id = self._warm.pop() if self._warm else self._create_fresh()
        slot = WarmSlot(container_id=container_id, user_id=user_id, created_at=self._clock())
        self._checked_out[container_id] = slot
        return slot

    def start(self, slot: WarmSlot, *, input: str, timeout_s: int) -> dict | None:
        """Start a checked-out slot with ``input`` on stdin.

        Delegates to the runtime; returns the run result (same
        shape as the runner's one-shot result) or None when the
        container failed to start. Refuses a slot that is not
        checked out from this pool or was already released: a
        run must never start on a container the pool has
        destroyed (or never handed out).
        """
        owner = self._checked_out.get(slot.container_id)
        if owner is not slot or slot.released:
            raise PoolError(
                f"warm slot {slot.container_id!r} is not checked out; "
                "refusing to start a container that is not live for a run"
            )
        return self._runtime.start(slot.container_id, input=input, timeout_s=timeout_s)

    def release(self, slot: WarmSlot, *, user_id: str | None = None) -> None:
        """Return a slot: destroy its container, refill the pool.

        The container is removed (``rm -f``) and a fresh one is
        created to refill the warm stock. Nothing is "cleaned"
        and nothing is reused: the container id that ran for
        this user never serves another run. ``user_id``, when
        given, must match the slot's owner; a mismatch, or a
        second release of the same slot, raises.
        """
        if slot.released:
            raise PoolError(
                f"warm slot {slot.container_id!r} was already released; "
                "a container is destroyed exactly once"
            )
        owner = self._checked_out.get(slot.container_id)
        if owner is None or owner is not slot:
            raise PoolError(f"warm slot {slot.container_id!r} is not checked out from this pool")
        if user_id is not None and user_id != slot.user_id:
            raise PoolError(
                f"warm slot {slot.container_id!r} belongs to user "
                f"{slot.user_id!r}, not {user_id!r}; refusing to release "
                "another user's container"
            )
        slot.released = True
        del self._checked_out[slot.container_id]
        self._runtime.remove(slot.container_id)
        self._refill()

    def shutdown(self) -> None:
        """Destroy every warm (idle) container; checked-out slots stay.

        A checked-out container is mid-run; destroying it would
        kill a job. It is destroyed by its own :meth:`release`
        instead. After shutdown the pool holds nothing warm.
        """
        warm, self._warm = self._warm, []
        for container_id in warm:
            self._runtime.remove(container_id)

    # -- helpers ---------------------------------------------------------------

    def _create_fresh(self) -> str:
        flags = self._flags_builder()
        return self._runtime.create(self._image, flags, list(self._command))

    def _refill(self) -> None:
        """Create one fresh container when the warm stock is short."""
        if len(self._warm) < self._size:
            self._warm.append(self._create_fresh())
