"""First-class multimodal chat (COW-19).

A message can carry an image, a voice note, or a file — not
just text. CowAgent treats text, images, voice, and files as
first-class message content; PuspaBot's chat log was
text-only, its voice transcription was a stub behind a
confirm flow, and it had no image generation at all. This
package is the real thing, built on the seams that already
exist instead of rebuilding them:

- :mod:`puspa.multimodal.attachments` — attachments stored
  with the message and retrievable by id.
- :mod:`puspa.multimodal.turns` — the bridge between the chat
  log and the turn loop, so an image reaches the model as an
  image block and a voice note as its transcript.
- :mod:`puspa.multimodal.vision` — image understanding through
  the routed vision provider (COW-08).
- :mod:`puspa.multimodal.generation` — image generation
  through the routed image provider, stored as an attachment.
- Voice notes reuse the existing ASR path
  (:mod:`puspa.voice.asr`, V2-30): the upload is transcribed
  through the user's own OpenAI-compatible endpoint, and the
  kept recording rides on the confirmed message as an audio
  attachment. No second ASR is built here.

Exports are lazy (PEP 562), like :mod:`puspa.voice`: importing
this package must not import its submodules.
"""

from __future__ import annotations

import importlib

__all__ = [
    "ALLOWED_FILE_MIME_TYPES",
    "ALLOWED_IMAGE_MIME_TYPES",
    "MAX_ATTACHMENT_BYTES",
    "Attachment",
    "AttachmentError",
    "AttachmentStore",
    "VisionDescription",
    "assistant_message_with_image",
    "attachment_kind_for_mime",
    "chat_message_to_internal",
    "chat_messages_to_internal",
    "describe_image",
    "generate_image_attachment",
]

_LAZY_EXPORTS = {
    "ALLOWED_FILE_MIME_TYPES": (".attachments", "ALLOWED_FILE_MIME_TYPES"),
    "ALLOWED_IMAGE_MIME_TYPES": (".attachments", "ALLOWED_IMAGE_MIME_TYPES"),
    "Attachment": (".attachments", "Attachment"),
    "AttachmentError": (".attachments", "AttachmentError"),
    "AttachmentStore": (".attachments", "AttachmentStore"),
    "MAX_ATTACHMENT_BYTES": (".attachments", "MAX_ATTACHMENT_BYTES"),
    "attachment_kind_for_mime": (".attachments", "attachment_kind_for_mime"),
    "VisionDescription": (".vision", "VisionDescription"),
    "describe_image": (".vision", "describe_image"),
    "generate_image_attachment": (".generation", "generate_image_attachment"),
    "assistant_message_with_image": (".turns", "assistant_message_with_image"),
    "chat_message_to_internal": (".turns", "chat_message_to_internal"),
    "chat_messages_to_internal": (".turns", "chat_messages_to_internal"),
}


def __getattr__(name: str):
    try:
        module_name, attr = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_name, __package__)
    return getattr(module, attr)
