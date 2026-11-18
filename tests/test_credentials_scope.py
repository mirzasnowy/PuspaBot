"""Tests for the per-profile secret scope (G22).

No credential leaks between profiles: a scope lookup for one
profile can only ever invoke that profile's sources. The
``check_credential`` name and signature are the shared
interface with the G23 tool worker — do NOT rename them. Run
in-memory: ``pytest tests/test_credentials_scope.py --noconftest``.
"""

import pytest

from puspa.credentials.pool import CredentialPool
from puspa.credentials.scope import (
    CredentialScope,
    check_credential,
    default_scope,
    pool_source,
    scoped_env_source,
    set_default_scope,
)


def value_source(value):
    """A source returning one fixed value (or None)."""

    def _source(provider, model):
        return value

    return _source


def test_profiles_do_not_leak_credentials():
    scope = CredentialScope()
    scope.bind("profile-a", "openai", value_source("key-for-a"))
    scope.bind("profile-b", "openai", value_source("key-for-b"))
    assert scope.get("profile-a", "openai") == "key-for-a"
    assert scope.get("profile-b", "openai") == "key-for-b"
    # A third profile has nothing, even though others do.
    with pytest.raises(KeyError):
        scope.get("profile-c", "openai")


def test_no_fallback_to_another_profile_provider():
    scope = CredentialScope()
    scope.bind("profile-a", "openai", value_source("key-for-a"))
    # Profile A has no anthropic key; openai's key is never used.
    with pytest.raises(KeyError):
        scope.get("profile-a", "anthropic")


def test_check_credential_true_false():
    scope = CredentialScope()
    scope.bind("profile-a", "openai", value_source("key-for-a"))
    assert scope.check_credential("profile-a", "openai") is True
    assert scope.check_credential("profile-a", "anthropic") is False
    assert scope.check_credential("profile-b", "openai") is False


def test_check_credential_with_model():
    seen = {}

    def _source(provider, model):
        seen["model"] = model
        return "key-x" if model == "gpt-4o-mini" else None

    scope = CredentialScope()
    scope.bind("profile-a", "openai", _source)
    assert scope.check_credential("profile-a", "openai", model="gpt-4o-mini") is True
    assert scope.check_credential("profile-a", "openai", model="other-model") is False
    assert seen["model"] == "other-model"


def test_check_credential_empty_value_is_false():
    scope = CredentialScope()
    scope.bind("profile-a", "openai", value_source("   "))
    assert scope.check_credential("profile-a", "openai") is False


def test_check_credential_broken_source_is_loud():
    def _broken(provider, model):
        raise RuntimeError("vault is down")

    scope = CredentialScope()
    scope.bind("profile-a", "openai", _broken)
    with pytest.raises(RuntimeError):
        scope.check_credential("profile-a", "openai")


def test_module_check_credential_uses_default_scope():
    scope = CredentialScope()
    scope.bind("profile-a", "openai", value_source("key-for-a"))
    set_default_scope(scope)
    try:
        assert check_credential("profile-a", "openai") is True
        assert check_credential("profile-a", "anthropic") is False
        assert default_scope() is scope
    finally:
        set_default_scope(CredentialScope())


def test_scoped_env_source_bound_to_one_profile_only(monkeypatch):
    monkeypatch.setenv("PUSPA_TEST_PROFILE_KEY", "env-key-for-a")
    scope = CredentialScope()
    scope.bind("profile-a", "openai", scoped_env_source("PUSPA_TEST_PROFILE_KEY"))
    assert scope.get("profile-a", "openai") == "env-key-for-a"
    # Profile B never bound the var, so it sees nothing.
    assert scope.check_credential("profile-b", "openai") is False
    monkeypatch.delenv("PUSPA_TEST_PROFILE_KEY")
    assert scope.check_credential("profile-a", "openai") is False


def test_pool_source_draws_from_pool():
    pool = CredentialPool()
    pool.add("openai", "gpt-4o-mini", "pool-key-one")
    scope = CredentialScope()
    scope.bind("profile-a", "openai", pool_source(pool, "openai", model="gpt-4o-mini"))
    assert scope.get("profile-a", "openai") == "pool-key-one"
    assert scope.check_credential("profile-a", "openai", model="gpt-4o-mini") is True


def test_unbind_and_providers_listing():
    scope = CredentialScope()
    scope.bind("profile-a", "openai", value_source("k1"))
    scope.bind("profile-a", "anthropic", value_source("k2"))
    assert scope.providers("profile-a") == ["anthropic", "openai"]
    assert scope.unbind("profile-a", "openai") is True
    assert scope.unbind("profile-a", "openai") is False
    assert scope.providers("profile-a") == ["anthropic"]


def test_bind_validates_inputs():
    scope = CredentialScope()
    with pytest.raises(ValueError):
        scope.bind("", "openai", value_source("k"))
    with pytest.raises(ValueError):
        scope.bind("profile-a", "", value_source("k"))
    with pytest.raises(ValueError):
        scope.bind("profile-a", "openai", "not-callable")
    with pytest.raises(ValueError):
        scope.get("", "openai")
