"""Tests for OAuth flows (G22): PKCE, device flow, anonymous auth.

All flows run against a fake transport — no live credential,
no live call, no waiting. Run in-memory:
``pytest tests/test_credentials_auth.py --noconftest``.
"""

import base64
import hashlib

import pytest

from puspa.credentials.auth import (
    AnonymousAuth,
    DeviceCode,
    DeviceFlowError,
    OAuthError,
    TokenSet,
    anonymous_auth,
    build_authorization_url,
    code_challenge_s256,
    exchange_code,
    exchange_device_code,
    generate_code_verifier,
    generate_state,
    poll_device_token,
    refresh_access_token,
    start_device_flow,
)


def test_code_verifier_length_and_alphabet():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert all(c.isalnum() or c in "-_" for c in verifier)
    with pytest.raises(ValueError):
        generate_code_verifier(length=10)


def test_code_challenge_is_s256_b64url():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert code_challenge_s256(verifier) == expected
    # Cross-check against a direct computation.
    digest = hashlib.sha256(verifier.encode()).digest()
    direct = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert code_challenge_s256(verifier) == direct


def test_build_authorization_url_pkce():
    request = build_authorization_url(
        "https://example.invalid/oauth/authorize",
        client_id="client-1",
        redirect_uri="https://app.invalid/callback",
        scope=("openid", "email"),
    )
    assert request.url.startswith("https://example.invalid/oauth/authorize?")
    assert "response_type=code" in request.url
    assert "client_id=client-1" in request.url
    assert "code_challenge_method=S256" in request.url
    assert f"state={request.state}" in request.url
    assert request.code_challenge == code_challenge_s256(request.code_verifier)
    assert "code_verifier" not in request.url
    assert "E9Melhoa" not in request.url  # no verifier material in URL


