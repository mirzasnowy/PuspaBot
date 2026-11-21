import { describe, expect, it, vi } from "vitest";
import { speakReply } from "./speech";

function audioResponse(audio: Blob, engine = "edge") {
  return {
    ok: true,
    status: 200,
    headers: {
      get: (name: string) =>
        name === "content-type"
          ? "audio/mpeg"
          : name === "x-voice-engine"
            ? engine
            : null,
    },
    blob: async () => audio,
    json: async () => ({}),
  } as unknown as Response;
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    blob: async () => new Blob([]),
    json: async () => body,
  } as unknown as Response;
}

function fakeSpeech() {
  const spoken: { text: string; lang: string }[] = [];
  const synth = {
    getVoices: () => [{ lang: "id-ID", default: true } as SpeechSynthesisVoice],
    speak: (utterance: SpeechSynthesisUtterance) => {
      spoken.push({ text: utterance.text, lang: utterance.lang });
    },
    cancel: () => undefined,
  } as unknown as SpeechSynthesis;
  const utteranceCtor = (text: string) =>
    ({ text, lang: "", voice: undefined }) as unknown as SpeechSynthesisUtterance;
  return { synth, spoken, utteranceCtor };
}

function fakeAudio() {
  const played: string[] = [];
  const audioCtor = (url: string) => ({
    play: async () => {
      played.push(url);
    },
    addEventListener: () => undefined,
  });
  return { audioCtor, played };
}

describe("speakReply", () => {
  it("plays server audio when Edge TTS succeeds and never touches speechSynthesis", async () => {
    const { synth, spoken } = fakeSpeech();
    const { audioCtor, played } = fakeAudio();
    const fetchMock = vi.fn().mockResolvedValue(audioResponse(new Blob(["MP3"], { type: "audio/mpeg" })));
    const result = await speakReply("Halo, ini balasan.", {
      fetchFn: fetchMock as unknown as typeof fetch,
      speechSynthesis: synth,
      audioCtor,
      createObjectUrl: () => "blob:fake",
      revokeObjectUrl: () => undefined,
    });
    expect(result).toEqual({ engine: "edge", fallback: false });
    expect(played).toEqual(["blob:fake"]);
    expect(spoken).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/voice/speak");
    expect(init.body).toBe(JSON.stringify({ text: "Halo, ini balasan." }));
  });

  it("falls back to the browser voice when Edge TTS fails with a 502", async () => {
    const { synth, spoken, utteranceCtor } = fakeSpeech();
    const { audioCtor, played } = fakeAudio();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ error: "blocked", fallback: "browser" }, 502));
    const result = await speakReply("Halo.", {
      fetchFn: fetchMock as unknown as typeof fetch,
      speechSynthesis: synth,
      utteranceCtor,
      audioCtor,
    });
    expect(result.engine).toBe("browser");
    expect(result.fallback).toBe(true);
    expect(played).toEqual([]);
    expect(spoken).toHaveLength(1);
    expect(spoken[0]?.text).toBe("Halo.");
    expect(spoken[0]?.lang).toBe("id-ID");
  });

  it("uses the browser voice when the server says the provider is browser", async () => {
    const { synth, spoken, utteranceCtor } = fakeSpeech();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ ok: true, fallback: "browser", engine: "browser" }));
    const result = await speakReply("Halo.", {
      fetchFn: fetchMock as unknown as typeof fetch,
      speechSynthesis: synth,
      utteranceCtor,
    });
    expect(result).toEqual({ engine: "browser", fallback: true });
    expect(spoken).toHaveLength(1);
  });

  it("falls back to the browser voice when the request itself fails", async () => {
    const { synth, spoken, utteranceCtor } = fakeSpeech();
    const fetchMock = vi.fn().mockRejectedValue(new Error("network down"));
    const result = await speakReply("Halo.", {
      fetchFn: fetchMock as unknown as typeof fetch,
      speechSynthesis: synth,
      utteranceCtor,
    });
    expect(result.engine).toBe("browser");
    expect(result.fallback).toBe(true);
    expect(spoken).toHaveLength(1);
  });

  it("reports none when no voice at all is available", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("network down"));
    const result = await speakReply("Halo.", {
      fetchFn: fetchMock as unknown as typeof fetch,
      speechSynthesis: undefined,
    });
    expect(result.engine).toBe("none");
    expect(result.error).toMatch(/Tidak ada suara/);
  });

  it("refuses empty text without calling anything", async () => {
    const fetchMock = vi.fn();
    const result = await speakReply("   ", { fetchFn: fetchMock as unknown as typeof fetch });
    expect(result.engine).toBe("none");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
