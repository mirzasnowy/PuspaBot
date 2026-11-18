import { describe, expect, it } from "vitest";
import {
  DEFAULT_LOCAL_BASE_URL,
  DEFAULT_LOCAL_MODEL,
  defaultModelSettings,
  describeSource,
  validateBaseUrl,
  withDefaults,
} from "./modelSettings";

describe("modelSettings state", () => {
  it("defaults to hosted with no overrides", () => {
    expect(defaultModelSettings()).toEqual({
      source: "hosted",
      base_url: null,
      model: null,
      provider: null,
      has_byok_key: false,
    });
  });

  it("accepts http and https base URLs", () => {
    expect(validateBaseUrl("http://localhost:11434/v1")).toBeNull();
    expect(validateBaseUrl("https://api.example.com/v1")).toBeNull();
  });

  it("rejects non-http schemes, missing hosts, and userinfo", () => {
    expect(validateBaseUrl("ftp://example.com/v1")).not.toBeNull();
    expect(validateBaseUrl("localhost:11434/v1")).not.toBeNull();
    expect(validateBaseUrl("https://user:secret@example.com/v1")).not.toBeNull();
    expect(validateBaseUrl("")).not.toBeNull();
  });

  it("fills Ollama defaults for a blank local selection", () => {
    const filled = withDefaults({
      source: "local",
      base_url: null,
      model: null,
      provider: null,
      has_byok_key: false,
    });
    expect(filled.base_url).toBe(DEFAULT_LOCAL_BASE_URL);
    expect(filled.model).toBe(DEFAULT_LOCAL_MODEL);
    expect(filled.provider).toBeNull();
  });

  it("clears overrides for hosted", () => {
    const cleared = withDefaults({
      source: "hosted",
      base_url: "http://localhost:11434/v1",
      model: "llama3.1",
      provider: "openrouter",
      has_byok_key: true,
    });
    expect(cleared.base_url).toBeNull();
    expect(cleared.model).toBeNull();
    expect(cleared.provider).toBeNull();
  });

  it("normalizes the BYOK provider name", () => {
    const normalized = withDefaults({
      source: "byok",
      base_url: null,
      model: null,
      provider: "OpenRouter",
      has_byok_key: false,
    });
    expect(normalized.provider).toBe("openrouter");
  });

  it("describes all three sources", () => {
    for (const source of ["hosted", "byok", "local"] as const) {
      expect(describeSource(source).length).toBeGreaterThan(0);
    }
  });
});