def test_build_authorization_url_uses_given_state_and_verifier():
    request = build_authorization_url(
        "https://example.invalid/oauth/authorize",
        client_id="client-1",
        redirect_uri="https://app.invalid/callback",
        state="state-1234567890abcdef",
        code_verifier="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    )
    assert request.state == "state-1234567890abcdef"
    assert request.code_challenge == ("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")


def test_generate_state_is_random():
    assert generate_state() != generate_state()
    with pytest.raises(ValueError):
        generate_state(length=4)


def fake_http_post(bodies):
    """A transport returning queued bodies (dicts)."""
    calls = []

    def _post(url, data):
        calls.append((url, data))
        return bodies.pop(0)

    _post.calls = calls
    return _post


def test_exchange_code_returns_token_set():
    transport = fake_http_post(
        [
            {
                "access_token": "access-123",
                "refresh_token": "refresh-123",
                "expires_in": 3600,
                "scope": "openid email",
            }
        ]
    )
    token_set = exchange_code(
        "https://example.invalid/token",
        "auth-code-1",
        provider="example",
        client_id="client-1",
        redirect_uri="https://app.invalid/callback",
        code_verifier="verifier-abc",
        http_post=transport,
    )
    assert isinstance(token_set, TokenSet)
    assert token_set.access_token == "access-123"
    assert token_set.refresh_token == "refresh-123"
    assert token_set.scope == ("openid", "email")
    assert token_set.expires_at is not None
    assert not token_set.is_expired()
    assert token_set.authorization_header() == {"Authorization": "Bearer access-123"}
    # The verifier was sent with the exchange.
    assert transport.calls[0][1]["code_verifier"] == "verifier-abc"
    assert transport.calls[0][1]["grant_type"] == "authorization_code"
    # Repr never shows the tokens.
    assert "access-123" not in repr(token_set)
    assert "refresh-123" not in repr(token_set)


def test_exchange_code_error_is_loud():
    transport = fake_http_post([{"error": "invalid_grant"}])
    with pytest.raises(OAuthError) as excinfo:
        exchange_code(
            "https://example.invalid/token",
            "bad-code",
            provider="example",
            client_id="client-1",
            redirect_uri="https://app.invalid/callback",
            code_verifier="verifier-abc",
            http_post=transport,
        )
    assert "invalid_grant" in str(excinfo.value)
    assert "example" in str(excinfo.value)


def test_exchange_code_without_access_token_fails():
    transport = fake_http_post([{"token_type": "Bearer"}])
    with pytest.raises(OAuthError):
        exchange_code(
            "https://example.invalid/token",
            "code-1",
            provider="example",
            client_id="client-1",
            redirect_uri="https://app.invalid/callback",
            code_verifier="v",
            http_post=transport,
        )


def test_refresh_access_token_keeps_old_refresh_token():
    transport = fake_http_post([{"access_token": "access-2", "expires_in": 60}])
    token_set = refresh_access_token(
        "https://example.invalid/token",
        "refresh-old",
        provider="example",
        client_id="client-1",
        http_post=transport,
    )
    assert token_set.access_token == "access-2"
    assert token_set.refresh_token == "refresh-old"
    assert transport.calls[0][1]["grant_type"] == "refresh_token"


def test_refresh_access_token_uses_new_refresh_token():
    transport = fake_http_post(
        [{"access_token": "access-3", "refresh_token": "refresh-new", "expires_in": 60}]
    )
    token_set = refresh_access_token(
        "https://example.invalid/token",
        "refresh-old",
        provider="example",
        client_id="client-1",
        http_post=transport,
    )
    assert token_set.refresh_token == "refresh-new"


def test_token_set_expiry_with_leeway():
    token_set = TokenSet(provider="example", access_token="a", expires_at=1000.0)
    assert token_set.is_expired(now=999.0) is False
    assert token_set.is_expired(now=1000.0) is True
    assert token_set.is_expired(now=900.0, leeway_seconds=150) is True
    no_expiry = TokenSet(provider="example", access_token="a")
    assert no_expiry.is_expired() is False


def test_start_device_flow():
    transport = fake_http_post(
        [
            {
                "device_code": "device-1",
                "user_code": "USER-CODE",
                "verification_uri": "https://example.invalid/device",
                "expires_in": 600,
                "interval": 5,
            }
        ]
    )
    code = start_device_flow(
        "https://example.invalid/device/code",
        provider="example",
        client_id="client-1",
        scope=("openid",),
        http_post=transport,
    )
    assert isinstance(code, DeviceCode)
    assert code.user_code == "USER-CODE"
    assert code.verification_uri == "https://example.invalid/device"
    assert code.interval == 5
    assert "device-1" not in repr(code)


def test_start_device_flow_incomplete_body_fails():
    transport = fake_http_post([{"device_code": "device-1"}])
    with pytest.raises(OAuthError):
        start_device_flow(
            "https://example.invalid/device/code",
            provider="example",
            client_id="client-1",
            http_post=transport,
        )


def test_poll_device_token_pending_then_success_no_wait():
    transport = fake_http_post(
        [
            {"error": "authorization_pending"},
            {"error": "authorization_pending"},
            {"access_token": "access-device", "expires_in": 3600},
        ]
    )
    sleeps = []
    token_set = poll_device_token(
        "https://example.invalid/token",
        DeviceCode(
            provider="example",
            device_code="device-1",
            user_code="USER-CODE",
            verification_uri="https://example.invalid/device",
            expires_in=600,
            interval=5,
        ),
        provider="example",
        client_id="client-1",
        http_post=transport,
        sleep=sleeps.append,
    )
    assert token_set.access_token == "access-device"
    assert sleeps == [5, 5]  # polled twice while pending, never waited


def test_poll_device_token_slow_down_lengthens_interval():
    transport = fake_http_post(
        [
            {"error": "slow_down"},
            {"access_token": "access-device", "expires_in": 60},
        ]
    )
    sleeps = []
    poll_device_token(
        "https://example.invalid/token",
        "device-1",
        provider="example",
        client_id="client-1",
        http_post=transport,
        sleep=sleeps.append,
    )
    assert sleeps == [10]  # 5 + 5 after slow_down


def test_poll_device_token_denied_is_terminal():
    transport = fake_http_post([{"error": "access_denied"}])
    with pytest.raises(DeviceFlowError):
        poll_device_token(
            "https://example.invalid/token",
            "device-1",
            provider="example",
            client_id="client-1",
            http_post=transport,
            sleep=lambda s: None,
        )


def test_poll_device_token_expired_is_terminal():
    transport = fake_http_post([{"error": "expired_token"}])
    with pytest.raises(DeviceFlowError):
        poll_device_token(
            "https://example.invalid/token",
            "device-1",
            provider="example",
            client_id="client-1",
            http_post=transport,
            sleep=lambda s: None,
        )


def test_poll_device_token_timeout_after_max_attempts():
    transport = fake_http_post([{"error": "authorization_pending"}] * 10)
    with pytest.raises(DeviceFlowError):
        poll_device_token(
            "https://example.invalid/token",
            "device-1",
            provider="example",
            client_id="client-1",
            http_post=transport,
            max_attempts=3,
            sleep=lambda s: None,
        )


def test_exchange_device_code_direct():
    transport = fake_http_post([{"access_token": "access-device", "expires_in": 60}])
    token_set = exchange_device_code(
        "https://example.invalid/token",
        "device-1",
        provider="example",
        client_id="client-1",
        http_post=transport,
    )
    assert token_set.access_token == "access-device"


def test_anonymous_auth_carries_no_credential():
    anon = anonymous_auth("example")
    assert isinstance(anon, AnonymousAuth)
    assert anon.access_token is None
    assert anon.authorization_header() == {}
    assert anon.provider == "example"
    with pytest.raises(ValueError):
        anonymous_auth("")
