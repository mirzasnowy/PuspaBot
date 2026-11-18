"""Relay frames: AES-256-GCM envelopes the relay only forwards.

Pattern (DuskBot ``apps/cloudflare/link-relay/``, rewritten as Puspa
code): when neither a direct connection nor the Cloudflare tunnel
works, app and server talk through a relay that forwards opaque
blobs. The relay NEVER holds the relay key (minted by
``LinkService.create_link`` and held only by app + server), so it
cannot read or alter a frame — any tampering fails the GCM tag
check on the receiving side.

Frame layout (raw bytes, no header struct to drift): 12-byte random
nonce || AES-256-GCM ciphertext (tag appended by the cipher, 16
bytes). The optional ``aad`` binds a frame to its context — callers
pass the link code hash or a sequence number there so a frame cannot
be replayed onto another link or reordered silently. Frames are
capped at 1 MiB; anything bigger is refused before encryption, so a
runaway sender cannot make the relay buffer unbounded data.

``RelayHub`` is the in-memory relay itself: per-code FIFO queues of
opaque frames. It holds no keys and never opens a frame — the test
suite proves a hub operator with only the frame bytes cannot read
them. A real networked relay (HTTP/WebSocket endpoint in front of
this hub) is a follow-up; the queueing, caps, and crypto are real
here, the transport is the seam.
"""

from __future__ import annotations

import secrets
from collections import deque
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import RelayError

__all__ = [
    "MAX_FRAME_BYTES",
    "MAX_QUEUED_FRAMES_PER_CODE",
    "RelayHub",
    "open_frame",
    "seal_frame",
]

NONCE_BYTES = 12
TAG_BYTES = 16
RELAY_KEY_BYTES = 32

MAX_FRAME_BYTES = 1_048_576  # 1 MiB per frame, nonce + ciphertext + tag
MAX_QUEUED_FRAMES_PER_CODE = 64


def _check_key(key: bytes) -> None:
    if not isinstance(key, (bytes, bytearray)) or len(key) != RELAY_KEY_BYTES:
        raise RelayError("relay key must be 32 bytes (AES-256)")


def seal_frame(key: bytes, plaintext: bytes, *, aad: bytes | None = None) -> bytes:
    """Seal plaintext into a relay frame (nonce || ciphertext || tag).

    Raises ``RelayError`` on a bad key or an oversized plaintext.
    """
    _check_key(key)
    if not isinstance(plaintext, (bytes, bytearray)):
        raise RelayError("relay frame plaintext must be bytes")
    if len(plaintext) > MAX_FRAME_BYTES:
        raise RelayError(f"relay frame exceeds {MAX_FRAME_BYTES} bytes")
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(bytes(key)).encrypt(nonce, bytes(plaintext), aad)
    return nonce + ciphertext


def open_frame(key: bytes, frame: bytes, *, aad: bytes | None = None) -> bytes:
    """Open a relay frame. Raises ``RelayError`` on a bad key, a
    truncated frame, an oversized frame, or a failed tag check.
    Never returns partial plaintext."""
    _check_key(key)
    if not isinstance(frame, (bytes, bytearray)):
        raise RelayError("relay frame must be bytes")
    frame = bytes(frame)
    if len(frame) > MAX_FRAME_BYTES:
        raise RelayError(f"relay frame exceeds {MAX_FRAME_BYTES} bytes")
    if len(frame) < NONCE_BYTES + TAG_BYTES:
        raise RelayError("relay frame too short to hold nonce + tag")
    nonce, ciphertext = frame[:NONCE_BYTES], frame[NONCE_BYTES:]
    try:
        return AESGCM(bytes(key)).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise RelayError("relay frame authentication failed (wrong key or tampered)") from exc
    except Exception as exc:  # malformed input to the cipher
        raise RelayError("relay frame could not be opened") from exc


@dataclass
class RelayHub:
    """In-memory relay: per-code FIFO queues of opaque frames.

    The hub never sees a key and never opens a frame. Queues are
    bounded (64 frames per code); a full queue refuses the publish
    instead of silently dropping a frame.
    """

    _queues: dict[str, deque] = field(default_factory=dict, init=False, repr=False)

    def publish(self, code: str, frame: bytes) -> int:
        """Queue one opaque frame under a link code. Returns the queue
        depth after the publish. Raises ``RelayError`` on a bad code,
        an oversized frame, or a full queue."""
        if not isinstance(code, str) or not code:
            raise RelayError("relay publish requires a link code")
        if not isinstance(frame, (bytes, bytearray)) or not frame:
            raise RelayError("relay publish requires a non-empty frame")
        if len(frame) > MAX_FRAME_BYTES:
            raise RelayError(f"relay frame exceeds {MAX_FRAME_BYTES} bytes")
        queue = self._queues.setdefault(code, deque())
        if len(queue) >= MAX_QUEUED_FRAMES_PER_CODE:
            raise RelayError(f"relay queue for this link is full ({MAX_QUEUED_FRAMES_PER_CODE})")
        queue.append(bytes(frame))
        return len(queue)

    def collect(self, code: str) -> list[bytes]:
        """Pop and return all queued frames for a code, oldest first.
        Returns an empty list when nothing is queued."""
        queue = self._queues.pop(code, None)
        return list(queue) if queue else []

    def queued_count(self, code: str) -> int:
        """Number of frames currently queued for a code."""
        queue = self._queues.get(code)
        return len(queue) if queue else 0

    def drop(self, code: str) -> int:
        """Drop a code's queue (link closed or expired). Returns the
        number of frames dropped."""
        queue = self._queues.pop(code, None)
        return len(queue) if queue else 0
