"""Tests for auto OAuth token refresh + format translation
(puspa/router/oauth.py, puspa/router/translate.py).

Pattern studied, then rewritten as Puspa's own code: 9Router
(decolua/9router, MIT) keeps OAuth subscription sessions alive by
refreshing access tokens, and speaks every provider behind it in
that provider's own wire format while the client only speaks
OpenAI. These tests prove the mechanism with a FAKE token
endpoint and a FAKE clock (no live provider is ever called, no
real credential is ever used):

- a token expiring inside the leeway is refreshed BEFORE it is
  handed out (exactly one endpoint call, the new token is used);
- a token far from expiry is returned untouched, with no refresh;
- a failed refresh raises a clear error naming the provider, and
  the dead token is never returned silently;
- two parallel uses while the token is near expiry cause exactly
  one refresh (no double refresh);
- a refresh token never appears in any recorded output (reprs,
  error messages, audit events);
- request/response translation between the two formats the router
  already serves (OpenAI chat-completions and Anthropic Messages)
  really changes structure both ways, round-trips for fields with
  an equivalent, and reports fields with no equivalent instead of
  dropping them silently.

In-memory only: this module shadows the conftest autouse Postgres
reset fixture (``_reset_tables``) with a no-op, so no test here
touches the puspa_test database. Time is a fake clock: a mutable
holder the refresher reads through ``now_fn``.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from puspa.router.oauth import (
    DEFAULT_REFRESH_LEEWAY_SECONDS,
    OAuthProviderConfig,
    TokenRefreshError,
    TokenRefresher,
    TokenSet,
    TokenStore,
)
from puspa.router.translate import (
    FORMAT_ANTHROPIC,
    FORMAT_OPENAI,
    translate_request,
    translate_response,
)

PROVIDER = "xai"

BASE_TIME = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)

# Deliberately fake token values, so a leak test can search for them.
OLD_ACCESS = "access-token-FAKE-OLD-7f3a"
OLD_REFRESH = "refresh-token-FAKE-OLD-9c1e"
NEW_ACCESS = "access-token-FAKE-NEW-4b2d"
NEW_REFRESH = "refresh-token-FAKE-NEW-8e5f"


@pytest.fixture(autouse=True)
def _reset_tables():
    """Shadow the conftest autouse Postgres reset: in-memory only."""
    yield


# ---------------------------------------------------------------------------
# Helpers: fake clock + fake OAuth token endpoint
# ---------------------------------------------------------------------------

class FakeClock:
    """Mutable clock: the refresher reads the current fake time."""

    def __init__(self, start: datetime = BASE_TIME) -> None:
        self.now_value = start

    def now(self) -> datetime:
        return self.now_value

    def advance(self, **kwargs) -> None:
        self.now_value = self.now_value + timedelta(**kwargs)


class FakeResponse:
    """Minimal stand-in for an httpx response."""

    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeTokenEndpoint:
    """Fake OAuth token endpoint: scripted answers, calls recorded.

    ``script`` maps a call number to (status, payload); a payload
    may also be a callable taking the posted data. Every post is
    recorded as (url, data) on ``.posts``. ``.posts`` holds the
    refresh token value itself (that is the endpoint's job: it is
    the party the token is sent to), so leak tests must NOT search
    ``.posts`` — they search everything the Puspa module emits.
    """

    def __init__(self, script=None, *, delay_seconds: float = 0.0) -> None:
        self.script = script or {}
        self.posts: list[tuple[str, dict]] = []
        self.delay_seconds = delay_seconds
        self._lock = threading.Lock()

    def post(self, url, data=None):
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        with self._lock:
            self.posts.append((url, dict(data or {})))
            call_number = len(self.posts)
        answer = self.script.get(call_number, self.script.get("default"))
        if answer is None:
            answer = (200, {"access_token": NEW_ACCESS,
                            "refresh_token": NEW_REFRESH,
                            "expires_in": 3600, "token_type": "Bearer"})
        status, payload = answer
        if callable(payload):
            payload = payload(dict(data or {}))
        return FakeResponse(status, payload)


def make_refresher(clock: FakeClock, *, leeway: int = DEFAULT_REFRESH_LEEWAY_SECONDS,
                   token_url: str | None = "https://auth.example.test/oauth2/token",
                   client_id: str | None = "fake-client-id") -> TokenRefresher:
    refresher = TokenRefresher(now_fn=clock.now)
    refresher.register(OAuthProviderConfig(
        provider=PROVIDER, token_url=token_url, client_id=client_id,
        scope=("openid", "profile"), refresh_leeway_seconds=leeway,
        simulated=True))
    return refresher


def token_set(clock: FakeClock, *, access: str = OLD_ACCESS,
              refresh: str | None = OLD_REFRESH,
              expires_in_seconds: int | None = 3600) -> TokenSet:
    return TokenSet(
        access_token=access, refresh_token=refresh,
        expires_at=(clock.now() + timedelta(seconds=expires_in_seconds)
                    if expires_in_seconds is not None else None),
        obtained_at=clock.now(),
    )


# ---------------------------------------------------------------------------
# Proactive refresh: before expiry, exactly once, new token used
# ---------------------------------------------------------------------------

def test_near_expiry_token_is_refreshed_once_and_new_token_used():
    clock = FakeClock()
    refresher = make_refresher(clock)
    # 120 seconds left, inside the 300-second leeway: must refresh.
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=120))
    endpoint = FakeTokenEndpoint()
    token = refresher.get_valid_token(PROVIDER, endpoint)
    assert token == NEW_ACCESS
    assert len(endpoint.posts) == 1
    url, data = endpoint.posts[0]
    assert url == "https://auth.example.test/oauth2/token"
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == OLD_REFRESH
    assert data["client_id"] == "fake-client-id"
    # The store now holds the fresh pair (rotated refresh token).
    stored = refresher.store.get(PROVIDER)
    assert stored.access_token == NEW_ACCESS
    assert stored.refresh_token == NEW_REFRESH
    assert stored.expires_at == BASE_TIME + timedelta(seconds=3600)
    assert stored.is_expired(clock.now()) is False
    assert any(event.outcome == "refreshed" for event in refresher.events)


def test_expired_token_is_refreshed_not_used():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=-60))
    endpoint = FakeTokenEndpoint()
    assert refresher.get_valid_token(PROVIDER, endpoint) == NEW_ACCESS
    assert len(endpoint.posts) == 1


def test_far_from_expiry_token_is_returned_without_refresh():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=3600))
    endpoint = FakeTokenEndpoint()
    assert refresher.get_valid_token(PROVIDER, endpoint) == OLD_ACCESS
    assert endpoint.posts == []
    assert refresher.store.get(PROVIDER).access_token == OLD_ACCESS


def test_token_without_known_expiry_is_used_as_is():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=None))
    endpoint = FakeTokenEndpoint()
    assert refresher.get_valid_token(PROVIDER, endpoint) == OLD_ACCESS
    assert endpoint.posts == []


def test_leeway_boundary_is_respected():
    clock = FakeClock()
    # Leeway 300: 299 seconds left refreshes, 301 does not.
    refresher = make_refresher(clock, leeway=300)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=299))
    endpoint = FakeTokenEndpoint()
    assert refresher.get_valid_token(PROVIDER, endpoint) == NEW_ACCESS
    assert len(endpoint.posts) == 1
    refresher2 = make_refresher(clock, leeway=300)
    refresher2.store.put(PROVIDER, token_set(clock, expires_in_seconds=301))
    endpoint2 = FakeTokenEndpoint()
    assert refresher2.get_valid_token(PROVIDER, endpoint2) == OLD_ACCESS
    assert endpoint2.posts == []


def test_refresh_without_new_refresh_token_keeps_old_one():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=60))
    endpoint = FakeTokenEndpoint({
        "default": (200, {"access_token": NEW_ACCESS, "expires_in": 1800}),
    })
    assert refresher.get_valid_token(PROVIDER, endpoint) == NEW_ACCESS
    assert refresher.store.get(PROVIDER).refresh_token == OLD_REFRESH
    assert refresher.store.get(PROVIDER).expires_at == \
        BASE_TIME + timedelta(seconds=1800)


def test_refresh_without_expires_in_assumes_one_hour():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=60))
    endpoint = FakeTokenEndpoint({
        "default": (200, {"access_token": NEW_ACCESS}),
    })
    assert refresher.get_valid_token(PROVIDER, endpoint) == NEW_ACCESS
    assert refresher.store.get(PROVIDER).expires_at == \
        BASE_TIME + timedelta(seconds=3600)


def test_second_call_after_refresh_makes_no_second_refresh():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=60))
    endpoint = FakeTokenEndpoint()
    assert refresher.get_valid_token(PROVIDER, endpoint) == NEW_ACCESS
    assert refresher.get_valid_token(PROVIDER, endpoint) == NEW_ACCESS
    assert len(endpoint.posts) == 1


# ---------------------------------------------------------------------------
# Refresh failure: clear error, dead token never used silently
# ---------------------------------------------------------------------------

def test_invalid_grant_failure_names_provider_and_needs_relogin():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=60))
    endpoint = FakeTokenEndpoint({
        "default": (400, {"error": "invalid_grant",
                          "error_description": "refresh token expired"}),
    })
    with pytest.raises(TokenRefreshError) as excinfo:
        refresher.get_valid_token(PROVIDER, endpoint)
    error = excinfo.value
    assert error.provider == PROVIDER
    assert error.status == 400
    assert error.error_code == "invalid_grant"
    assert error.reauth_required is True
    message = str(error)
    assert PROVIDER in message
    assert "invalid_grant" in message
    assert "interactive login required" in message
    # The dead token is still in the store, but it was never returned.
    assert refresher.store.get(PROVIDER).access_token == OLD_ACCESS
    assert any(event.outcome == "failed" for event in refresher.events)


def test_server_error_failure_is_not_a_relogin():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=60))
    endpoint = FakeTokenEndpoint({
        "default": (500, {"error": "server_error"}),
    })
    with pytest.raises(TokenRefreshError) as excinfo:
        refresher.get_valid_token(PROVIDER, endpoint)
    assert excinfo.value.status == 500
    assert excinfo.value.reauth_required is False


def test_refresh_answer_without_access_token_is_an_error():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=60))
    endpoint = FakeTokenEndpoint({
        "default": (200, {"token_type": "Bearer", "expires_in": 3600}),
    })
    with pytest.raises(TokenRefreshError) as excinfo:
        refresher.get_valid_token(PROVIDER, endpoint)
    assert "no access_token" in str(excinfo.value)


def test_unreachable_token_endpoint_is_a_clear_error():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=60))

    class BrokenEndpoint:
        def post(self, url, data=None):
            raise ConnectionError("network down")

    with pytest.raises(TokenRefreshError) as excinfo:
        refresher.get_valid_token(PROVIDER, BrokenEndpoint())
    assert excinfo.value.status is None
    assert "unreachable" in str(excinfo.value)


def test_no_stored_token_means_login_required():
    clock = FakeClock()
    refresher = make_refresher(clock)
    with pytest.raises(TokenRefreshError) as excinfo:
        refresher.get_valid_token(PROVIDER, FakeTokenEndpoint())
    assert excinfo.value.reauth_required is True
    assert "interactive login" in str(excinfo.value)


def test_no_refresh_token_means_login_required():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(
        PROVIDER, token_set(clock, refresh=None, expires_in_seconds=60))
    with pytest.raises(TokenRefreshError) as excinfo:
        refresher.get_valid_token(PROVIDER, FakeTokenEndpoint())
    assert excinfo.value.reauth_required is True


def test_unregistered_provider_is_rejected_by_name():
    clock = FakeClock()
    refresher = TokenRefresher(now_fn=clock.now)
    with pytest.raises(TokenRefreshError) as excinfo:
        refresher.get_valid_token("never_registered", FakeTokenEndpoint())
    assert "never_registered" in str(excinfo.value)
    assert "not registered" in str(excinfo.value)


def test_refresh_without_token_url_or_client_id_names_the_gap():
    clock = FakeClock()
    refresher = TokenRefresher(now_fn=clock.now)
    refresher.register_simulated("openai_codex", token_url=None,
                                 client_id=None,
                                 notes="token endpoint not verified")
    refresher.store.put("openai_codex",
                        token_set(clock, expires_in_seconds=60))
    with pytest.raises(TokenRefreshError) as excinfo:
        refresher.get_valid_token("openai_codex", FakeTokenEndpoint())
    assert "no token URL" in str(excinfo.value)
    assert "token endpoint not verified" in str(excinfo.value)

    refresher2 = TokenRefresher(now_fn=clock.now)
    refresher2.register_simulated(
        "github_copilot",
        token_url="https://github.com/login/oauth/access_token",
        client_id=None, notes="no client id wired yet")
    refresher2.store.put("github_copilot",
                         token_set(clock, expires_in_seconds=60))
    with pytest.raises(TokenRefreshError) as excinfo:
        refresher2.get_valid_token("github_copilot", FakeTokenEndpoint())
    assert "no client id" in str(excinfo.value)


def test_non_https_token_url_is_rejected_at_registration():
    refresher = TokenRefresher(now_fn=FakeClock().now)
    with pytest.raises(ValueError):
        refresher.register(OAuthProviderConfig(
            provider="x", token_url="http://plain.example/token",
            client_id="id"))


# ---------------------------------------------------------------------------
# Single-flight: two parallel uses, one refresh
# ---------------------------------------------------------------------------

def test_parallel_uses_share_a_single_refresh():
    clock = FakeClock()
    refresher = make_refresher(clock)
    refresher.store.put(PROVIDER, token_set(clock, expires_in_seconds=60))
    # A slow endpoint forces the two threads to overlap inside the
    # refresh; without single-flight both would post.
    endpoint = FakeTokenEndpoint(delay_seconds=0.2)
    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[BaseException] = []

    def use_token():
        try:
            barrier.wait(timeout=5)
            results.append(refresher.get_valid_token(PROVIDER, endpoint))
        except BaseException as exc:  # noqa: BLE001 - collected, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=use_token) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    assert results == [NEW_ACCESS, NEW_ACCESS]
    assert len(endpoint.posts) == 1
    assert len([e for e in refresher.events if e.outcome == "refreshed"]) == 1


# ---------------------------------------------------------------------------
# Token values never appear in module output
# ---------------------------------------------------------------------------

def test_refresh_token_never_appears_in_module_output():
    clock = FakeClock()
    refresher = make_refresher(clock)
    tokens = token_set(clock, expires_in_seconds=60)
    refresher.store.put(PROVIDER, tokens)
    endpoint = FakeTokenEndpoint({
        "default": (400, {"error": "invalid_grant"}),
    })
    try:
        refresher.get_valid_token(PROVIDER, endpoint)
    except TokenRefreshError as exc:
        error_text = str(exc)
    else:  # pragma: no cover - the endpoint above always fails
        error_text = ""
    outputs = [
        repr(tokens),
        repr(refresher.store),
        repr(refresher.config(PROVIDER)),
        repr(refresher.events),
        error_text,
        json.dumps([{"provider": e.provider, "outcome": e.outcome,
                     "reason": e.reason} for e in refresher.events]),
    ]
    for output in outputs:
        assert OLD_REFRESH not in output
        assert OLD_ACCESS not in output
        assert NEW_ACCESS not in output
    # The endpoint itself DID receive the refresh token: that is the
    # one place it belongs (the token endpoint), not a leak.
    assert endpoint.posts[0][1]["refresh_token"] == OLD_REFRESH


def test_token_set_repr_and_store_repr_hide_values():
    tokens = TokenSet(access_token="sekrit-access", refresh_token="sekrit-refresh",
                      expires_at=BASE_TIME + timedelta(seconds=3600))
    assert "sekrit-access" not in repr(tokens)
    assert "sekrit-refresh" not in repr(tokens)
    assert "present" in repr(tokens)
    store = TokenStore()
    store.put("xai", tokens)
    assert "sekrit-access" not in repr(store)
    assert "xai" in repr(store)


# ---------------------------------------------------------------------------
# Registry: the three named OAuth providers, simulated
# ---------------------------------------------------------------------------

def test_known_oauth_providers_register_as_simulated():
    from puspa.router.router import register_known_oauth_providers

    refresher = TokenRefresher(now_fn=FakeClock().now)
    names = register_known_oauth_providers(refresher)
    assert names == ["xai", "github_copilot", "openai_codex"]
    for name in names:
        config = refresher.config(name)
        assert config is not None
        assert config.simulated is True
    xai = refresher.config("xai")
    assert xai.token_url == "https://auth.x.ai/oauth2/token"
    assert xai.client_id is not None
    assert refresher.config("openai_codex").token_url is None


def test_router_wiring_get_valid_oauth_token_delegates():
    from puspa.router import router as router_module
    from puspa.router.router import get_valid_oauth_token, oauth_refresher

    # Reset the shared refresher to a fresh one for this test, then
    # restore it so other tests are unaffected.
    previous = router_module._oauth_refresher
    try:
        clock = FakeClock()
        fresh = TokenRefresher(now_fn=clock.now)
        router_module._oauth_refresher = fresh
        fresh.register(OAuthProviderConfig(
            provider=PROVIDER, token_url="https://auth.example.test/oauth2/token",
            client_id="fake-client-id", simulated=True))
        fresh.store.put(PROVIDER, token_set(clock, expires_in_seconds=60))
        endpoint = FakeTokenEndpoint()
        assert get_valid_oauth_token(PROVIDER, endpoint) == NEW_ACCESS
        assert oauth_refresher() is fresh
    finally:
        router_module._oauth_refresher = previous


# ---------------------------------------------------------------------------
# Format translation: OpenAI request -> Anthropic request
# ---------------------------------------------------------------------------

OPENAI_REQUEST = {
    "model": "gpt-4o-mini",
    "messages": [
        {"role": "system", "content": "You are Puspa, a helpful agent."},
        {"role": "user", "content": "What is the price of this phone?"},
        {"role": "assistant", "content": "Let me check.",
         "tool_calls": [
             {"id": "call_1", "type": "function",
              "function": {"name": "search_price",
                           "arguments": json.dumps({"query": "phone X"})}},
         ]},
        {"role": "tool", "tool_call_id": "call_1",
         "content": "Rp11.999.000 at Tokopedia"},
    ],
    "tools": [
        {"type": "function",
         "function": {"name": "search_price",
                      "description": "Search a price",
                      "parameters": {"type": "object",
                                     "properties": {"query": {"type": "string"}}}}},
    ],
    "tool_choice": "auto",
    "temperature": 0.2,
    "max_tokens": 1024,
    "stop": ["END"],
}


def test_openai_request_translates_to_anthropic_structure():
    result = translate_request(OPENAI_REQUEST, source=FORMAT_OPENAI,
                               target=FORMAT_ANTHROPIC)
    out = result.payload
    assert out["model"] == "gpt-4o-mini"
    assert out["system"] == "You are Puspa, a helpful agent."
    assert out["max_tokens"] == 1024
    assert out["temperature"] == 0.2
    assert out["stop_sequences"] == ["END"]
    assert out["tool_choice"] == {"type": "auto"}
    assert out["tools"] == [
        {"name": "search_price", "description": "Search a price",
         "input_schema": {"type": "object",
                          "properties": {"query": {"type": "string"}}}},
    ]
    messages = out["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == [
        {"type": "text", "text": "What is the price of this phone?"}]
    assistant_blocks = messages[1]["content"]
    assert assistant_blocks[0] == {"type": "text", "text": "Let me check."}
    assert assistant_blocks[1] == {
        "type": "tool_use", "id": "call_1", "name": "search_price",
        "input": {"query": "phone X"}}
    assert messages[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1",
         "content": "Rp11.999.000 at Tokopedia"}]
    assert result.report.dropped == []


def test_openai_request_without_max_tokens_defaults_and_notes_it():
    request = {"model": "gpt-4o-mini",
               "messages": [{"role": "user", "content": "hi"}]}
    result = translate_request(request, source=FORMAT_OPENAI,
                               target=FORMAT_ANTHROPIC)
    assert result.payload["max_tokens"] == 4096
    assert any("max_tokens defaulted" in note for note in result.report.notes)


def test_openai_image_url_becomes_anthropic_image_block():
    request = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64,AAAABBBB"}},
                {"type": "image_url",
                 "image_url": {"url": "https://example.test/photo.jpg"}},
            ]},
        ],
        "max_tokens": 100,
    }
    result = translate_request(request, source=FORMAT_OPENAI,
                               target=FORMAT_ANTHROPIC)
    blocks = result.payload["messages"][0]["content"]
    assert blocks[1] == {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": "AAAABBBB"}}
    assert blocks[2] == {"type": "image", "source": {
        "type": "url", "url": "https://example.test/photo.jpg"}}
    assert result.report.dropped == []


# ---------------------------------------------------------------------------
# Format translation: Anthropic request -> OpenAI request
# ---------------------------------------------------------------------------

ANTHROPIC_REQUEST = {
    "model": "claude-haiku-4-5",
    "system": "You are Puspa, a helpful agent.",
    "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "Check the price."},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": "CCCCDDDD"}},
        ]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "toolu_1", "name": "search_price",
             "input": {"query": "phone X"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": "Rp11.999.000"},
        ]},
    ],
    "tools": [
        {"name": "search_price", "description": "Search a price",
         "input_schema": {"type": "object",
                          "properties": {"query": {"type": "string"}}}},
    ],
    "tool_choice": {"type": "auto"},
    "temperature": 0.2,
    "max_tokens": 1024,
    "stop_sequences": ["END"],
}


def test_anthropic_request_translates_to_openai_structure():
    result = translate_request(ANTHROPIC_REQUEST, source=FORMAT_ANTHROPIC,
                               target=FORMAT_OPENAI)
    out = result.payload
    assert out["model"] == "claude-haiku-4-5"
    assert out["messages"][0] == {
        "role": "system", "content": "You are Puspa, a helpful agent."}
    user_parts = out["messages"][1]["content"]
    assert user_parts[0] == {"type": "text", "text": "Check the price."}
    assert user_parts[1] == {"type": "image_url", "image_url": {
        "url": "data:image/jpeg;base64,CCCCDDDD"}}
    assistant = out["messages"][2]
    assert assistant["content"] == "Checking."
    assert assistant["tool_calls"] == [
        {"id": "toolu_1", "type": "function",
         "function": {"name": "search_price",
                      "arguments": json.dumps({"query": "phone X"})}}]
    assert out["messages"][3] == {
        "role": "tool", "tool_call_id": "toolu_1",
        "content": "Rp11.999.000"}
    assert out["tools"] == [
        {"type": "function",
         "function": {"name": "search_price", "description": "Search a price",
                      "parameters": {"type": "object",
                                     "properties": {"query": {"type": "string"}}}}}]
    assert out["tool_choice"] == "auto"
    assert out["stop"] == ["END"]
    assert out["max_tokens"] == 1024
    assert result.report.dropped == []


def test_anthropic_thinking_block_is_reported_not_silently_dropped():
    request = {
        "model": "claude-haiku-4-5",
        "messages": [
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "Let me think.",
                 "signature": "sig"},
                {"type": "text", "text": "Answer."},
            ]},
        ],
        "max_tokens": 100,
    }
    result = translate_request(request, source=FORMAT_ANTHROPIC,
                               target=FORMAT_OPENAI)
    assert result.payload["messages"][0]["content"] == "Answer."
    assert "messages[0].content[0].type='thinking'" in result.report.dropped_fields


# ---------------------------------------------------------------------------
# Round-trips: fields with an equivalent survive both ways
# ---------------------------------------------------------------------------

def test_request_round_trip_openai_to_anthropic_and_back():
    first = translate_request(OPENAI_REQUEST, source=FORMAT_OPENAI,
                              target=FORMAT_ANTHROPIC)
    assert first.report.dropped == []
    back = translate_request(first.payload, source=FORMAT_ANTHROPIC,
                             target=FORMAT_OPENAI)
    out = back.payload
    assert out["model"] == OPENAI_REQUEST["model"]
    assert out["temperature"] == 0.2
    assert out["max_tokens"] == 1024
    assert out["stop"] == ["END"]
    assert out["tool_choice"] == "auto"
    assert out["messages"][0] == OPENAI_REQUEST["messages"][0]
    assert out["messages"][1]["content"][0]["text"] == \
        "What is the price of this phone?"
    assistant = out["messages"][2]
    assert assistant["content"] == "Let me check."
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == \
        {"query": "phone X"}
    assert out["messages"][3] == OPENAI_REQUEST["messages"][3]
    assert out["tools"] == OPENAI_REQUEST["tools"]
    assert back.report.dropped == []


def test_request_round_trip_anthropic_to_openai_and_back():
    first = translate_request(ANTHROPIC_REQUEST, source=FORMAT_ANTHROPIC,
                              target=FORMAT_OPENAI)
    assert first.report.dropped == []
    back = translate_request(first.payload, source=FORMAT_OPENAI,
                             target=FORMAT_ANTHROPIC)
    out = back.payload
    assert out["model"] == ANTHROPIC_REQUEST["model"]
    assert out["system"] == ANTHROPIC_REQUEST["system"]
    assert out["temperature"] == 0.2
    assert out["max_tokens"] == 1024
    assert out["stop_sequences"] == ["END"]
    assert out["tool_choice"] == {"type": "auto"}
    assert out["tools"] == ANTHROPIC_REQUEST["tools"]
    user_blocks = out["messages"][0]["content"]
    assert user_blocks[0] == {"type": "text", "text": "Check the price."}
    assert user_blocks[1]["source"] == {
        "type": "base64", "media_type": "image/jpeg", "data": "CCCCDDDD"}
    assistant_blocks = out["messages"][1]["content"]
    assert assistant_blocks[1] == {
        "type": "tool_use", "id": "toolu_1", "name": "search_price",
        "input": {"query": "phone X"}}
    assert out["messages"][2]["content"][0] == {
        "type": "tool_result", "tool_use_id": "toolu_1",
        "content": "Rp11.999.000"}
    assert back.report.dropped == []


def test_openai_fields_without_anthropic_equivalent_are_reported():
    request = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 100,
        "temperature": 0.5,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.1,
        "logit_bias": {"123": -100},
        "response_format": {"type": "json_object"},
        "n": 2,
        "seed": 42,
    }
    result = translate_request(request, source=FORMAT_OPENAI,
                               target=FORMAT_ANTHROPIC)
    dropped = result.report.dropped_fields
    for field_name in ("frequency_penalty", "presence_penalty", "logit_bias",
                       "response_format", "n", "seed"):
        assert field_name in dropped
    # None of them leaked into the Anthropic payload.
    for field_name in ("frequency_penalty", "presence_penalty", "logit_bias",
                       "response_format", "n", "seed"):
        assert field_name not in result.payload
    assert result.payload["temperature"] == 0.5


def test_anthropic_cache_control_is_reported_not_silently_dropped():
    request = {
        "model": "claude-haiku-4-5",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "hi",
                 "cache_control": {"type": "ephemeral"}},
            ]},
        ],
        "max_tokens": 100,
    }
    result = translate_request(request, source=FORMAT_ANTHROPIC,
                               target=FORMAT_OPENAI)
    assert any("cache_control" in field_name
               for field_name in result.report.dropped_fields)
    assert result.payload["messages"][0]["content"][0]["text"] == "hi"


# ---------------------------------------------------------------------------
# Responses: both directions
# ---------------------------------------------------------------------------

OPENAI_RESPONSE = {
    "id": "chatcmpl-123",
    "object": "chat.completion",
    "created": 1793000000,
    "model": "gpt-4o-mini",
    "choices": [
        {"index": 0,
         "message": {
             "role": "assistant",
             "content": "The price is Rp11.999.000.",
             "tool_calls": [
                 {"id": "call_9", "type": "function",
                  "function": {"name": "search_price",
                               "arguments": json.dumps({"query": "phone X"})}},
             ],
         },
         "finish_reason": "tool_calls"},
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 30,
              "total_tokens": 150,
              "prompt_tokens_details": {"cached_tokens": 40}},
}


def test_openai_response_translates_to_anthropic():
    result = translate_response(OPENAI_RESPONSE, source=FORMAT_OPENAI,
                                target=FORMAT_ANTHROPIC)
    out = result.payload
    assert out["type"] == "message"
    assert out["role"] == "assistant"
    assert out["id"] == "chatcmpl-123"
    assert out["model"] == "gpt-4o-mini"
    assert out["content"][0] == {"type": "text",
                                 "text": "The price is Rp11.999.000."}
    assert out["content"][1] == {
        "type": "tool_use", "id": "call_9", "name": "search_price",
        "input": {"query": "phone X"}}
    assert out["stop_reason"] == "tool_use"
    assert out["usage"] == {"input_tokens": 120, "output_tokens": 30,
                            "cache_read_input_tokens": 40}
    # OpenAI-only fields are reported, not silently dropped.
    assert "created" in result.report.dropped_fields
    assert "object" in result.report.dropped_fields


def test_openai_response_extra_choices_are_reported():
    response = dict(OPENAI_RESPONSE)
    response["choices"] = list(OPENAI_RESPONSE["choices"]) + [
        {"index": 1,
         "message": {"role": "assistant", "content": "Second choice."},
         "finish_reason": "stop"},
    ]
    result = translate_response(response, source=FORMAT_OPENAI,
                                target=FORMAT_ANTHROPIC)
    assert result.payload["content"][0]["text"] == "The price is Rp11.999.000."
    assert "choices[1:2]" in result.report.dropped_fields


ANTHROPIC_RESPONSE = {
    "id": "msg_123",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5",
    "content": [
        {"type": "text", "text": "The price is Rp11.999.000."},
        {"type": "tool_use", "id": "toolu_9", "name": "search_price",
         "input": {"query": "phone X"}},
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 120, "output_tokens": 30,
              "cache_creation_input_tokens": 50,
              "cache_read_input_tokens": 40},
}


def test_anthropic_response_translates_to_openai():
    result = translate_response(ANTHROPIC_RESPONSE, source=FORMAT_ANTHROPIC,
                                target=FORMAT_OPENAI)
    out = result.payload
    assert out["object"] == "chat.completion"
    assert out["id"] == "msg_123"
    assert out["model"] == "claude-haiku-4-5"
    choice = out["choices"][0]
    assert choice["message"]["content"] == "The price is Rp11.999.000."
    assert choice["message"]["tool_calls"] == [
        {"id": "toolu_9", "type": "function",
         "function": {"name": "search_price",
                      "arguments": json.dumps({"query": "phone X"})}}]
    assert choice["finish_reason"] == "tool_calls"
    assert out["usage"]["prompt_tokens"] == 120
    assert out["usage"]["completion_tokens"] == 30
    assert out["usage"]["total_tokens"] == 150
    assert out["usage"]["prompt_tokens_details"] == {"cached_tokens": 40}
    # Cache-creation tokens have no OpenAI home: reported.
    assert "usage.cache_creation_input_tokens" in result.report.dropped_fields


def test_response_round_trip_openai_to_anthropic_and_back():
    first = translate_response(OPENAI_RESPONSE, source=FORMAT_OPENAI,
                               target=FORMAT_ANTHROPIC)
    back = translate_response(first.payload, source=FORMAT_ANTHROPIC,
                              target=FORMAT_OPENAI)
    out = back.payload
    choice = out["choices"][0]
    assert choice["message"]["content"] == "The price is Rp11.999.000."
    assert json.loads(choice["message"]["tool_calls"][0]["function"]["arguments"]) == \
        {"query": "phone X"}
    assert choice["finish_reason"] == "tool_calls"
    assert out["usage"]["prompt_tokens"] == 120
    assert out["usage"]["completion_tokens"] == 30
    assert out["usage"]["prompt_tokens_details"] == {"cached_tokens": 40}


def test_response_round_trip_anthropic_to_openai_and_back():
    first = translate_response(ANTHROPIC_RESPONSE, source=FORMAT_ANTHROPIC,
                               target=FORMAT_OPENAI)
    back = translate_response(first.payload, source=FORMAT_OPENAI,
                              target=FORMAT_ANTHROPIC)
    out = back.payload
    assert out["content"][0] == {"type": "text",
                                 "text": "The price is Rp11.999.000."}
    assert out["content"][1] == {
        "type": "tool_use", "id": "toolu_9", "name": "search_price",
        "input": {"query": "phone X"}}
    assert out["stop_reason"] == "tool_use"
    assert out["usage"]["input_tokens"] == 120
    assert out["usage"]["output_tokens"] == 30
    assert out["usage"]["cache_read_input_tokens"] == 40


def test_finish_reason_mapping_table():
    for openai_reason, anthropic_reason in (
            ("stop", "end_turn"), ("tool_calls", "tool_use"),
            ("length", "max_tokens")):
        response = {"model": "gpt-4o-mini",
                    "choices": [{"message": {"role": "assistant",
                                             "content": "x"},
                                 "finish_reason": openai_reason}]}
        result = translate_response(response, source=FORMAT_OPENAI,
                                    target=FORMAT_ANTHROPIC)
        assert result.payload["stop_reason"] == anthropic_reason
    for anthropic_reason, openai_reason in (
            ("end_turn", "stop"), ("tool_use", "tool_calls"),
            ("max_tokens", "length"), ("stop_sequence", "stop")):
        response = {"model": "claude-haiku-4-5",
                    "content": [{"type": "text", "text": "x"}],
                    "stop_reason": anthropic_reason}
        result = translate_response(response, source=FORMAT_ANTHROPIC,
                                    target=FORMAT_OPENAI)
        assert result.payload["choices"][0]["finish_reason"] == openai_reason


def test_translate_rejects_unknown_formats_and_bad_payloads():
    with pytest.raises(ValueError):
        translate_request({}, source="azure", target=FORMAT_ANTHROPIC)
    with pytest.raises(ValueError):
        translate_request({}, source=FORMAT_OPENAI, target=FORMAT_OPENAI)
    with pytest.raises(ValueError):
        translate_request("not-a-dict", source=FORMAT_OPENAI,
                          target=FORMAT_ANTHROPIC)
    with pytest.raises(ValueError):
        translate_response({"choices": []}, source=FORMAT_OPENAI,
                           target=FORMAT_ANTHROPIC)


def test_router_wiring_translate_delegates():
    from puspa.router.router import (
        translate_chat_request,
        translate_chat_response,
    )

    result = translate_chat_request(
        {"model": "gpt-4o-mini",
         "messages": [{"role": "user", "content": "hi"}],
         "max_tokens": 10},
        source=FORMAT_OPENAI, target=FORMAT_ANTHROPIC)
    assert result.payload["messages"][0]["content"][0]["text"] == "hi"
    response = translate_chat_response(
        {"model": "gpt-4o-mini",
         "choices": [{"message": {"role": "assistant", "content": "hi"},
                      "finish_reason": "stop"}]},
        source=FORMAT_OPENAI, target=FORMAT_ANTHROPIC)
    assert response.payload["stop_reason"] == "end_turn"
