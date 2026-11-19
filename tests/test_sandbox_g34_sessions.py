"""G34: persistent sandbox sessions (create/get/list/kill, reaping).

OpenShell mechanisms under test (rewritten as Puspa code):
long-lived named sandboxes with a lifecycle, admission before
execution (Attach + Confirm), idle/orphan reaping, and a
per-user live-session cap enforced at creation.
"""

from pathlib import Path

import pytest

from puspa.computer.errors import BackendUnavailable
from puspa.sandbox import (
    NetworkPolicy,
    QuotaExceeded,
    QuotaRegistry,
    ResourceQuota,
    SandboxPolicy,
    SandboxSession,
    SessionManager,
    SessionStatus,
)


class FakeClock:
    """Controllable clock for lifetime/idle tests."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_create_get_list_kill_local_session(tmp_path):
    manager = SessionManager(base_dir=tmp_path)
    session = manager.create("user-a", backend_name="local")
    assert session.status is SessionStatus.READY
    assert session.user_id == "user-a"
    assert session.backend_name == "local"
    assert session.workdir.exists()
    assert session.workdir.parent == tmp_path
    assert manager.get(session.session_id) is session
    assert manager.get("no-such-id") is None
    assert manager.backend_for(session.session_id) is not None
    assert [s.session_id for s in manager.list_sessions()] == [session.session_id]
    assert manager.list_sessions(user_id="user-b") == []
    assert manager.list_sessions(status=SessionStatus.READY) == [session]
    assert manager.list_sessions(status=SessionStatus.CLOSED) == []
    assert manager.heartbeat(session.session_id) is True
    assert manager.kill(session.session_id) is True
    assert session.status is SessionStatus.CLOSED
    assert manager.kill(session.session_id) is False
    assert manager.heartbeat(session.session_id) is False
    assert manager.list_sessions(status=SessionStatus.CLOSED) == [session]


def test_session_to_dict_names_network_mode(tmp_path):
    manager = SessionManager(base_dir=tmp_path)
    session = manager.create("user-a")
    snapshot = session.to_dict()
    assert snapshot["status"] == "ready"
    assert snapshot["network_mode"] == "none"
    assert snapshot["quota"]["memory_mb"] == 256
    manager.kill(session.session_id)


def test_create_rejects_credential_env_and_bad_ids(tmp_path):
    manager = SessionManager(base_dir=tmp_path)
    with pytest.raises(ValueError, match="credential-looking env key"):
        manager.create("user-a", env={"OPENAI_API_KEY": "sk-x"})
    with pytest.raises(ValueError, match="user_id"):
        manager.create("")
    with pytest.raises(ValueError, match="lifetime_s"):
        manager.create("user-a", lifetime_s=0)
    assert manager.list_sessions() == []


def test_create_validates_policy_before_reserving_quota(tmp_path):
    registry = QuotaRegistry(max_sessions_per_user=1)
    manager = SessionManager(base_dir=tmp_path, registry=registry)
    with pytest.raises(ValueError, match="never run as root"):
        manager.create("user-a", policy=SandboxPolicy(run_as_user="root"))
    # No slot was consumed by the refused creation.
    assert registry.active_sessions("user-a") == 0
    session = manager.create("user-a")
    assert registry.active_sessions("user-a") == 1
    manager.kill(session.session_id)
    assert registry.active_sessions("user-a") == 0


def test_per_user_session_cap_enforced_at_creation(tmp_path):
    registry = QuotaRegistry(max_sessions_per_user=2)
    manager = SessionManager(base_dir=tmp_path, registry=registry)
    first = manager.create("user-a")
    second = manager.create("user-a")
    with pytest.raises(QuotaExceeded, match="per-user cap"):
        manager.create("user-a")
    # A different user is unaffected.
    other = manager.create("user-b")
    manager.kill(first.session_id)
    third = manager.create("user-a")
    assert third.session_id not in (first.session_id, second.session_id)
    for session in (second, third, other):
        manager.kill(session.session_id)


def test_custom_quota_and_policy_stored_on_session(tmp_path):
    manager = SessionManager(base_dir=tmp_path)
    quota = ResourceQuota(cpu_cores=1, memory_mb=512, disk_mb=512, max_pids=32)
    policy = SandboxPolicy()
    session = manager.create(
        "user-a", quota=quota, policy=policy, env={"MODE": "test"}, lifetime_s=60
    )
    assert session.quota == quota
    assert session.env == {"MODE": "test"}
    assert session.lifetime_s == 60
    manager.kill(session.session_id)


def test_reap_idle_and_expired_sessions(tmp_path):
    clock = FakeClock()
    manager = SessionManager(base_dir=tmp_path, clock=clock)
    idle = manager.create("user-a", lifetime_s=3600, idle_timeout_s=60)
    long_lived = manager.create("user-b", lifetime_s=3600, idle_timeout_s=3600)
    clock.advance(61)
    manager.heartbeat(long_lived.session_id)
    reaped = manager.reap_expired()
    assert reaped == [idle.session_id]
    assert idle.status is SessionStatus.CLOSED
    assert long_lived.status is SessionStatus.READY
    # Lifetime expiry reaps even a recently active session.
    clock.advance(3600)
    reaped = manager.reap_expired()
    assert reaped == [long_lived.session_id]
    assert manager.reap_expired() == []


def test_kill_deletes_workdir_when_asked(tmp_path):
    manager = SessionManager(base_dir=tmp_path)
    session = manager.create("user-a")
    workdir = session.workdir
    assert workdir.exists()
    manager.kill(session.session_id, delete_workdir=True)
    assert not workdir.exists()


def test_docker_session_admission_fails_closed_on_this_vm(tmp_path):
    """No container can run here, so Docker admission refuses.

    The quota slot is released and no session record is left
    behind; nothing is executed anywhere.
    """
    registry = QuotaRegistry()
    manager = SessionManager(base_dir=tmp_path, registry=registry)
    with pytest.raises(BackendUnavailable, match="cannot run on this host"):
        manager.create("user-a", backend_name="docker")
    assert registry.active_sessions("user-a") == 0
    assert manager.list_sessions() == []


def test_session_is_expired_and_is_idle_helpers():
    session = SandboxSession(
        session_id="s1",
        user_id="u1",
        backend_name="local",
        workdir=Path("/tmp/x"),
        created_at=1000.0,
        last_activity_at=1000.0,
        lifetime_s=100,
        idle_timeout_s=10,
    )
    assert session.is_expired(now=1099.0) is False
    assert session.is_expired(now=1100.0) is True
    assert session.is_idle(now=1009.0) is False
    assert session.is_idle(now=1011.0) is True
    session.touch(now=2000.0)
    assert session.is_idle(now=2005.0) is False


def test_network_policy_with_hosts_is_admitted_but_flagged(tmp_path):
    """A session may carry an allow-host policy (admission), even
    though running with it needs a configured egress proxy."""
    manager = SessionManager(base_dir=tmp_path)
    policy = SandboxPolicy(network=NetworkPolicy.from_hosts(["api.openai.com"]))
    session = manager.create("user-a", policy=policy)
    assert session.policy.network.docker_network_mode() == "proxy"
    assert session.to_dict()["network_mode"] == "proxy"
    manager.kill(session.session_id)
