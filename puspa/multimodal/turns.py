"""Chat messages <-> turn history (COW-19).

The PWA chat log (plain dicts in ``app.state.chat``) and the
turn loop (``puspa.turns.messages.InternalMessage``) spoke
different languages: a photo on a chat message never reached
the model, and a generated image never came back as a message.
This module is the bridge, both directions:

- :func:`chat_message_to_internal` turns one chat message into
  the turn's format. Text stays text. An image attachment
  becomes an image block, so the model sees it through the
  wire format the turn already sends (chat completions
  ``image_url``, Anthropic, Responses). A voice note becomes
  its transcript, marked as a voice note, so the model answers
  the words the user spoke. A file becomes a short note — and
  a small plain-text file is inlined, so the model can answer
  about its contents instead of only its name.
- :func:`chat_messages_to_internal` converts a whole history,
  skipping messages that carry nothing (an empty text with no
  attachment is not a turn).
- :func:`assistant_message_with_image` builds the chat message
  for a reply that carries a generated image, in the same dict
  shape the chat page already renders.

A message the bridge cannot read (an attachment id with no
bytes behind it — the store forgot it after a restart) is
reported in the text, never silently dropped: the model sees
"[image unavailable]" instead of a turn that pretends the
photo was never sent.
"""

from __future__ import annotations

import time

from puspa.turns.messages import InternalMessage

from .attachments import TEXT_FILE_MIME_TYPES, Attachment, AttachmentError, AttachmentStore

__all__ = [
    "MAX_INLINE_TEXT_CHARS",
    "assistant_message_with_image",
    "chat_message_to_internal",
    "chat_messages_to_internal",
]

#: Most characters of a text file inlined into the turn.
#: A longer file is named, not pasted: pasting all of it would
#: eat the turn's budget on one attachment.
MAX_INLINE_TEXT_CHARS = 4000


def _attachment_from_dict(data: dict, *, user_id: str) -> Attachment | None:
    """A public attachment dict back into an Attachment, or None."""
    try:
        return Attachment.from_public_dict(data, user_id=user_id)
    except (AttachmentError, ValueError, TypeError, KeyError):
        return None


def _image_block(attachment: Attachment, *, store: AttachmentStore | None) -> dict | None:
    """An image attachment as an internal image source, or None.

    A public dict alone carries no bytes, so an image with no
    store behind it cannot cross — the caller reports that
    instead (see :func:`chat_message_to_internal`).
    """
    if attachment.storage.get("type") == "url" and attachment.storage.get("url"):
        return {"type": "url", "url": attachment.storage["url"]}
    if store is None:
        return None
    stored = store.get(attachment.user_id, attachment.id)
    if stored is None:
        return None
    try:
        return store.image_source(stored)
    except AttachmentError:
        return None


def chat_message_to_internal(
    message: dict,
    *,
    store: AttachmentStore | None = None,
    user_id: str | None = None,
) -> InternalMessage | None:
    """One chat message as an internal turn message, or None.

    ``user_id`` is the message owner (needed to look an
    attachment up in the store); without it, image attachments
    cross only when they already carry a provider URL. Returns
    None when the message carries neither text nor an
    attachment the turn can use.
    """
    if not isinstance(message, dict):
        return None
    sender = str(message.get("sender") or "")
    role = "user" if sender in ("users", "user") else "assistant"
    text = str(message.get("text") or "")
    owner = user_id or str(message.get("user_id") or "")
    notes: list[str] = []
    images: list[dict] = []
    for raw in message.get("attachments") or []:
        if not isinstance(raw, dict):
            continue
        attachment = _attachment_from_dict(raw, user_id=owner)
        if attachment is None:
            continue
        if attachment.kind == "image":
            source = _image_block(attachment, store=store)
            if source is not None:
                images.append(source)
            else:
                notes.append("[image unavailable: the attachment is no longer stored]")
        elif attachment.kind == "audio":
            if attachment.transcript:
                notes.append(f"[voice note: {attachment.transcript}]")
            else:
                notes.append("[voice note: no transcript yet]")
        elif attachment.kind == "file":
            name = attachment.filename or "file"
            notes.append(
                f"[file: {name} ({attachment.mime_type or 'unknown type'}, "
                f"{attachment.size_bytes} bytes)]"
            )
            if (
                attachment.mime_type in TEXT_FILE_MIME_TYPES
                and store is not None
                and attachment.size_bytes <= MAX_INLINE_TEXT_CHARS
            ):
                stored = store.get(owner, attachment.id)
                if stored is not None:
                    try:
                        content = store.read_bytes(stored).decode("utf-8", errors="replace")
                    except AttachmentError:
                        content = ""
                    if content:
                        notes.append(content[:MAX_INLINE_TEXT_CHARS])
    full_text = text
    if notes:
        joined_notes = "\n".join(notes)
        full_text = f"{text}\n{joined_notes}" if text else joined_notes
    if role == "user":
        if not full_text and not images:
            return None
        return InternalMessage.user(full_text, images=images)
    if not full_text and not images:
        return None
    blocks: list[dict] = []
    if full_text:
        blocks.append({"type": "text", "text": full_text})
    for image in images:
        blocks.append({"type": "image", "source": dict(image)})
    return InternalMessage(role="assistant", blocks=blocks)


def chat_messages_to_internal(
    messages: list[dict],
    *,
    store: AttachmentStore | None = None,
    user_id: str | None = None,
) -> list[InternalMessage]:
    """A chat history as turn history, dropping empty messages."""
    converted: list[InternalMessage] = []
    for message in messages or []:
        internal = chat_message_to_internal(message, store=store, user_id=user_id)
        if internal is not None:
            converted.append(internal)
    return converted


def assistant_message_with_image(
    text: str,
    attachment: Attachment,
    *,
    sender: str = "puspa",
) -> dict:
    """A chat message for an assistant reply carrying an image.

    The same dict shape the chat page renders: ``sender``,
    ``text``, ``created_at``, and ``attachments`` as public
    dicts, so the image shows under the reply.
    """
    return {
        "sender": sender,
        "text": text,
        "created_at": time.time(),
        "attachments": [attachment.to_public_dict()],
    }
