"""Seccomp profiles for the sandbox runner (E-03, plan 4.3).

Plan 4.3 requires a default-or-stricter seccomp profile on every
agent container. Before E-03 the runner passed no seccomp option
at all, so containers ran under whatever the Docker daemon's
built-in default happened to be, with nothing pinned, validated,
or recorded. This module pins the profile:

- :func:`default_profile` — Docker's own default profile,
  vendored verbatim as ``seccomp-default.json`` from moby/moby
  ``v28.0.0`` (``profiles/seccomp/default.json``, Apache-2.0;
  attribution in ``REUSE.md`` / ``NOTICE.md``). ``defaultAction``
  is ``SCMP_ACT_ERRNO``: anything not listed is denied.
- :func:`strict_profile` — the default minus
  :data:`STRICT_REMOVED_SYSCALLS`, vendored as
  ``seccomp-strict.json`` (generated from the default by
  :func:`derive_strict`, never hand-edited). Every removed
  syscall is one the Docker default allows (often only behind a
  capability gate such as ``CAP_SYS_ADMIN`` or
  ``CAP_SYS_PTRACE``, which Puspa already drops with
  ``--cap-drop ALL``) and no Python agent workload needs:
  tracing, BPF, profiling, cross-process memory access, module
  loading, mounting, rebooting, clock setting, and hostname
  changes. Removing them is defence in depth: even if the
  capability drop were ever misconfigured, seccomp still denies
  them. A Python workload keeps ``read``/``write``/``openat``/
  ``execve``/``socket``/``connect``/``clone``/``futex``/``mmap``
  and friends; :func:`derive_strict` refuses to produce a
  profile that drops one of those.
- :func:`load_profile` — any operator-supplied profile file,
  validated the same way (fail-closed: a missing file, bad
  JSON, a non-``ERRNO`` default action, or a malformed
  ``syscalls`` list raises :class:`SeccompError` before any
  container is built).

Validation notes (read from the vendored file, not assumed):

- Syscall names repeat across groups: the default has
  conditional groups (``includes: {minKernel: ...}``,
  ``includes: {caps: [CAP_SYS_ADMIN]}``), so uniqueness is
  enforced *within* a group only, never across groups.
- :meth:`SeccompProfile.allows` is the union of every
  ``SCMP_ACT_ALLOW`` group, conditions ignored. That
  over-approximates (a ``CAP_SYS_ADMIN``-gated allow counts as
  allowed) — the conservative reading for "is this syscall
  reachable", and the only reading available without evaluating
  the container's capabilities here. ``denies`` is its exact
  complement within the profile.
- The strict profile removes a name from *every* group that
  lists it, conditions included, so the removal holds whatever
  the conditions evaluate to.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_PROFILE_FILENAME",
    "STRICT_PROFILE_FILENAME",
    "STRICT_REMOVED_SYSCALLS",
    "SeccompError",
    "SeccompProfile",
    "default_profile",
    "derive_strict",
    "load_profile",
    "strict_profile",
    "validate_profile_data",
]

#: Vendored profile files (next to this module).
DEFAULT_PROFILE_FILENAME = "seccomp-default.json"
STRICT_PROFILE_FILENAME = "seccomp-strict.json"

#: Actions a profile group may carry. Only ``ALLOW`` and
#: ``ERRNO`` appear in the vendored profiles; the rest are the
#: actions libseccomp/Docker accept, so an operator profile
#: using them is not rejected out of hand.
_KNOWN_ACTIONS = frozenset(
    {
        "SCMP_ACT_ALLOW",
        "SCMP_ACT_ERRNO",
        "SCMP_ACT_KILL",
        "SCMP_ACT_KILL_PROCESS",
        "SCMP_ACT_KILL_THREAD",
        "SCMP_ACT_LOG",
        "SCMP_ACT_TRACE",
        "SCMP_ACT_TRAP",
        "SCMP_ACT_NOTIFY",
    }
)

#: Syscalls the strict profile removes from the Docker default,
#: each with the reason. All were verified present in the
#: vendored default (``derive_strict`` raises if one is absent,
#: so this table cannot silently drift from the file). None is
#: needed by a Python agent workload; several are already
#: unreachable in practice (allowed only behind a capability
#: Puspa drops), and removing them makes that denial hold even
#: if the capability drop were ever misconfigured.
STRICT_REMOVED_SYSCALLS: dict[str, str] = {
    "acct": "process accounting; not needed by agent code",
    "bpf": "loading BPF programs; kernel-code execution surface",
    "chroot": "changing the root directory; escape primitive",
    "clock_adjtime": "setting the system clock; not needed",
    "clock_settime": "setting the system clock; not needed",
    "delete_module": "unloading kernel modules; not needed",
    "fanotify_init": "filesystem monitoring; not needed",
    "finit_module": "loading kernel modules; not needed",
    "init_module": "loading kernel modules; not needed",
    "iopl": "x86 I/O port access; hardware access",
    "ioperm": "x86 I/O port access; hardware access",
    "lookup_dcookie": "oprofile cookie lookup; not needed",
    "mount": "mounting filesystems; escape primitive",
    "name_to_handle_at": "file-handle access; escape-prone",
    "open_by_handle_at": "file-handle access; escape-prone",
    "perf_event_open": "hardware performance counters; profiling",
    "personality": "changing the execution domain; not needed",
    "process_vm_readv": "reading another process's memory",
    "process_vm_writev": "writing another process's memory",
    "ptrace": "tracing another process; debugging surface",
    "reboot": "rebooting the host; not needed",
    "sethostname": "changing the hostname; not needed",
    "setdomainname": "changing the NIS domain; not needed",
    "settimeofday": "setting the system clock; not needed",
    "umount2": "unmounting filesystems; escape primitive",
    "vhangup": "hanging up the controlling terminal; not needed",
}

#: Syscalls a Python agent workload must keep. If derivation
#: ever dropped one of these, the profile would break the
#: workload itself (it could not start, read, or connect), so
#: :func:`derive_strict` refuses instead of shipping it.
_ESSENTIAL_SYSCALLS = frozenset(
    {
        "read",
        "write",
        "openat",
        "close",
        "execve",
        "socket",
        "connect",
        "clone",
        "futex",
        "mmap",
        "munmap",
        "brk",
        "exit_group",
        "getrandom",
        "newfstatat",
        "readlinkat",
    }
)


class SeccompError(ValueError):
    """A seccomp profile is missing, malformed, or unsafe.

    Raised at load/derivation time, before any container is
    built: a bad profile fails closed, never silently runs
    without seccomp.
    """


def _vendored_path(filename: str) -> Path:
    return Path(__file__).resolve().parent / filename


def validate_profile_data(data: object) -> None:
    """Check a parsed profile; raise :class:`SeccompError` if bad.

    Rules: a dict, ``defaultAction`` exactly
    ``SCMP_ACT_ERRNO`` (any other default would allow syscalls
    the profile does not list), a non-empty ``syscalls`` list,
    each group a dict with a non-empty ``names`` list of
    non-empty strings (unique within the group) and an
    ``action`` in :data:`_KNOWN_ACTIONS`.
    """
    if not isinstance(data, dict):
        raise SeccompError(f"seccomp profile must be a JSON object, got {type(data).__name__}")
    if data.get("defaultAction") != "SCMP_ACT_ERRNO":
        raise SeccompError(
            "seccomp profile defaultAction must be SCMP_ACT_ERRNO "
            f"(fail-closed default); got {data.get('defaultAction')!r}"
        )
    groups = data.get("syscalls")
    if not isinstance(groups, list) or not groups:
        raise SeccompError("seccomp profile 'syscalls' must be a non-empty list")
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise SeccompError(f"seccomp profile syscalls[{index}] must be an object")
        names = group.get("names")
        if not isinstance(names, list) or not names:
            raise SeccompError(f"seccomp profile syscalls[{index}].names must be a non-empty list")
        for name in names:
            if not isinstance(name, str) or not name:
                raise SeccompError(
                    f"seccomp profile syscalls[{index}].names must be non-empty strings"
                )
        if len(set(names)) != len(names):
            raise SeccompError(f"seccomp profile syscalls[{index}].names has duplicates")
        action = group.get("action")
        if not isinstance(action, str) or action not in _KNOWN_ACTIONS:
            raise SeccompError(
                f"seccomp profile syscalls[{index}].action must be one of "
                f"{sorted(_KNOWN_ACTIONS)}, got {action!r}"
            )


@dataclass(frozen=True)
class SeccompProfile:
    """A validated seccomp profile, ready for ``docker run``.

    ``path`` is the file the Docker daemon reads (``--security-opt
    seccomp=<path>``); ``None`` means "use the daemon's built-in
    default" (no flag is passed). ``data`` is the parsed JSON.
    """

    name: str
    path: Path | None
    data: dict

    @property
    def allowed(self) -> frozenset[str]:
        """Syscalls any ``SCMP_ACT_ALLOW`` group admits (union)."""
        allowed: set[str] = set()
        for group in self.data.get("syscalls", []):
            if group.get("action") == "SCMP_ACT_ALLOW":
                allowed.update(group.get("names", []))
        return frozenset(allowed)

    @property
    def default_action(self) -> str:
        """The profile's default action (always ``SCMP_ACT_ERRNO``)."""
        action = self.data.get("defaultAction")
        return action if isinstance(action, str) else ""

    def allows(self, syscall: str) -> bool:
        """True when an ALLOW group lists ``syscall``."""
        return syscall in self.allowed

    def denies(self, syscall: str) -> bool:
        """True when no ALLOW group lists ``syscall``."""
        return syscall not in self.allowed

    def docker_flag(self) -> list[str]:
        """``--security-opt seccomp=<path>`` (empty for daemon default)."""
        if self.path is None:
            return []
        return ["--security-opt", f"seccomp={self.path}"]


