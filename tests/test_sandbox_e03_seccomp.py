"""E-03: seccomp profiles (plan 4.3).

The vendored Docker default profile (moby v28.0.0) and the
strict variant derived from it; profile validation; runner
wiring (``--security-opt seccomp=<path>``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from puspa.sandbox import Sandbox, SandboxUnavailable
from puspa.sandbox.seccomp import (
    STRICT_REMOVED_SYSCALLS,
    SeccompError,
    default_profile,
    derive_strict,
    load_profile,
    strict_profile,
    validate_profile_data,
)

ESSENTIAL = (
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
)


# --- the vendored default ------------------------------------------------------


def test_default_profile_loads_and_is_fail_closed():
    profile = default_profile()
    assert profile.name == "default"
    assert profile.default_action == "SCMP_ACT_ERRNO"
    assert profile.path is not None and profile.path.exists()
    # 414 allowed names in the vendored moby v28.0.0 default.
    assert len(profile.allowed) == 414


def test_default_profile_allows_a_python_workload():
    profile = default_profile()
    for syscall in ESSENTIAL:
        assert profile.allows(syscall), f"default profile must allow {syscall}"
    # clone3 is explicitly denied (ERRNO group, errnoRet 38) for
    # containers without CAP_SYS_ADMIN — the only ERRNO group in
    # the vendored profile.
    errno_groups = [
        group for group in profile.data["syscalls"] if group.get("action") == "SCMP_ACT_ERRNO"
    ]
    assert errno_groups
    assert any("clone3" in group["names"] for group in errno_groups)
    # Syscalls the default never lists are denied by the ERRNO
    # default action (checked through the profile's own reading).
    assert profile.denies("kexec_load")
    assert profile.denies("add_key")


def test_default_profile_docker_flag_points_at_the_vendored_file():
    profile = default_profile()
    assert profile.docker_flag() == ["--security-opt", f"seccomp={profile.path}"]


# --- the strict variant ----------------------------------------------------------


def test_strict_profile_removes_every_listed_syscall():
    strict = strict_profile()
    default = default_profile()
    for syscall in STRICT_REMOVED_SYSCALLS:
        assert default.allows(syscall), f"{syscall} must be in the default to remove"
        assert strict.denies(syscall), f"strict profile must deny {syscall}"
    assert strict.allowed == default.allowed - set(STRICT_REMOVED_SYSCALLS)


def test_strict_profile_keeps_the_python_workload_usable():
    strict = strict_profile()
    assert strict.default_action == "SCMP_ACT_ERRNO"
    for syscall in ESSENTIAL:
        assert strict.allows(syscall), f"strict profile must keep {syscall}"
    assert strict.allows("socket") and strict.allows("connect")


def test_strict_profile_file_matches_derivation():
    """The vendored strict file is exactly derive_strict(default)."""
    derived = derive_strict(default_profile().data)
    on_disk = json.loads(
        Path(strict_profile().path).read_text(encoding="utf-8")  # type: ignore[arg-type]
    )
    assert derived == on_disk


def test_derive_strict_refuses_a_missing_removal():
    minimal = {
        "defaultAction": "SCMP_ACT_ERRNO",
        "syscalls": [
            {"names": ["read", "write", "socket"], "action": "SCMP_ACT_ALLOW"},
        ],
    }
    with pytest.raises(SeccompError, match="absent"):
        derive_strict(minimal)


def test_derive_strict_refuses_losing_an_essential_syscall():
    from puspa.sandbox.seccomp import STRICT_REMOVED_SYSCALLS

    names = list(STRICT_REMOVED_SYSCALLS) + [name for name in ESSENTIAL if name != "socket"]
    data = {
        "defaultAction": "SCMP_ACT_ERRNO",
        "syscalls": [
            {"names": names, "action": "SCMP_ACT_ALLOW"},
        ],
    }
    # Every listed removal is present, but the profile never
    # allowed `socket`: derivation must refuse instead of
    # shipping a profile the workload cannot use.
    with pytest.raises(SeccompError, match="needs"):
        derive_strict(data)


# --- validation ------------------------------------------------------------------


def test_load_profile_rejects_missing_file(tmp_path):
    with pytest.raises(SeccompError, match="not readable"):
        load_profile(tmp_path / "nope.json")


def test_load_profile_rejects_bad_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SeccompError, match="not valid JSON"):
        load_profile(bad)


def test_load_profile_rejects_allow_default(tmp_path):
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(
        json.dumps(
            {
                "defaultAction": "SCMP_ACT_ALLOW",
                "syscalls": [{"names": ["read"], "action": "SCMP_ACT_ALLOW"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SeccompError, match="SCMP_ACT_ERRNO"):
        load_profile(profile_file)


def test_load_profile_rejects_empty_syscalls_and_duplicates(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(
        json.dumps({"defaultAction": "SCMP_ACT_ERRNO", "syscalls": []}),
        encoding="utf-8",
    )
    with pytest.raises(SeccompError, match="non-empty list"):
        load_profile(empty)
    dup = tmp_path / "dup.json"
    dup.write_text(
        json.dumps(
            {
                "defaultAction": "SCMP_ACT_ERRNO",
                "syscalls": [
                    {"names": ["read", "read"], "action": "SCMP_ACT_ALLOW"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SeccompError, match="duplicates"):
        load_profile(dup)


def test_validate_profile_data_accepts_conditional_groups():
    """Duplicates across groups are legal (minKernel/caps conditions)."""
    validate_profile_data(
        {
            "defaultAction": "SCMP_ACT_ERRNO",
            "syscalls": [
                {"names": ["read", "ptrace"], "action": "SCMP_ACT_ALLOW"},
                {
                    "names": ["ptrace"],
                    "action": "SCMP_ACT_ALLOW",
                    "includes": {"caps": ["CAP_SYS_PTRACE"]},
                },
            ],
        }
    )


# --- runner wiring -----------------------------------------------------------------


def test_runner_flags_include_default_seccomp_profile():
    sandbox = Sandbox()
    flags = sandbox.docker_flags()
    profile = default_profile()
    assert "--security-opt" in flags
    assert f"seccomp={profile.path}" in flags


def test_runner_flags_strict_seccomp_and_runsc():
    sandbox = Sandbox(seccomp="strict", runtime="runsc")
    flags = sandbox.docker_flags()
    assert f"seccomp={strict_profile().path}" in flags
    assert "--runtime" in flags and "runsc" in flags


def test_runner_flags_daemon_default_seccomp_passes_no_flag():
    sandbox = Sandbox(seccomp="docker-default")
    flags = sandbox.docker_flags()
    assert not any(item.startswith("seccomp=") for item in flags)


def test_runner_rejects_unknown_seccomp_setting():
    with pytest.raises(SeccompError, match="unknown seccomp setting"):
        Sandbox(seccomp="banana")


def test_runner_rejects_missing_seccomp_file(tmp_path):
    with pytest.raises(SeccompError, match="not readable"):
        Sandbox(seccomp=tmp_path / "nope.json")


def test_runner_rejects_unknown_runtime():
    with pytest.raises(Exception, match="unknown isolation runtime"):
        Sandbox(runtime="firecracker")


def test_runner_default_has_no_runtime_flag():
    assert "--runtime" not in Sandbox().docker_flags()


def test_audit_rejects_a_seccomp_flag_pointing_at_docker_sock(tmp_path):
    # A hand-built flag list is audited the same way: even a
    # seccomp flag cannot smuggle the socket path in.
    from puspa.sandbox.runner import audit_docker_flags

    with pytest.raises(SandboxUnavailable, match=r"docker\.sock"):
        audit_docker_flags(
            [
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,size=16m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--security-opt",
                "seccomp=/var/run/docker.sock",
                "--user",
                "65534:65534",
            ]
        )
