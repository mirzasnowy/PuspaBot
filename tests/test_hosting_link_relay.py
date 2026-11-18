"""Tests: link codes + rate limits, relay frames + hub, fallback order.

All in-memory with a fake clock; no database, no network.
"""

import pytest

from puspa.hosting.errors import (
    AllRoutesFailedError,
    LinkError,
    RateLimitError,
    RelayError,
)
from puspa.hosting.fallback import (
    FALLBACK_ORDER,
    connect_with_fallback,
    pick_route,
)
from puspa.hosting.link import (
    LINK_CODE_ALPHABET,
    LINK_CODE_LENGTH,
    LINK_CODE_TTL_SECONDS,
    LinkService,
    generate_link_code,
)
from puspa.hosting.relay import (
    MAX_FRAME_BYTES,
    MAX_QUEUED_FRAMES_PER_CODE,
    RelayHub,
    open_frame,
    seal_frame,
)


class FakeClock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# --- link codes ---


def test_link_code_shape_and_alphabet():
    for _ in range(200):
        code = generate_link_code()
        assert len(code) == LINK_CODE_LENGTH
        assert all(char in LINK_CODE_ALPHABET for char in code)
        assert not set(code) & set("lo01")  # no ambiguous characters


def test_link_codes_are_unique_in_practice():
    assert len({generate_link_code() for _ in range(500)}) == 500


def test_create_and_redeem_link():
    clock = FakeClock()
    service = LinkService(clock=clock)
    created = service.create_link("user-1", address="10.0.0.1")
    assert len(created["relay_key"]) == 32
    assert created["expires_at"] == clock.now + LINK_CODE_TTL_SECONDS
    assert service.is_live(created["code"]) is True
    redeemed = service.redeem(created["code"], address="10.0.0.2")
    assert redeemed["user_id"] == "user-1"
    assert redeemed["relay_key"] == created["relay_key"]
    assert service.is_live(created["code"]) is False


def test_redeem_is_single_use():
    service = LinkService(clock=FakeClock())
    code = service.create_link("user-1", address="10.0.0.1")["code"]
    service.redeem(code, address="10.0.0.1")
    with pytest.raises(LinkError):
        service.redeem(code, address="10.0.0.1")


def test_redeem_unknown_code_fails():
    service = LinkService(clock=FakeClock())
    with pytest.raises(LinkError):
        service.redeem("aaaaaaa", address="10.0.0.1")
    assert service.is_live("aaaaaaa") is False


def test_link_expires_after_ten_minutes():
    clock = FakeClock()
    service = LinkService(clock=clock)
    code = service.create_link("user-1", address="10.0.0.1")["code"]
    clock.advance(LINK_CODE_TTL_SECONDS + 1)
    assert service.is_live(code) is False
    with pytest.raises(LinkError):
        service.redeem(code, address="10.0.0.1")
    assert service.purge_expired() == 0  # is_live already dropped it


def test_link_rate_limit_per_address():
    clock = FakeClock()
    service = LinkService(clock=clock, per_address_limit=3, global_limit=1000)
    for _ in range(3):
        service.create_link("user-1", address="10.0.0.1")
    with pytest.raises(RateLimitError):
        service.create_link("user-1", address="10.0.0.1")
    # Another address still has its own budget.
    service.create_link("user-1", address="10.0.0.2")
    # The window slides: one hour later the budget is back.
    clock.advance(3601)
    service.create_link("user-1", address="10.0.0.1")


def test_link_rate_limit_global():
    clock = FakeClock()
    service = LinkService(clock=clock, per_address_limit=1000, global_limit=5)
    for i in range(5):
        service.create_link("user-1", address=f"10.0.0.{i}")
    with pytest.raises(RateLimitError):
        service.create_link("user-1", address="10.0.1.99")


# --- relay frames ---


def test_relay_frame_roundtrip_with_aad():
    key = bytes(range(32))
    frame = seal_frame(key, b"hello relay", aad=b"link:abc1234")
    assert open_frame(key, frame, aad=b"link:abc1234") == b"hello relay"


def test_relay_frame_wrong_key_fails():
    frame = seal_frame(bytes(range(32)), b"secret")
    with pytest.raises(RelayError):
        open_frame(bytes(range(1, 33)), frame)


def test_relay_frame_tampered_fails():
    key = bytes(range(32))
    frame = bytearray(seal_frame(key, b"secret"))
    frame[-1] ^= 1
    with pytest.raises(RelayError):
        open_frame(key, bytes(frame))


def test_relay_frame_wrong_aad_fails():
    key = bytes(range(32))
    frame = seal_frame(key, b"secret", aad=b"link:one")
    with pytest.raises(RelayError):
        open_frame(key, frame, aad=b"link:two")


