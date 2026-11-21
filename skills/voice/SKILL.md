---
name: "Voice"
description: "Handles voice in both directions: transcribing the user's voice notes and speaking replies aloud with free Edge text-to-speech. Speech recognition needs a configured engine; the bundled transcriber is a stub."
author: "Puspa Team"
---

# Voice

This skill covers voice: understanding the user's voice notes
and speaking replies aloud. One direction is fully real (speaking);
the other is real only with a configured engine (listening).

## When to Use This Skill

Use this skill when the user:

- Sends a voice note and asks what it says, or asks Puspa to act
  on it
- Asks Puspa to read something aloud, or to reply by voice
- Asks to change the speaking voice

## Transcribing a Voice Note

```python
from puspa.voice import VoicePipeline, resolve_transcriber, validate_audio

validate_audio(audio_bytes, mime_type)  # size, type, and duration checks
pipeline = VoicePipeline(transcriber=resolve_transcriber("mapping", mapping={...}))
result = pipeline.transcribe_upload(audio_bytes, mime_type)
transcript = result.transcript
```

- `validate_audio` rejects audio that is too large, in an
  unsupported format, or otherwise unusable — before any
  transcription is attempted. A rejected upload is reported with
  the reason, not silently retried. By default the audio file is
  deleted after transcription (`keep_audio=True` keeps it).
- `available_transcribers()` lists the transcribers this install
  can use; `resolve_transcriber(name, ...)` picks one.

## Speaking Aloud

```python
from puspa.voice.edge_tts import EdgeTTSClient, build_ssml, voice_language

ssml = build_ssml(text, voice="id-ID-ArdiNeural")  # SSML for a chosen voice
audio = await EdgeTTSClient().synthesize(text, voice="id-ID-ArdiNeural")
```

Text-to-speech uses Edge TTS, free by default (default voice
`id-ID-GadisNeural`): `synthesize` takes plain text and a voice
name and returns MP3 bytes; `build_ssml` is the SSML builder for
a chosen voice, and `voice_language` reports the voice's
language. The default voice setup is the free path (BYOK speech
recognition and speech synthesis are the wider option — see the
limits).

## Limits — Read These First

- **No real speech-recognition engine is bundled.** The
  transcribers shipped are `StubTranscriber` (returns empty
  text) and `MappingTranscriber` (returns pre-mapped text for
  known audio, for tests). A voice note transcribed with the
  stub says nothing — that is honest, not a failure to try. Real
  transcription needs a configured engine (BYOK); until one is
  configured, tell the user voice notes cannot be transcribed
  yet instead of inventing what they said.
- **Never invent a transcript.** If transcription returns empty
  or fails, say so and ask the user to type it. A guessed
  transcript is worse than none.
- **Speaking needs the network.** Edge TTS fetches audio from
  the Edge service; if it is unreachable, speaking fails with
  `EdgeTTSError`. Report the failure instead of falling back to
  silence.
- **Audio is validated first.** Oversized or unsupported audio
  is rejected by `validate_audio` — do not bypass the check by
  calling the transcriber directly.

## Example

User sends a voice note: "Tolong ingetin aku bayar PLN besok."

1. `validate_audio`, then transcribe with the configured
   transcriber.
2. If the transcript is empty because only the stub is
   configured, say the voice note cannot be transcribed yet and
   ask the user to type it — do not guess.
3. If it transcribes, act on the text (here: a bill reminder —
   see the `scheduler` skill) and confirm in text.
