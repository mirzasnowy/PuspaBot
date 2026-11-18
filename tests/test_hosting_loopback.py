"""Tests: loopback trust, service allowlist, and request gateway.

All in-memory, no database, no network.
"""

import pytest

from puspa.hosting.gateway import SERVICE_MODE, SINGLE_USER_MODE, authorize_request
from puspa.hosting.loopback import (
    is_browser_request,
    is_loopback_host,
    is_loopback_origin,
    loopback_trusted,
)
from puspa.hosting.pairing import PairingStore
from puspa.hosting.service_allow import DEFAULT_SERVICE_ALLOW, ServiceAllowlist


class FakeClock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# --- loopback host parsing ---


@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.0.0.1", True),
        ("127.0.0.1:8080", True),
        ("127.0.0.2", True),
        ("127.255.255.255", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("localhost:3000", True),
        ("app.localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("[::1]:8080", True),
        ("192.168.1.1", False),
        ("10.0.0.1", False),
        ("8.8.8.8", False),
        ("0.0.0.0", False),
        ("example.com", False),
        ("localhost.example.com", False),
        ("", False),
        (None, False),
    ],
)
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected


@pytest.mark.parametrize(
    "origin, expected",
    [
        ("http://localhost:8080", True),
        ("https://127.0.0.1", True),
        ("ws://[::1]:9000", True),
        ("http://example.com", False),
        ("http://192.168.1.1:8080", False),
        ("https://localhost.example.com", False),
        ("ftp://localhost", False),
        ("not a url", False),
        ("", False),
        (None, False),
    ],
)
def test_is_loopback_origin(origin, expected):
    assert is_loopback_origin(origin) is expected


# --- browser detection ---


def test_browser_detected_by_sec_fetch_mode():
    assert is_browser_request({"Sec-Fetch-Mode": "navigate"}) is True
    assert is_browser_request({"SEC-FETCH-MODE": "cors"}) is True


def test_browser_detected_by_mozilla_user_agent():
    assert is_browser_request({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}) is True


def test_non_browser_clients_not_detected():
    assert is_browser_request({"User-Agent": "curl/8.0"}) is False
    assert is_browser_request({"User-Agent": "PuspaApp/1.0"}) is False
    assert is_browser_request({}) is False
    assert is_browser_request(None) is False


# --- loopback_trusted matrix ---


def test_loopback_trusted_plain_client():
    assert loopback_trusted("127.0.0.1", None, {}) is True
    assert loopback_trusted("127.0.0.1", "http://localhost:8080", {}) is True
    assert loopback_trusted("[::1]:8080", None, {}) is True


def test_loopback_trusted_rejects_remote_origin():
    assert loopback_trusted("127.0.0.1", "http://example.com", {}) is False


def test_loopback_trusted_rejects_remote_host():
    assert loopback_trusted("192.168.1.1", None, {}) is False


def test_loopback_trusted_never_for_browsers():
    browser = {"Sec-Fetch-Mode": "navigate", "User-Agent": "Mozilla/5.0"}
    assert loopback_trusted("127.0.0.1", "http://localhost", browser) is False
    assert loopback_trusted("127.0.0.1", None, browser) is False


# --- service allowlist ---


def test_default_allowlist_is_minimal_and_read_only():
    allow = ServiceAllowlist(DEFAULT_SERVICE_ALLOW)
    assert len(allow) == 3
    assert allow.allowed("GET", "/health") is True
    assert allow.allowed("GET", "/healthz") is True
    assert allow.allowed("GET", "/version") is True
    assert allow.allowed("POST", "/health") is False
    assert allow.allowed("get", "/health") is True  # method is case-insensitive
    assert allow.allowed("GET", "/health/secret") is False  # fullmatch, no prefix
    assert allow.allowed("GET", "/admin") is False


