"""E-03: runner wiring (plan 4.3).

tmpfs for writes, seccomp + runtime flags, the egress-proxy
network mode, the flag audit, and the warm-pool run path.
"""

from __future__ import annotations

import pytest

from puspa.sandbox import Sandbox, SandboxUnavailable
from puspa.sandbox.egress import EgressProxy, InMemoryEgressSink
from puspa.sandbox.policy import NetworkEndpoint, NetworkPolicy, SandboxPolicy
from puspa.sandbox.quota import ResourceQuota
from puspa.sandbox.runner import audit_docker_flags
from puspa.sandbox.warmpool import ContainerRuntime, WarmPool


def _policy_with_host() -> SandboxPolicy:
    return SandboxPolicy(
        network=NetworkPolicy(endpoints=(NetworkEndpoint(host="api.openai.com", port=443),))
    )


# --- tmpfs --------------------------------------------------------------------------------


def test_tmpfs_flag_is_present_with_a_quota_derived_size():
    # Default quota (256 MB memory, 256 MB disk): tmpfs is half
    # the memory limit, 128 MiB.
    flags = Sandbox().docker_flags()
    assert "--tmpfs" in flags
    assert "/tmp:rw,size=128m" in flags


def test_tmpfs_size_is_the_smaller_of_disk_and_half_memory():
    quota = ResourceQuota(cpu_cores=1, memory_mb=512, disk_mb=512, max_pids=32)
    flags = Sandbox(quota=quota).docker_flags()
    assert "/tmp:rw,size=256m" in flags
    small_memory = ResourceQuota(cpu_cores=1, memory_mb=64, disk_mb=256, max_pids=32)
    assert "/tmp:rw,size=32m" in Sandbox(quota=small_memory).docker_flags()


# --- flag audit ------------------------------------------------------------------------------


def test_audit_accepts_the_default_flag_list():
    audit_docker_flags(Sandbox().docker_flags())


def test_audit_rejects_volume_and_mount_flags():
    base = Sandbox().docker_flags()
    for bad in (
        ["-v", "/host:/guest"],
        ["--volume", "/host:/guest"],
        ["--mount", "type=bind,src=/host,dst=/guest"],
        ["--volumes-from", "other"],
    ):
        with pytest.raises(SandboxUnavailable, match="mount flag"):
            audit_docker_flags([*base, *bad])
    with pytest.raises(SandboxUnavailable, match="mount flag"):
        audit_docker_flags([*base, "--volume=/host:/guest"])


def test_audit_rejects_a_docker_socket_reference():
    base = Sandbox().docker_flags()
    with pytest.raises(SandboxUnavailable, match=r"docker\.sock"):
        audit_docker_flags([*base, "--env", "DOCKER_HOST=unix:///var/run/docker.sock"])


def test_audit_rejects_missing_hardening():
    good = Sandbox().docker_flags()

    def _without(*items):
        flags = list(good)
        for item in items:
            flags.remove(item)
        return flags

    with pytest.raises(SandboxUnavailable, match="--read-only"):
        audit_docker_flags(_without("--read-only"))
    with pytest.raises(SandboxUnavailable, match="cap-drop ALL"):
        audit_docker_flags(_without("--cap-drop", "ALL"))
    with pytest.raises(SandboxUnavailable, match="no-new-privileges"):
        audit_docker_flags(_without("--security-opt", "no-new-privileges"))
    with pytest.raises(SandboxUnavailable, match="--user"):
        audit_docker_flags(_without("--user", "65534:65534"))
    with pytest.raises(SandboxUnavailable, match="--network"):
        audit_docker_flags(_without("--network", "none"))
    with pytest.raises(SandboxUnavailable, match="--tmpfs"):
        audit_docker_flags(_without("--tmpfs", "/tmp:rw,size=128m"))


def test_audit_rejects_a_root_user():
    flags = Sandbox().docker_flags()
    root_index = flags.index("--user")
    flags[root_index + 1] = "0:0"
    with pytest.raises(SandboxUnavailable, match="non-root"):
        audit_docker_flags(flags)


# --- egress-proxy mode -------------------------------------------------------------------------


