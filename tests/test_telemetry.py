"""Tests for anonymous usage telemetry, off by default (AST-09).

The switch: ``PUSPA_TELEMETRY`` env flag wins when set, else
the per-user Settings toggle, else off. The sender only posts
when switched on AND a collector endpoint is configured.

No test touches the network: the sender is driven with an
``httpx.MockTransport`` that records every request, so the
default-off proof is a recorded zero calls, not an absence of
evidence. The only endpoint used is an obviously fake
``.invalid`` host, and it is only ever reached through the
mock transport.
"""

import json
import math
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

import httpx
import pytest

from puspa.telemetry.client import Telemetry
from puspa.telemetry.config import (
    parse_endpoint,
    parse_env_flag,
    resolve_enabled,
)

FAKE_ENDPOINT = "https://telemetry.example.invalid/v1/events"


@pytest.fixture(autouse=True)
def _clean_telemetry_env(monkeypatch):
    monkeypatch.delenv("PUSPA_TELEMETRY", raising=False)
    monkeypatch.delenv("PUSPA_TELEMETRY_ENDPOINT", raising=False)


class RecordingTransport(httpx.MockTransport):
    """An httpx transport that records every request it sees."""

    def __init__(self, status: int = 200):
        self.calls: list[httpx.Request] = []
        self.status = status
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(self.status, json={"ok": True})


def _recording_client(status: int = 200) -> tuple[httpx.Client, RecordingTransport]:
    transport = RecordingTransport(status=status)
    return httpx.Client(transport=transport), transport


def test_default_is_off_and_nothing_is_sent():
    client, transport = _recording_client()
    telemetry = Telemetry(endpoint=FAKE_ENDPOINT, http_client=client)
    assert telemetry.enabled is False
    telemetry.record_event("chat_message_sent")
    telemetry.record_duration("turn_seconds", 1.5)
    assert telemetry.counters == {}
    assert telemetry.durations == {}
    assert telemetry.build_payload() is None
    assert telemetry.flush() is None
    assert transport.calls == []


def test_env_flag_switches_on_and_off():
    assert parse_env_flag(None) is None
    assert parse_env_flag("") is None
    assert parse_env_flag("  ") is None
    for raw in ("1", "true", "TRUE", "yes", "on", " On "):
        assert parse_env_flag(raw) is True, raw
    for raw in ("0", "false", "FALSE", "no", "off"):
        assert parse_env_flag(raw) is False, raw
    with pytest.raises(ValueError, match="PUSPA_TELEMETRY"):
        parse_env_flag("2")
    with pytest.raises(ValueError, match="PUSPA_TELEMETRY"):
        parse_env_flag("maybe")


def test_telemetry_reads_env_flag_at_construction(monkeypatch):
    monkeypatch.setenv("PUSPA_TELEMETRY", "1")
    assert Telemetry(endpoint=FAKE_ENDPOINT).enabled is True
    monkeypatch.setenv("PUSPA_TELEMETRY", "0")
    assert Telemetry(endpoint=FAKE_ENDPOINT).enabled is False


def test_env_flag_beats_the_stored_toggle():
    assert resolve_enabled(env=True, stored=False) is True
    assert resolve_enabled(env=False, stored=True) is False
    assert resolve_enabled(env=None, stored=True) is True
    assert resolve_enabled(env=None, stored=False) is False
    assert resolve_enabled() is False


def test_opt_out_at_runtime_drops_buffered_data():
    client, transport = _recording_client()
    telemetry = Telemetry(enabled=True, endpoint=FAKE_ENDPOINT, http_client=client)
    telemetry.record_event("chat_message_sent", count=3)
    assert telemetry.counters == {"chat_message_sent": 3}
    telemetry.disable()
    assert telemetry.enabled is False
    assert telemetry.counters == {}
    assert telemetry.flush() is None
    assert transport.calls == []
    telemetry.enable()
    telemetry.record_event("chat_message_sent")
    assert telemetry.flush() is not None
    assert len(transport.calls) == 1