def test_relay_frame_rejects_bad_key_short_frame_and_oversize():
    with pytest.raises(RelayError):
        seal_frame(b"short", b"data")
    with pytest.raises(RelayError):
        open_frame(bytes(range(32)), b"\x00" * 10)
    with pytest.raises(RelayError):
        seal_frame(bytes(range(32)), b"x" * (MAX_FRAME_BYTES + 1))


# --- relay hub ---


def test_hub_forwards_opaque_frames_fifo_and_cannot_read_them():
    hub = RelayHub()
    key = bytes(range(32))
    frames = [seal_frame(key, f"message {i}".encode()) for i in range(3)]
    for frame in frames:
        hub.publish("abc1234", frame)
    assert hub.queued_count("abc1234") == 3
    collected = hub.collect("abc1234")
    assert collected == frames  # FIFO, byte-identical
    assert hub.queued_count("abc1234") == 0
    assert hub.collect("abc1234") == []
    # A hub operator holding only the frame bytes cannot read them.
    wrong_key = bytes(range(1, 33))
    with pytest.raises(RelayError):
        open_frame(wrong_key, collected[0])
    # The real key still opens them after the round trip.
    assert open_frame(key, collected[1]) == b"message 1"


def test_hub_rejects_empty_and_oversized_publish():
    hub = RelayHub()
    with pytest.raises(RelayError):
        hub.publish("", b"frame")
    with pytest.raises(RelayError):
        hub.publish("abc1234", b"")
    with pytest.raises(RelayError):
        hub.publish("abc1234", b"x" * (MAX_FRAME_BYTES + 1))


def test_hub_queue_cap_refuses_instead_of_dropping():
    hub = RelayHub()
    for _ in range(MAX_QUEUED_FRAMES_PER_CODE):
        hub.publish("abc1234", b"f")
    with pytest.raises(RelayError):
        hub.publish("abc1234", b"f")
    assert hub.drop("abc1234") == MAX_QUEUED_FRAMES_PER_CODE
    assert hub.drop("abc1234") == 0


# --- fallback order ---


def test_fallback_order_is_direct_tunnel_relay():
    assert FALLBACK_ORDER == ("direct", "tunnel", "relay")


def test_pick_route_returns_first_available():
    assert pick_route({"direct": True, "tunnel": True, "relay": True}) == "direct"
    assert pick_route({"direct": False, "tunnel": True, "relay": True}) == "tunnel"
    assert pick_route({"direct": False, "tunnel": False, "relay": True}) == "relay"
    assert pick_route({"direct": False, "tunnel": False, "relay": False}) is None
    assert pick_route({}) is None


def test_connect_tries_in_order_and_returns_first_success():
    seen = []

    def direct():
        seen.append("direct")
        raise ConnectionError("no route to host")

    def tunnel():
        seen.append("tunnel")
        # falsy: treated as a failed attempt

    def relay():
        seen.append("relay")
        return "relay-connection"

    result = connect_with_fallback({"direct": direct, "tunnel": tunnel, "relay": relay})
    assert result.route == "relay"
    assert result.result == "relay-connection"
    assert seen == ["direct", "tunnel", "relay"]
    assert [attempt.route for attempt in result.attempts] == ["direct", "tunnel", "relay"]
    assert result.attempts[0].ok is False
    assert "ConnectionError" in result.attempts[0].error
    assert result.attempts[1].ok is False
    assert result.attempts[2].ok is True


def test_connect_stops_at_first_success():
    calls = []

    def direct():
        calls.append("direct")
        return "direct-connection"

    def relay():
        calls.append("relay")
        return "relay-connection"

    result = connect_with_fallback({"direct": direct, "relay": relay})
    assert result.route == "direct"
    assert calls == ["direct"]


def test_connect_all_routes_failed_carries_every_attempt():
    attempts_seen = []
    with pytest.raises(AllRoutesFailedError) as exc_info:
        connect_with_fallback(
            {
                "direct": lambda: (_ for _ in ()).throw(ConnectionError("down")),
                "tunnel": lambda: False,
                "relay": lambda: (_ for _ in ()).throw(TimeoutError("timed out")),
            },
            on_attempt=attempts_seen.append,
        )
    assert len(exc_info.value.attempts) == 3
    assert len(attempts_seen) == 3
    assert all(attempt.ok is False for attempt in exc_info.value.attempts)


def test_connect_missing_probe_counts_as_failed_attempt():
    with pytest.raises(AllRoutesFailedError) as exc_info:
        connect_with_fallback({})
    assert [attempt.error for attempt in exc_info.value.attempts] == ["no probe for route"] * 3
