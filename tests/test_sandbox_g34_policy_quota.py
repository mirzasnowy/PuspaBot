"""G34: network/filesystem policy objects + resource quotas.

OpenShell mechanisms under test (rewritten as Puspa code):
deny-by-default network policy with explicit host:port
endpoints (``proto/sandbox.proto`` ``NetworkPolicyRule``),
filesystem allow lists (``FilesystemPolicy``), and typed
resource requirements (RFC 0004 ``ResourceRequirements``).
"""

import pytest

from puspa.sandbox import (
    DEFAULT_QUOTA,
    Allowlist,
    FilesystemPolicy,
    NetworkEndpoint,
    NetworkPolicy,
    PolicyError,
    QuotaExceeded,
    QuotaRegistry,
    ResourceQuota,
    SandboxPolicy,
    measure_disk_usage,
)

# --- NetworkPolicy -----------------------------------------------------------


def test_network_policy_deny_all_by_default():
    policy = NetworkPolicy.deny_all()
    assert policy.is_empty
    assert policy.docker_network_mode() == "none"
    assert not policy.allows("api.openai.com", 443)
    assert not policy.allows_host("tokopedia.com")


def test_network_policy_from_hosts_requires_allowlist_membership():
    policy = NetworkPolicy.from_hosts(["api.openai.com", "tokopedia.com"])
    assert policy.allows("api.openai.com", 443)
    assert policy.allows("api.openai.com", 80)
    assert not policy.allows("api.openai.com", 8080)
    assert policy.allows("www.tokopedia.com", 443)
    assert not policy.allows("tokopedia.com.evil.com", 443)
    assert not policy.allows("evil.com", 443)
    assert policy.docker_network_mode() == "proxy"


def test_network_policy_from_hosts_rejects_outside_allowlist():
    with pytest.raises(PolicyError, match="not on the session allowlist"):
        NetworkPolicy.from_hosts(["evil.com"])
    with pytest.raises(PolicyError, match="not on the session allowlist"):
        NetworkPolicy.from_hosts(["api.openai.com", "evil.com"])


def test_network_policy_rejects_url_as_host():
    with pytest.raises(PolicyError, match="bare hostname"):
        NetworkPolicy.from_hosts(["https://api.openai.com/v1"])
    with pytest.raises(PolicyError, match="non-empty"):
        NetworkEndpoint(host="  ", port=443)
    with pytest.raises(PolicyError, match="1-65535"):
        NetworkEndpoint(host="api.openai.com", port=0)
    with pytest.raises(PolicyError, match="1-65535"):
        NetworkEndpoint(host="api.openai.com", port=70000)


def test_network_policy_custom_allowlist_narrows_not_widens():
    narrow = Allowlist(["example.com"])
    policy = NetworkPolicy.from_hosts(["example.com"], allowlist=narrow)
    assert policy.allows("example.com", 443)
    with pytest.raises(PolicyError):
        NetworkPolicy.from_hosts(["api.openai.com"], allowlist=narrow)


def test_network_policy_allowed_hosts_view():
    policy = NetworkPolicy.from_hosts(["api.openai.com"], ports=(443,), allowlist=Allowlist())
    assert policy.allowed_hosts == frozenset({"api.openai.com"})
    assert policy.allows_host("api.openai.com")
    assert not policy.allows_host("api.anthropic.com")


# --- FilesystemPolicy --------------------------------------------------------


def test_filesystem_policy_workdir_writable_by_default():
    policy = FilesystemPolicy()
    assert policy.include_workdir is True
    assert policy.allows_write("notes/todo.txt")
    assert policy.allows_read("notes/todo.txt")


def test_filesystem_policy_without_workdir_uses_lists():
    policy = FilesystemPolicy(include_workdir=False, read_write=("data",), read_only=("docs",))
    assert policy.allows_write("data/out.txt")
    assert not policy.allows_write("other.txt")
    assert policy.allows_read("docs/readme.md")
    assert policy.allows_read("data/out.txt")
    assert not policy.allows_read("secret.txt")


def test_filesystem_policy_rejects_absolute_and_dotdot():
    with pytest.raises(PolicyError, match="sandbox-relative"):
        FilesystemPolicy(read_only=("/etc",))
    with pytest.raises(PolicyError, match="must not contain"):
        FilesystemPolicy(read_write=("../escape",))


# --- SandboxPolicy ------------------------------------------------------------


def test_sandbox_policy_defaults_are_fail_closed():
    policy = SandboxPolicy()
    assert policy.version == 1
    assert policy.network.is_empty
    assert policy.docker_user_flag() == "65534:65534"
    policy.validate()