def test_custom_allowlist_regex_paths():
    allow = ServiceAllowlist((("POST", r"/v1/hosting/link/[a-z0-9]+"),))
    assert allow.allowed("POST", "/v1/hosting/link/abc1234") is True
    assert allow.allowed("GET", "/v1/hosting/link/abc1234") is False
    assert allow.allowed("POST", "/v1/hosting/link/ABC") is False


def test_allowlist_rejects_bad_regex_at_construction():
    with pytest.raises(ValueError):
        ServiceAllowlist((("GET", r"/unclosed["),))


def test_allowlist_wildcard_method():
    allow = ServiceAllowlist((("*", r"/ping"),))
    assert allow.allowed("GET", "/ping") is True
    assert allow.allowed("DELETE", "/ping") is True


# --- gateway ---


def _google_validator(token):
    if token == "good-google-token":
        return "user-1"
    raise ValueError("unknown session token")


def _pairing_token():
    clock = FakeClock()
    store = PairingStore(clock=clock)
    code = store.issue("user-2", "headless-server")["code"]
    return store, store.verify(code, address="10.0.0.5")["session_token"]


def test_gateway_google_session_allows_anywhere():
    decision = authorize_request(
        "POST",
        "/v1/admin",
        {},
        host="203.0.113.7",
        session_token="good-google-token",
        session_validator=_google_validator,
    )
    assert decision.allowed is True
    assert decision.via == "google_session"
    assert decision.user_id == "user-1"


def test_gateway_bad_session_falls_through_to_deny():
    decision = authorize_request(
        "GET",
        "/admin",
        {},
        host="203.0.113.7",
        session_token="bad-token",
        session_validator=_google_validator,
    )
    assert decision.allowed is False
    assert decision.via == "denied"


def test_gateway_validator_exception_is_fail_closed_not_a_crash():
    def raising_validator(token):
        raise RuntimeError("database down")

    decision = authorize_request(
        "GET",
        "/health",
        {},
        host="127.0.0.1",
        session_token="any",
        session_validator=raising_validator,
    )
    # No session, but loopback + allowlisted path still applies.
    assert decision.allowed is True
    assert decision.via == "service_allow"


def test_gateway_pairing_session_allows_anywhere():
    store, token = _pairing_token()
    decision = authorize_request(
        "POST",
        "/v1/admin",
        {},
        host="203.0.113.7",
        pairing_token=token,
        pairing_store=store,
    )
    assert decision.allowed is True
    assert decision.via == "pairing_session"
    assert decision.user_id == "user-2"


def test_gateway_revoked_pairing_session_denied():
    store, token = _pairing_token()
    store.revoke_session(token)
    decision = authorize_request(
        "GET", "/admin", {}, host="203.0.113.7", pairing_token=token, pairing_store=store
    )
    assert decision.allowed is False


def test_gateway_service_mode_loopback_only_allowlisted_paths():
    allowed = authorize_request("GET", "/health", {}, host="127.0.0.1", mode=SERVICE_MODE)
    assert allowed.allowed is True
    assert allowed.via == "service_allow"
    denied = authorize_request("GET", "/admin", {}, host="127.0.0.1", mode=SERVICE_MODE)
    assert denied.allowed is False
    assert denied.via == "denied"


def test_gateway_single_user_mode_trusts_loopback_outright():
    decision = authorize_request("GET", "/admin", {}, host="127.0.0.1", mode=SINGLE_USER_MODE)
    assert decision.allowed is True
    assert decision.via == "loopback"


def test_gateway_browser_loopback_never_trusted():
    browser = {"Sec-Fetch-Mode": "navigate"}
    for mode in (SERVICE_MODE, SINGLE_USER_MODE):
        decision = authorize_request("GET", "/health", browser, host="127.0.0.1", mode=mode)
        assert decision.allowed is False, mode


def test_gateway_remote_without_session_denied():
    decision = authorize_request("GET", "/health", {}, host="203.0.113.7")
    assert decision.allowed is False
    assert decision.via == "denied"


def test_gateway_unknown_mode_denied():
    decision = authorize_request("GET", "/health", {}, host="127.0.0.1", mode="cluster")
    assert decision.allowed is False
