"""Tests for the provider pool's Settings side (V2-40).

Covers what the in-memory pool tests do not: discovering a user's
credentials as pool accounts (BYOK keys in the vault, OAuth login
via ``VaultTokenStore``), resolving a credential at call time,
the quota bars ``puspa/pwa/model_settings.pool_quota_bars`` returns
for the Settings screen, and the
``GET /api/model-settings/pool-quota`` endpoint in ``puspa/pwa/app.py``.

These tests use the puspa_test_w1 database (vault + model settings
rows). No live provider is ever called and no key value is ever
returned in any response: every assertion that touches a key checks
the value does not appear in the output.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.pwa.model_settings import create_schema, pool_quota_bars, save_byok_key
from puspa.router.oauth import TokenRefresher, TokenSet
from puspa.router.pool import (
    build_pool_for_user,
    discover_accounts,
    provider_pool_for_user,
    reset_provider_pools,
    resolve_credential,
)
from puspa.router.provider_login import VaultTokenStore

BASE_TIME = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_pools():
    """The process-wide pool registry starts empty per test."""
    reset_provider_pools()
    yield
    reset_provider_pools()


@pytest.fixture
def user_id(make_user):
    """A real user id (the vault has a foreign key to users)."""
    return make_user()["user_id"]


@pytest.fixture
def client(make_user, test_dsn):
    u = make_user()
    create_schema(test_dsn)
    app = create_app(test_user_id=u["user_id"])
    with TestClient(app) as c:
        c.user_id = u["user_id"]
        yield c


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_no_credentials_means_no_pool(user_id, test_dsn, vault_master_key):
    assert discover_accounts("openrouter", user_id, dsn=test_dsn) == []
    assert build_pool_for_user("openrouter", user_id, dsn=test_dsn) is None
    bars = pool_quota_bars(user_id, "openrouter", dsn=test_dsn)
    assert bars == {"provider": "openrouter", "strategy": "rotate", "accounts": []}


def test_primary_byok_key_is_discovered_as_slot_1(user_id, test_dsn, vault_master_key):
    save_byok_key(user_id, "openrouter", "test-key-not-real", dsn=test_dsn)
    accounts = discover_accounts("openrouter", user_id, dsn=test_dsn)
    assert [a.account_id for a in accounts] == ["openrouter:1"]
    assert accounts[0].kind == "byok"
    assert accounts[0].auth_ref == "byok:openrouter"
    assert accounts[0].label == "openrouter key 1"
    assert "test-key-not-real" not in repr(accounts)


def test_numbered_extra_byok_keys_are_discovered_as_slots(user_id, test_dsn, vault_master_key):
    from puspa.accounts.vault import put as vault_put

    save_byok_key(user_id, "openrouter", "test-key-1-not-real", dsn=test_dsn)
    vault_put(user_id, "byok:openrouter:2", "test-key-2-not-real", dsn=test_dsn)
    vault_put(user_id, "byok:openrouter:3", "test-key-3-not-real", dsn=test_dsn)
    accounts = discover_accounts("openrouter", user_id, dsn=test_dsn)
    assert [a.account_id for a in accounts] == ["openrouter:1", "openrouter:2", "openrouter:3"]
    assert [a.auth_ref for a in accounts] == [
        "byok:openrouter",
        "byok:openrouter:2",
        "byok:openrouter:3",
    ]
    assert "test-key-2-not-real" not in repr(accounts)


def test_oauth_login_is_discovered_as_the_oauth_slot(user_id, test_dsn, vault_master_key):
    tokens = TokenSet(
        access_token="test-access-not-real",
        refresh_token="test-refresh-not-real",
        expires_at=BASE_TIME + timedelta(hours=1),
        obtained_at=BASE_TIME,
    )
    VaultTokenStore(dsn=test_dsn).save(user_id, "openrouter", tokens)
    accounts = discover_accounts("openrouter", user_id, dsn=test_dsn)
    assert [a.account_id for a in accounts] == ["openrouter:oauth"]
    assert accounts[0].kind == "oauth"
    assert accounts[0].label == "openrouter subscription"
    assert "test-access-not-real" not in repr(accounts)


def test_byok_and_oauth_combine_into_one_pool(user_id, test_dsn, vault_master_key):
    save_byok_key(user_id, "openrouter", "test-key-not-real", dsn=test_dsn)
    tokens = TokenSet(
        access_token="test-access-not-real",
        refresh_token="test-refresh-not-real",
        expires_at=BASE_TIME + timedelta(hours=1),
        obtained_at=BASE_TIME,
    )
    VaultTokenStore(dsn=test_dsn).save(user_id, "openrouter", tokens)
    pool = build_pool_for_user("openrouter", user_id, dsn=test_dsn)
    assert pool is not None
    assert [a.account_id for a in pool.accounts] == ["openrouter:1", "openrouter:oauth"]


# ---------------------------------------------------------------------------
# Credential resolution (at call time only)
# ---------------------------------------------------------------------------


def test_resolve_byok_credential_reads_the_vault(user_id, test_dsn, vault_master_key):
    save_byok_key(user_id, "openrouter", "test-key-not-real", dsn=test_dsn)
    pool = build_pool_for_user("openrouter", user_id, dsn=test_dsn)
    assert (
        resolve_credential(pool.accounts[0], user_id=user_id, dsn=test_dsn) == "test-key-not-real"
    )


def test_resolve_byok_credential_fails_without_a_key(user_id, test_dsn, vault_master_key):
    from puspa.router.pool import PoolAccount

    account = PoolAccount(
        account_id="openrouter:1",
        provider="openrouter",
        kind="byok",
        label="openrouter key 1",
        auth_ref="byok:openrouter",
    )
    with pytest.raises(KeyError, match="no BYOK key stored"):
        resolve_credential(account, user_id=user_id, dsn=test_dsn)


def test_resolve_oauth_credential_returns_a_valid_token(user_id, test_dsn, vault_master_key):
    tokens = TokenSet(
        access_token="test-access-not-real",
        refresh_token="test-refresh-not-real",
        expires_at=BASE_TIME + timedelta(hours=1),
        obtained_at=BASE_TIME,
    )
    VaultTokenStore(dsn=test_dsn).save(user_id, "openrouter", tokens)
    pool = build_pool_for_user("openrouter", user_id, dsn=test_dsn)
    refresher = TokenRefresher(now_fn=lambda: BASE_TIME)
    refresher.register_simulated(
        "openrouter", token_url="https://api.openrouter.ai/oauth/token", client_id="test-client"
    )
    resolved = resolve_credential(
        pool.accounts[0], user_id=user_id, dsn=test_dsn, oauth_refresher=refresher
    )
    assert resolved == "test-access-not-real"


# ---------------------------------------------------------------------------
# Quota bars for Settings
# ---------------------------------------------------------------------------


def test_pool_quota_bars_show_usage_without_key_material(user_id, test_dsn, vault_master_key):
    save_byok_key(user_id, "openrouter", "test-key-not-real", dsn=test_dsn)
    pool = provider_pool_for_user("openrouter", user_id, dsn=test_dsn)
    pool.register_quota("openrouter:1", five_hour_tokens=1000, weekly_tokens=7000)
    pool.quota_store.record_usage("openrouter:1:5h", 250, 0)
    bars = pool_quota_bars(user_id, "openrouter", dsn=test_dsn)
    assert bars["provider"] == "openrouter"
    assert bars["strategy"] == "rotate"
    assert len(bars["accounts"]) == 1
    account = bars["accounts"][0]
    assert account["account_id"] == "openrouter:1"
    assert account["available"] is True
    windows = {w["window"]: w for w in account["windows"]}
    assert windows["5h"]["used_fraction"] == pytest.approx(0.25)
    assert windows["5h"]["tokens_remaining"] == 750
    # Real clock: up to a second may pass between registering the
    # window and reading the bar.
    assert 17_990 <= windows["5h"]["reset_in_seconds"] <= 18_000
    assert "test-key-not-real" not in repr(bars)


def test_cached_pool_is_shared_between_calls(user_id, test_dsn, vault_master_key):
    save_byok_key(user_id, "openrouter", "test-key-not-real", dsn=test_dsn)
    first = provider_pool_for_user("openrouter", user_id, dsn=test_dsn)
    first.register_quota("openrouter:1", five_hour_tokens=100)
    second = provider_pool_for_user("openrouter", user_id, dsn=test_dsn)
    assert second is first
    assert second.quota_store.get("openrouter:1:5h") is not None


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


def test_pool_quota_endpoint_empty_without_credentials(client, vault_master_key):
    resp = client.get("/api/model-settings/pool-quota?provider=openrouter")
    assert resp.status_code == 200
    assert resp.json() == {"provider": "openrouter", "strategy": "rotate", "accounts": []}


def test_pool_quota_endpoint_shows_accounts_without_keys(client, vault_master_key):
    assert (
        client.post(
            "/api/model-settings/byok-key",
            json={"provider": "openrouter", "key": "test-key-not-real"},
        ).status_code
        == 200
    )
    resp = client.get("/api/model-settings/pool-quota?provider=openrouter")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "openrouter"
    assert [a["account_id"] for a in body["accounts"]] == ["openrouter:1"]
    assert body["accounts"][0]["label"] == "openrouter key 1"
    assert body["accounts"][0]["available"] is True
    assert "test-key-not-real" not in resp.text


def test_pool_quota_endpoint_shows_recorded_usage(client, vault_master_key):
    client.post(
        "/api/model-settings/byok-key",
        json={"provider": "openrouter", "key": "test-key-not-real"},
    )
    # First read builds + caches the pool for this user + provider.
    assert client.get("/api/model-settings/pool-quota?provider=openrouter").status_code == 200
    # The endpoint reads the same cached pool, so usage recorded
    # on it shows up in the next read.
    cached = provider_pool_for_user("openrouter", client.user_id, dsn=None)
    assert cached is not None
    cached.register_quota("openrouter:1", five_hour_tokens=1000)
    cached.quota_store.record_usage("openrouter:1:5h", 500, 0)
    body = client.get("/api/model-settings/pool-quota?provider=openrouter").json()
    windows = {w["window"]: w for w in body["accounts"][0]["windows"]}
    assert windows["5h"]["used_fraction"] == pytest.approx(0.5)
    assert windows["5h"]["tokens_remaining"] == 500


def test_pool_quota_endpoint_requires_login():
    app = create_app(test_user_id=None)
    with TestClient(app) as c:
        assert c.get("/api/model-settings/pool-quota?provider=openrouter").status_code == 401