def test_flush_sends_aggregates_only_when_on():
    client, transport = _recording_client()
    telemetry = Telemetry(enabled=True, endpoint=FAKE_ENDPOINT, http_client=client)
    telemetry.record_event("chat_message_sent", count=2)
    telemetry.record_event("voice_message_sent")
    telemetry.record_duration("turn_seconds", 1.5)
    telemetry.record_duration("turn_seconds", 2.5)
    sent = telemetry.flush()
    assert sent is not None
    assert len(transport.calls) == 1
    request = transport.calls[0]
    assert request.method == "POST"
    assert str(request.url) == FAKE_ENDPOINT
    body = json.loads(request.content.decode())
    assert body == sent
    assert set(body) == {
        "puspa_version",
        "python_version",
        "platform",
        "counters",
        "durations",
    }
    # The client reports the installed package version, so the test
    # reads it from the same source instead of hardcoding it: the
    # literal went stale on the 0.1.1 bump and would again at 0.1.2.
    try:
        expected_version = _package_version("puspa")
    except PackageNotFoundError:
        expected_version = "0.1.0"
    assert body["puspa_version"] == expected_version
    assert body["counters"] == {"chat_message_sent": 2, "voice_message_sent": 1}
    assert body["durations"] == {"turn_seconds": {"count": 2, "total_seconds": 4.0}}
    # Buffers are cleared: a second flush sends nothing.
    assert telemetry.counters == {}
    assert telemetry.durations == {}
    assert telemetry.flush() is None
    assert len(transport.calls) == 1


def test_payload_never_carries_identifiers_content_or_paths():
    client, _transport = _recording_client()
    telemetry = Telemetry(enabled=True, endpoint=FAKE_ENDPOINT, http_client=client)
    telemetry.record_event("chat_message_sent")
    telemetry.record_duration("turn_seconds", 0.5)
    sent = telemetry.flush()
    assert sent is not None
    dumped = json.dumps(sent, sort_keys=True)
    for forbidden in (
        "user-123",
        "sk-secret-token",
        "halo, ini isi pesan rahasia",
        "/home/nero/dokumen/rahasia.txt",
        "nero-laptop",
        "192.168.1.7",
    ):
        assert forbidden not in dumped


def test_enabled_but_no_endpoint_sends_nothing():
    client, transport = _recording_client()
    telemetry = Telemetry(enabled=True, endpoint=None, http_client=client)
    assert telemetry.endpoint is None
    telemetry.record_event("chat_message_sent")
    assert telemetry.flush() is None
    assert transport.calls == []
    # The data stays buffered (nothing was sent anywhere), so a
    # later flush with an endpoint would still send it.
    assert telemetry.counters == {"chat_message_sent": 1}


def test_empty_when_enabled_sends_no_beacon():
    client, transport = _recording_client()
    telemetry = Telemetry(enabled=True, endpoint=FAKE_ENDPOINT, http_client=client)
    assert telemetry.build_payload() is None
    assert telemetry.flush() is None
    assert transport.calls == []


def test_failed_send_is_swallowed_and_dropped():
    client, transport = _recording_client(status=500)
    telemetry = Telemetry(enabled=True, endpoint=FAKE_ENDPOINT, http_client=client)
    telemetry.record_event("chat_message_sent")
    assert telemetry.flush() is None
    assert len(transport.calls) == 1
    assert telemetry.counters == {}


def test_event_names_must_be_snake_case():
    telemetry = Telemetry(enabled=True, endpoint=FAKE_ENDPOINT)
    for bad in ("UserId", "chat.send", "chat send", "", "1st_event", "chat-message"):
        with pytest.raises(ValueError, match="snake_case"):
            telemetry.record_event(bad)
        with pytest.raises(ValueError, match="snake_case"):
            telemetry.record_duration(bad, 1.0)
    with pytest.raises(ValueError, match="count must be positive"):
        telemetry.record_event("chat_message_sent", count=0)


def test_durations_must_be_finite_and_non_negative():
    telemetry = Telemetry(enabled=True, endpoint=FAKE_ENDPOINT)
    for bad in (-1.0, math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="finite non-negative"):
            telemetry.record_duration("turn_seconds", bad)
    telemetry.record_duration("turn_seconds", 0.0)
    assert telemetry.durations == {"turn_seconds": {"count": 1, "total_seconds": 0.0}}


def test_endpoint_must_be_http_without_credentials():
    assert parse_endpoint(None) is None
    assert parse_endpoint("") is None
    assert parse_endpoint("https://collector.example.invalid/v1/events") == (
        "https://collector.example.invalid/v1/events"
    )
    for bad in (
        "ftp://collector.example.invalid/v1",
        "collector.example.invalid/v1",
        "https://",
        "https://user:pass@collector.example.invalid/v1",
    ):
        with pytest.raises(ValueError, match="telemetry endpoint"):
            parse_endpoint(bad)
