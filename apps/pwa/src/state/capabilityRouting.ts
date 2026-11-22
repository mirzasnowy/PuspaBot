/**
 * Per-capability routing state for the Settings screen (COW-08).
 *
 * Pure helpers around the `CapabilityRouting` wire type from
 * `../api/client`: the five capabilities in display order, the
 * providers that can serve each one, and one-line explanations
 * for the picker. The server is the source of truth for the
 * provider lists (GET /api/routing returns them); the lists
 * here are the fallback the screen shows before the table loads.
 */
import type { CapabilityName, CapabilityRoute, CapabilityRouting } from "../api/client";

export type { CapabilityName, CapabilityRoute, CapabilityRouting };

/** The five routable capabilities, in console display order. */
export const CAPABILITIES: CapabilityName[] = ["chat", "vision", "image", "speech", "embeddings"];

/**
 * Providers that can serve each capability, as the server
 * enforces them. Keep in sync with
 * `puspa/pwa/capability_routing.py` (CAPABILITY_PROVIDERS);
 * the server refuses anything outside these lists.
 */
export const CAPABILITY_PROVIDERS: Record<CapabilityName, string[]> = {
  chat: ["hosted", "ollama", "openrouter", "openai", "deepseek", "groq", "together", "mistral", "xai", "gemini"],
  vision: ["hosted", "ollama", "openrouter", "openai", "groq", "together", "mistral", "xai", "gemini"],
  image: ["openai", "openrouter", "together", "xai", "gemini"],
  speech: ["openai", "openrouter", "groq", "deepseek", "together"],
  embeddings: ["hosted", "ollama", "openai", "openrouter", "together", "mistral", "gemini"],
};

/** The routing table for a user who never opened the control. */
export function defaultCapabilityRouting(): CapabilityRouting {
  const capabilities = {} as Record<CapabilityName, CapabilityRoute>;
  for (const capability of CAPABILITIES) {
    capabilities[capability] = {
      capability,
      provider: capability === "image" || capability === "speech" ? null : "hosted",
      model: null,
      base_url: null,
      has_key: false,
      is_default: true,
    };
  }
  return { capabilities, providers: CAPABILITY_PROVIDERS };
}

/** One-line explanation of what each capability is for, for the picker. */
export function describeCapability(capability: CapabilityName): string {
  switch (capability) {
    case "chat":
      return "Obrolan utama. Defaultnya ikut pilihan model di atas; atur di sini untuk menimpa khusus chat.";
    case "vision":
      return "Membaca gambar dan label (misalnya foto struk atau kemasan).";
    case "image":
      return "Membuat gambar. Butuh provider dan model; belum ada default.";
    case "speech":
      return "Suara masuk dan keluar (transkripsi dan bicara) lewat endpoint yang kompatibel dengan OpenAI.";
    case "embeddings":
      return "Vektor untuk ingatan (pencarian memori). Defaultnya model lokal di perangkat sendiri.";
  }
}

/** Whether a provider needs a model id for this capability (hosted never does). */
export function providerNeedsModel(capability: CapabilityName, provider: string): boolean {
  if (provider === "hosted" || provider === "ollama") return false;
  return CAPABILITY_PROVIDERS[capability].includes(provider);
}
