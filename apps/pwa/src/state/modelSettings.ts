/**
 * Own-model selection state for the Settings screen (G33).
 *
 * Pure helpers around the `ModelSettings` wire type from
 * `../api/client`: the three sources the picker offers, the
 * Ollama default base URL, and the same base-URL validation
 * the server enforces (`puspa/pwa/model_settings.py`), so the
 * screen can refuse a bad URL before anything is sent.
 */
import type { ModelSettings, ModelSource } from "../api/client";

export type { ModelSettings, ModelSource };

/** Default base URL for a local model (Ollama's OpenAI-compatible endpoint). */
export const DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1";

/** Default model id asked of a local server when the user names none. */
export const DEFAULT_LOCAL_MODEL = "llama3.1";

/** The three sources the Settings picker offers, in display order. */
export const MODEL_SOURCES: ModelSource[] = ["hosted", "byok", "local"];

/** The selection for a user who never opened the picker. */
export function defaultModelSettings(): ModelSettings {
  return {
    source: "hosted",
    base_url: null,
    model: null,
    provider: null,
    has_byok_key: false,
  };
}

/**
 * Validate a model base URL the way the server does: only
 * http(s) URLs with a host, and no userinfo (so credentials
 * cannot hide in the URL). Returns an error message, or null
 * when the URL is fine.
 */
export function validateBaseUrl(baseUrl: string): string | null {
  const cleaned = baseUrl.trim();
  if (cleaned.length === 0) return "Base URL tidak boleh kosong.";
  let parsed: URL;
  try {
    parsed = new URL(cleaned);
  } catch {
    return "Base URL tidak valid.";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return "Base URL harus memakai http atau https.";
  }
  if (!parsed.hostname) return "Base URL harus punya host.";
  if (parsed.username || parsed.password) {
    return "Base URL tidak boleh berisi kredensial.";
  }
  return null;
}

/** One-line explanation of what each source means, for the picker. */
export function describeSource(source: ModelSource): string {
  switch (source) {
    case "hosted":
      return "Default Puspa. Tidak perlu kunci atau pengaturan apa pun.";
    case "byok":
      return "Pakai kunci API sendiri. Kunci hanya tersimpan di vault terenkripsi, tidak pernah di layar ini.";
    case "local":
      return "Model di komputer sendiri (Ollama). Butuh hardware beneran; untuk kebanyakan pengguna, BYOK lebih realistis.";
  }
}

/**
 * Fill in the per-source defaults for a selection the user is
 * about to save: local gets the Ollama base URL and model when
 * the user left them blank; hosted keeps no overrides.
 */
export function withDefaults(settings: ModelSettings): ModelSettings {
  if (settings.source === "hosted") {
    return { ...settings, base_url: null, model: null, provider: null };
  }
  if (settings.source === "local") {
    return {
      ...settings,
      base_url: settings.base_url?.trim() ? settings.base_url.trim() : DEFAULT_LOCAL_BASE_URL,
      model: settings.model?.trim() ? settings.model.trim() : DEFAULT_LOCAL_MODEL,
      provider: null,
    };
  }
  return {
    ...settings,
    provider: settings.provider?.trim().toLowerCase() || null,
    base_url: settings.base_url?.trim() ? settings.base_url.trim() : null,
    model: settings.model?.trim() ? settings.model.trim() : null,
  };
}
