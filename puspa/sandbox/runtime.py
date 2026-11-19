"""Stronger-isolation runtimes and spawn measurement (E-03, plan 4.3).

Plan 4.3 asks for a stronger-isolation runtime (gVisor or Kata)
*if performance allows*, measured against the spawn target of
under 3 seconds (gate 9). Before E-03 Puspa had no runtime
selection at all: every container ran under the daemon's
default runtime (runc), with nothing recording the choice or
its cost. This module adds both, as config plus a measurement
harness — not as a switch flipped blindly:

- :class:`RuntimeConfig` — the runtime for one sandbox
  (``runc`` default, ``runsc`` for gVisor, ``kata`` for Kata
  Containers) plus the spawn target. A non-default runtime is
  refused when the daemon does not offer it (checked against
  ``docker info`` when the available set is known), never
  silently run under runc instead.
- :func:`measure_spawn` / :func:`evaluate_runtimes` — time a
  spawn (any callable that starts a container and returns,
  real or fake) and report whether it meets the target. The
  harness takes the spawn function as a parameter, so tests
  measure fake spawns deterministically and the real
  measurement runs on a Docker host through
  :func:`docker_spawn_probe`.
- :func:`list_docker_runtimes` — the runtimes the local daemon
  offers (empty set when Docker is absent or unreadable:
  unknown means unavailable, never "assume runc is there").

What this module does *not* decide: which runtime production
uses. gVisor's syscall interception costs CPU on every call
and Kata boots a lightweight VM per container; both can break
the 3-second spawn target or slow the agent workload. The
measurement exists so that choice is made from numbers on the
real host (E-02's VPS), not from a guess made here. The
default stays runc until a measurement says otherwise.
"""

from __future__ import annotations

import enum
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "DEFAULT_SPAWN_TARGET_S",
    "IsolationRuntime",
    "IsolationRuntimeError",
    "RuntimeConfig",
    "SpawnMeasurement",
    "docker_spawn_probe",
    "evaluate_runtimes",
    "list_docker_runtimes",
    "measure_spawn",
]

#: Gate 9: a sandbox must spawn in under 3 seconds.
DEFAULT_SPAWN_TARGET_S = 3.0

#: How long a real spawn probe may take before it is a failure
#: rather than a slow measurement (twice the gate target plus
#: pull slack is not needed: the image must already be present).
_PROBE_TIMEOUT_S = 30


class IsolationRuntimeError(ValueError):
    """An isolation runtime is unknown, unavailable, or unusable.

    Raised at config time (unknown name, non-default runtime
    not offered by the daemon), never at container start: a
    container must never start under a different runtime than
    the one configured.
    """


class IsolationRuntime(enum.Enum):
    """Container runtimes the sandbox may run under.

    ``RUNC`` is the Docker default (namespaces + cgroups).
    ``GVisor`` (``runsc``) interposes a user-space kernel
    between the container and the host; ``KATA`` (``kata``)
    runs each container inside a lightweight VM. Both are
    stronger isolation than runc, both cost spawn time and CPU.
    """

    RUNC = "runc"
    GVisor = "runsc"
    KATA = "kata"

    @classmethod
    def from_name(cls, name: str) -> IsolationRuntime:
        """Parse a runtime name; unknown names are refused."""
        cleaned = (name or "").strip().lower()
        for runtime in cls:
            if runtime.value == cleaned:
                return runtime
        raise IsolationRuntimeError(
            f"unknown isolation runtime {name!r}; expected one of "
            f"{', '.join(runtime.value for runtime in cls)}"
        )


@dataclass(frozen=True)
class RuntimeConfig:
    """The isolation runtime for a sandbox, plus its spawn target.

    ``spawn_target_s`` is gate 9 (under 3 seconds). It is
    config, not enforcement: the harness measures against it,
    the runner does not kill a slow spawn (a slow spawn is a
    measurement to act on, not a request to fail).
    """

    runtime: IsolationRuntime = IsolationRuntime.RUNC
    spawn_target_s: float = DEFAULT_SPAWN_TARGET_S

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, IsolationRuntime):
            raise IsolationRuntimeError("runtime must be an IsolationRuntime")
        if not isinstance(self.spawn_target_s, (int, float)) or isinstance(
            self.spawn_target_s, bool
        ):
            raise IsolationRuntimeError("spawn_target_s must be a number")
        if self.spawn_target_s <= 0:
            raise IsolationRuntimeError("spawn_target_s must be > 0")

    @classmethod
    def from_name(
        cls, name: str, *, spawn_target_s: float = DEFAULT_SPAWN_TARGET_S
    ) -> RuntimeConfig:
        """Build from a runtime name (``"runc"`` / ``"runsc"`` / ``"kata"``)."""
        return cls(runtime=IsolationRuntime.from_name(name), spawn_target_s=spawn_target_s)

    def validate(self, available: frozenset[str] | None) -> None:
        """Refuse a runtime the daemon does not offer.

        ``available`` is the set from :func:`list_docker_runtimes`
        (runtime names as the daemon reports them). ``None``
        means "unknown" — the check is skipped and the daemon
        decides at container start (its refusal is still
        fail-closed, it just happens later). An empty set means
        "the daemon offers nothing we could read", which refuses
        every non-default runtime.
        """
        if available is None or self.runtime is IsolationRuntime.RUNC:
            return
        if self.runtime.value not in available:
            raise IsolationRuntimeError(
                f"isolation runtime {self.runtime.value!r} is not offered by "
                f"this Docker daemon (offers: {', '.join(sorted(available)) or 'none'}); "
                "refusing to fall back to runc silently"
            )

    def docker_flags(self) -> list[str]:
        """``--runtime <name>`` for a non-default runtime, else []."""
        if self.runtime is IsolationRuntime.RUNC:
            return []
        return ["--runtime", self.runtime.value]

    def to_dict(self) -> dict:
        """Plain-dict form (for records and reports)."""
        return {"runtime": self.runtime.value, "spawn_target_s": self.spawn_target_s}


