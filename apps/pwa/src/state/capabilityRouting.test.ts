/**
 * Tests for the per-capability routing state helpers (COW-08).
 * Pure helpers only: no fetch, no server.
 */
import { describe, expect, it } from "vitest";
import {
  CAPABILITIES,
  CAPABILITY_PROVIDERS,
  defaultCapabilityRouting,
  describeCapability,
  providerNeedsModel,
} from "./capabilityRouting";

describe("defaultCapabilityRouting", () => {
  it("covers all five capabilities with hosted defaults except image and speech", () => {
    const routing = defaultCapabilityRouting();
    expect(Object.keys(routing.capabilities).sort()).toEqual([...CAPABILITIES].sort());
    expect(routing.capabilities.chat.provider).toBe("hosted");
    expect(routing.capabilities.vision.provider).toBe("hosted");
    expect(routing.capabilities.embeddings.provider).toBe("hosted");
    expect(routing.capabilities.image.provider).toBeNull();
    expect(routing.capabilities.speech.provider).toBeNull();
    for (const route of Object.values(routing.capabilities)) {
      expect(route.is_default).toBe(true);
      expect(route.has_key).toBe(false);
    }
  });

  it("lists providers per capability, and deepseek cannot serve vision", () => {
    expect(CAPABILITY_PROVIDERS.vision).not.toContain("deepseek");
    expect(CAPABILITY_PROVIDERS.image).not.toContain("groq");
    expect(CAPABILITY_PROVIDERS.image).not.toContain("hosted");
    expect(CAPABILITY_PROVIDERS.speech).toContain("groq");
    expect(CAPABILITY_PROVIDERS.embeddings).not.toContain("groq");
  });
});

describe("describeCapability", () => {
  it("explains every capability in one line", () => {
    for (const capability of CAPABILITIES) {
      const text = describeCapability(capability);
      expect(typeof text).toBe("string");
      expect(text.length).toBeGreaterThan(0);
    }
  });
});

describe("providerNeedsModel", () => {
  it("hosted and ollama never need a model, named providers do", () => {
    expect(providerNeedsModel("chat", "hosted")).toBe(false);
    expect(providerNeedsModel("chat", "ollama")).toBe(false);
    expect(providerNeedsModel("chat", "groq")).toBe(true);
    expect(providerNeedsModel("image", "openai")).toBe(true);
  });
});
