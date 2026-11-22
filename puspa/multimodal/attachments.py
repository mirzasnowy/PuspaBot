"""Attachments on chat messages (COW-19).

A chat message can carry attachments — an image, a voice note,
or a file — instead of text alone. An attachment is one
:class:`Attachment`: its kind, mime type, size, and where the
bytes live. The bytes live in exactly one of three places:

- ``inline`` — base64 in memory, for images small enough to
  ride with the message (this is also the shape the turn
  pipeline's image blocks already use, so an inline image needs
  no conversion to reach a vision model).
- ``file`` — a file under the store's per-user directory, under
  a random name the uploader never chose, for audio and files.
- ``url`` — a remote URL the provider returned (a generated
  image served by the provider), so nothing is downloaded
  twice.

:class:`AttachmentStore` owns the index (in memory, like the
chat store itself — attachments do not survive a restart, and
neither do the messages they ride on) and the files on disk.
Callers never build a path themselves: :meth:`store_bytes`
validates and writes, :meth:`adopt_file` registers a file
another module already wrote (a kept voice recording), and
:meth:`read_bytes` is the only way the bytes come back out.
The public dict (:meth:`to_public_dict`) never carries bytes
or a filesystem path — only the download URL
(``/api/chat/attachments/{id}``), which checks the owner.

Limits are loud, not silent: a refused mime type, an empty
upload, or an oversize file raises :class:`AttachmentError`
and nothing is stored. Video is refused: it is large, and
nothing in the app reads video yet — the same rule the share
target already uses.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from puspa.voice.pipeline import ALLOWED_AUDIO_MIME_TYPES

__all__ = [
    "ALLOWED_FILE_MIME_TYPES",
    "ALLOWED_IMAGE_MIME_TYPES",
    "MAX_ATTACHMENT_BYTES",
    "MAX_INLINE_BYTES",
    "Attachment",
    "AttachmentError",
    "AttachmentStore",
    "attachment_kind_for_mime",
]

#: Largest attachment of any kind (10 MiB — the same ceiling the
#: voice pipeline and the share inbox already use).
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

#: Images at or below this size ride inline (base64) with the
#: message; larger images are stored as files instead, so one
#: photo cannot bloat the in-memory chat store.
MAX_INLINE_BYTES = 2 * 1024 * 1024

#: Image types phone cameras and browsers produce.
ALLOWED_IMAGE_MIME_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    }
)

#: File types something in the app can actually read: a PDF
#: (the knowledge base), or plain text the turn can inline.
ALLOWED_FILE_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "text/plain",
        "text/csv",
        "text/markdown",
    }
)

#: Mime types whose content the turn inlines as text (see
#: :mod:`puspa.multimodal.turns`); anything else is only named.
TEXT_FILE_MIME_TYPES = frozenset({"text/plain", "text/csv", "text/markdown"})

#: Extension per mime type, for files the store writes.
_MIME_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/aac": ".aac",
    "audio/3gpp": ".3gp",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/markdown": ".md",
}

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class AttachmentError(ValueError):
    """An attachment was refused (type, size, or empty)."""


def attachment_kind_for_mime(mime_type: str) -> str:
    """The attachment kind for a mime type (``"image"`` / ``"audio"`` / ``"file"``)."""
    if mime_type in ALLOWED_IMAGE_MIME_TYPES:
        return "image"
    if mime_type in ALLOWED_AUDIO_MIME_TYPES:
        return "audio"
    if mime_type in ALLOWED_FILE_MIME_TYPES:
        return "file"
    raise AttachmentError(
        f"unsupported attachment type {mime_type!r}; accepted: "
        f"{', '.join(sorted(ALLOWED_IMAGE_MIME_TYPES | ALLOWED_AUDIO_MIME_TYPES | ALLOWED_FILE_MIME_TYPES))}"
    )


def _safe_filename(filename: str | None, mime_type: str) -> str:
    """A display filename: the uploader's name, cleaned, or a generated one."""
    cleaned = _FILENAME_SAFE.sub("_", (filename or "").strip())[:80].strip("._") or ""
    if cleaned:
        return cleaned
    return f"attachment{_MIME_SUFFIX.get(mime_type, '')}"