@dataclass(frozen=True)
class SpawnMeasurement:
    """One timed spawn: how long, against which target, did it pass.

    ``error`` is set when the spawn itself failed (the
    container never started): a failed spawn never meets the
    target, however fast the failure was.
    """

    runtime: str
    spawn_s: float
    target_s: float
    meets_target: bool
    error: str | None = None

    def to_dict(self) -> dict:
        """Plain-dict form (for reports)."""
        return {
            "runtime": self.runtime,
            "spawn_s": self.spawn_s,
            "target_s": self.target_s,
            "meets_target": self.meets_target,
            "error": self.error,
        }


def measure_spawn(
    spawn_fn: Callable[[], None],
    *,
    runtime: str,
    target_s: float = DEFAULT_SPAWN_TARGET_S,
    clock: Callable[[], float] | None = None,
) -> SpawnMeasurement:
    """Time one spawn and judge it against ``target_s``.

    ``spawn_fn`` starts a container and returns when it is
    running (or raises when it cannot start). The clock is
    injectable so tests measure fake spawns deterministically.
    """
    now = clock if clock is not None else time.monotonic
    started = now()
    error: str | None = None
    try:
        spawn_fn()
    except Exception as exc:  # the error is the measurement
        error = str(exc)
    elapsed = now() - started
    return SpawnMeasurement(
        runtime=runtime,
        spawn_s=elapsed,
        target_s=target_s,
        meets_target=error is None and elapsed < target_s,
        error=error,
    )


def evaluate_runtimes(
    configs: list[RuntimeConfig],
    spawn_fn_for: Callable[[RuntimeConfig], Callable[[], None]],
    *,
    clock: Callable[[], float] | None = None,
) -> dict:
    """Measure a spawn under each config; return the report.

    The report is ``{"measurements": [...], "fastest": ...,
    "all_meet_target": bool}``: ``fastest`` is the runtime name
    with the smallest successful spawn (``None`` when none
    succeeded), and ``all_meet_target`` is True only when every
    measured runtime met its target. The report decides nothing
    by itself; it is the evidence the runtime choice is made
    from.
    """
    measurements = [
        measure_spawn(
            spawn_fn_for(config),
            runtime=config.runtime.value,
            target_s=config.spawn_target_s,
            clock=clock,
        )
        for config in configs
    ]
    successful = [item for item in measurements if item.error is None]
    fastest = min(successful, key=lambda item: item.spawn_s).runtime if successful else None
    return {
        "measurements": [item.to_dict() for item in measurements],
        "fastest": fastest,
        "all_meet_target": bool(measurements) and all(item.meets_target for item in measurements),
    }


def list_docker_runtimes(docker_cmd: list[str] | None = None) -> frozenset[str]:
    """Runtime names the local Docker daemon offers (empty when unknown).

    Reads ``docker info --format '{{json .Runtimes}}'``. Any
    failure (no binary, daemon down, unparsable output) returns
    an empty set: unknown is unavailable, never assumed.
    """
    cmd = docker_cmd if docker_cmd is not None else _docker_cmd()
    if cmd is None:
        return frozenset()
    try:
        proc = subprocess.run(
            [*cmd, "info", "--format", "{{json .Runtimes}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    if proc.returncode != 0:
        return frozenset()
    try:
        runtimes = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return frozenset()
    if not isinstance(runtimes, dict):
        return frozenset()
    return frozenset(name for name in runtimes if isinstance(name, str) and name)


def docker_spawn_probe(
    image: str,
    *,
    runtime: RuntimeConfig | None = None,
    extra_flags: list[str] | None = None,
    python_bin: str = "/usr/bin/python3.12",
    docker_cmd: list[str] | None = None,
) -> float:
    """Time a real container spawn on this host; return seconds.

    Runs ``docker run --rm`` with the runtime's flags plus
    ``extra_flags``, executing ``python -c pass``. Raises
    :class:`IsolationRuntimeError` when Docker is absent or the spawn
    fails — a failed probe is not a measurement of zero.
    """
    cmd = docker_cmd if docker_cmd is not None else _docker_cmd()
    if cmd is None:
        raise IsolationRuntimeError("docker binary not found; cannot measure a spawn")
    config = runtime if runtime is not None else RuntimeConfig()
    argv = [
        *cmd,
        "run",
        "--rm",
        *config.docker_flags(),
        *(extra_flags or []),
        image,
        python_bin,
        "-c",
        "pass",
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, check=False, capture_output=True, timeout=_PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IsolationRuntimeError(f"spawn probe failed: {exc}") from exc
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        raise IsolationRuntimeError(
            "spawn probe container exited "
            f"{proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:500]}"
        )
    return elapsed


def _docker_cmd() -> list[str] | None:
    path = shutil.which("docker")
    return [path] if path is not None else None