def test_proxy_mode_flags_route_through_the_proxy():
    sink = InMemoryEgressSink()
    proxy = EgressProxy(_policy_with_host().network, sink=sink)
    proxy.start()
    try:
        sandbox = Sandbox(policy=_policy_with_host(), egress_proxy=proxy)
        flags = sandbox.docker_flags()
        port = proxy.listen_port
    finally:
        proxy.stop()
    assert flags[0:2] == ["--network", "puspa-egress"]
    assert "--dns" in flags and "127.0.0.1" in flags
    assert "host.docker.internal:host-gateway" in flags
    url = f"http://host.docker.internal:{port}"
    assert f"http_proxy={url}" in flags
    assert "--network" in flags and "none" not in flags
    # Hardening survives proxy mode: read-only, cap-drop, seccomp.
    assert "--read-only" in flags and "ALL" in flags
    assert any(item.startswith("seccomp=") for item in flags)


def test_proxy_mode_without_a_proxy_still_refuses():
    sandbox = Sandbox(policy=_policy_with_host())
    with pytest.raises(SandboxUnavailable, match="no egress proxy is configured"):
        sandbox.docker_flags()
    with pytest.raises(SandboxUnavailable, match="no egress proxy is configured"):
        sandbox.run("print(1)")


def test_proxy_mode_with_a_stopped_proxy_refuses():
    proxy = EgressProxy(_policy_with_host().network)
    sandbox = Sandbox(policy=_policy_with_host(), egress_proxy=proxy)
    with pytest.raises(SandboxUnavailable, match="egress proxy is not running"):
        sandbox.docker_flags()


# --- warm-pool run path ---------------------------------------------------------------------------


class FakeRuntime(ContainerRuntime):
    """A runtime that records creates/removes instead of calling Docker."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.removed: list[str] = []
        self._counter = 0

    def create(self, image: str, flags: list[str], command: list[str]) -> str:
        self._counter += 1
        container_id = f"container-{self._counter}"
        self.created.append(container_id)
        return container_id

    def start(self, container_id: str, *, input: str, timeout_s: int) -> dict | None:
        return {
            "ok": True,
            "stdout": "warm-ok",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
        }

    def remove(self, container_id: str) -> None:
        self.removed.append(container_id)

    def exists(self, container_id: str) -> bool:
        return container_id in self.created and container_id not in self.removed


def _pooled_sandbox(runtime, **kwargs):
    pool = WarmPool(
        runtime,
        image="puspa-sandbox:3.12-local",
        flags_builder=lambda: ["--network", "none"],
        size=1,
    )
    pool.warm_up()
    sandbox = Sandbox(warm_pool=pool, **kwargs)
    sandbox._available_cache = True  # pretend the probe passed
    return sandbox, pool


def test_pooled_run_uses_one_slot_then_destroys_it():
    runtime = FakeRuntime()
    sandbox, pool = _pooled_sandbox(runtime)
    result = sandbox.run("print(1)", user_id="user-a")
    assert result["stdout"] == "warm-ok"
    used = runtime.created[0]
    assert used in runtime.removed, "the run's container must be destroyed"
    assert pool.warm_count == 1, "the pool refills with a fresh container"
    assert pool.checked_out_count == 0


def test_pooled_runs_never_share_a_container_across_users():
    runtime = FakeRuntime()
    sandbox, pool = _pooled_sandbox(runtime)
    sandbox.run("print(1)", user_id="user-a")
    sandbox.run("print(2)", user_id="user-b")
    assert len(runtime.created) == 3  # warm-up + two on-demand/refill creates
    assert runtime.removed == ["container-1", "container-2"]
    assert pool.warm_count == 1


def test_pooled_run_releases_the_slot_even_when_start_fails():
    runtime = FakeRuntime()
    runtime.start = lambda container_id, *, input, timeout_s: None
    sandbox, pool = _pooled_sandbox(runtime)
    with pytest.raises(SandboxUnavailable, match="container gagal start"):
        sandbox.run("print(1)", user_id="user-a")
    assert runtime.created[0] in runtime.removed
    assert pool.checked_out_count == 0
