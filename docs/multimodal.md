# Multimodal messages (COW-19)

A chat message can carry an image, a voice note, or a file —
not just text. This page says which providers do what, how
the pieces fit, and what is still a stub or a limit.

## What works

- **Send an attachment** — `POST /api/chat` takes multipart
  `text` plus optional `files`. Each file is validated and
  stored as an attachment on the message
  (`puspa/multimodal/attachments.py`, `AttachmentStore`).
  Accepted: images (`image/png`, `image/jpeg`, `image/webp`,
  `image/gif`), audio (the voice pipeline's types:
  webm/mp4/mp3/wav/ogg/aac/3gpp), files (`application/pdf`,
  `text/plain`, `text/csv`, `text/markdown`). Limit 10 MiB
  per attachment; images up to 2 MiB ride inline (base64)
  with the message, larger ones are stored as files. Video
  is refused: nothing in the app reads video yet.
- **Retrieve an attachment** — `GET /api/chat/attachments/{id}`
  serves the bytes (or redirects for a provider-hosted
  image). Only the owner can download it; anyone else gets
  404. The chat page renders images inline and links voice
  notes and files.
- **Turn pipeline** — `puspa/multimodal/turns.py` converts a
  chat history into turn history (`InternalMessage`): an
  image attachment becomes an image block, so the model sees
  it through the wire format the turn already sends; a voice
  note becomes its transcript, marked as a voice note; a file
  is named, and a small plain-text file (up to 4000
  characters) is inlined so the model can answer about its
  contents. An attachment whose bytes are gone is reported
  as "[image unavailable]", never silently dropped.
- **Image understanding** — `POST /api/multimodal/describe`
  (JSON `attachment_id`, optional `prompt`) asks the routed
  vision model about an attached image
  (`puspa/multimodal/vision.py`). The picture crosses as an
  image block; the answer carries provider and model.
- **Image generation** — `POST /api/multimodal/generate-image`
  (JSON `prompt`, optional `size`) generates a picture
  through the routed image provider and posts it to the chat
  as an assistant message carrying the image
  (`puspa/multimodal/generation.py`). Base64 bytes from the
  provider are stored; a provider URL is registered as a URL
  attachment and never downloaded twice.
- **Voice notes** — the existing voice flow
  (`POST /api/voice/upload` → confirm). Uploading with
  `keep_audio` keeps the recording; confirming attaches it
  to the sent message with its transcript, so the voice note
  stays playable and retrievable. Transcription reuses the
  existing ASR path (V2-30): the user's own OpenAI-compatible
  endpoint with their vault key, else the stub. No second ASR
  is built here. Uploading without `keep_audio` still deletes
  the audio right away and sends text only, as before.

## Providers (per-capability routing, COW-08)

Routes live in Settings (`/api/routing/{capability}`); keys
live only in the vault. See `puspa/pwa/capability_routing.py`
(`CAPABILITY_PROVIDERS`) for the full map:

- **Vision** — default `hosted` (the router's vision chain
  as today). BYOK providers: ollama, openrouter, openai,
  groq, together, mistral, xai, gemini. A chat model that
  cannot see images fails loudly at call time; the routing
  check is provider-level, not model-level.
- **Image generation** — no usable default: it needs a
  provider and a model, so generating without a route is
  refused with "pick a provider in Settings first".
  Providers with an image endpoint: openai, openrouter,
  together, xai, gemini.
- **Speech** — the voice settings stay the speech engines;
  a stored speech route only sets the endpoint + model the
  OpenAI-compatible ASR/TTS engines call. Providers: openai,
  openrouter, groq, deepseek, together.
- **Chat** — the turn's model call already sends image
  blocks (chat completions `image_url`), so a vision-capable
  chat model sees an attached image directly in the turn,
  without the describe endpoint.

No provider key is ever in this code or in the tests: every
provider call in the suite goes through an `httpx`
mock transport with an obviously fake key.

## What is still a stub or a limit

- **In-memory only.** Attachments live in the same in-memory
  store as the chat log: restarting the server forgets both.
  A message that references an attachment after a restart
  shows "[image unavailable]" in the turn instead of the
  picture.
- **No video.** Video uploads are refused (400).
- **No channel media.** Telegram / WhatsApp / other channels
  do not receive media yet: attachments work in the PWA chat
  and the turn pipeline, not in channel inbound.
- **No OCR on attached images.** An image is understood by
  the vision model, not by text extraction; the receipt OCR
  flow (`/api/receipts/*`) is separate and still stub-gated
  by its own accuracy measurement.
- **SPA chat does not render attachments yet.** The Jinja
  fallback chat page renders images and links; the built SPA
  receives the messages (with attachments) in its bootstrap
  data but has no attachment UI. The composer file input is
  in the Jinja template only.
- **Generated images are PNG by assumption.** A provider
  returning base64 bytes is stored as `image/png`; the
  generation endpoint does not report the image's real media
  type.
- **Voice notes need `keep_audio`.** By default the recording
  is deleted after transcription and only the transcript is
  sent; the audio attachment exists only when the upload
  asked to keep it.
