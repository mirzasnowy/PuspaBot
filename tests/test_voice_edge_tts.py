"""Tests for the Edge TTS client (V2-30).

The Microsoft endpoint is replaced by a fake WebSocket peer:
no real network, no real credentials. The tests assert the
client sends the config + SSML messages, concatenates the
binary audio frames, and raises EdgeTTSError (never a partial
file) when the peer fails.
"""

import asyncio

import pytest

from puspa.voice.edge_tts import (
    DEFAULT_EDGE_VOICE,
    EDGE_TTS_VOICES,
    EdgeTTSClient,
    EdgeTTSError,
    build_ssml,
)


class FakeWS:
    """A fake WebSocket peer: records sends, replays queued frames."""

    def __init__(self, frames, *, fail_on_recv=None):
        self.frames = list(frames)
        self.sent: list[str] = []
        self.fail_on_recv = fail_on_recv
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True
        return False

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        if self.fail_on_recv is not None:
            raise self.fail_on_recv
        if not self.frames:
            raise ConnectionError("peer closed with no turn.end")
        return self.frames.pop(0)


def _audio_frame(payload: bytes) -> bytes:
    return b"Path:audio\r\nContent-Type:audio/mpeg\r\n\r\n" + payload


def _connect_with(peer):
    def connect(url, headers):
        connect.seen = {"url": url, "headers": headers}
        return peer

    return connect


def test_synthesize_concatenates_audio_frames():
    peer = FakeWS(
        [
            "X-Timestamp:0\r\nPath:turn.start\r\n\r\n",
            _audio_frame(b"MP3PART1"),
            _audio_frame(b"MP3PART2"),
            "X-Timestamp:0\r\nPath:turn.end\r\n\r\n",
        ]
    )
    connect = _connect_with(peer)
    client = EdgeTTSClient(connect=connect)
    audio = asyncio.run(client.synthesize("Halo, apa kabar?", "id-ID-GadisNeural"))
    assert audio == b"MP3PART1MP3PART2"
    assert "speech.platform.bing.com" in connect.seen["url"]
    assert "TrustedClientToken=" in connect.seen["url"]
    assert len(peer.sent) == 2
    assert "Path:speech.config" in peer.sent[0]
    assert "audio-24khz-48kbitrate-mono-mp3" in peer.sent[0]
    assert "Path:ssml" in peer.sent[1]
    assert 'name="id-ID-GadisNeural"' in peer.sent[1]
    assert "Halo, apa kabar?" in peer.sent[1]


def test_synthesize_escapes_text_in_ssml():
    ssml = build_ssml("harga < Rp100 & gula", "id-ID-ArdiNeural")
    assert "&lt;" in ssml and "&amp;" in ssml
    assert "< Rp100" not in ssml
    assert 'name="id-ID-ArdiNeural"' in ssml
    assert 'xml:lang="id-ID"' in ssml


def test_peer_closing_without_audio_is_an_edge_error():
    peer = FakeWS(
        ["X-Timestamp:0\r\nPath:turn.start\r\n\r\n"], fail_on_recv=ConnectionError("closed")
    )
    client = EdgeTTSClient(connect=_connect_with(peer))
    with pytest.raises(EdgeTTSError):
        asyncio.run(client.synthesize("halo", "id-ID-GadisNeural"))


def test_turn_end_without_audio_is_an_edge_error():
    peer = FakeWS(["X-Timestamp:0\r\nPath:turn.end\r\n\r\n"])
    client = EdgeTTSClient(connect=_connect_with(peer))
    with pytest.raises(EdgeTTSError, match="without audio"):
        asyncio.run(client.synthesize("halo", "id-ID-GadisNeural"))


def test_unknown_voice_is_refused_before_connecting():
    peer = FakeWS([])
    client = EdgeTTSClient(connect=_connect_with(peer))
    with pytest.raises(EdgeTTSError, match="unknown Edge voice"):
        asyncio.run(client.synthesize("halo", "not-a-voice"))
    assert peer.sent == []


def test_empty_text_is_refused():
    peer = FakeWS([])
    client = EdgeTTSClient(connect=_connect_with(peer))
    with pytest.raises(EdgeTTSError, match="nothing to speak"):
        asyncio.run(client.synthesize("   ", "id-ID-GadisNeural"))


def test_indonesian_voices_are_offered_and_default_is_indonesian():
    names = [name for name, _, _ in EDGE_TTS_VOICES]
    assert "id-ID-GadisNeural" in names
    assert "id-ID-ArdiNeural" in names
    assert DEFAULT_EDGE_VOICE == "id-ID-GadisNeural"
