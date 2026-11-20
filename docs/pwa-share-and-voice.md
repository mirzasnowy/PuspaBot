# PWA share target and voice messages (P-03, plan section 12.4)

Two ways to get content into Puspa from outside the chat box: share
from another app, and talk instead of type. Both land as a chat
message only after the user confirms — nothing is ever sent from a
share or a recording alone.

## Web Share Target

The installed PWA appears in Android's share menu. The manifest
(`GET /manifest.json`) declares one `share_target` (`POST /share`,
multipart) accepting a title, text, a URL, and up to 5 files
(images, PDF, plain text, 10 MiB each).

Landing, by share kind:

- **Text / URL** (WhatsApp forward, browser share): `GET /share` or
  `POST /share` renders the Chat screen with the shared content
  prefilled as a draft. The user reviews it and taps Send.
- **Files** (gallery photos): the files are stored in the per-user
  share inbox (`PUSPA_SHARE_INBOX_DIR`, default
  `~/.puspa/share-inbox/<user_id>/`) and announced in chat as one
  message listing each file, so the agent can read them later. The
  response redirects to `/chat` (303). Videos are refused: nothing
  in the app reads video yet, and storing bytes nobody can use is
  worse than a clear error.

A share with no text, no URL, and no files redirects to `/chat`.
Anonymous shares redirect to `/masuk`, like every other screen.

This is the "forward to Puspa" flow the plan keeps alive. It is
**not** WhatsApp integration: Meta's WhatsApp Business API terms
(effective 15 Jan 2026) bar general-purpose AI assistants, and the
plan puts beta on Telegram + PWA instead. WhatsApp is out of scope
for this item; sharing *from* WhatsApp into the PWA needs no
WhatsApp API at all.

### Platform support

| Platform | Share target | Notes |
| --- | --- | --- |
| Android + Chrome | Supported | Full: text, URL, and files from the share menu. |
| Android + other Chromium browsers | Supported | Same Web Share Target API as Chrome. |
| iOS Safari | **Not supported** | Safari does not implement Web Share Target; an installed PWA never appears in the iOS share sheet. iPhone users share by pasting into the in-app chat instead. |
| Desktop Chrome/Edge | Supported | The PWA appears in the OS share menu where the OS offers one; in practice shares come from phones. |

Verified only in a test harness (manifest + handler routes under
`tests/test_pwa_share_api.py`); no live device was used to check
the Android share sheet.

## Voice messages

The Chat screen has a "Rekam pesan suara" button. The flow is
always: record -> upload -> transcribe -> **show the transcript
for confirmation** (editable) -> send on confirm, or discard.
The transcript is the message; the audio is never acted on
directly, and the server deletes the recording right after
transcribing it (`audio_deleted: true` in the upload response,
unless the caller explicitly passes `keep_audio`).

API (all require login; contract in `docs/openapi-pwa.yaml`):

- `POST /api/voice/upload` (multipart `audio`) -> pending transcript
  with `pending_id`. 400 on a bad recording (wrong type, empty,
  over 10 MiB).
- `POST /api/voice/confirm` (`pending_id`, optional edited `text`)
  -> sends the transcript as a chat message, exactly as
  `POST /api/chat` would, and drops the pending record.
- `POST /api/voice/discard` (`pending_id`) -> drops the pending
  transcript without sending anything.
- `GET /api/voice/status` -> which transcriber is configured and
  whether engine selection is done.

### Speech-to-text engine: not chosen yet

The pipeline (`puspa/voice/`) is built around a pluggable
transcriber seam (`puspa.voice.transcriber.Transcriber`), and the
only registered transcribers are test doubles (`stub`,
`stub-mapping`). **No real ASR engine is picked here, on purpose:**
plan 12.4 requires measuring word error rate (WER) on 50 real
recordings (slang + regional mix) before choosing, and no such
recordings exist yet — they need real test users.

The measurement harness is ready: `puspa/voice/wer.py`
(`python -m puspa.voice.wer recordings/ --transcriber <name>`)
scores a directory of audio + `.txt` reference pairs, reports the
overall WER plus one line per case, and says plainly whether the
measurement is big enough to choose from (`selection_ready` is
true only at 50+ cases). Until that run exists,
`GET /api/voice/status` reports `engine_selected: false` and
uploads are answered by the test double.

### Platform support

| Platform | Voice recording | Notes |
| --- | --- | --- |
| Android + Chrome | Supported | MediaRecorder + `getUserMedia`; records WebM audio. |
| iOS Safari 14.5+ | Supported | MediaRecorder is available; records MP4 audio. |
| Desktop Chrome/Edge/Firefox | Supported | Microphone permission required, as everywhere. |
| Browsers without MediaRecorder | Not supported | The button stays disabled and says so instead of failing on tap. |

As with the share target, the recording flow is verified in tests
only (`tests/test_pwa_voice_api.py`,
`apps/pwa/src/components/VoiceRecorder.test.tsx`); no live device
recording was made.
