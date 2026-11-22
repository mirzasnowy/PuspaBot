"""Image understanding (COW-19).

One question about one picture, answered by the vision model
the user routed in Settings (:mod:`puspa.pwa.capability_routing`).
The picture reaches the model as an image block on a user
message — the same internal format the turn loop already
sends — so this module never builds wire JSON itself: it
converts the message with :func:`puspa.turns.messages.to_chat_completions`
and hands the wire list to
:func:`puspa.pwa.capability_routing.complete_vision_for_user`,
which picks the routed vision provider, model, and vault key.

Which provider answers is the user's routing choice, not this
module's: ``hosted`` means the router's vision chain as today,
a BYOK route means that provider's vision model. A provider
that cannot see the picture fails loudly at call time
(:class:`puspa.router.complete.CompletionError`); a missing
route or key is refused before any call (``ValueError`` /
``KeyError`` from the routing module). No live provider key
is ever in this module: tests inject an ``httpx`` client or
transport and a fake vault key, and the suite never touches a
real vision endpoint.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx

from puspa.turns.messages import InternalMessage, to_chat_completions

from .attachments import Attachment, AttachmentError, AttachmentStore

__all__ = ["VisionDescription", "describe_image"]

#: What the model is asked when the caller gives no question.
DEFAULT_VISION_PROMPT = "Describe what is in this image."


@dataclass(frozen=True)
class VisionDescription:
    """What the vision model said about one picture."""

    text: str
    provider: str
    model: str | None


def _source_for(
    *,
    attachment: Attachment | None,
    store: AttachmentStore | None,
    image_bytes: bytes | None,
    mime_type: str | None,
    image_url: str | None,
) -> dict:
    """The image source for one of the three ways to give a picture."""
    if attachment is not None:
        if attachment.kind != "image":
            raise AttachmentError(f"attachment {attachment.id} is not an image")
        if attachment.storage.get("type") == "url" and attachment.storage.get("url"):
            return {"type": "url", "url": attachment.storage["url"]}
        if store is not None:
            stored = store.get(attachment.user_id, attachment.id)
            if stored is not None:
                return store.image_source(stored)
        raise AttachmentError(f"attachment {attachment.id} has no bytes the vision call can send")
    if image_bytes is not None:
        if not mime_type:
            raise AttachmentError("mime_type is required with image_bytes")
        return {
            "type": "base64",
            "media_type": mime_type,
            "data": base64.b64encode(image_bytes).decode("ascii"),
        }
    if image_url:
        return {"type": "url", "url": image_url}
    raise AttachmentError("describe_image needs an attachment, image_bytes, or image_url")


def describe_image(
    prompt: str | None,
    *,
    user_id,
    attachment: Attachment | None = None,
    store: AttachmentStore | None = None,
    image_bytes: bytes | None = None,
    mime_type: str | None = None,
    image_url: str | None = None,
    dsn=None,
    transport=None,
    client: httpx.Client | None = None,
) -> VisionDescription:
    """Ask the routed vision model about one picture.

    Give the picture exactly one way: a stored ``attachment``
    (plus its ``store``), raw ``image_bytes`` with a
    ``mime_type``, or a public ``image_url``. ``prompt`` is the
    question; empty means "describe this image".
    """
    from puspa.pwa.capability_routing import complete_vision_for_user

    source = _source_for(
        attachment=attachment,
        store=store,
        image_bytes=image_bytes,
        mime_type=mime_type,
        image_url=image_url,
    )
    question = (prompt or "").strip() or DEFAULT_VISION_PROMPT
    message = InternalMessage.user(question, images=[source])
    wire = to_chat_completions([message])
    if wire.dropped:
        raise AttachmentError(f"image could not be sent to the vision model: {wire.dropped[0]}")
    result = complete_vision_for_user(
        wire.value,
        user_id=user_id,
        dsn=dsn,
        transport=transport,
        client=client,
    )
    return VisionDescription(text=result.text, provider=result.provider, model=result.model)
