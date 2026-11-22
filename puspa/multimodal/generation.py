"""Image generation as a chat attachment (COW-19).

One prompt in, one image attachment out. The generation
itself is the routed image provider call that already exists
(:func:`puspa.pwa.capability_routing.generate_image`): the
user picks the provider and model in Settings, the key lives
only in the vault, and a missing route or key is refused
before any call. This module only turns the result into an
attachment the chat can carry:

- base64 bytes from the provider are stored in the
  :class:`puspa.multimodal.attachments.AttachmentStore`
  (inline when small, as a file when large);
- a provider URL is registered as a URL attachment, so the
  image is served by the provider and nothing is downloaded
  twice.

A provider failure is loud (``RuntimeError`` from the
generation call): no fake image is ever stored. No live
provider key is in this module: tests inject an ``httpx``
transport or client and a fake vault key.
"""

from __future__ import annotations

import base64
import binascii

import httpx

from puspa.pwa.capability_routing import ImageResult, generate_image

from .attachments import Attachment, AttachmentError, AttachmentStore

__all__ = ["generate_image_attachment"]


def generate_image_attachment(
    prompt: str,
    *,
    user_id,
    store: AttachmentStore,
    size: str = "1024x1024",
    dsn=None,
    transport=None,
    client: httpx.Client | None = None,
) -> tuple[Attachment, ImageResult]:
    """Generate one image and store it as an attachment.

    Returns ``(attachment, result)``: the attachment for the
    chat message, and the raw provider result (URL or base64,
    provider, model, revised prompt) for the caller to report.
    """
    result = generate_image(
        prompt,
        user_id=user_id,
        size=size,
        dsn=dsn,
        transport=transport,
        client=client,
    )
    if result.b64_json:
        try:
            data = base64.b64decode(result.b64_json, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise AttachmentError(
                f"{result.provider}/{result.model} returned image bytes that are not valid base64"
            ) from exc
        attachment = store.store_bytes(
            user_id,
            data,
            "image/png",
            filename="generated-image.png",
        )
        return attachment, result
    if result.url:
        attachment = store.store_url(
            user_id,
            result.url,
            "image/png",
            filename="generated-image.png",
        )
        return attachment, result
    raise AttachmentError(
        f"{result.provider}/{result.model} returned an image with neither URL nor bytes"
    )