@dataclass(frozen=True)
class Attachment:
    """One attachment on a message.

    ``storage`` is exactly one of ``{"type": "inline", "data":
    <base64>}``, ``{"type": "file", "path": <str>}``, or
    ``{"type": "url", "url": <str>}``. ``transcript`` is set
    only on a voice note that was transcribed. ``user_id`` is
    the owner: a download is served only to them.
    """

    id: str
    user_id: str
    kind: str
    mime_type: str
    size_bytes: int
    filename: str | None
    storage: dict
    transcript: str | None = None

    def to_public_dict(self) -> dict:
        """The attachment as the API and the chat page see it.

        No bytes, no filesystem path: only the download URL
        (plus the provider URL itself for a ``url`` attachment,
        which is already a public location).
        """
        public: dict = {
            "id": self.id,
            "kind": self.kind,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "filename": self.filename,
            "download_url": f"/api/chat/attachments/{self.id}",
        }
        if self.transcript:
            public["transcript"] = self.transcript
        if self.storage.get("type") == "url":
            public["url"] = self.storage.get("url")
        return public

    @classmethod
    def from_public_dict(cls, data: dict, *, user_id: str) -> Attachment:
        """Rebuild an attachment from its public dict.

        Only the identity and metadata cross: the bytes are
        never in the public dict, so a rebuilt attachment
        carries no storage and cannot be downloaded until the
        store re-registers it. Used by the turn bridge, which
        only needs kind / mime / transcript / filename.
        """
        if not isinstance(data, dict) or not data.get("id"):
            raise AttachmentError("attachment must be a dict with an 'id'")
        kind = str(data.get("kind") or "")
        if kind not in ("image", "audio", "file"):
            raise AttachmentError(f"unknown attachment kind {kind!r}")
        return cls(
            id=str(data["id"]),
            user_id=user_id,
            kind=kind,
            mime_type=str(data.get("mime_type") or ""),
            size_bytes=int(data.get("size_bytes") or 0),
            filename=data.get("filename"),
            storage={"type": "url", "url": data["url"]} if data.get("url") else {"type": "missing"},
            transcript=data.get("transcript"),
        )


