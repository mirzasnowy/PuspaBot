"""Versioned opaque capability handles (G25).

A handle is the only key to a child. It is serializable (so a caller
can store it and hand it back later) and opaque: it carries a
version, the child id, the process generation that minted it, and a
random capability token. The token is never stored in cleartext in
the lifecycle registry — only its SHA-256 is kept — and it is never
shown by ``repr``. A handle that fails to parse, names an unknown
child, or carries the wrong token is refused with
``UNKNOWN_HANDLE``; the caller learns nothing about which ids exist.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
from dataclasses import dataclass

from .errors import UnknownHandleError

__all__ = [
    "HANDLE_VERSION",
    "SubagentHandle",
    "mint_handle",
]

#: Handle format version. Bump when the serialized shape changes;
#: old versions are refused, never silently reinterpreted.
HANDLE_VERSION = 1


@dataclass(frozen=True)
class SubagentHandle:
    """A serializable capability for one child.

    ``token`` is the secret: whoever holds the serialized handle
    holds the capability. ``repr`` never shows it.
    """

    subagent_id: str
    generation: str
    token: str
    version: int = HANDLE_VERSION

    def __repr__(self) -> str:
        return (
            f"SubagentHandle(subagent_id={self.subagent_id!r}, "
            f"generation={self.generation!r}, version={self.version})"
        )

    def token_hash(self) -> str:
        """SHA-256 of the capability token (what the registry stores)."""
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()

    def serialize(self) -> str:
        """Render the handle as one URL-safe base64 string."""
        payload = {
            "v": self.version,
            "id": self.subagent_id,
            "gen": self.generation,
            "tok": self.token,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @classmethod
    def parse(cls, raw: str) -> SubagentHandle:
        """Parse a serialized handle, or raise ``UnknownHandleError``.

        Anything malformed — not a string, not base64, not JSON, a
        wrong version, a missing field — is refused the same way as
        a forged handle: no detail leaks about what was wrong.
        """
        if not isinstance(raw, str) or not raw:
            raise UnknownHandleError("handle is not a usable serialized handle")
        try:
            decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
            payload = json.loads(decoded.decode("utf-8"))
        except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
            raise UnknownHandleError("handle is not a usable serialized handle") from exc
        if not isinstance(payload, dict):
            raise UnknownHandleError("handle is not a usable serialized handle")
        if payload.get("v") != HANDLE_VERSION:
            raise UnknownHandleError("handle version is not supported")
        subagent_id = payload.get("id")
        generation = payload.get("gen")
        token = payload.get("tok")
        if not (
            isinstance(subagent_id, str)
            and subagent_id
            and isinstance(generation, str)
            and generation
            and isinstance(token, str)
            and token
        ):
            raise UnknownHandleError("handle is not a usable serialized handle")
        return cls(subagent_id=subagent_id, generation=generation, token=token)


def mint_handle(*, subagent_id: str, generation: str) -> SubagentHandle:
    """Mint a fresh handle for a child in this process generation."""
    return SubagentHandle(
        subagent_id=subagent_id,
        generation=generation,
        token=secrets.token_urlsafe(32),
    )
