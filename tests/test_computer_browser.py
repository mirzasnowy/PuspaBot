"""Tests for the browser / computer-use session contract (G16).

Interface only: there is no live browser, no CDP driving, no
Chromium launch. What is tested is the contract the post-MVP
work must honour — one profile per agent, one session per task,
explicit human takeover (the bot never sees a password), and a
launcher that reaps orphan sessions.

These tests use no Puspa database tables themselves (in-memory
registry only). Note: the shared ``tests/conftest.py`` autouse
fixture still creates the schemas and truncates tables in
``puspa_test`` for every test file in this directory, including
this one; that fixture cannot be opted out of from here.
"""

import pytest

from puspa.computer.browser import (
    BrowserSession,
    SessionRegistry,
    TakeoverReason,
    TakeoverRequest,
    TakeoverStatus,
    UnavailableBrowserLauncher,
    profile_dir_for,
)
from puspa.computer.errors import BrowserSessionBusy, BrowserUnavailable


class FakeClock:
    """Wall clock the test advances by hand (no sleeping)."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- profiles: one per agent ---------------------------------------------------


def test_profile_dir_is_per_agent(tmp_path):
    first = profile_dir_for("agent-a", base=tmp_path)
    second = profile_dir_for("agent-b", base=tmp_path)
    assert first != second
    assert first == tmp_path / "agent-a"
    assert profile_dir_for("agent-a", base=tmp_path) == first


def test_profile_dir_rejects_unsafe_agent_id(tmp_path):
    for bad in ("", ".", "..", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            profile_dir_for(bad, base=tmp_path)


# --- registry: one live session per agent, one per task -------------------------


def test_register_and_list_live():
    registry = SessionRegistry()
    session = registry.register("agent-a", "task-1")
    assert session.agent_id == "agent-a"
    assert session.task_id == "task-1"
    assert session.profile_dir == profile_dir_for("agent-a")
    assert registry.list_live() == [session]
    assert registry.get(session.session_id) is session


def test_second_live_session_for_same_agent_is_refused():
    registry = SessionRegistry()
    first = registry.register("agent-a", "task-1")
    with pytest.raises(BrowserSessionBusy, match="already has live browser session"):
        registry.register("agent-a", "task-2")
    # A different agent is fine.
    registry.register("agent-b", "task-1")
    assert len(registry.list_live()) == 2
    assert registry.list_live()[0] is first


def test_close_then_relaunch_is_allowed():
    registry = SessionRegistry()
    first = registry.register("agent-a", "task-1")
    assert registry.close(first.session_id) is True
    assert registry.close(first.session_id) is False
    assert registry.close("no-such-session") is False
    second = registry.register("agent-a", "task-2")
    assert second.session_id != first.session_id
    assert registry.list_live() == [second]


def test_reap_orphans_closes_idle_sessions_only():
    clock = FakeClock()
    registry = SessionRegistry(clock=clock)
    fresh = registry.register("agent-a", "task-1")
    stale = registry.register("agent-b", "task-2")
    clock.advance(60)
    fresh.heartbeat(now=clock())
    clock.advance(901)
    # Fresh heartbeated 901s ago, stale 961s ago; max idle 950s,
    # so only the stale session is an orphan.
    reaped = registry.reap_orphans(max_idle_s=950)
    assert reaped == [stale.session_id]
    assert registry.list_live() == [fresh]
    assert registry.get(stale.session_id).closed is True


def test_session_is_orphan_after_max_idle():
    session = BrowserSession(
        session_id="s1",
        agent_id="agent-a",
        task_id="task-1",
        profile_dir=profile_dir_for("agent-a"),
        created_at=1000.0,
        last_heartbeat_at=1000.0,
    )
    assert session.is_orphan(max_idle_s=900, now=1899.0) is False
    assert session.is_orphan(max_idle_s=900, now=1901.0) is True
    session.heartbeat(now=1901.0)
    assert session.is_orphan(max_idle_s=900, now=2000.0) is False


# --- human takeover: no password anywhere ---------------------------------------


def test_takeover_request_has_no_password_field():
    request = TakeoverRequest(
        request_id="req-1",
        session_id="s1",
        agent_id="agent-a",
        reason=TakeoverReason.SIGN_IN,
    )
    assert not hasattr(request, "password")
    assert not hasattr(request, "secret")
    assert not hasattr(request, "token")
    assert request.status is TakeoverStatus.PENDING
    assert "password" not in repr(request).lower()


def test_takeover_request_resolves_once():
    request = TakeoverRequest(
        request_id="req-1",
        session_id="s1",
        agent_id="agent-a",
        reason=TakeoverReason.CAPTCHA,
    )
    request.resolve(TakeoverStatus.COMPLETED)
    assert request.status is TakeoverStatus.COMPLETED
    assert request.resolved_at is not None
    with pytest.raises(ValueError, match="already"):
        request.resolve(TakeoverStatus.DENIED)
    with pytest.raises(ValueError, match="pending"):
        TakeoverRequest(
            request_id="req-2",
            session_id="s1",
            agent_id="agent-a",
            reason=TakeoverReason.TWO_FA,
        ).resolve(TakeoverStatus.PENDING)


def test_takeover_reasons_cover_sign_in_captcha_2fa():
    assert {r.value for r in TakeoverReason} >= {"sign_in", "captcha", "two_fa"}


# --- unavailable launcher: honest, no fake browser -------------------------------


def test_unavailable_launcher_raises_on_launch():
    launcher = UnavailableBrowserLauncher()
    with pytest.raises(BrowserUnavailable) as excinfo:
        launcher.launch("agent-a", "task-1")
    message = str(excinfo.value)
    assert "deferred until after the MVP" in message
    assert "Docker host" in message
    assert "human-takeover UI" in message
    assert launcher.list_sessions() == []


def test_unavailable_launcher_raises_on_takeover():
    launcher = UnavailableBrowserLauncher()
    with pytest.raises(BrowserUnavailable):
        launcher.request_takeover("s1", TakeoverReason.SIGN_IN)


def test_unavailable_launcher_close_and_reap_still_work():
    launcher = UnavailableBrowserLauncher()
    assert launcher.close("no-such-session") is False
    assert launcher.reap_orphans() == []
