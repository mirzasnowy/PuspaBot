"""Tests for per-profile availability checks with a TTL cache (G23).

In-memory only: run with ``pytest tests/test_tool_check.py --noconftest``.

The real credential seam (``puspa.credentials.scope.check_credential``)
is built by the G22 worker in parallel, so these tests never touch it:
they inject stub checkers, and one test proves the missing-module
path fails soft instead of raising.
"""

import pytest

from puspa.tools.check import (
    AvailabilityChecker,
    CheckResult,
    check_credential_available,
)
from puspa.tools.registry import ToolDefinition


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def checker(clock):
    return AvailabilityChecker(ttl_s=60.0, clock=clock)


def test_no_provider_means_no_check(checker):
    result = checker.check("some_module", "profile-1")
    assert result == CheckResult(True, "no_check", cached=False)


def test_custom_check_is_used_and_cached(checker):
    calls = []

    def stub(profile_id, provider=None, model=None):
        calls.append(profile_id)
        return profile_id == "owner"

    checker.register_check("notes", stub)
    first = checker.check("notes", "owner", provider="notes-api")
    second = checker.check("notes", "owner", provider="notes-api")
    assert first.available is True
    assert second.cached is True
    assert len(calls) == 1


def test_stale_cache_rechecks_instead_of_failing(checker, clock):
    answers = {"value": True}

    def stub(profile_id, provider=None, model=None):
        return answers["value"]

    checker.register_check("notes", stub)
    assert checker.check("notes", "p1", provider="notes-api").available is True
    answers["value"] = False
    # Still inside the TTL: the stale answer is served.
    assert checker.check("notes", "p1", provider="notes-api").available is True
    clock.advance(61.0)
    # Past the TTL: re-checked, and the new answer is False -- not a failure.
    result = checker.check("notes", "p1", provider="notes-api")
    assert result.available is False
    assert result.cached is False


def test_cache_is_per_profile(checker):
    calls = []

    def stub(profile_id, provider=None, model=None):
        calls.append(profile_id)
        return True

    checker.register_check("notes", stub)
    checker.check("notes", "alice", provider="notes-api")
    checker.check("notes", "bob", provider="notes-api")
    assert calls == ["alice", "bob"]


def test_cache_is_per_provider_and_model(checker):
    calls = []

    def stub(profile_id, provider=None, model=None):
        calls.append((provider, model))
        return True

    checker.register_check("llm", stub)
    checker.check("llm", "p1", provider="openai", model="gpt-5")
    checker.check("llm", "p1", provider="openai", model="gpt-4")
    checker.check("llm", "p1", provider="anthropic", model="gpt-5")
    assert len(calls) == 3


def test_check_exception_is_fail_soft(checker):
    def broken(profile_id, provider=None, model=None):
        raise RuntimeError("boom")

    checker.register_check("broken", broken)
    result = checker.check("broken", "p1", provider="x")
    assert result.available is False
    assert result.reason == "check_failed"


def test_invalidate_by_profile(checker):
    checker.register_check("notes", lambda p, provider=None, model=None: True)
    checker.check("notes", "alice", provider="n")
    checker.check("notes", "bob", provider="n")
    assert checker.cache_size() == 2
    dropped = checker.invalidate(profile_id="alice")
    assert dropped == 1
    assert checker.cache_size() == 1


def test_invalidate_all(checker):
    checker.register_check("notes", lambda p, provider=None, model=None: True)
    checker.check("notes", "alice", provider="n")
    assert checker.invalidate() == 1
    assert checker.cache_size() == 0


def test_injected_credential_checker_is_used(checker):
    stub = AvailabilityChecker(
        ttl_s=60.0,
        clock=FakeClock(),
        credential_checker=lambda profile_id, provider, model=None: provider == "openai",
    )
    assert stub.check("llm", "p1", provider="openai").available is True
    assert stub.check("llm", "p1", provider="other").available is False
    assert stub.check("llm", "p1", provider="other").reason == "credential_missing"


def test_missing_credential_module_fails_soft(monkeypatch):
    # Simulate the G22 module not existing yet (it exists in this
    # checkout, so block the import): the seam must report
    # unavailable, never raise.
    import sys

    monkeypatch.setitem(sys.modules, "puspa.credentials.scope", None)
    result = check_credential_available("p1", "openai")
    assert result.available is False
    assert result.reason == "credential_module_missing"


def test_credential_check_against_real_scope_when_present():
    # The G22 seam exists in this checkout with the agreed signature
    # check_credential(profile_id, provider, model=None) -> bool.
    # An unknown profile has no credential, so it is unavailable --
    # but the call itself must work end to end.
    result = check_credential_available("no-such-profile", "openai")
    assert result.available is False
    assert result.reason == "credential_missing"


def test_check_tool_with_own_check_is_cached(checker):
    calls = []

    def gate(profile_id):
        calls.append(profile_id)
        return True

    definition = ToolDefinition(name="gated", description="Gated.", handler=lambda: "x", check=gate)
    first = checker.check_tool(definition, "p1")
    second = checker.check_tool(definition, "p1")
    assert first.available is True
    assert second.cached is True
    assert len(calls) == 1


def test_check_tool_without_check_or_provider_is_available(checker):
    definition = ToolDefinition(name="plain", description="Plain.", handler=lambda: "x")
    assert checker.check_tool(definition, "p1").available is True


def test_check_tool_with_provider_uses_credential_stub():
    stub = AvailabilityChecker(
        ttl_s=60.0,
        credential_checker=lambda profile_id, provider, model=None: False,
    )
    definition = ToolDefinition(
        name="llm_tool", description="LLM.", handler=lambda: "x", provider="openai"
    )
    result = stub.check_tool(definition, "p1")
    assert result.available is False
    assert result.reason == "credential_missing"


def test_unregister_check_drops_cache(checker):
    checker.register_check("notes", lambda p, provider=None, model=None: True)
    checker.check("notes", "p1", provider="n")
    assert checker.cache_size() == 1
    assert checker.unregister_check("notes") is True
    assert checker.cache_size() == 0
    assert checker.unregister_check("notes") is False
