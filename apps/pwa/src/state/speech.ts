/**
 * Speaking a reply out loud (V2-30).
 *
 * Chain, in order: ask the server (`POST /api/voice/speak`,
 * Edge TTS by default) and play the returned audio; when the
 * server says to fall back, or the request fails outright,
 * speak with the browser's built-in voice (`speechSynthesis`).
 * The reply text always stays visible in the chat thread, so
 * the spoken reply is never the only copy of what was said
 * (accessibility guide: audio content needs a text equivalent,
 * and here the text is the message itself).
 *
 * Everything the browser gives us is injected through `deps`,
 * so tests can drive the chain with fakes and no real audio.
 */

export type SpeakEngine = "edge" | "browser" | "elevenlabs" | "openai-compatible" | "azure" | "none";

export interface SpeakResult {
  /** Which voice actually spoke, or "none" when nothing could. */
  engine: SpeakEngine;
  /** True when the server voice was unavailable and the browser voice spoke instead. */
  fallback: boolean;
  /** Human-readable reason when nothing spoke. */
  error?: string;
}

/** Minimal audio player: what `new Audio(url)` gives us. */
export interface SpeakAudio {
  play: () => Promise<void>;
  addEventListener: (type: string, listener: () => void) => void;
}

export interface SpeakDeps {
  fetchFn?: typeof fetch;
  /** Browser speech synthesis, or undefined when the browser has none. */
  speechSynthesis?: SpeechSynthesis | undefined;
  /** Builds an utterance; defaults to `new SpeechSynthesisUtterance(text)`. */
  utteranceCtor?: (text: string) => SpeechSynthesisUtterance;
  /** Builds an audio player for a blob URL; defaults to `new Audio(url)`. */
  audioCtor?: (url: string) => SpeakAudio;
  createObjectUrl?: (blob: Blob) => string;
  revokeObjectUrl?: (url: string) => void;
}

function pickBrowserVoice(
  synth: SpeechSynthesis,
  lang: string,
): SpeechSynthesisVoice | undefined {
  const voices = synth.getVoices();
  return (
    voices.find((voice) => voice.lang === lang) ??
    voices.find((voice) => voice.lang.startsWith(lang.slice(0, 2))) ??
    voices.find((voice) => voice.default)
  );
}

function speakWithBrowser(
  text: string,
  synth: SpeechSynthesis | undefined,
  utteranceCtor: (text: string) => SpeechSynthesisUtterance,
): boolean {
  if (!synth) return false;
  try {
    const utterance = utteranceCtor(text);
    utterance.lang = "id-ID";
    const voice = pickBrowserVoice(synth, "id-ID");
    if (voice) utterance.voice = voice;
    synth.speak(utterance);
    return true;
  } catch {
    return false;
  }
}

/**
 * Speak `text` through the server voice, falling back to the
 * browser voice. Never throws: every failure is reported in
 * the returned `SpeakResult`.
 */
export async function speakReply(text: string, deps: SpeakDeps = {}): Promise<SpeakResult> {
  const fetchFn = deps.fetchFn ?? fetch;
  const utteranceCtor =
    deps.utteranceCtor ?? ((value: string) => new SpeechSynthesisUtterance(value));
  const audioCtor = deps.audioCtor ?? ((url: string) => new Audio(url) as SpeakAudio);
  const createObjectUrl = deps.createObjectUrl ?? ((blob: Blob) => URL.createObjectURL(blob));
  const revokeObjectUrl = deps.revokeObjectUrl ?? ((url: string) => URL.revokeObjectURL(url));
  const cleaned = text.trim();
  if (!cleaned) return { engine: "none", fallback: false, error: "Tidak ada teks untuk dibacakan." };

  let serverAudio: Blob | null = null;
  let serverEngine: SpeakEngine = "edge";
  let serverSaidFallback = false;
  try {
    const response = await fetchFn("/api/voice/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: cleaned }),
      credentials: "same-origin",
    });
    const contentType = response.headers.get("content-type") ?? "";
    if (response.ok && contentType.includes("audio")) {
      serverAudio = await response.blob();
      const header = response.headers.get("x-voice-engine") ?? "edge";
      serverEngine = (
        ["edge", "elevenlabs", "openai-compatible", "azure"] as SpeakEngine[]
      ).includes(header as SpeakEngine)
        ? (header as SpeakEngine)
        : "edge";
    } else if (!response.ok || contentType.includes("json")) {
      // A JSON body (or any failed speak) means "use the browser
      // voice": the server reports { fallback: "browser" } for
      // both a configured browser provider and a failed Edge call.
      serverSaidFallback = true;
    }
  } catch {
    serverSaidFallback = true;
  }

  if (serverAudio) {
    const url = createObjectUrl(serverAudio);
    try {
      const audio = audioCtor(url);
      audio.addEventListener("ended", () => revokeObjectUrl(url));
      await audio.play();
      return { engine: serverEngine, fallback: false };
    } catch {
      revokeObjectUrl(url);
      // The file arrived but this browser would not play it:
      // fall through to the browser voice below.
    }
  }

  if (serverSaidFallback || serverAudio) {
    const spoke = speakWithBrowser(cleaned, deps.speechSynthesis, utteranceCtor);
    if (spoke) return { engine: "browser", fallback: true };
  }
  return {
    engine: "none",
    fallback: serverSaidFallback,
    error: "Tidak ada suara yang bisa membacakan balasan di perangkat ini.",
  };
}
