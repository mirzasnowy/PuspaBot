/**
 * Voice choice state for the Settings screen (V2-30).
 *
 * Pure helpers around the `VoiceSettings` wire type from
 * `../api/client`: the ASR and TTS providers the picker offers,
 * the Edge TTS voices (Indonesian first), and the same base-URL
 * validation the server enforces (`puspa/pwa/voice_settings.py`),
 * so the screen can refuse a bad URL before anything is sent.
 */
import type { VoiceSettings } from "../api/client";
import { validateBaseUrl } from "./modelSettings";

export type { VoiceSettings };

/** ASR providers the Settings picker offers. */
export type AsrProvider = VoiceSettings["asr_provider"];

/** TTS providers the Settings picker offers. */
export type TtsProvider = VoiceSettings["tts_provider"];

/** The ASR providers, in display order. */
export const ASR_PROVIDERS: AsrProvider[] = ["none", "openai-compatible"];

/** The TTS providers, in display order (free default first). */
export const TTS_PROVIDERS: TtsProvider[] = [
  "edge",
  "browser",
  "elevenlabs",
  "openai-compatible",
  "azure",
];

/** One Edge TTS voice: id, display label, language tag. */
export interface EdgeVoice {
  id: string;
  label: string;
  lang: string;
}

/**
 * Edge TTS voices offered in Settings. Indonesian first (the
 * default), then a small English set. Mirrors
 * `puspa/voice/edge_tts.py` (EDGE_TTS_VOICES); keep both lists
 * in step when a voice is added.
 */
export const EDGE_TTS_VOICES: EdgeVoice[] = [
  { id: "id-ID-GadisNeural", label: "Gadis (Indonesia, perempuan)", lang: "id-ID" },
  { id: "id-ID-ArdiNeural", label: "Ardi (Indonesia, laki-laki)", lang: "id-ID" },
  { id: "en-US-AriaNeural", label: "Aria (Amerika, perempuan)", lang: "en-US" },
  { id: "en-US-GuyNeural", label: "Guy (Amerika, laki-laki)", lang: "en-US" },
  { id: "en-US-JennyNeural", label: "Jenny (Amerika, perempuan)", lang: "en-US" },
  { id: "en-GB-SoniaNeural", label: "Sonia (Inggris, perempuan)", lang: "en-GB" },
  { id: "en-GB-RyanNeural", label: "Ryan (Inggris, laki-laki)", lang: "en-GB" },
  { id: "en-AU-NatashaNeural", label: "Natasha (Australia, perempuan)", lang: "en-AU" },
];

/** The voice a user who never opened Settings hears. */
export const DEFAULT_EDGE_VOICE = "id-ID-GadisNeural";

/** The selection for a user who never opened the picker. */
export function defaultVoiceSettings(): VoiceSettings {
  return {
    asr_provider: "none",
    asr_base_url: null,
    asr_model: null,
    tts_provider: "edge",
    tts_voice: DEFAULT_EDGE_VOICE,
    tts_model: null,
    tts_base_url: null,
    tts_region: null,
    has_asr_key: false,
    has_tts_key: false,
  };
}

/** One-line explanation of what each ASR provider means, for the picker. */
export function describeAsrProvider(provider: AsrProvider): string {
  switch (provider) {
    case "none":
      return "Tidak ada transkripsi sungguhan; rekaman dijawab transkrip kosong (stub).";
    case "openai-compatible":
      return "Transkripsi lewat endpoint speech milikmu (OpenAI, Groq, atau server lokal). Butuh kunci, tersimpan di vault.";
  }
}

/** One-line explanation of what each TTS provider means, for the picker. */
export function describeTtsProvider(provider: TtsProvider): string {
  switch (provider) {
    case "edge":
      return "Default gratis, tanpa kunci: suara neural Microsoft, termasuk bahasa Indonesia.";
    case "browser":
      return "Suara bawaan browser ini (speechSynthesis). Gratis, tapi kualitas tergantung perangkat.";
    case "elevenlabs":
      return "Suara ElevenLabs. Butuh kunci, tersimpan di vault.";
    case "openai-compatible":
      return "Endpoint TTS milikmu yang bicara format OpenAI (model tts-1 dan sejenisnya). Butuh kunci, tersimpan di vault.";
    case "azure":
      return "Azure Speech tier F0: gratis selamanya (500 ribu karakter/bulan), tapi butuh akun Azure dan region.";
  }
}

/**
 * Whether the picked TTS provider needs a key in the vault.
 * Edge and browser need none.
 */
export function ttsProviderNeedsKey(provider: TtsProvider): boolean {
  return provider === "elevenlabs" || provider === "openai-compatible" || provider === "azure";
}

/**
 * Fill in the per-provider defaults for a selection the user is
 * about to save: Edge gets the default Indonesian voice when the
 * user left it blank; the rest keep what the user typed.
 */
export function withVoiceDefaults(settings: VoiceSettings): VoiceSettings {
  if (settings.tts_provider === "edge") {
    return {
      ...settings,
      tts_voice: settings.tts_voice?.trim() ? settings.tts_voice.trim() : DEFAULT_EDGE_VOICE,
    };
  }
  return { ...settings };
}

export { validateBaseUrl };