def load_profile(path: str | Path, *, name: str | None = None) -> SeccompProfile:
    """Load and validate a seccomp profile file (fail-closed)."""
    resolved = Path(path)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise SeccompError(f"seccomp profile file is not readable: {resolved} ({exc})") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SeccompError(f"seccomp profile is not valid JSON: {resolved} ({exc})") from exc
    validate_profile_data(data)
    return SeccompProfile(name=name or resolved.name, path=resolved, data=data)


def default_profile() -> SeccompProfile:
    """The vendored Docker default profile (moby v28.0.0)."""
    return load_profile(_vendored_path(DEFAULT_PROFILE_FILENAME), name="default")


def strict_profile() -> SeccompProfile:
    """The vendored strict profile (default minus the removals)."""
    return load_profile(_vendored_path(STRICT_PROFILE_FILENAME), name="strict")


def derive_strict(data: dict) -> dict:
    """Derive the strict profile data from default profile data.

    Removes every :data:`STRICT_REMOVED_SYSCALLS` name from
    every group that lists it and drops groups left empty.
    Raises :class:`SeccompError` when a listed removal is not
    present at all (the table drifted from the profile), or
    when an essential syscall would be lost.
    """
    validate_profile_data(data)
    removed: set[str] = set()
    strict = copy.deepcopy(data)
    kept_groups: list[dict] = []
    for group in strict.get("syscalls", []):
        names = [name for name in group["names"] if name not in STRICT_REMOVED_SYSCALLS]
        removed.update(name for name in group["names"] if name in STRICT_REMOVED_SYSCALLS)
        if names:
            kept = dict(group)
            kept["names"] = names
            kept_groups.append(kept)
    strict["syscalls"] = kept_groups
    missing = set(STRICT_REMOVED_SYSCALLS) - removed
    if missing:
        raise SeccompError(
            "strict derivation expected these syscalls in the default "
            f"profile but they are absent: {', '.join(sorted(missing))}"
        )
    allowed = {
        name
        for group in kept_groups
        if group.get("action") == "SCMP_ACT_ALLOW"
        for name in group["names"]
    }
    lost = _ESSENTIAL_SYSCALLS - allowed
    if lost:
        raise SeccompError(
            "strict derivation would drop syscalls a Python workload "
            f"needs: {', '.join(sorted(lost))}"
        )
    validate_profile_data(strict)
    return strict
