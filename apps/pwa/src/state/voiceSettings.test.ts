import { describe, expect, it } from "vitest";
import {
  DEFAULT_EDGE_VOICE,
  EDGE_TTS_VOICES,
  defaultVoiceSettings,
  describeAsrProvider,
  describeTtsProvider,
  ttsProviderNeedsKey,
  validateBaseUrl,
  withVoiceDefaults,
} from "./voiceSettings";

describe("defaultVoiceSettings", () => {
  it("defaults to free Edge TTS with an Indonesian voice and no ASR", () => {
    const settings = defaultVoiceSettings();
    expect(settings.tts_provider).toBe("edge");
    expect(settings.tts_voice).toBe("id-ID-GadisNeural");
    expect(settings.asr_provider).toBe("none");
    expect(settings.has_asr_key).toBe(false);
    expect(settings.has_tts_key).toBe(false);
  });
});

describe("EDGE_TTS_VOICES", () => {
  it("offers Indonesian voices first and defaults to one", () => {
    const ids = EDGE_TTS_VOICES.map((voice) => voice.id);
    expect(ids).toContain("id-ID-GadisNeural");
    expect(ids).toContain("id-ID-ArdiNeural");
    expect(EDGE_TTS_VOICES[0]?.lang).toBe("id-ID");
    expect(DEFAULT_EDGE_VOICE).toBe("id-ID-GadisNeural");
  });
});

describe("describeTtsProvider", () => {
  it("says Edge is free and needs no key, and Azure needs an account", () => {
    expect(describeTtsProvider("edge")).toMatch(/gratis/i);
    expect(describeTtsProvider("azure")).toMatch(/Azure/);
    expect(describeTtsProvider("elevenlabs")).toMatch(/ElevenLabs/);
  });

  it("says ASR none is a stub and openai-compatible needs a key", () => {
    expect(describeAsrProvider("none")).toMatch(/stub/);
    expect(describeAsrProvider("openai-compatible")).toMatch(/kunci/);
  });
});

describe("ttsProviderNeedsKey", () => {
  it("needs a key only for BYOK providers", () => {
    expect(ttsProviderNeedsKey("edge")).toBe(false);
    expect(ttsProviderNeedsKey("browser")).toBe(false);
    expect(ttsProviderNeedsKey("elevenlabs")).toBe(true);
    expect(ttsProviderNeedsKey("openai-compatible")).toBe(true);
    expect(ttsProviderNeedsKey("azure")).toBe(true);
  });
});

describe("withVoiceDefaults", () => {
  it("fills the default Edge voice when the user left it blank", () => {
    const filled = withVoiceDefaults({
      ...defaultVoiceSettings(),
      tts_voice: "",
    });
    expect(filled.tts_voice).toBe(DEFAULT_EDGE_VOICE);
  });

  it("keeps a voice the user picked", () => {
    const kept = withVoiceDefaults({
      ...defaultVoiceSettings(),
      tts_voice: "id-ID-ArdiNeural",
    });
    expect(kept.tts_voice).toBe("id-ID-ArdiNeural");
  });
});

describe("validateBaseUrl", () => {
  it("accepts http(s) URLs and refuses credentials in them", () => {
    expect(validateBaseUrl("https://api.openai.com/v1")).toBeNull();
    expect(validateBaseUrl("https://user:pass@example.com/v1")).not.toBeNull();
    expect(validateBaseUrl("ftp://example.com/v1")).not.toBeNull();
  });
});