@dataclass
class AttachmentStore:
    """Where attachment bytes live, per user.

    ``root`` is the base directory (the PWA passes the share
    inbox's ``attachments`` subdirectory); files land under
    ``root/<user_id>/<random id><suffix>``. The index is in
    memory: restarting the server forgets the attachments,
    exactly like the in-memory chat store forgets its messages.
    """

    root: str | Path
    _index: dict[tuple[str, str], Attachment] = field(default_factory=dict, repr=False)

    def _user_dir(self, user_id: str) -> Path:
        return Path(self.root) / user_id

    def store_bytes(
        self,
        user_id: str,
        data: bytes,
        mime_type: str,
        *,
        filename: str | None = None,
        transcript: str | None = None,
    ) -> Attachment:
        """Validate and store one upload; return the attachment."""
        kind = attachment_kind_for_mime(mime_type)
        if not data:
            raise AttachmentError("empty attachment upload")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise AttachmentError(
                f"attachment is {len(data)} bytes, over the {MAX_ATTACHMENT_BYTES} byte limit"
            )
        attachment_id = uuid.uuid4().hex
        safe_name = _safe_filename(filename, mime_type)
        if kind == "image" and len(data) <= MAX_INLINE_BYTES:
            storage: dict = {
                "type": "inline",
                "data": base64.b64encode(data).decode("ascii"),
            }
        else:
            user_dir = self._user_dir(user_id)
            user_dir.mkdir(parents=True, exist_ok=True)
            path = user_dir / f"{attachment_id}{_MIME_SUFFIX.get(mime_type, '')}"
            path.write_bytes(data)
            storage = {"type": "file", "path": str(path)}
        attachment = Attachment(
            id=attachment_id,
            user_id=user_id,
            kind=kind,
            mime_type=mime_type,
            size_bytes=len(data),
            filename=safe_name,
            storage=storage,
            transcript=transcript,
        )
        self._index[(user_id, attachment_id)] = attachment
        return attachment

    def store_url(
        self,
        user_id: str,
        url: str,
        mime_type: str,
        *,
        filename: str | None = None,
    ) -> Attachment:
        """Register a remote image (a generated image the provider hosts).

        Nothing is downloaded: the attachment points at the
        provider's URL, and the download route redirects to it.
        """
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise AttachmentError("image URL must be an http(s) URL")
        kind = attachment_kind_for_mime(mime_type)
        if kind != "image":
            raise AttachmentError("only an image can be stored by URL")
        attachment = Attachment(
            id=uuid.uuid4().hex,
            user_id=user_id,
            kind=kind,
            mime_type=mime_type,
            size_bytes=0,
            filename=_safe_filename(filename, mime_type),
            storage={"type": "url", "url": url},
        )
        self._index[(user_id, attachment.id)] = attachment
        return attachment

    def adopt_file(
        self,
        user_id: str,
        path: str | Path,
        mime_type: str,
        *,
        filename: str | None = None,
        transcript: str | None = None,
    ) -> Attachment:
        """Register a file another module already wrote.

        The voice pipeline keeps a recording under the share
        inbox when the user asked to keep it; adopting it here
        makes it a voice-note attachment without copying the
        bytes. The path must already exist.
        """
        kind = attachment_kind_for_mime(mime_type)
        real_path = Path(path)
        if not real_path.is_file():
            raise AttachmentError(f"no file to adopt at {real_path}")
        attachment = Attachment(
            id=uuid.uuid4().hex,
            user_id=user_id,
            kind=kind,
            mime_type=mime_type,
            size_bytes=real_path.stat().st_size,
            filename=_safe_filename(filename, mime_type),
            storage={"type": "file", "path": str(real_path)},
            transcript=transcript,
        )
        self._index[(user_id, attachment.id)] = attachment
        return attachment

    def get(self, user_id: str, attachment_id: str) -> Attachment | None:
        """The attachment, or None when unknown or foreign."""
        return self._index.get((user_id, attachment_id))

    def read_bytes(self, attachment: Attachment) -> bytes:
        """The attachment's bytes (inline or file storage)."""
        storage = attachment.storage
        if storage.get("type") == "inline":
            try:
                return base64.b64decode(storage.get("data") or "", validate=True)
            except (binascii.Error, ValueError) as exc:
                raise AttachmentError("stored image bytes are not valid base64") from exc
        if storage.get("type") == "file":
            path = Path(storage.get("path") or "")
            if not path.is_file():
                raise AttachmentError("stored attachment file is gone")
            return path.read_bytes()
        raise AttachmentError(
            f"attachment {attachment.id} has no downloadable bytes "
            f"(storage {storage.get('type')!r})"
        )

    def image_source(self, attachment: Attachment) -> dict:
        """The image source a turn image block wants.

        Inline bytes become a base64 source, a file is read and
        becomes one too, and a provider URL stays a URL — the
        same shapes :class:`puspa.turns.messages.InternalMessage`
        already accepts.
        """
        if attachment.kind != "image":
            raise AttachmentError(f"attachment {attachment.id} is not an image")
        if attachment.storage.get("type") == "url":
            return {"type": "url", "url": attachment.storage.get("url") or ""}
        data = self.read_bytes(attachment)
        return {
            "type": "base64",
            "media_type": attachment.mime_type,
            "data": base64.b64encode(data).decode("ascii"),
        }