def test_sandbox_policy_refuses_root():
    with pytest.raises(PolicyError, match="never run as root"):
        SandboxPolicy(run_as_user="root")
    with pytest.raises(PolicyError, match="never run as root"):
        SandboxPolicy(run_as_group="0")
    with pytest.raises(PolicyError, match="positive int"):
        SandboxPolicy(version=0)


# --- ResourceQuota ------------------------------------------------------------


def test_default_quota_matches_existing_runner_flags():
    assert DEFAULT_QUOTA.cpu_cores == 0.5
    assert DEFAULT_QUOTA.memory_mb == 256
    assert DEFAULT_QUOTA.max_pids == 64
    assert DEFAULT_QUOTA.docker_flags() == [
        "--memory",
        "256m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "64",
    ]


def test_quota_custom_flags():
    quota = ResourceQuota(cpu_cores=2, memory_mb=1024, disk_mb=512, max_pids=128)
    assert quota.docker_flags() == [
        "--memory",
        "1024m",
        "--cpus",
        "2",
        "--pids-limit",
        "128",
    ]
    assert quota.to_dict() == {
        "cpu_cores": 2,
        "memory_mb": 1024,
        "disk_mb": 512,
        "max_pids": 128,
        "gpu_count": 0,
    }
    assert ResourceQuota.from_dict(quota.to_dict()) == quota


def test_quota_validation_bounds():
    with pytest.raises(ValueError, match="cpu_cores"):
        ResourceQuota(cpu_cores=0)
    with pytest.raises(ValueError, match="cpu_cores"):
        ResourceQuota(cpu_cores=32)
    with pytest.raises(ValueError, match="memory_mb"):
        ResourceQuota(memory_mb=32)
    with pytest.raises(ValueError, match="disk_mb"):
        ResourceQuota(disk_mb=0)
    with pytest.raises(ValueError, match="max_pids"):
        ResourceQuota(max_pids=0)
    with pytest.raises(ValueError, match="unknown ResourceQuota keys"):
        ResourceQuota.from_dict({"cpu_cores": 1, "bogus": 2})


def test_quota_gpu_request_is_refused_not_silently_cpu():
    with pytest.raises(ValueError, match="not provisioned"):
        ResourceQuota(gpu_count=1)


# --- QuotaRegistry ------------------------------------------------------------


def test_registry_default_and_override():
    registry = QuotaRegistry()
    assert registry.quota_for("user-a") == DEFAULT_QUOTA
    custom = ResourceQuota(cpu_cores=1, memory_mb=512, disk_mb=512, max_pids=32)
    registry.set_user_quota("user-a", custom)
    assert registry.quota_for("user-a") == custom
    assert registry.quota_for("user-b") == DEFAULT_QUOTA


def test_registry_reserve_release_and_cap():
    registry = QuotaRegistry(max_sessions_per_user=2)
    assert registry.active_sessions("u1") == 0
    registry.reserve("u1")
    registry.reserve("u1")
    assert registry.active_sessions("u1") == 2
    with pytest.raises(QuotaExceeded, match="per-user cap"):
        registry.reserve("u1")
    registry.release("u1")
    assert registry.active_sessions("u1") == 1
    registry.reserve("u1")
    registry.release("u1")
    registry.release("u1")
    assert registry.active_sessions("u1") == 0
    registry.release("u1")  # releasing below zero stays at zero
    assert registry.active_sessions("u1") == 0
    with pytest.raises(ValueError):
        registry.reserve("")
    with pytest.raises(ValueError):
        QuotaRegistry(max_sessions_per_user=0)


def test_disk_usage_accounting(tmp_path):
    assert measure_disk_usage(tmp_path) == 0
    assert measure_disk_usage(tmp_path / "missing") == 0
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 250)
    assert measure_disk_usage(tmp_path) == 350
    # A symlink pointing outside the root is not followed.
    (tmp_path / "link").symlink_to(tmp_path / "a.txt")
    assert measure_disk_usage(tmp_path) == 350


def test_registry_check_disk(tmp_path):
    quota = ResourceQuota(disk_mb=16)
    (tmp_path / "f.bin").write_bytes(b"x" * 1024)
    assert QuotaRegistry.check_disk(tmp_path, quota, 100) == 1124
    with pytest.raises(QuotaExceeded, match="disk quota exceeded"):
        QuotaRegistry.check_disk(tmp_path, quota, 16 * 1024 * 1024)
    with pytest.raises(ValueError):
        QuotaRegistry.check_disk(tmp_path, quota, -1)
